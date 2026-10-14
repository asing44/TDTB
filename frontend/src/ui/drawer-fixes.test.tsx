/* Drawer fixes (operator 2026-10): the preview/commit drawer must show a
   refused-preview error instead of the generic empty state, open a small
   exact-writes list by default, render withheld-write notes, label custom
   prompt keys, label Capacities rows, and style the results rows.

   Fixtures and direct store dispatches only — no network, no live calls. */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/preact";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { makeHarness } from "./test-harness";
import { makeScenario } from "../fixtures/scenarios";
import type { CommitReport, ShadowDiff } from "../model/types";

afterEach(cleanup);

function previewShadow(): ShadowDiff {
  return structuredClone(makeScenario("commit-preview").shadow);
}

describe("A1 — a refused preview shows its message, not the empty state", () => {
  it("renders shadowError in the drawer with a working retry action", () => {
    const h = makeHarness("sequenced");
    h.store.dispatch({ type: "SHADOW_FAIL", error: "commit refused: sample refusal" });
    h.store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText(/commit refused: sample refusal/)).toBeTruthy();
    // The generic empty state must not mask the failure.
    expect(r.queryByText(/No preview yet/)).toBeNull();
    const retry = r.getByRole("button", { name: /Retry preview/ }) as HTMLButtonElement;
    expect(retry.disabled).toBe(false);
  });
});

describe("A2 — exact writes open by default for a small preview", () => {
  it("expands a five-entry preview without a click", () => {
    const h = makeHarness("commit-preview");
    const r = h.ui(<ApprovalDrawer />);
    expect(r.container.querySelectorAll(".write-row").length).toBe(5);
  });

  it("keeps a large preview collapsed", () => {
    const h = makeHarness("commit-preview");
    const shadow = previewShadow();
    shadow.entries = Array.from({ length: 13 }, (_, i) => ({
      ...shadow.entries[i % shadow.entries.length],
      name: `Row ${i}`,
    }));
    h.store.dispatch({ type: "SHADOW_OK", shadow });
    const r = h.ui(<ApprovalDrawer />);
    expect(r.container.querySelectorAll(".write-row").length).toBe(0);
  });

  it("expands a large preview when any entry is a conflict", () => {
    const h = makeHarness("commit-preview");
    const shadow = previewShadow();
    shadow.entries = Array.from({ length: 13 }, (_, i) => ({
      ...shadow.entries[i % shadow.entries.length],
      name: `Row ${i}`,
      classification: i === 0 ? "conflict" : "would-create",
    }));
    h.store.dispatch({ type: "SHADOW_OK", shadow });
    const r = h.ui(<ApprovalDrawer />);
    expect(r.container.querySelectorAll(".write-row").length).toBe(13);
  });
});

describe("A3 — withheld writes render as 'Not included in this preview'", () => {
  it("lists the server notes when non-empty", () => {
    const h = makeHarness("commit-preview");
    const shadow = previewShadow();
    shadow.notes = ["Capacities row withheld — no usable block time"];
    h.store.dispatch({ type: "SHADOW_OK", shadow });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText("Not included in this preview")).toBeTruthy();
    expect(r.getByText(/no usable block time/)).toBeTruthy();
    expect(r.container.querySelector(".alerts__list")).toBeTruthy();
  });

  it("renders no notes section when the server sends none", () => {
    const h = makeHarness("commit-preview");
    const r = h.ui(<ApprovalDrawer />);
    expect(r.queryByText("Not included in this preview")).toBeNull();
  });
});

describe("B3/B5 — prompt-export labels and the blocked fallback", () => {
  function withReport(report: CommitReport) {
    const h = makeHarness("commit-preview");
    h.store.dispatch({ type: "COMMIT_DONE", report });
    return h.ui(<ApprovalDrawer />);
  }

  it("names built-ins and never leaks a raw custom key", () => {
    const r = withReport({
      status: "partial",
      surfaces: [{ system: "todoist", status: "ok", detail: null }],
      verifyFailures: [],
      promptExports: [
        {
          promptKey: "custom-abc123",
          action: "todoist_task",
          status: "done",
          taskId: "1",
          reason: null,
        },
        {
          promptKey: "megan_nicety",
          action: "todoist_task",
          status: "done",
          taskId: "2",
          reason: null,
        },
      ],
      promptExportsOk: false,
    });
    expect(r.getByText(/Custom prompt — exported to Todoist Inbox · task 1/)).toBeTruthy();
    expect(r.queryByText(/custom-abc123/)).toBeNull();
    expect(r.getByText(/For Meegy —/)).toBeTruthy();
  });

  it("does not render a literal 'blocked' when the reason is null", () => {
    const r = withReport({
      status: "partial",
      surfaces: [{ system: "todoist", status: "ok", detail: null }],
      verifyFailures: [],
      promptExports: [
        {
          promptKey: "stoic_intention",
          action: "todoist_task",
          status: "blocked",
          taskId: null,
          reason: null,
        },
      ],
      promptExportsOk: false,
    });
    expect(r.getByText(/Stoic — Blocked — no reason given/)).toBeTruthy();
  });
});

describe("B4 — Capacities rows name their destination", () => {
  it("renders 'Capacities · <object>' for a canonical capacities id", () => {
    const h = makeHarness("commit-preview");
    const shadow = previewShadow();
    shadow.entries = [
      {
        step: "D",
        system: "calendar",
        action: "create-event",
        name: "Deep CWEAN",
        idOrPath: "capacities:space-1:custom-project:obj-1",
        time: "09:00",
        durationMin: 60,
        classification: "would-create",
        detail: {},
      },
    ];
    h.store.dispatch({ type: "SHADOW_OK", shadow });
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText("Capacities · obj-1")).toBeTruthy();
    expect(r.queryByText(/BusyCal/)).toBeNull();
  });
});

describe("B5 — ok surface rows carry a styled status", () => {
  it("marks an ok surface with no note using the status class", () => {
    const h = makeHarness("commit-preview");
    h.store.dispatch({
      type: "COMMIT_DONE",
      report: {
        status: "ok",
        surfaces: [{ system: "todoist", status: "ok", detail: null }],
        verifyFailures: [],
        promptExportsOk: true,
      },
    });
    const r = h.ui(<ApprovalDrawer />);
    const status = r.container.querySelector(".verify-list__status--ok");
    expect(status?.textContent).toBe("ok");
    expect(r.container.querySelector(".verify-list li")?.textContent).toContain("todoist");
  });
});

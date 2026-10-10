/* U5 Set up day prompts + commit prompt-export outcomes.
   - Prompt drafts save through a captures-only PATCH and never confirm the day.
   - Intention never gets an export toggle; opted prompts persist immediately.
   - Preferences are unavailable (not false) until the server exposes a read.
   - A landed main commit with unclean exports reads as a partial/failure, and
     the __main_failure__ sentinel is never rendered as a prompt name. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { makeHarness } from "./test-harness";
import { DaySetupPanel } from "./DaySetupPanel";
import { ApprovalDrawer } from "./ApprovalDrawer";
import type { CommitReport } from "../model/types";

// This file mounts several harnesses; unmount between tests so whole-document
// text queries do not match a previous render.
afterEach(() => cleanup());

function makePrefsAvailable(harness: ReturnType<typeof makeHarness>) {
  const { store } = harness;
  store.dispatch({
    type: "INPUTS_LOADED",
    inputs: {
      ...store.getState().inputs!,
      promptOptins: {
        optins: { intention: false, megan_nicety: false, stoic_intention: false },
        revision: 0,
      },
    },
    ledger: { ...store.getState().ledger! },
  });
}

describe("U5 Set up day prompts", () => {
  it("saves drafts through a captures-only PATCH and never confirms the day", () => {
    const h = makeHarness("fresh");
    const spy = vi.spyOn(h.controller, "savePromptDrafts").mockResolvedValue(undefined);
    const r = h.ui(<DaySetupPanel active={true} />);
    fireEvent.input(r.getByLabelText("Intention"), { target: { value: "one focus" } });
    fireEvent.click(r.getByText("Save prompts"));
    expect(spy).toHaveBeenCalledWith({
      intention: "one focus",
      megan_nicety: "",
      stoic_intention: "",
    });
    expect(h.store.getState().daySetup.confirmed).toBe(false);
  });

  it("shows preferences as unavailable rather than false until a server read exists", () => {
    const h = makeHarness("fresh");
    const r = h.ui(<DaySetupPanel active={true} />);
    expect(r.getByText(/Export preferences are unavailable/)).toBeTruthy();
    expect(r.queryByRole("checkbox")).toBeNull();
  });

  it("offers export toggles for Meegy and Stoic only, and persists on toggle", () => {
    const h = makeHarness("fresh");
    makePrefsAvailable(h);
    const spy = vi.spyOn(h.controller, "savePromptOptins").mockResolvedValue(undefined);
    const r = h.ui(<DaySetupPanel active={true} />);
    const boxes = r.getAllByRole("checkbox");
    expect(boxes).toHaveLength(2);
    expect(r.getByText(/Intention is never exported/)).toBeTruthy();
    fireEvent.click(boxes[0]);
    expect(spy).toHaveBeenCalledWith(
      expect.objectContaining({ megan_nicety: true, intention: false }),
    );
  });
});

describe("U5 commit prompt-export outcomes", () => {
  function openWith(report: CommitReport) {
    const h = makeHarness("fresh");
    h.store.dispatch({ type: "COMMIT_DONE", report });
    h.store.dispatch({ type: "UI", patch: { approvalOpen: true } });
    return h.ui(<ApprovalDrawer />);
  }

  it("renders the __main_failure__ sentinel as a skip, never as a prompt name", () => {
    const r = openWith({
      status: "failed",
      surfaces: [],
      verifyFailures: [],
      promptExports: [
        {
          promptKey: "__main_failure__",
          action: "todoist_task",
          status: "skipped_main_failure",
          taskId: null,
          reason: "main commit did not land — exports skipped",
        },
      ],
      promptExportsOk: false,
    });
    expect(r.getByText(/Prompt exports skipped/)).toBeTruthy();
    expect(r.queryByText(/__main_failure__/)).toBeNull();
  });

  it("reads a landed main commit with unclean exports as partial with a non-retry hint", () => {
    const r = openWith({
      status: "partial",
      surfaces: [{ system: "todoist", status: "ok", detail: null }],
      verifyFailures: [],
      promptExports: [
        {
          promptKey: "megan_nicety",
          action: "todoist_task",
          status: "needs_review",
          taskId: null,
          reason: "a prior attempt is unconfirmed",
        },
      ],
      promptExportsOk: false,
    });
    expect(r.getByText(/Prompt exports did not all land/)).toBeTruthy();
    expect(r.getByText(/review before retrying/)).toBeTruthy();
  });

  it("shows a landed export with its task id", () => {
    const r = openWith({
      status: "ok",
      surfaces: [{ system: "todoist", status: "ok", detail: null }],
      verifyFailures: [],
      promptExports: [
        {
          promptKey: "stoic_intention",
          action: "todoist_task",
          status: "done",
          taskId: "88990011",
          reason: null,
        },
      ],
      promptExportsOk: true,
    });
    expect(r.getByText(/task 88990011/)).toBeTruthy();
  });
});

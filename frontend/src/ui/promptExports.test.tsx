/* U5 Set up day prompts + commit prompt-export outcomes (Planning 2a).
   - Prompt drafts save through a captures-only PATCH and never confirm the day.
   - There are no per-prompt export opt-ins any more (the block and the store
     state were removed); every non-empty prompt exports on Commit.
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

describe("U5 Set up day prompts", () => {
  it("autosaves drafts through a captures-only PATCH and never confirms the day", () => {
    const h = makeHarness("fresh");
    const spy = vi.spyOn(h.controller, "savePromptDrafts").mockResolvedValue(undefined);
    const r = h.ui(<DaySetupPanel active={true} />);
    const intention = r.getByLabelText("Intention");
    fireEvent.input(intention, { target: { value: "one focus" } });
    fireEvent.blur(intention);
    expect(spy).toHaveBeenCalledWith({
      intention: "one focus",
      megan_nicety: "",
      stoic_intention: "",
    });
    expect(h.store.getState().daySetup.confirmed).toBe(false);
  });

  it("renders no Prompt exports block and no per-prompt export checkbox", () => {
    const h = makeHarness("fresh");
    const r = h.ui(<DaySetupPanel active={true} />);
    expect(r.queryByText("Prompt exports")).toBeNull();
    expect(r.queryByText(/Intention is never exported/)).toBeNull();
    expect(r.queryByRole("checkbox")).toBeNull();
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

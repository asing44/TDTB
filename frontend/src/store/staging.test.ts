/* staging.test.ts — allocator-rewrite T3 → IMP-07: staging-phase verbs fired
   from a Today's-work row BEFORE any commit. Fixture adapter only; the
   server-side phase rules (digest_index resolution, "still staged" refusal)
   are backend-tested in test_staging_actions.py. What's asserted here is the
   frontend contract: the verb list matches the server's final action table
   (Done / Drop from plan / Unassign / Delete — no Defer, no Assign), a
   rejected verb never reaches the wire, and a successful apply refreshes
   sources so the row leaves the queue. */

import { describe, expect, it, vi } from "vitest";
import { createStore } from "./createStore";
import { Controller } from "./controller";
import { FixtureAdapter } from "../adapters/fixture";
import {
  STAGING_VERBS,
  DIRECT_VERBS,
  MORE_VERBS,
  isStagingVerb,
  stagingVerbSpec,
} from "../model/staging";

function harness() {
  const store = createStore();
  const adapter = new FixtureAdapter("verified");
  const controller = new Controller(adapter, store.dispatch, store.getState);
  return { store, adapter, controller };
}

describe("staging verb catalogue", () => {
  it("offers exactly the four final verbs the server allows pre-commit", () => {
    expect(STAGING_VERBS.map((s) => s.verb)).toEqual([
      "done",
      "drop_from_plan",
      "unassign",
      "delete",
    ]);
  });

  it("Done and Drop from plan render directly; Unassign and Delete live behind More", () => {
    expect(DIRECT_VERBS.map((s) => s.verb)).toEqual(["done", "drop_from_plan"]);
    expect(MORE_VERBS.map((s) => s.verb)).toEqual(["unassign", "delete"]);
  });

  it("the catalogue never exposes the retired defer or assign verbs", () => {
    expect(isStagingVerb("defer")).toBe(false);
    expect(isStagingVerb("assign")).toBe(false);
  });

  it("rejects every placement-only verb", () => {
    for (const verb of [
      "skip_today",
      "remove_from_today",
      "duration_edit",
      "move_resize",
    ]) {
      expect(isStagingVerb(verb)).toBe(false);
    }
  });

  it("marks only the destructive delete verb", () => {
    expect(STAGING_VERBS.filter((s) => s.destructive).map((s) => s.verb)).toEqual(
      ["delete"],
    );
  });

  it("every verb carries an accessible name distinct from its label", () => {
    for (const spec of STAGING_VERBS) {
      expect(spec.aria).not.toBe(spec.label);
      expect(spec.aria.length).toBeGreaterThan(spec.label.length);
    }
  });

  it("Drop from plan uses the final intent wording, never skip/defer/remove", () => {
    const drop = stagingVerbSpec("drop_from_plan");
    expect(drop?.label).toBe("Drop from plan");
  });

  it("spec lookup is null for an unknown verb", () => {
    expect(stagingVerbSpec("nope")).toBeNull();
    expect(stagingVerbSpec("done")?.label).toBe("Done");
  });
});

describe("controller.stagingAction", () => {
  it("applies through the same journal as a committed-phase verb", async () => {
    const { store, controller } = harness();
    await controller.stagingAction("done", "Press");
    expect(store.getState().lastRuntimeAction).toMatchObject({
      verb: "done",
      targetName: "Press",
      status: "applied",
    });
  });

  it("refreshes sources on success so the row leaves the queue", async () => {
    const { adapter, controller } = harness();
    const spy = vi.spyOn(adapter, "refreshSources");
    await controller.stagingAction("done", "Press");
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("does NOT refresh when the verb failed — the row is still actionable", async () => {
    const { adapter, controller, store } = harness();
    adapter.runtimeAction = async () => {
      throw new Error("surface unavailable: todoist");
    };
    const spy = vi.spyOn(adapter, "refreshSources");
    await controller.stagingAction("done", "Press");
    expect(spy).not.toHaveBeenCalled();
    expect(store.getState().runtimeError).toMatch(/surface unavailable/);
  });

  it("a 200 carrying status:failed is a failure, not a silent success", async () => {
    // The server returns HTTP 200 with the failure inside the journal entry;
    // only a thrown adapter error sets runtimeError. Treating that as success
    // showed no message AND refreshed sources, making the row look handled.
    const { adapter, controller, store } = harness();
    const spy = vi.spyOn(adapter, "refreshSources");
    adapter.runtimeAction = async (verb: string, target: string) => ({
      id: "ra-1", verb, targetName: target, status: "failed" as const,
      error: "no editable status frontmatter line", duplicate: false,
    });
    await controller.stagingAction("done", "Press");
    expect(spy).not.toHaveBeenCalled();
    expect(store.getState().runtimeError).toMatch(/no editable status/);
  });

  it("names the status when the server gave no error string", async () => {
    const { adapter, controller, store } = harness();
    adapter.runtimeAction = async (verb: string, target: string) => ({
      id: "ra-1", verb, targetName: target, status: "compensated" as const,
      error: null, duplicate: false,
    });
    await controller.stagingAction("drop_from_plan", "Press");
    expect(store.getState().runtimeError).toMatch(/compensated/);
  });

  it("refuses a placement verb locally, without touching the adapter", async () => {
    const { adapter, controller, store } = harness();
    const spy = vi.spyOn(adapter, "runtimeAction");
    await controller.stagingAction("move_resize", "Press");
    expect(spy).not.toHaveBeenCalled();
    expect(store.getState().runtimeError).toMatch(/committed plan item/);
  });

  it("drop_from_plan applies like the other verbs", async () => {
    const { store, controller } = harness();
    await controller.stagingAction("drop_from_plan", "Press");
    expect(store.getState().lastRuntimeAction).toMatchObject({
      verb: "drop_from_plan",
      status: "applied",
    });
  });

  it("unassign applies like the other verbs", async () => {
    const { store, controller } = harness();
    await controller.stagingAction("unassign", "Press");
    expect(store.getState().lastRuntimeAction).toMatchObject({
      verb: "unassign",
      status: "applied",
    });
  });

  it("delete applies like the other verbs", async () => {
    const { store, controller } = harness();
    await controller.stagingAction("delete", "Press");
    expect(store.getState().lastRuntimeAction).toMatchObject({
      verb: "delete",
      status: "applied",
    });
  });

  it("a staged action stays undoable through the existing scoped undo", async () => {
    const { store, controller } = harness();
    await controller.stagingAction("done", "Press");
    await controller.undoRuntimeAction();
    expect(store.getState().lastRuntimeAction!.status).toBe("undone");
  });

  it("clears a prior error when a later verb succeeds", async () => {
    const { store, controller } = harness();
    await controller.stagingAction("move_resize", "Press");
    expect(store.getState().runtimeError).not.toBeNull();
    await controller.stagingAction("done", "Press");
    expect(store.getState().runtimeError).toBeNull();
  });
});

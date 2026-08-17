import { describe, expect, it } from "vitest";
import { buildDayPrompt } from "./exportPrompt";
import { createStore } from "./createStore";
import { makeScenario } from "../fixtures/scenarios";

function stateFor(
  name: Parameters<typeof makeScenario>[0],
  stage?: (store: ReturnType<typeof createStore>) => void,
) {
  const sc = makeScenario(name);
  const store = createStore();
  store.dispatch({ type: "INPUTS_LOADED", inputs: sc.inputs, ledger: { ...sc.ledger } });
  stage?.(store);
  return store.getState();
}

describe("buildDayPrompt (manual LLM fallback)", () => {
  it("serializes frame, fixed commitments, and tasks from current state", () => {
    const p = buildDayPrompt(stateFor("ready"));
    expect(p).toContain("# Schedule my day —");
    expect(p).toContain("## Frame");
    expect(p).toContain("## Fixed commitments — do not move these");
    expect(p).toMatch(/## Tasks to place \(\d+\)/);
    expect(p).toContain("## Instructions");
    expect(p).toContain("wait for my approval before writing anything");
  });

  it("includes source warnings verbatim so the LLM knows what's missing", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const s = store.getState();
        store.dispatch({
          type: "INPUTS_LOADED",
          inputs: {
            ...s.inputs!,
            sourceWarnings: [
              "Calendar store has 0 visible calendars — grant likely missing for this process; busy blocks missing",
            ],
          },
          ledger: s.ledger!,
        });
      }),
    );
    expect(p).toContain("## Source warnings — data below may be incomplete");
    expect(p).toContain("0 visible calendars");
    expect(p).toContain("check my real calendar for today first");
  });

  it("routes excluded and all-day items to their own sections", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const first = store.getState().inputs!.assigned[0].id;
        const second = store.getState().inputs!.assigned[1].id;
        store.dispatch({
          type: "OVERRIDE_SET",
          id: first,
          override: { included: false, blocks: null },
        });
        store.dispatch({
          type: "OVERRIDE_SET",
          id: second,
          override: { included: true, blocks: 0 },
        });
      }),
    );
    expect(p).toContain("## Excluded today — ignore");
    expect(p).toContain("## All-day — no time slot");
  });

  it("skipped anchored blocks are omitted; effective overrides are applied", () => {
    const state = stateFor("ready", (store) => {
      const anchored = store.getState().inputs!.anchored.filter((a) => a.kind !== "calendar");
      const target = anchored[0];
      const daySetup = store.getState().daySetup;
      store.dispatch({
        type: "SETUP_SAVED",
        daySetup: {
          ...daySetup,
          confirmed: true,
          anchored: {
            [target.id]: { on: true, skipToday: true, time: null, blocks: null },
          },
        },
      });
    });
    const skipped = state.inputs!.anchored.filter((a) => a.kind !== "calendar")[0];
    const p = buildDayPrompt(state);
    const fixedSection = p.split("## Tasks to place")[0];
    expect(fixedSection).not.toContain(`- ${skipped.name}:`);
  });

  // FEEDBACK-04 (2026-08-14): a quarantined calendar row is excluded from
  // planning on the server — the manual fallback must not present it as a
  // "Fixed commitment — do not move these" the app itself ignores.
  it("excludes quarantined calendar rows from Fixed commitments", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const s = store.getState();
        store.dispatch({
          type: "INPUTS_LOADED",
          inputs: {
            ...s.inputs!,
            anchored: [
              ...s.inputs!.anchored,
              {
                id: "Steelers Game", name: "Steelers Game", kind: "calendar",
                start: "20:00", end: "22:00", durationMin: 120,
                overlapAllowed: false, on: true, skipToday: false,
                calendarId: "sports", calendarTitle: "Sports",
                capacityClass: "quarantined",
              },
            ],
          },
          ledger: s.ledger!,
        });
      }),
    );
    const fixedSection = p.split("## Tasks to place")[0];
    expect(fixedSection).not.toContain("Steelers Game");
  });

  it("placed rows carry their start–end range", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const first = store.getState().inputs!.assigned[0];
        store.dispatch({ type: "ROW_PLACED", id: first.id, start: "14:15" });
      }),
    );
    expect(p).toContain("## Already placed — keep unless they conflict");
    expect(p).toContain("2:15 PM");
  });

  it("returns empty string before inputs load", () => {
    expect(buildDayPrompt(createStore().getState())).toBe("");
  });

  it("todoist rows carry their task id; write instructions mirror the app's commit conventions", () => {
    const p = buildDayPrompt(stateFor("ready"));
    expect(p).toContain("(todoist · id 6fx001AWS)");
    expect(p).toContain("UPDATE that task by its id");
    expect(p).toContain("Never create a duplicate");
    expect(p).toMatch(/recurring.*reschedule with.*full datetime/s);
    expect(p).toContain("create them in my PHEP project, not the Inbox");
  });

  /* SUPERSEDES the old "placed work AND fixed blocks" contract (2026-07-27,
     Adam: vault items route to the PHEP Todoist project; only daily anchors
     belong on ⬜ Blocks). The old wording asked an external scheduler to do
     what the app's own manifest never does — work rows are Step A Todoist
     writes — and following it put eight work blocks on the calendar. */
  it("publishes ONLY fixed commitments to ⬜ Blocks — work blocks stay Todoist-only", () => {
    const p = buildDayPrompt(stateFor("ready"));
    expect(p).toContain('publish ONLY the fixed commitments listed above to my "⬜ Blocks" calendar');
    expect(p).toContain("Placed work blocks do NOT get calendar events");
    expect(p).not.toContain("one event per placed work block");
    expect(p).toContain('except rows marked "(calendar event)"');
    expect(p).toContain("never modify events on any other calendar");
  });
});

describe("FEEDBACK-28 prompt surfacing for real calendar commitments", () => {
  /* An unlisted timed calendar (no capacity_class on the wire) defaults to
     fixed — it must surface in the exported plan as a fixed commitment, not
     silently omit the real event. */
  it("surfaces an unlisted timed calendar (A + M Busy Bees) as a fixed commitment", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const s = store.getState();
        store.dispatch({
          type: "INPUTS_LOADED",
          inputs: {
            ...s.inputs!,
            anchored: [
              ...s.inputs!.anchored,
              {
                id: "A + M Busy Bees", name: "A + M Busy Bees", kind: "calendar",
                start: "10:30", end: "11:00", durationMin: 30, overlapAllowed: false,
                on: true, skipToday: false, calendarId: "busy-bees",
                calendarTitle: "A + M Busy Bees",
              },
            ],
          },
          ledger: s.ledger!,
        });
      }),
    );
    const fixedSection = p.split("## Tasks to place")[0];
    expect(fixedSection).toContain("A + M Busy Bees");
    expect(fixedSection).toContain("(calendar event)");
    expect(fixedSection).toContain("10:30 AM");
  });

  /* FEEDBACK-28 (retry, 2026-08-17): a PERSISTED skip (loaded from the server
     daySetup or merged onto the raw calendar row by a previous run) must not
     silently hide the real commitment. The event stays visible as a fixed
     commitment and participates in planning walls until the user re-expresses
     the skip in the CURRENT run. */
  it("never silently hides a persisted skipped calendar commitment (Meegy cooking)", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const s = store.getState();
        store.dispatch({
          type: "INPUTS_LOADED",
          inputs: {
            ...s.inputs!,
            anchored: [
              ...s.inputs!.anchored,
              {
                id: "Meegy cooking", name: "Meegy cooking", kind: "calendar",
                start: "17:30", end: "18:30", durationMin: 60, overlapAllowed: false,
                on: true, skipToday: false, calendarId: "cooking",
                calendarTitle: "Personal", capacityClass: "fixed",
              },
            ],
            daySetup: {
              ...s.inputs!.daySetup,
              anchored: {
                ...(s.inputs!.daySetup?.anchored ?? {}),
                // Persisted skip from a previous run — NOT current-run intent.
                "Meegy cooking": { on: true, skipToday: true, time: null },
              },
            },
          },
          ledger: s.ledger!,
        });
      }),
    );
    const fixedSection = p.split("## Tasks to place")[0];
    // The event remains visible and participates in planning walls.
    expect(fixedSection).toContain("Meegy cooking");
    expect(fixedSection).toContain("(calendar event)");
    expect(fixedSection).not.toMatch(/skipped today/i);
    expect(fixedSection).not.toMatch(/not planned around/i);
  });

  /* Explicit CURRENT-RUN intent (CalendarImpact → saveAnchoredOverride,
     recorded in currentRunCalendarSkips) DOES suppress the wall: the prompt
     marks the event as skipped and not planned around. */
  it("marks an explicitly skipped calendar as skipped today (current-run intent)", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const s = store.getState();
        store.dispatch({
          type: "INPUTS_LOADED",
          inputs: {
            ...s.inputs!,
            anchored: [
              ...s.inputs!.anchored,
              {
                id: "Meegy cooking", name: "Meegy cooking", kind: "calendar",
                start: "17:30", end: "18:30", durationMin: 60, overlapAllowed: false,
                on: true, skipToday: false, calendarId: "cooking",
                calendarTitle: "Personal", capacityClass: "fixed",
              },
            ],
          },
          ledger: s.ledger!,
        });
        // Current-run explicit skip: the user toggled the row this run
        // (saveAnchoredOverride dispatches both the marker and the override).
        store.dispatch({
          type: "CALENDAR_SKIP_EXPLICIT",
          id: "Meegy cooking",
          skipToday: true,
        });
        store.dispatch({
          type: "SETUP_SAVED",
          daySetup: {
            ...store.getState().daySetup,
            confirmed: true,
            anchored: {
              ...store.getState().daySetup.anchored,
              "Meegy cooking": { on: true, skipToday: true, time: null },
            },
          },
        });
      }),
    );
    const fixedSection = p.split("## Tasks to place")[0];
    expect(fixedSection).toContain("Meegy cooking");
    expect(fixedSection).toContain("(calendar event)");
    expect(fixedSection).toMatch(/skipped today/i);
  });
});

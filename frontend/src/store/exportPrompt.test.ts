import { describe, expect, it } from "vitest";
import { buildDayPrompt } from "./exportPrompt";
import { createStore } from "./createStore";
import { makeScenario } from "../fixtures/scenarios";
import type { AssignedItem } from "../model/types";

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

/** One native-timed Todoist task. Native times are protected by default;
    `scheduledStart: null` and `isRecurring` cover the other S1 cases. */
function timedTodoist(overrides: Partial<AssignedItem> = {}): AssignedItem {
  return {
    id: "Native task",
    name: "Native task",
    path: null,
    source: "todoist",
    types: ["task"],
    urgency: null,
    deadline: null,
    priorityScore: 10,
    blocks: 1,
    durationLabel: "30min",
    todoistId: "native-1",
    scheduledStart: "14:00",
    ...overrides,
  };
}

function withAssigned(
  store: ReturnType<typeof createStore>,
  items: AssignedItem[],
): void {
  const s = store.getState();
  store.dispatch({
    type: "INPUTS_LOADED",
    inputs: { ...s.inputs!, assigned: [...s.inputs!.assigned, ...items] },
    ledger: s.ledger!,
  });
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

/* S1 (2026-10-06): Copy prompt must carry the full external scheduling
   handoff — live capacity arithmetic, recurrence identity, native-time
   permission, sequence freshness, and source identity — without widening the
   app's external-write contract. */
describe("S1 external scheduling handoff", () => {
  it("exports live capacity components, task room, local selection, and signed remaining", () => {
    const p = buildDayPrompt(stateFor("ready"));
    expect(p).toContain("## Capacity");
    expect(p).toContain("Day capacity: 31 blk");
    expect(p).toContain("Task room (budget for chosen tasks): 15 blk");
    expect(p).toContain("Chosen tasks (my local selection): 13 blk");
    expect(p).toContain("Remaining: 2 blk left");
    expect(p).toContain("fixed 3 blk");
    expect(p).toContain("buffer 4 blk");
  });

  it("recomputes capacity from local edits instead of stale server arithmetic", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const first = store.getState().inputs!.assigned[0];
        store.dispatch({
          type: "OVERRIDE_SET",
          id: first.id,
          override: { included: true, blocks: 1 },
        });
      }),
    );
    expect(p).toContain("Chosen tasks (my local selection): 11 blk");
    expect(p).toContain("Remaining: 4 blk left");
    expect(p).not.toContain("Chosen tasks (my local selection): 13 blk");
  });

  it("shows a signed overage when the local selection exceeds task room", () => {
    const p = buildDayPrompt(stateFor("conflict"));
    expect(p).toContain("Chosen tasks (my local selection): 17 blk");
    expect(p).toContain("Over by: 2 blk");
    expect(p).not.toContain("Remaining:");
  });

  it("does not invent capacity numbers when the capacity read is missing", () => {
    const p = buildDayPrompt({ ...stateFor("ready"), capacity: null });
    expect(p).toContain("## Capacity");
    expect(p).toMatch(/capacity read unavailable/i);
    expect(p).not.toContain("Task room (budget for chosen tasks)");
    expect(p).not.toContain("Day capacity:");
  });

  it("exports the resolved preset and effective allotment as context, not capacity", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        const s = store.getState();
        store.dispatch({
          type: "INPUTS_LOADED",
          inputs: {
            ...s.inputs!,
            daySemantics: {
              ...s.inputs!.daySemantics,
              selectedPreset: {
                name: "Ninja",
                days: [],
                enabledZones: [],
                workAllotmentMinutes: 240,
              },
              resolutionSource: "today override",
              effectiveAllotmentMinutes: 240,
            },
          },
          ledger: s.ledger!,
        });
      }),
    );
    expect(p).toMatch(/Day preset: Ninja \(today override\).*4hr/);
    expect(p).toMatch(/context/i);
    // The resolved preset is context; the live task room is unchanged.
    expect(p).toContain("Task room (budget for chosen tasks): 15 blk");
  });

  it("identifies recurring commitments, locks their native time, and keeps them placed", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        withAssigned(store, [
          timedTodoist({
            id: "LOOTS",
            name: "LOOTS",
            todoistId: "loots-1",
            isRecurring: true,
            scheduledStart: "12:30",
          }),
          timedTodoist({
            id: "Untimed recurring",
            name: "Untimed recurring",
            todoistId: "loots-2",
            isRecurring: true,
            scheduledStart: null,
          }),
        ]);
      }),
    );
    const placed = p.split("## Already placed")[1].split("##")[0];
    expect(placed).toContain("LOOTS");
    expect(placed).toContain("recurring");
    expect(placed).toContain("12:30 PM");
    expect(placed).toContain("time locked");
    const toPlace = p.split("## Tasks to place")[1].split("##")[0];
    expect(toPlace).toContain("Untimed recurring");
    expect(toPlace).toContain("recurring");
    expect(p).not.toMatch(
      /Review AWS module 4 — 1hr \(todoist · id 6fx001AWS\) · recurring/,
    );
  });

  it("exports native-time permission: protected by default, adjustable after opt-in", () => {
    const protectedPrompt = buildDayPrompt(
      stateFor("ready", (store) => {
        withAssigned(store, [timedTodoist()]);
      }),
    );
    const protectedPlaced = protectedPrompt
      .split("## Already placed")[1]
      .split("##")[0];
    expect(protectedPlaced).toContain("Native task");
    expect(protectedPlaced).toContain("native 2 PM");
    expect(protectedPlaced).toContain("protected");
    expect(protectedPlaced).not.toContain("adjustable");

    const optedPrompt = buildDayPrompt(
      stateFor("ready", (store) => {
        withAssigned(store, [timedTodoist()]);
        store.dispatch({
          type: "TIME_ADJUSTMENT_SET",
          id: "Native task",
          allow: true,
        });
      }),
    );
    const optedToPlace = optedPrompt.split("## Tasks to place")[1].split("##")[0];
    expect(optedToPlace).toContain("Native task");
    expect(optedToPlace).toContain("adjustable");
  });

  it("exports the locally chosen duration for a recurring commitment", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        withAssigned(store, [
          timedTodoist({
            id: "LOOTS",
            name: "LOOTS",
            todoistId: "loots-1",
            isRecurring: true,
            scheduledStart: "12:30",
          }),
        ]);
        store.dispatch({
          type: "OVERRIDE_SET",
          id: "LOOTS",
          override: { included: true, blocks: 3 },
        });
      }),
    );
    const placed = p.split("## Already placed")[1].split("##")[0];
    expect(placed).toContain("LOOTS — 1hr 30min");
    expect(placed).not.toContain("LOOTS — 30min");
    expect(placed).toContain("12:30 PM");
    expect(placed).toContain("2 PM");
  });

  it("marks the staged sequence current or dirty without changing placement precedence", () => {
    const stage = (store: ReturnType<typeof createStore>) => {
      const s = store.getState();
      store.dispatch({
        type: "SEQUENCE_OK",
        sequence: makeScenario("ready").proposal!.sequence.map((r) => ({ ...r })),
        warnings: [],
        fingerprint: "s1-test",
        anchoredSourceFingerprint: s.inputs!.anchoredSourceFingerprint,
        ledger: s.ledger!,
      });
    };
    const current = buildDayPrompt(stateFor("ready", stage));
    const currentPlaced = current.split("## Already placed")[1].split("##")[0];
    expect(currentPlaced).toContain("Sequence status: current");
    expect(currentPlaced).toContain("9:45 AM");
    expect(currentPlaced).toContain("10:45 AM");

    const dirty = buildDayPrompt(
      stateFor("ready", (store) => {
        stage(store);
        const first = store.getState().inputs!.assigned[0];
        store.dispatch({
          type: "OVERRIDE_SET",
          id: first.id,
          override: { included: true, blocks: 1 },
        });
      }),
    );
    const dirtyPlaced = dirty.split("## Already placed")[1].split("##")[0];
    expect(dirtyPlaced).toContain("Sequence status: dirty");
    expect(dirtyPlaced).toContain("9:45 AM");
    expect(dirtyPlaced).toContain("10:45 AM");
  });

  it("preserves source identity and path metadata, and invents no write route", () => {
    const p = buildDayPrompt(
      stateFor("ready", (store) => {
        withAssigned(store, [
          {
            id: "Capacities idea",
            name: "Capacities idea",
            path: null,
            source: "capacities",
            types: ["task"],
            urgency: null,
            deadline: null,
            priorityScore: 5,
            blocks: 1,
            durationLabel: "30min",
            todoistId: null,
            identity: "capacities:space-1:structure-2:object-3",
          },
          {
            id: "No identity",
            name: "No identity",
            path: null,
            source: "todoist",
            types: ["task"],
            urgency: null,
            deadline: null,
            priorityScore: 4,
            blocks: 1,
            durationLabel: "30min",
            todoistId: null,
          },
        ]);
      }),
    );
    expect(p).toContain("(vault · 50 - Operations/Projects/Magic Mirror.md)");
    expect(p).toContain("(capacities · capacities:space-1:structure-2:object-3)");
    expect(p).toContain("(todoist · unidentified)");
    expect(p).toMatch(/no write route from this prompt/i);
    expect(p).toMatch(/unidentified[\s\S]*do not write/i);
  });
});

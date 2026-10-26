/* task-table.test.tsx — U11 "Fit-today task table" (R20b; KTD10, KTD12,
   KTD13). Re-composes the assigned queue into the mockup's grouped, compact
   table: "Assigned this morning" + scope note, a hint row, lane headers with
   collapse toggles, and dense single-line rows whose actions and steppers
   stay visible. Grouping maps the existing band/state model (KTD12) —
   the trim plan and sequencing order are unchanged.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render as render_, waitFor } from "@testing-library/preact";

afterEach(cleanup);
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ComponentChildren } from "preact";

import { Queue } from "./Queue";
import { UNASSIGN_TRIGGER_LABEL } from "./ConfirmUnassign";
import { Ctx } from "./context";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { makeScenario, restorePlannedDay } from "../fixtures/scenarios";
import { makeHarness } from "./test-harness";

const APP_CSS = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");

function rowNamed(container: Element, name: string): HTMLElement {
  const rows = Array.from((container as HTMLElement).querySelectorAll<HTMLElement>(".qrow"));
  const row = rows.find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

function laneFor(container: Element, label: string): HTMLElement {
  const lanes = Array.from(
    container.querySelectorAll<HTMLElement>(".queue__band"),
  );
  const lane = lanes.find(
    (l) => l.querySelector(".band__label")?.textContent === label,
  );
  if (!lane) throw new Error(`no lane named ${label}`);
  return lane;
}

function spyHarness() {
  const sc = makeScenario("ready");
  const store = createStore();
  store.dispatch({
    type: "INPUTS_LOADED",
    inputs: sc.inputs,
    ledger: { ...sc.ledger },
  });
  restorePlannedDay(store);
  store.dispatch({
    type: "SETUP_SAVED",
    daySetup: { ...sc.inputs.daySetup, confirmed: true },
  });
  const adapter = new FixtureAdapter("ready");
  // F7: the row verbs ride the journaled batch wire, so the observable call is
  // the batch item (verb + stable source identity), not the single runtime
  // action.
  const calls: Array<{ verb: string; target: string }> = [];
  const original = adapter.applyRuntimeBatch.bind(adapter);
  adapter.applyRuntimeBatch = async (items) => {
    for (const item of items) calls.push({ verb: item.verb, target: item.target });
    return original(items);
  };
  const controller = new Controller(adapter, store.dispatch, store.getState);
  const ui = (children: ComponentChildren) =>
    render_(
      <Ctx.Provider value={{ store, controller }}>{children}</Ctx.Provider>,
    );
  return { store, controller, calls, ui };
}

describe("U11 headings: Assigned this morning replaces Today's work", () => {
  it("names the surface Assigned this morning with the today-only scope note", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    expect(r.getByRole("region", { name: /Assigned this morning/i })).toBeTruthy();
    expect(r.queryByText(/Today's work/i)).toBeNull();
    expect(
      r.getByText("Edits here affect today only. Mark complete writes back to the source."),
    ).toBeTruthy();
  });

  it("drops the ALLOCATION kicker and the redundant column header row", () => {
    const h = makeHarness("ready");
    const { container, queryByText } = h.ui(<Queue />);
    expect(queryByText("Allocation")).toBeNull();
    expect(container.querySelector(".queue__cols")).toBeNull();
    expect(queryByText(/Needs placement ·/)).toBeNull();
  });
});

describe("U11 hint row lists exactly the shortcuts that work", () => {
  it("renders the row-shortcuts hint with the local plan-scope note", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    const hint = r.container.querySelector(".queue__hint");
    expect(hint).toBeTruthy();
    const text = hint?.textContent ?? "";
    expect(text).toContain("Row shortcuts");
    expect(text).not.toContain("Hover a row");
    expect(text).toContain("C complete");
    // U3b: E is local plan membership for every source kind. The hint says so
    // instead of advertising the retired durable Skip today.
    expect(text).toContain("E remove from plan");
    expect(text).toContain("move");
    expect(text).toContain("in this browser only");
    expect(text).toContain("no source is written");
    expect(text).toContain("Not in plan puts it back");
    expect(text).not.toContain("E skip today");
    expect(text).not.toContain("records it on the server");
    // The retired controls' keys are not advertised.
    expect(text).not.toContain("D exact duration");
    expect(text).not.toContain("±15min");
    expect(text).not.toContain("hides it in this browser");
    expect(text).not.toMatch(/\bx\b/);
  });
});

describe("U11 grouping maps the band/state model (KTD12)", () => {
  it("groups critical rows under Critical", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "Critical");
    expect(
      lane.querySelector(".qrow__name")?.textContent,
    ).toBe("Rowe's T-shirt Redesign 2026");
  });

  it("merges High and everything-else under When there's room in original order", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "When there's room");
    const names = Array.from(lane.querySelectorAll(".qrow__name")).map(
      (n) => n.textContent,
    );
    // Magic Mirror (high, score 86) leads Charge GoPro (low, 12).
    expect(names[0]).toBe("Magic Mirror");
    expect(names).toContain("Press");
    expect(names).toContain("Charge GoPro");
    expect(names).not.toContain("Rowe's T-shirt Redesign 2026");
  });

  it("groups scheduled/native rows under Fixed", () => {
    const h = makeHarness("sequenced");
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "Fixed");
    expect(lane.querySelectorAll(".qrow").length).toBeGreaterThan(0);
  });

  it("groups legacy browser exclusions under a collapsible lane", () => {
    // F4a: durable skips left this lane for the Skipped today section, so the
    // trailing lane now carries only browser-only exclusions from before the
    // migration and says so in its own words.
    const h = makeHarness("ready");
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "Excluded in this browser");
    expect(lane.querySelector(".band--drop")).toBeTruthy();
  });

  it("keeps a durable skip out of the lanes — it belongs to Skipped today", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.droppedToday = [
        { identity: "p1", name: "Press", droppedAt: "2026-08-13T08:00:00Z" },
      ];
    });
    const { container } = h.ui(<Queue />);
    expect(container.querySelector(".band--drop")).toBeNull();
    const skipped = container.querySelector("#skipped-today") as HTMLElement;
    expect(skipped.textContent).toContain("Press");
  });
});

describe("U11 row actions stay visible without a hover", () => {
  it("keeps the action cluster in the DOM and visible at every width", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Press");
    expect(row.querySelector(".trow__acts")).toBeTruthy();
    // F4a/U4: three labelled controls in order — the row's source verb names
    // Todoist because that is where it writes.
    const labels = Array.from(row.querySelectorAll(".trow__acts button")).map(
      (b) => (b.getAttribute("aria-label") ?? b.textContent ?? "").trim(),
    );
    expect(labels[0]).toMatch(/^Complete/);
    expect(labels[1]).toBe(UNASSIGN_TRIGGER_LABEL);
    expect(labels[2]).toMatch(/^Expand/);
    // The cluster is always visible: no rule hides it at rest, and the
    // hover/focus reveal layer is gone (removed 2026-10-12).
    expect(APP_CSS).not.toMatch(/\.trow__acts\s*\{[^}]*opacity:\s*0/);
    expect(APP_CSS).not.toMatch(/\.qrow:hover\s+\.trow__acts/);
    expect(APP_CSS).not.toMatch(/\.qrow:focus-within\s+\.trow__acts/);
    expect(APP_CSS).not.toMatch(/\.qrow--focused\s+\.trow__acts/);
  });

  it("keeps every row control reachable from the focused row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Press");
    act(() => row.focus());
    expect(document.activeElement).toBe(row);
    // No control depends on a hover to exist: every one is a real button in
    // the focused row, and the row's checkbox is the selection control.
    const buttons = Array.from(row.querySelectorAll(".trow__acts button"));
    expect(buttons.length).toBeGreaterThanOrEqual(3);
    for (const btn of buttons) expect(btn.tagName).toBe("BUTTON");
    expect(row.querySelector(".trow__check")).toBeTruthy();
  });

  it("wraps the cluster onto its own line at phone width instead of clipping", () => {
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*640px\)[\s\S]*?\.trow__acts\s*\{[^}]*grid-column:\s*1\s*\/\s*-1[^}]*flex-wrap:\s*wrap/,
    );
  });
});

describe("U11 keyboard shortcuts act on the focused row", () => {
  it("no longer advertises a duration-trim key from the collapsed row", () => {
    // F4a: ←/→ duration trimming and D exact duration left with the slider and
    // the Exact duration button. They come back with the expanded row panel.
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const store = h.store;
    const row = rowNamed(container, "Press");
    fireEvent.keyDown(row, { key: "ArrowRight" });
    fireEvent.keyDown(row, { key: "d" });
    expect(store.getState().overrides["Press"]).toBeUndefined();
    expect(store.getState().ui.editorItem).toBeNull();
  });

  it("↑/↓ move focus between rows", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const rows = Array.from((container as HTMLElement).querySelectorAll<HTMLElement>(".qrow"));
    const first = rows[0];
    act(() => first.focus());
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rows[1]);
  });

  it("C dispatches Mark complete through the row's batch verb", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Press");
    fireEvent.keyDown(row, { key: "c" });
    await waitFor(() => expect(h.calls.length).toBeGreaterThan(0));
    expect(h.calls[0]).toEqual({ verb: "done", target: "todoist:6fx00xPRESS" });
  });
});

describe("U11 the legacy exclusion lane collapses and restores rows", () => {
  function excludedHarness() {
    const h = makeHarness("ready");
    h.store.dispatch({
      type: "OVERRIDE_SET",
      id: "Press",
      override: { included: false, blocks: null },
    });
    return h;
  }

  it("announces its expanded/collapsed state in words", () => {
    // The lane starts collapsed and is only rendered when something is out.
    const h = excludedHarness();
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "Excluded in this browser");
    const btn = lane.querySelector(".band--drop") as HTMLButtonElement;
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.getAttribute("aria-label")).toMatch(/collapsed/);
    expect(btn.textContent).toMatch(/collapsed/i);
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(btn.getAttribute("aria-label")).toMatch(/expanded/);
    expect(btn.textContent).toMatch(/expanded/i);
  });

  it("puts a re-included row back in its group", () => {
    const h = excludedHarness();
    const { container } = h.ui(<Queue />);
    const store = h.store;
    // F4a: the collapsed row carries no inclusion toggle; re-including is the
    // store's own path until the expanded panel owns it (F4b).
    act(() => {
      store.dispatch({
        type: "OVERRIDE_SET",
        id: "Press",
        override: { included: true, blocks: null },
      });
    });
    expect(laneFor(container, "When there's room").textContent).toContain("Press");
    expect(container.querySelector(".band--drop")).toBeNull();
  });
});

describe("U11 quiet day keeps the table frame", () => {
  it("shows the quiet-day message inside the table frame, no dead area", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.assigned = [];
    });
    const { container } = h.ui(<Queue />);
    const frame = container.querySelector(".queue__quiet") as HTMLElement;
    expect(frame).toBeTruthy();
    expect(frame.textContent).toMatch(/No assigned items today/);
    // Capacity evidence still heads the quiet frame.
    expect(container.querySelector(".cap-card")).toBeTruthy();
  });
});

/* jsdom cannot measure layout, so the overflow fixes are pinned as CSS text
   (the same approach as the a11y/responsive suites). The defect: the revealed
   row-action cluster widened the page and squeezed "Mark complete". */
describe("overflow containment (2026-10-10)", () => {
  it("desktop rows are one line with the actions in their own reserved track", () => {
    // One-line mockup grid at >=1440px: stripe | pill | title | due | time |
    // actions. The reserved action track holds the cluster, so a revealed
    // cluster never paints over the title or the slider.
    expect(APP_CSS).toMatch(
      /grid-template-columns:\s*3px\s+96px\s+minmax\(0,\s*1fr\)\s+132px\s+300px\s+500px/,
    );
    // 1101-1439: the always-visible cluster drops to its own grid line, so
    // the due text stays readable and nothing is painted over the title.
    expect(APP_CSS).toMatch(
      /@media\s*\(min-width:\s*1101px\)\s+and\s+\(max-width:\s*1439px\)\s*\{[\s\S]*?\.qrow\s*\{[\s\S]*?grid-template-rows:\s*auto\s+auto;[\s\S]*?\.qrow__actions\s*\{[\s\S]*?grid-row:\s*2;[\s\S]*?opacity:\s*1;/,
    );
    // Nothing hides the due text on hover.
    expect(APP_CSS).not.toMatch(/visibility:\s*hidden/);
    // The title clamps to one line so the reserved cluster never grows it.
    expect(APP_CSS).toMatch(/\.qrow__name\s*\{[^}]*?-webkit-line-clamp:\s*1/);
    expect(APP_CSS).toMatch(/\.qrow__actions\s*\{[^}]*grid-column:\s*6/);
    expect(APP_CSS).toMatch(/\.qrow__due\s*\{[^}]*grid-column:\s*4/);
    expect(APP_CSS).toMatch(/\.qrow__time\s*\{[^}]*grid-column:\s*5/);
    // The cluster is in flow: no absolute overlay, no right/left anchor.
    expect(APP_CSS).not.toMatch(/\.qrow__actions\s*\{[^}]*position:\s*absolute/);
    expect(APP_CSS).not.toMatch(/\.qrow__actions\s*\{[^}]*right:\s*352px/);
    expect(APP_CSS).not.toMatch(/\.qrow__actions\s*\{[^}]*right:\s*8px/);
    // The cluster is never zero-width: the track is reserved, not collapsed.
    expect(APP_CSS).not.toMatch(/\.qrow__actions\s*\{[^}]*?(?<!min-)width:\s*0;/);
    expect(APP_CSS).not.toMatch(/340px\s+max-content/);
  });

  it("the always-visible cluster sits in flow, never over the slider", () => {
    // The cluster owns its reserved grid track and is visible at rest, so it
    // never needs a hover to sit in flow.
    expect(APP_CSS).not.toMatch(/\.qrow:hover\s+\.qrow__actions/);
    expect(APP_CSS).not.toMatch(/\.qrow__actions\s*\{[^}]*display:\s*none/);
    expect(APP_CSS).not.toMatch(
      /\.qrow__actions\s*\{[^}]*pointer-events:\s*none/,
    );
  });

  it("the page frame is full width with one shared side inset", () => {
    // No bounded centered column: the frame fills the viewport on every screen.
    expect(APP_CSS).not.toMatch(/\.cockpit\s*\{[^}]*max-width:\s*1280px/);
    expect(APP_CSS).toMatch(
      /\.cockpit\s*\{[^}]*--page-pad:\s*clamp\(16px,\s*2\.5vw,\s*40px\)/,
    );
    expect(APP_CSS).toMatch(/\.queue\s*\{[^}]*padding:\s*0\s+var\(--page-pad/);
  });

  it("the page body cannot scroll sideways", () => {
    expect(APP_CSS).toMatch(
      /\.cockpit__main,\s*\.queue\s*\{[^}]*overflow-x:\s*clip/,
    );
  });

  it("lane tables carry side padding so rows are not flush to the card", () => {
    expect(APP_CSS).toMatch(/\.tasktable\s*\{[^}]*padding-inline:\s*8px/);
  });

  it("the More menu stacks title over detail and stays inside the viewport", () => {
    expect(APP_CSS).toMatch(/\.row-more__menu\s*\{[^}]*min-width:\s*240px/);
    expect(APP_CSS).toMatch(
      /\.row-more__menu\s+\.row-more__item\s*\{[^}]*flex-direction:\s*column/,
    );
  });

  it("the timeline's last hour label stays inside its track", () => {
    expect(APP_CSS).toMatch(
      /\.timeline__tick:last-child\s*\{[^}]*transform:\s*translateX\(-100%\)/,
    );
  });

  it("native controls follow the app theme, not the OS preference", () => {
    const tokens = readFileSync(resolve(process.cwd(), "src/tokens.css"), "utf8");
    expect(tokens).toMatch(/:root\[data-theme="light"\]\s*\{[^}]*color-scheme:\s*light/);
    expect(tokens).toMatch(/:root\[data-theme="dark"\]\s*\{[^}]*color-scheme:\s*dark/);
  });
});

/* POLISH-3 (2026-10-11): three live-screenshot defects pinned as CSS text — the
   64px strip of rows above the sticky lane header, the sticky footer hiding the
   last row, and the ~450px dead trailing action track at >=1500px — plus the
   four-track action layout and the Fixed-row permission line. */
describe("POLISH-3 lane header, footer clearance, one action layout", () => {
  it("the lane header sticks at the viewport top with no 64px gap", () => {
    expect(APP_CSS).toMatch(/:root\s*\{[^}]*--app-header-h:\s*0px/);
    expect(APP_CSS).toMatch(
      /\.band\s*\{[^}]*top:\s*var\(--app-header-h,\s*64px\)/,
    );
    // Opaque fill + hairline rule + bottom padding: rows slide cleanly under.
    expect(APP_CSS).toMatch(/\.band\s*\{[^}]*background:\s*var\(--t-surface\)/);
    expect(APP_CSS).toMatch(
      /\.band\s*\{[^}]*border-bottom:\s*0\.5px\s+solid\s+var\(--t-border\)/,
    );
  });

  it("the sticky footer cannot hide the last row", () => {
    expect(APP_CSS).toMatch(
      /\.cockpit__main\s*\{[^}]*padding-bottom:\s*var\(--footer-clearance,\s*112px\)/,
    );
  });

  it("no trailing max-content action track reserves a dead band", () => {
    expect(APP_CSS).not.toMatch(/340px\s+max-content/);
    expect(APP_CSS).not.toMatch(/grid-template-columns:[^;]*max-content/);
  });

  it("the timeline detail row keeps its controls apart", () => {
    expect(APP_CSS).toMatch(/\.timeline__detail\s*\{[^}]*flex-wrap:\s*wrap/);
    expect(APP_CSS).toMatch(
      /\.timeline__detail-actions\s*\{[^}]*flex-wrap:\s*nowrap/,
    );
    expect(APP_CSS).toMatch(
      /\.calendar-impact__attend\s*\{[^}]*white-space:\s*nowrap/,
    );
    expect(APP_CSS).toMatch(
      /\.calendar-impact__stepper\s+\.timeline__detail-dur\s*\{[^}]*min-width:\s*72px/,
    );
  });

  it("keeps the fixed-row permission out of the row CSS (the More menu owns it)", () => {
    // Planning 2a cleanup (2026-10-14): the inline "Allow move" checkbox left
    // the meta line; the permission is a menuitemcheckbox inside More now.
    expect(APP_CSS).not.toMatch(/\.qrow__time-adjustment/);
  });
});

describe("priority labels on a row", () => {
  it("shows a Todoist p1 once, not as both the chip and a badge", () => {
    const h = makeHarness("ready", (sc) => {
      const it = sc.inputs.assigned.find((a) => a.id === "Review AWS module 4");
      if (it) it.urgency = "4";
    });
    const r = h.ui(<Queue />);
    const row = rowNamed(r.container, "Review AWS module 4");
    // Planning 2a: priority rides its own column as .qrow__prio; the p-level
    // appears exactly once, never as both a chip and a duplicate badge.
    const prios = Array.from(row.querySelectorAll(".qrow__prio")).map((n) => n.textContent);
    expect(prios.filter((t) => t === "p1")).toHaveLength(1);
    expect(row.querySelector(".badge")).toBeNull();
  });

  it("a Todoist p1 row carries no duplicate urgency badge", () => {
    const r = makeHarness("ready").ui(<Queue />);
    const row = rowNamed(r.container, "Rowe's T-shirt Redesign 2026");
    expect(row.querySelector(".qrow__prio--crit")?.textContent).toBe("p1");
    expect(row.querySelector(".badge")).toBeNull();
  });
});

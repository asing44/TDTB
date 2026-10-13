/* task-table.test.tsx — U11 "Fit-today task table" (R20b; KTD10, KTD12,
   KTD13). Re-composes the assigned queue into the mockup's grouped, compact
   table: "Assigned this morning" + scope note, a hint row, lane headers with
   collapse toggles, and dense single-line rows whose actions appear on hover
   or keyboard focus. Grouping maps the existing band/state model (KTD12) —
   the trim plan and sequencing order are unchanged.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render as render_, waitFor } from "@testing-library/preact";

afterEach(cleanup);
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ComponentChildren } from "preact";

import { Queue } from "./Queue";
import { Ctx } from "./context";
import { createStore } from "../store/createStore";
import { Controller } from "../store/controller";
import { FixtureAdapter } from "../adapters/fixture";
import { makeScenario } from "../fixtures/scenarios";
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
  store.dispatch({
    type: "SETUP_SAVED",
    daySetup: { ...sc.inputs.daySetup, confirmed: true },
  });
  const adapter = new FixtureAdapter("ready");
  const calls: Array<{ verb: string; target: string }> = [];
  const original = adapter.runtimeAction.bind(adapter);
  adapter.runtimeAction = async (verb, target, args) => {
    calls.push({ verb, target });
    return original(verb, target, args);
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
  it("renders the hover/keys hint with the today-only scope note", () => {
    const h = makeHarness("ready");
    const r = h.ui(<Queue />);
    const hint = r.container.querySelector(".queue__hint");
    expect(hint).toBeTruthy();
    const text = hint?.textContent ?? "";
    expect(text).toContain("Hover a row for actions");
    expect(text).toContain("C complete");
    expect(text).toContain("E exclude");
    expect(text).toContain("D exact duration");
    expect(text).toContain("±15min");
    expect(text).toContain("move");
    expect(text).toContain("apply to today only and can be undone");
    // The retired keys are not advertised.
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

  it("groups excluded and dropped rows under a collapsible Out today", () => {
    const h = makeHarness("ready", (sc) => {
      sc.inputs.droppedToday = [
        { identity: "p1", name: "Press", droppedAt: "2026-08-13T08:00:00Z" },
      ];
    });
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "Out today");
    expect(lane.querySelector(".band--drop")).toBeTruthy();
  });
});

describe("U11 row actions reveal on hover at desktop width", () => {
  it("keeps the action cluster in the DOM at every width", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Press");
    expect(row.querySelector(".qrow__actions")).toBeTruthy();
    // Wide: quiet at rest (opacity, not display) and revealed on hover or
    // keyboard focus; the grid track is reserved so the row never reflows.
    expect(APP_CSS).toMatch(/\.qrow__actions\s*\{[^}]*opacity:\s*0/);
    expect(APP_CSS).toMatch(/\.qrow:hover\s+\.qrow__actions/);
    expect(APP_CSS).toMatch(/\.qrow:focus-within\s+\.qrow__actions/);
    expect(APP_CSS).toMatch(/\.qrow--focused\s+\.qrow__actions/);
    // Narrow: the stacked layout shows the cluster without a hover.
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*1100px\)\s*\{\s*\.qrow__actions\s*\{[^}]*opacity:\s*1/,
    );
  });

  it("reveals actions when the row receives keyboard focus", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Press");
    act(() => row.focus());
    expect(document.activeElement).toBe(row);
    expect(row.querySelector('button[aria-label="Exclude Press today"]')).toBeTruthy();
  });
});

describe("U11 keyboard shortcuts act on the focused row", () => {
  it("E toggles today-only exclusion", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const store = h.store;
    const row = rowNamed(container, "Press");
    fireEvent.keyDown(row, { key: "e" });
    expect(store.getState().overrides["Press"]).toEqual({ included: false, blocks: null });
  });

  it("D opens the exact-duration editor for the focused row", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const store = h.store;
    const row = rowNamed(container, "Press");
    fireEvent.keyDown(row, { key: "d" });
    expect(store.getState().ui.editorItem).toBe("Press");
    expect(store.getState().ui.editorIntent).toBe("duration");
  });

  it("←/→ shape the focused row's duration by ±15min", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const store = h.store;
    const row = rowNamed(container, "Press");
    fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(store.getState().overrides["Press"]?.blocks).toBe(3.5);
    fireEvent.keyDown(row, { key: "ArrowLeft" });
    expect(store.getState().overrides["Press"]?.blocks).toBe(3);
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

  it("C dispatches Mark complete through the same controller call as before", async () => {
    const h = spyHarness();
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Press");
    fireEvent.keyDown(row, { key: "c" });
    await waitFor(() => expect(h.calls.length).toBeGreaterThan(0));
    expect(h.calls[0]).toEqual({ verb: "done", target: "Press" });
  });
});

describe("U11 Out today collapses and restores rows", () => {
  it("announces its expanded/collapsed state in words", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const lane = laneFor(container, "Out today");
    const btn = lane.querySelector(".band--drop") as HTMLButtonElement;
    expect(btn.getAttribute("aria-label")).toMatch(/expanded/);
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-label")).toMatch(/collapsed/);
    expect(btn.textContent).toMatch(/collapsed/i);
  });

  it("Include today returns an excluded row to its group", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const store = h.store;
    fireEvent.click(
      container.querySelector('button[aria-label="Exclude Press today"]')!,
    );
    expect(store.getState().overrides["Press"]).toEqual({ included: false, blocks: null });
    const include = container.querySelector(
      'button[aria-label="Include Press today"]',
    ) as HTMLButtonElement;
    expect(include).toBeTruthy();
    fireEvent.click(include);
    expect(store.getState().overrides["Press"]).toEqual({ included: true, blocks: null });
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
      /grid-template-columns:\s*3px\s+96px\s+minmax\(0,\s*1fr\)\s+112px\s+300px\s+500px/,
    );
    // 1101-1439: the always-visible cluster drops to its own grid line, so
    // the due text stays readable and nothing is painted over the title.
    expect(APP_CSS).toMatch(
      /@media\s*\(min-width:\s*1101px\)\s+and\s+\(max-width:\s*1439px\)\s*\{[\s\S]*?\.qrow\s*\{[\s\S]*?grid-template-rows:\s*auto\s+auto;[\s\S]*?\.qrow__actions,[\s\S]*?grid-row:\s*2;[\s\S]*?opacity:\s*1;/,
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

  it("the revealed cluster sits in flow, never over the slider", () => {
    // The cluster owns its reserved grid track, so it cannot intercept a
    // slider click; it is quiet at rest, not inert or removed.
    expect(APP_CSS).toMatch(
      /\.qrow:hover\s+\.qrow__actions,[\s\S]*?pointer-events:\s*auto/,
    );
    expect(APP_CSS).not.toMatch(/\.qrow__actions\s*\{[^}]*display:\s*none/);
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

  it("the fixed-row permission rides the meta line", () => {
    expect(APP_CSS).toMatch(
      /\.qrow__source\s+\.qrow__time-adjustment\s*\{[^}]*flex:\s*0\s+0\s+auto/,
    );
  });
});

describe("priority labels on a row", () => {
  it("shows a Todoist p1 once, not as both the chip and a badge", () => {
    const h = makeHarness("ready", (sc) => {
      const it = sc.inputs.assigned.find((a) => a.id === "Review AWS module 4");
      if (it) it.urgency = "4";
    });
    const r = h.ui(<Queue />);
    const due = rowNamed(r.container, "Review AWS module 4").querySelector(".qrow__due");
    const texts = Array.from(due?.querySelectorAll("span") ?? []).map((n) => n.textContent);
    expect(texts.filter((t) => t === "p1")).toHaveLength(1);
  });

  it("a Todoist p1 row carries no duplicate urgency badge", () => {
    const r = makeHarness("ready").ui(<Queue />);
    const due = rowNamed(r.container, "Rowe's T-shirt Redesign 2026").querySelector(".qrow__due");
    expect(due?.querySelector(".qrow__prio--crit")?.textContent).toBe("p1");
    expect(due?.querySelector(".badge")).toBeNull();
  });
});

/* origin-chip.test.tsx — every queue/plan/drawer row names its ORIGIN with one
   readable, colour-coded chip: the Todoist project (in that project's real
   Todoist colour) or the Capacities object type (a fixed hue per type). The
   drawer's group headings wear the same chip at a readable size.

   Fixture adapters only: no live adapter, no billed call, no source write. */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";

import { Queue } from "./Queue";
import { projectAssigned } from "../adapters/wire";
import { drawerTierGroups, NO_FACTS } from "../model/drawerGroups";
import { makeHarness, type Harness } from "./test-harness";
import type { AssignedItem } from "../model/types";
import type { AssignmentRow } from "../store/store";
import {
  TODOIST_COLORS,
  capacitiesHue,
  originChipSpec,
} from "./sourceChip";

afterEach(cleanup);

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");
const drawerCss = readFileSync(resolve(process.cwd(), "src/ui/drawer-groups.css"), "utf8");

const PROJECT_STRUCTURE = "0d194525-c5a1-4af5-bb62-202b83006b5e";

function item(over: Partial<AssignedItem>): AssignedItem {
  return {
    id: "X",
    name: "X",
    path: null,
    source: "todoist",
    types: [],
    urgency: null,
    deadline: null,
    priorityScore: 0,
    blocks: 1,
    durationLabel: "30min",
    todoistId: null,
    ...over,
  };
}

function rowNamed(container: Element, name: string): HTMLElement {
  const row = Array.from(container.querySelectorAll<HTMLElement>(".qrow")).find(
    (r) => r.querySelector(".qrow__name")?.textContent === name,
  );
  if (!row) throw new Error(`no row named ${name}`);
  return row;
}

describe("projectAssigned carries the origin fields", () => {
  it("projects the Todoist project id, name and colour name", () => {
    const row = projectAssigned({
      name: "Fix sink",
      source: "todoist",
      todoist_id: "9",
      project_id: "2203306141",
      project_name: "  Home Lab ",
      project_color: "Berry_Red",
    });
    expect(row.projectId).toBe("2203306141");
    expect(row.projectName).toBe("Home Lab");
    expect(row.projectColor).toBe("berry_red");
  });

  it("degrades to null when the wire has no project fields", () => {
    const row = projectAssigned({ name: "Fix sink", source: "todoist", todoist_id: "9" });
    expect(row.projectId ?? null).toBeNull();
    expect(row.projectName ?? null).toBeNull();
    expect(row.projectColor ?? null).toBeNull();
    const blank = projectAssigned({ name: "A", source: "todoist", project_name: "  ", project_color: 4 });
    expect(blank.projectName ?? null).toBeNull();
    expect(blank.projectColor ?? null).toBeNull();
  });

  it("keeps the Capacities structure id the wire sends", () => {
    const row = projectAssigned({
      name: "Obj",
      source: "capacities",
      capacities_structure_id: "abc-123",
      types: ["abc-123"],
    });
    expect(row.capacitiesStructureId).toBe("abc-123");
  });
});

describe("originChipSpec", () => {
  it("uses the project name and its Todoist colour class", () => {
    const spec = originChipSpec(item({ projectName: "Home Lab", projectColor: "sky_blue" }), {});
    expect(spec?.label).toBe("Home Lab");
    expect(spec?.title).toBe("Home Lab");
    expect(spec?.className).toContain("origin-chip--td-sky_blue");
  });

  it("falls back to the Todoist label and existing hue without a project", () => {
    const spec = originChipSpec(item({}), {});
    expect(spec?.label).toBe("Todoist");
    expect(spec?.className).toContain("origin-chip--td-source");
  });

  it("falls back to a neutral chip for an unknown colour name", () => {
    const spec = originChipSpec(item({ projectName: "Odd", projectColor: "chartreuse" }), {});
    expect(spec?.label).toBe("Odd");
    expect(spec?.className).toContain("origin-chip--neutral");
    const noColor = originChipSpec(item({ projectName: "Odd", projectColor: null }), {});
    expect(noColor?.className).toContain("origin-chip--neutral");
  });

  it("uses the Capacities object type with a stable per-type hue", () => {
    const row = item({ source: "capacities", types: [PROJECT_STRUCTURE] });
    const spec = originChipSpec(row, { [PROJECT_STRUCTURE]: "Project" });
    expect(spec?.label).toBe("Project");
    expect(spec?.className).toContain(`origin-chip--cap-h${capacitiesHue("Project")}`);
    // Case-insensitive, deterministic, and distinct across the named types.
    expect(capacitiesHue("PROJECT")).toBe(capacitiesHue("project"));
    const named = ["Task", "Project", "Event", "Continual", "Press", "Purchase", "Print", "Horticos"];
    expect(new Set(named.map(capacitiesHue)).size).toBe(named.length);
    const unknown = capacitiesHue("Gizmo");
    expect(unknown).toBeGreaterThanOrEqual(1);
    expect(unknown).toBeLessThanOrEqual(8);
    expect(capacitiesHue("Gizmo")).toBe(unknown);
  });

  it("gives an untitled Capacities structure the placeholder label", () => {
    const spec = originChipSpec(item({ source: "capacities", types: ["RootTask"] }), {});
    expect(spec?.label).toBe("Capacities item");
    expect(spec?.className).toContain("origin-chip--cap-unknown");
  });

  it("leaves calendar rows to their current label", () => {
    expect(originChipSpec(item({ source: "calendar" }), {})).toBeNull();
  });
});

describe("rendered rows wear the origin chip", () => {
  it("shows the project name chip coloured by the project on a Todoist row", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.assigned.push(
      item({ id: "Proj row", name: "Proj row", projectName: "Home Lab", projectColor: "berry_red" }),
    );
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: { ...h.scenario.ledger } });
    const { container } = h.ui(<Queue />);
    const chip = rowNamed(container, "Proj row").querySelector<HTMLElement>(".origin-chip");
    expect(chip?.textContent).toBe("Home Lab");
    expect(chip?.classList.contains("origin-chip--td-berry_red")).toBe(true);
    expect(chip?.getAttribute("title")).toBe("Home Lab");
    expect(chip?.getAttribute("data-origin")).toBe("todoist");
  });

  it("keeps the Todoist label on a row without a project", () => {
    const h = makeHarness("ready");
    const { container } = h.ui(<Queue />);
    const row = rowNamed(container, "Review AWS module 4");
    expect(row.querySelector(".origin-chip")?.textContent).toBe("Todoist");
  });

  it("shows the Capacities object type as the chip", async () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.assigned.push(
      item({ id: "Cap project", name: "Cap project", source: "capacities", types: [PROJECT_STRUCTURE] }),
    );
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: { ...h.scenario.ledger } });
    const { container } = h.ui(<Queue />);
    await waitFor(() => {
      const chip = rowNamed(container, "Cap project").querySelector<HTMLElement>(".origin-chip");
      expect(chip?.textContent).toBe("Project");
      expect(chip?.classList.contains(`origin-chip--cap-h${capacitiesHue("Project")}`)).toBe(true);
    });
  });
});

describe("drawer group headings", () => {
  const row = (id: string, source: AssignmentRow["source"]): AssignmentRow =>
    ({ id, identity: id, name: id, source, kind: null, minutes: null } as AssignmentRow);

  it("names the shared project, else stays Todoist; grouping keys are unchanged", () => {
    const rows = [row("a", "todoist"), row("b", "todoist"), row("c", "todoist")];
    const facts = (r: AssignmentRow) => ({
      ...NO_FACTS,
      projectName: r.id === "c" ? "Other" : "Home Lab",
      projectColor: "teal",
    });
    const mixed = drawerTierGroups(rows, facts, "2026-10-29")[0].sources[0];
    expect(mixed.key).toBe("todoist");
    expect(mixed.projectName ?? null).toBeNull();

    const same = drawerTierGroups(rows.slice(0, 2), facts, "2026-10-29")[0].sources[0];
    expect(same.key).toBe("todoist");
    expect(same.label).toBe("Todoist");
    expect(same.projectName).toBe("Home Lab");
    expect(same.projectColor).toBe("teal");
  });

  it("renders the chip plus count, with no tiny uppercase rule", async () => {
    const h: Harness = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    for (const r of inputs.assigned) {
      if (r.source === "todoist") {
        r.projectName = "Home Lab";
        r.projectColor = "teal";
        r.deadline = null;
        r.urgency = null;
      }
    }
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: { ...h.scenario.ledger } });
    const { container } = h.ui(<Queue />);
    const name = "Press";
    const el = rowNamedPlan(container, name);
    fireEvent.keyDown(el, { key: "e" });
    await waitFor(() => expect(container.querySelector("#unassigned-today")).not.toBeNull());
    const headEl = container.querySelector<HTMLElement>(
      '#unassigned-today [data-source-group="todoist"] .unassigned-source__head',
    );
    expect(headEl?.querySelector(".origin-chip")?.textContent).toBe("Home Lab");
    expect(headEl?.querySelector(".origin-chip")?.classList.contains("origin-chip--td-teal")).toBe(true);
    expect(headEl?.querySelector(".unassigned-source__count")?.textContent).toBe("1");
  });

  it("no longer sizes the heading at 9px uppercase", () => {
    const m = drawerCss.match(/\.unassigned-source__head\s*\{([^}]*)\}/);
    expect(m).toBeTruthy();
    expect(m![1]).not.toMatch(/font-size:\s*9px/);
    expect(m![1]).not.toMatch(/text-transform:\s*uppercase/);
    expect(m![1]).toMatch(/font-size:\s*1[23]px/);
  });
});

function rowNamedPlan(container: Element, name: string): HTMLElement {
  return rowNamed(container, name);
}

describe("Todoist colour tokens", () => {
  const start = appCss.indexOf("/* origin-chip tokens */");
  const section = start >= 0 ? appCss.slice(start) : "";
  const blocks = (selector: string): string => {
    const m = section.match(new RegExp(`${selector}\\s*\\{([^}]*)\\}`));
    return m ? m[1] : "";
  };
  const light = blocks(":root");
  const mediaDark = (section.match(/@media \(prefers-color-scheme: dark\) \{\s*:root \{([^}]*)\}/) ?? [])[1] ?? "";
  const forcedDark = blocks(':root\\[data-theme="dark"\\]');

  it("lists the 20 Todoist colour names", () => {
    expect(TODOIST_COLORS).toHaveLength(20);
  });

  it("defines every name in the light and both dark theme blocks", () => {
    for (const name of TODOIST_COLORS) {
      const token = `--c-td-${name.replace(/_/g, "-")}:`;
      expect(light, `light ${name}`).toContain(token);
      expect(mediaDark, `media dark ${name}`).toContain(token);
      expect(forcedDark, `forced dark ${name}`).toContain(token);
    }
    for (let n = 1; n <= 8; n++) {
      for (const [label, body] of [["light", light], ["media", mediaDark], ["dark", forcedDark]]) {
        expect(body, `${label} cap ${n}`).toContain(`--c-cap-${n}:`);
      }
    }
  });
});

describe("origin chip contrast and hex discipline", () => {
  const start = appCss.indexOf("/* origin-chip tokens */");
  const section = appCss.slice(start);
  const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const mix = (a: string, b: string, pct: number) =>
    rgb(a).map((x, i) => x * (pct / 100) + rgb(b)[i] * (1 - pct / 100));
  const lum = (c: number[]) => {
    const [r, g, b] = c.map((v) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a: number[], b: number[]) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const themes = [
    { name: "light", body: /:root \{([^}]*)\}/, surface: "#fdfcf9", text: "#2a2a27" },
    { name: "dark", body: /:root\[data-theme="dark"\] \{([^}]*)\}/, surface: "#191917", text: "#e8e6e0" },
  ];

  for (const t of themes) {
    it(`keeps chip ink at 4.5:1 on its tint in the ${t.name} theme`, () => {
      const body = section.match(t.body)![1];
      const tokens = [...body.matchAll(/--c-(?:td|cap)-[\w-]+:\s*(#[0-9a-f]{6})/g)];
      expect(tokens.length).toBe(28);
      for (const [, hex] of tokens) {
        const bg = mix(hex, t.surface, 14);
        const ink = mix(hex, t.text, 40);
        expect(ratio(ink, bg), `${t.name} ${hex}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }

  it("uses hex only inside the token blocks, never in the chip rules", () => {
    const rules = section.slice(section.indexOf(".origin-chip[data-origin]"));
    expect(rules).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });
});

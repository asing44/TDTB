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
  CAPACITIES_SLUGS,
  TODOIST_COLORS,
  capacitiesColorClass,
  capacitiesHue,
  capacitiesSlug,
  labelChipClass,
  originChipSpec,
} from "./sourceChip";

afterEach(cleanup);

const appCss = readFileSync(resolve(process.cwd(), "src/app.css"), "utf8");
const tokensCss = readFileSync(resolve(process.cwd(), "src/origin-tokens.css"), "utf8");
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

  it("projects label colours as lowercased names, null for blank or non-string", () => {
    const row = projectAssigned({
      name: "T",
      source: "todoist",
      labels: ["Hour", "Habits", "Odd"],
      label_colors: { Hour: "Sky_Blue", Habits: null, Odd: "  ", Num: 4 },
    });
    expect(row.labelColors).toEqual({ Hour: "sky_blue", Habits: null, Odd: null, Num: null });
  });

  it("defaults label colours to {} when missing or malformed", () => {
    expect(projectAssigned({ name: "T", source: "todoist" }).labelColors).toEqual({});
    expect(projectAssigned({ name: "T", source: "todoist", label_colors: ["x"] }).labelColors).toEqual({});
    expect(projectAssigned({ name: "T", source: "capacities", label_colors: { a: "red" } }).labelColors).toEqual({});
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
    expect(spec?.className).toContain("origin-chip--cap-project");
    // Case-insensitive named slug; unknown titles hash onto the eight fallback hues.
    expect(capacitiesColorClass("PROJECT")).toBe("origin-chip--cap-project");
    expect(capacitiesColorClass("Gizmo")).toBe(`origin-chip--cap-h${capacitiesHue("Gizmo")}`);
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

describe("Capacities title to slug", () => {
  it("lowercases, dashes spaces and matches singular and plural alike", () => {
    expect(capacitiesSlug("Research Note")).toBe("research-note");
    expect(capacitiesSlug("Horticos")).toBe("hortico");
    expect(capacitiesSlug("Presses")).toBe("press");
    expect(capacitiesSlug("Press")).toBe("press");
    expect(capacitiesSlug("Purchases")).toBe("purchase");
    for (const [plural, slug] of [["Prints", "print"], ["Projects", "project"], ["Continuals", "continual"],
      ["Events", "event"], ["Tasks", "task"], ["Atomic Notes", "atomic-note"]]) {
      expect(capacitiesSlug(plural)).toBe(slug);
    }
  });

  it("gives every named type its own colour class and unknowns a hash hue", () => {
    expect(capacitiesColorClass("Horticos")).toBe("origin-chip--cap-hortico");
    expect(capacitiesColorClass("Presses")).toBe("origin-chip--cap-press");
    expect(capacitiesColorClass("Atomic Note")).toBe("origin-chip--cap-atomic-note");
    expect(capacitiesColorClass("Zzz Thing")).toMatch(/^origin-chip--cap-h[1-8]$/);
  });
});

describe("label chip class", () => {
  it("maps a known Todoist colour name to a label-chip class, else null", () => {
    expect(labelChipClass("sky_blue")).toBe("label-chip label-chip--td-sky_blue");
    expect(labelChipClass("Berry_Red")).toBe("label-chip label-chip--td-berry_red");
    expect(labelChipClass("chartreuse")).toBeNull();
    expect(labelChipClass(null)).toBeNull();
    expect(labelChipClass(undefined)).toBeNull();
  });

  it("colours a row's label chips from labelColors and leaves others plain", () => {
    const h = makeHarness("ready");
    const inputs = structuredClone(h.store.getState().inputs!);
    inputs.assigned.push(
      item({
        id: "Lab row",
        name: "Lab row",
        labels: ["Hour", "Habits", "Mystery"],
        labelColors: { Hour: "sky_blue", Habits: null, Mystery: "chartreuse" },
      }),
    );
    h.store.dispatch({ type: "INPUTS_LOADED", inputs, ledger: { ...h.scenario.ledger } });
    const { container } = h.ui(<Queue />);
    const tags = Array.from(rowNamed(container, "Lab row").querySelectorAll<HTMLElement>(".qrow__tag"));
    expect(tags.map((t) => t.textContent)).toEqual(["Hour", "Habits", "Mystery"]);
    expect(tags[0].classList.contains("label-chip--td-sky_blue")).toBe(true);
    expect(tags[1].classList.contains("label-chip")).toBe(false);
    expect(tags[2].classList.contains("label-chip")).toBe(false);
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
      expect(chip?.classList.contains("origin-chip--cap-project")).toBe(true);
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

/** The four token layouts in origin-tokens.css: base, system dark, forced
    light, forced dark. */
const tokenBlocks = (() => {
  const light = (tokensCss.match(/^:root \{([^}]*)\}/m) ?? [])[1] ?? "";
  const mediaDark = (tokensCss.match(/@media \(prefers-color-scheme: dark\) \{\s*:root \{([^}]*)\}/) ?? [])[1] ?? "";
  const forcedLight = (tokensCss.match(/:root\[data-theme="light"\] \{([^}]*)\}/) ?? [])[1] ?? "";
  const forcedDark = (tokensCss.match(/:root\[data-theme="dark"\] \{([^}]*)\}/) ?? [])[1] ?? "";
  return { light, mediaDark, forcedLight, forcedDark };
})();

describe("chip colour tokens", () => {
  it("lists the 20 Todoist colour names", () => {
    expect(TODOIST_COLORS).toHaveLength(20);
  });

  it("defines every Todoist name, hash hue and Capacities slug in all four blocks", () => {
    const names = [
      ...TODOIST_COLORS.map((n) => `--c-td-${n.replace(/_/g, "-")}:`),
      ...Array.from({ length: 8 }, (_, i) => `--c-cap-${i + 1}:`),
      ...CAPACITIES_SLUGS.map((n) => `--c-cap-${n}:`),
    ];
    for (const [label, body] of Object.entries(tokenBlocks)) {
      for (const token of names) expect(body, `${label} ${token}`).toContain(token);
    }
  });

  it("has a colour class for every token and for each Capacities slug", () => {
    for (const slug of CAPACITIES_SLUGS) {
      expect(tokensCss).toContain(`.origin-chip--cap-${slug} {`);
    }
    for (const n of TODOIST_COLORS) {
      expect(tokensCss).toContain(`.origin-chip--td-${n} {`);
      expect(tokensCss).toContain(`.label-chip--td-${n} {`);
    }
  });

  it("is no longer duplicated in app.css", () => {
    expect(appCss).not.toContain("--c-td-");
    expect(appCss).not.toContain("--c-cap-");
    expect(appCss).not.toMatch(/\.origin-chip--(td|cap)-/);
  });
});

describe("origin chip contrast and hex discipline", () => {
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
    { name: "light", body: tokenBlocks.light, surface: "#fdfcf9", text: "#2a2a27" },
    { name: "forced light", body: tokenBlocks.forcedLight, surface: "#fdfcf9", text: "#2a2a27" },
    { name: "dark", body: tokenBlocks.forcedDark, surface: "#191917", text: "#e8e6e0" },
    { name: "system dark", body: tokenBlocks.mediaDark, surface: "#191917", text: "#e8e6e0" },
  ];
  const expected = 20 + 8 + CAPACITIES_SLUGS.length;

  for (const t of themes) {
    it(`keeps chip ink at 4.5:1 on its tint in the ${t.name} theme`, () => {
      const tokens = [...t.body.matchAll(/--c-(?:td|cap)-[\w-]+:\s*(#[0-9a-f]{6})/g)];
      expect(tokens.length).toBe(expected);
      for (const [, hex] of tokens) {
        const bg = mix(hex, t.surface, 14);
        const ink = mix(hex, t.text, 40);
        expect(ratio(ink, bg), `${t.name} ${hex}`).toBeGreaterThanOrEqual(4.5);
      }
    });
  }

  it("uses hex only inside the token blocks, never in the chip rules", () => {
    const rules = tokensCss.slice(tokensCss.indexOf("/* Colour classes"));
    expect(rules).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    const base = appCss.slice(appCss.indexOf(".origin-chip[data-origin]"));
    expect(base).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });
});

describe("origin chip colour specificity", () => {
  it("the base chip rule does not set --chip-c, so colour classes can win", () => {
    const css = readFileSync(resolve(__dirname, "../app.css"), "utf8");
    const start = css.indexOf(".origin-chip[data-origin] {");
    expect(start).toBeGreaterThan(-1);
    const body = css.slice(start, css.indexOf("}", start));
    // A (0,2,0) base rule declaring --chip-c would beat every
    // .origin-chip--td-* / --cap-h* class (0,1,0) and paint every chip grey.
    expect(body).not.toMatch(/--chip-c\s*:/);
    expect(body).toMatch(/var\(--chip-c,\s*var\(--t-muted\)\)/);
  });

  it("the label-chip base rule does not set --chip-c either", () => {
    const start = tokensCss.indexOf(".qrow__tag.label-chip {");
    expect(start).toBeGreaterThan(-1);
    const body = tokensCss.slice(start, tokensCss.indexOf("}", start));
    expect(body).not.toMatch(/--chip-c\s*:/);
    expect(body).toMatch(/var\(--chip-c,\s*var\(--t-muted\)\)/);
  });
});

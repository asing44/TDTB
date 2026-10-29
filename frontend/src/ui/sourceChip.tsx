/* sourceChip.tsx — the ONE origin chip a queue / plan / drawer row wears.

   Todoist rows show their PROJECT NAME in that project's real Todoist colour;
   Capacities rows show their OBJECT TYPE in a fixed hue per type (Capacities
   exposes no colour through its API). Calendar and unknown sources keep their
   current plain label, so `originChipSpec` returns null for them.

   Colours are tokens, never inline hex: each class only selects a custom
   property (`--c-td-*`, `--c-cap-*` in app.css, light and dark) and the shared
   `.origin-chip` rule derives tint, border and ink from it. A missing or
   unrecognised colour reads as the neutral grey chip. */

import { kindLabel } from "./TaskRow";
import type { AssignedItem } from "../model/types";

/** Todoist's palette names, as the API sends them. */
export const TODOIST_COLORS = [
  "berry_red",
  "red",
  "orange",
  "yellow",
  "olive_green",
  "lime_green",
  "green",
  "mint_green",
  "teal",
  "sky_blue",
  "light_blue",
  "blue",
  "grape",
  "violet",
  "lavender",
  "magenta",
  "salmon",
  "charcoal",
  "grey",
  "taupe",
] as const;

const KNOWN_COLORS: ReadonlySet<string> = new Set(TODOIST_COLORS);

/** Fixed hue slot (1-8) per Capacities object type, keyed by lowercased title. */
const CAPACITIES_HUES: Record<string, number> = {
  task: 1,
  project: 2,
  event: 3,
  continual: 4,
  press: 5,
  purchase: 6,
  print: 7,
  horticos: 8,
};

/** The untitled-structure placeholder `kindLabel` returns. */
const PLACEHOLDER_KIND = "Capacities item";

/** Hue slot 1-8 for a Capacities type title: the named map first, else a
    deterministic hash onto the same eight slots. */
export function capacitiesHue(title: string): number {
  const key = title.trim().toLowerCase();
  const named = CAPACITIES_HUES[key];
  if (named) return named;
  let hash = 5381;
  for (let i = 0; i < key.length; i++) hash = (hash * 33 + key.charCodeAt(i)) >>> 0;
  return (hash % 8) + 1;
}

export interface OriginChipSpec {
  /** Visible text: project name or object type. */
  label: string;
  /** Full text for the title attribute (the chip truncates in CSS). */
  title: string;
  /** Space-separated classes: `origin-chip` plus its colour class. */
  className: string;
  origin: "todoist" | "capacities";
}

/** Colour class for a Todoist project: its palette name, the neutral chip for
    a named project with an unknown/missing colour, or the source hue when the
    row has no project at all. */
function todoistColorClass(projectName: string | null, colorName: string | null): string {
  if (!projectName) return "origin-chip--td-source";
  if (colorName && KNOWN_COLORS.has(colorName)) return `origin-chip--td-${colorName}`;
  return "origin-chip--neutral";
}

export function chipSpecFor(parts: {
  source: string;
  projectName?: string | null;
  projectColor?: string | null;
  kind?: string | null;
}): OriginChipSpec | null {
  if (parts.source === "todoist") {
    const name = (parts.projectName ?? "").trim() || null;
    const color = (parts.projectColor ?? "").trim().toLowerCase() || null;
    const label = name ?? "Todoist";
    return {
      label,
      title: label,
      className: `origin-chip ${todoistColorClass(name, color)}`,
      origin: "todoist",
    };
  }
  if (parts.source === "capacities") {
    const label = (parts.kind ?? "").trim() || PLACEHOLDER_KIND;
    const hue =
      label === PLACEHOLDER_KIND ? "origin-chip--cap-unknown" : `origin-chip--cap-h${capacitiesHue(label)}`;
    return { label, title: label, className: `origin-chip ${hue}`, origin: "capacities" };
  }
  return null;
}

export function originChipSpec(
  item: AssignedItem,
  titles: Record<string, string> | null | undefined,
): OriginChipSpec | null {
  return chipSpecFor({
    source: String(item.source),
    projectName: item.projectName,
    projectColor: item.projectColor,
    kind: kindLabel(item, titles),
  });
}

/** The chip element. `qrow__src` stays on it so row-level queries and the
    per-source modifier (`qrow__src--todoist`) keep meaning "the origin chip". */
export function OriginChip({ spec, source }: { spec: OriginChipSpec; source: string }) {
  return (
    <span
      class={`qrow__src qrow__src--${source} ${spec.className}`}
      data-origin={spec.origin}
      title={spec.title}
    >
      {spec.label}
    </span>
  );
}

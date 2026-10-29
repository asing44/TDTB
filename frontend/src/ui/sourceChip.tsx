/* sourceChip.tsx — the ONE origin chip a queue / plan / drawer row wears.

   Todoist rows show their PROJECT NAME in that project's real Todoist colour;
   Capacities rows show their OBJECT TYPE in the owner's
   Capacities type colour (a named token per type; unknown types hash onto
   eight fallback hues). Calendar and unknown sources keep their
   current plain label, so `originChipSpec` returns null for them.

   Colours are tokens, never inline hex: each class only selects a custom
   property (`--c-td-*`, `--c-cap-*` in origin-tokens.css, light and dark) and the shared
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

/** Capacities object types that have a named colour token
    (`--c-cap-<slug>` in origin-tokens.css). Keep in step with that file. */
export const CAPACITIES_SLUGS = [
  "task", "event", "day", "week", "month", "project", "print", "press", "continual",
  "adventure", "trip", "review", "meeting", "birthday", "hortico", "place", "purchase",
  "peep", "entity", "read", "film", "research-note", "atomic-note", "definition",
  "fleeting", "recipe", "area", "weblink", "page", "whiteboard", "tag", "query",
] as const;

const KNOWN_SLUGS: ReadonlySet<string> = new Set(CAPACITIES_SLUGS);

/** The untitled-structure placeholder `kindLabel` returns. */
const PLACEHOLDER_KIND = "Capacities item";

/** Lowercased, dash-separated, singular slug for a Capacities type title, so
    "Horticos" and "Hortico", "Presses" and "Press" resolve to one token. */
export function capacitiesSlug(title: string): string {
  const base = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (/sses$/.test(base)) return base.slice(0, -2);
  if (/s$/.test(base) && !/ss$/.test(base)) return base.slice(0, -1);
  return base;
}

/** Hash fallback slot 1-8 for a title with no named token. */
export function capacitiesHue(title: string): number {
  const key = title.trim().toLowerCase();
  let hash = 5381;
  for (let i = 0; i < key.length; i++) hash = (hash * 33 + key.charCodeAt(i)) >>> 0;
  return (hash % 8) + 1;
}

/** Colour class for a Capacities type title: its named slug token, else the
    deterministic hash hue. */
export function capacitiesColorClass(title: string): string {
  const slug = capacitiesSlug(title);
  return KNOWN_SLUGS.has(slug) ? `origin-chip--cap-${slug}` : `origin-chip--cap-h${capacitiesHue(title)}`;
}

/** Class for a Todoist label chip with a real Todoist colour name, or null
    (unknown/missing colour keeps the plain tag styling). */
export function labelChipClass(colorName: string | null | undefined): string | null {
  const name = (colorName ?? "").trim().toLowerCase();
  return KNOWN_COLORS.has(name) ? `label-chip label-chip--td-${name}` : null;
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
      label === PLACEHOLDER_KIND ? "origin-chip--cap-unknown" : capacitiesColorClass(label);
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

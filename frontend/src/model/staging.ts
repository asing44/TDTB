/* staging.ts — allocator-rewrite T3 → IMP-07: the verbs a Today's-work row
   can fire.

   Pre-commit these run through the SAME journal as the committed-phase
   runtime verbs (T20): same before-images, same one-step undo, same
   idempotency. The only difference is server-side — `resolve_target` falls
   back to today's `digest_index` when the item has no plan_manifest row (T2).

   IMP-05/IMP-07 final UI verb set (frozen action table): Done,
   Drop from plan, Unassign, Delete. 'Defer' and 'Assign' are NOT product
   verbs — they are gone from the catalogue. Drop from plan is a date-scoped
   runstate-only exclusion (no source write); Unassign flips vault
   assignment / clears or advances the Todoist due; Delete is the only
   destructive verb.

   The placement verbs (skip_today, remove_from_today, duration_edit,
   move_resize) are deliberately absent: they act on derived records that do
   not exist until /commit, and the server refuses them with "still staged".
   Keeping the list here means the UI never offers a verb the server will
   reject.

   Pure data + predicates — no Preact, no adapter, no I/O. */

export type StagingVerb = "done" | "drop_from_plan" | "unassign" | "delete";

export interface StagingVerbSpec {
  verb: StagingVerb;
  /** Button label. */
  label: string;
  /** Accessible name — verbs read out of context in a dense row. */
  aria: string;
  /** Visible scope note shown with the verb, so source writes never rely on
      an icon, tooltip, or inferred server behavior. */
  detail: string;
  /** True when the verb destroys a source and deserves a confirm step. */
  destructive: boolean;
  /** True when applying this verb writes to the upstream Obsidian or Todoist
      record rather than only changing today's local plan. */
  sourceMutation: boolean;
  /** Rendered directly on the row (Done / Drop from plan). False verbs
      (Unassign / Delete) live behind the per-row More menu. */
  direct: boolean;
}

export const STAGING_VERBS: readonly StagingVerbSpec[] = [
  {
    verb: "done",
    label: "Mark complete",
    aria: "Mark complete in source",
    detail: "updates source",
    destructive: false,
    sourceMutation: true,
    direct: true,
  },
  {
    verb: "drop_from_plan",
    label: "Leave as-is",
    aria: "Leave as-is today without changing source",
    detail: "today only · source unchanged",
    destructive: false,
    sourceMutation: false,
    direct: true,
  },
  {
    verb: "unassign",
    label: "Remove from planning",
    aria: "Remove from planning and update source",
    detail: "updates Obsidian or Todoist source",
    destructive: false,
    sourceMutation: true,
    direct: false,
  },
  {
    verb: "delete",
    label: "Delete",
    aria: "Delete permanently",
    detail: "permanently deletes source",
    destructive: true,
    sourceMutation: true,
    direct: false,
  },
] as const;

/** The verbs a row renders inline (Done, Drop from plan). */
export const DIRECT_VERBS: readonly StagingVerbSpec[] = STAGING_VERBS.filter(
  (s) => s.direct,
);

/** The verbs a row hides behind its More menu (Unassign, Delete). */
export const MORE_VERBS: readonly StagingVerbSpec[] = STAGING_VERBS.filter(
  (s) => !s.direct,
);

const BY_VERB = new Map<string, StagingVerbSpec>(
  STAGING_VERBS.map((s) => [s.verb, s]),
);

export function isStagingVerb(verb: string): verb is StagingVerb {
  return BY_VERB.has(verb);
}

export function stagingVerbSpec(verb: string): StagingVerbSpec | null {
  return BY_VERB.get(verb) ?? null;
}

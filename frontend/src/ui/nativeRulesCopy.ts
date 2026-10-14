/* nativeRulesCopy — the ONE place the "native rules" wording lives.

   "Native rules" is a control label on the Connections screen and in the
   Capacities settings panel, and neither surface explained what the rules do.
   Both call sites render these exact strings so the explanation cannot drift
   between screens.

   The sentence is pinned to the evaluator, not to either screen:
   `app/capacities_assignment.py` (`_auto_conditions`, `evaluate_assignment`)
   applies the native Auto policy only to structures listed in
   `native_task_structures` (persisted by `app/capacities_settings.py`), and
   admits an item when ANY of three independently switched conditions matches:
   status in `active_statuses`, due <= logical day, or deadline within
   `deadline_horizon_days`. Completed/dropped statuses always fall through, and
   a structure without native rules is judged only by its own per-structure
   rule. The Assigned On / legacy assignment mapping is a separate concern and
   deliberately never appears here. */

export const NATIVE_RULES_LABEL = "Native rules";

/** Accessible name of the info trigger. Same copy everywhere. */
export const NATIVE_RULES_TRIGGER_LABEL = "What native rules are";

/** Bubble detail: what the native rules are, not only what they are called. */
export const NATIVE_RULES_DETAIL =
  "Built-in admission rules, applied only to the structures that have native " +
  "rules switched on. An item is admitted when any one of them matches: its " +
  "source status matches a configured status value, its due date is today or " +
  "earlier, or its deadline is overdue or within the horizon. Each rule can " +
  "be switched off. Completed and dropped items are always left out; a " +
  "structure without native rules is judged only by its own per-structure rule.";

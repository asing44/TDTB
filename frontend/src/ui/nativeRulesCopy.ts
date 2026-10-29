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

export const NATIVE_RULES_LABEL = "Automatic task rules";

/** Accessible name of the info trigger. Same copy everywhere. */
export const NATIVE_RULES_TRIGGER_LABEL = "What automatic task rules are";

/** Bubble detail: what the native rules are, not only what they are called. */
export const NATIVE_RULES_DETAIL =
  "Built-in rules that decide which tasks come in without you doing anything. " +
  "They apply only to the types that have automatic task rules switched on. A " +
  "task comes in when any one of them matches: its status in Capacities counts " +
  "as active, it is due today or earlier, or its deadline is overdue or coming " +
  "up soon. Each rule can be switched off. Completed and dropped items always " +
  "stay out; a type without automatic task rules follows only its own rule.";

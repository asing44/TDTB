/* nativeRulesCopy — the ONE place the "native rules" wording lives.

   "Native rules" is a control label on the Connections screen and in the
   Capacities settings panel, and neither surface explained what the rules do.
   Both call sites render these exact strings so the explanation cannot drift
   between screens.

   The sentence is pinned to the evaluator, not to either screen:
   `app/capacities_assignment.py` (`_auto_conditions`, `evaluate_assignment`)
   applies the native rules only to structures whose schema-v2 type declaration
   is `mode: "native"` (persisted by `app/capacities_settings.py`), and admits an
   item when ANY of three conditions matches: status in the declaration's open
   names (inherited from `statuses.open` unless it states its own, and disabled
   entirely by `statuses: []`), due <= logical day, or deadline within
   `native.deadline_horizon_days`. Completed/dropped statuses always fall
   through, and a structure without a native rule is judged only by its own
   single rule. */

export const NATIVE_RULES_LABEL = "Automatic task rules";

/** Accessible name of the info trigger. Same copy everywhere. */
export const NATIVE_RULES_TRIGGER_LABEL = "What automatic task rules are";

/** Bubble detail: what the native rules are, not only what they are called. */
export const NATIVE_RULES_DETAIL =
  "Built-in rules that decide which tasks come in without you doing anything. " +
  "They apply only to the types whose rule is Native task rules. A task comes " +
  "in when any one of them matches: its status counts as open, it is due today " +
  "or earlier, or its deadline is overdue or coming up inside the deadline " +
  "window. A type can ignore status entirely, which leaves only the date " +
  "rules. Completed and dropped items always stay out; a type with any other " +
  "rule follows only that one.";

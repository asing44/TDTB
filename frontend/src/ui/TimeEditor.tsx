/* TimeEditor — task-actions F4b: the small inline editor that stages an exact
   start for one row (approved prototype r2, "time editor"). It validates
   HH:MM itself, and for a protected native-timed Todoist row it carries the
   "Allow changing source time" permission INSIDE the editor: Apply stays
   disabled until the operator ticks it (the UI half of `canApplyPin`).
   Apply/Cancel/Escape all close the editor; the parent returns focus to the
   trigger that opened it. Nothing here writes — Apply only reports the time
   and permission back so the parent can stage them on the row's draft. */

import { useEffect, useRef, useState } from "preact/hooks";

const HHMM = /^(?:[01]\d|2[0-3]):[0-5]\d$/;

export function TimeEditor({
  idBase,
  rowName,
  initialTime,
  protectedSource,
  initialAllow,
  onApply,
  onClose,
}: {
  /** Sanitized row id used to build stable element ids. */
  idBase: string;
  rowName: string;
  initialTime: string;
  /** The row's start is a protected Todoist native time. */
  protectedSource: boolean;
  initialAllow: boolean;
  onApply: (time: string, allowSourceTime: boolean) => void;
  /** Cancel, Escape, and the close after Apply. */
  onClose: () => void;
}) {
  const [time, setTime] = useState(initialTime);
  const [allow, setAllow] = useState(initialAllow);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);

  const valid = HHMM.test(time);
  const needsPermission = protectedSource && !allow;
  const blocked = !valid || needsPermission;
  const hint = !valid
    ? "Enter a start time."
    : needsPermission
      ? "Check “Allow changing source time” to enable Apply."
      : "Apply stages a pinned start; Save keeps it.";
  const inputId = `time-input-${idBase}`;
  const allowId = `allow-src-${idBase}`;
  const hintId = `editor-hint-${idBase}`;

  return (
    <div
      class="trow__editor"
      id={`time-editor-${idBase}`}
      role="group"
      aria-label={`Exact start time for ${rowName}`}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onClose();
        }
      }}
    >
      <div class="trow__ctl">
        <label for={inputId}>
          <strong>Start time</strong>
        </label>
        <input
          ref={inputRef}
          type="time"
          id={inputId}
          value={time}
          aria-invalid={!valid}
          aria-describedby={hintId}
          onInput={(e) => setTime((e.target as HTMLInputElement).value)}
        />
      </div>
      {protectedSource && (
        <label class="trow__perm" for={allowId}>
          <input
            type="checkbox"
            id={allowId}
            checked={allow}
            onChange={(e) => setAllow((e.target as HTMLInputElement).checked)}
          />
          <span>
            <strong>Allow changing source time</strong>
            <span class="trow__why">
              This time comes from Todoist (native-timed). Save keeps this in today's plan only;
              changing Todoist requires a separate commit.
            </span>
          </span>
        </label>
      )}
      <p class="trow__why" id={hintId}>
        {hint}
      </p>
      <div class="trow__ctl">
        <button
          type="button"
          class="trow__btn trow__btn--primary"
          disabled={blocked}
          onClick={() => {
            if (!blocked) onApply(time, allow);
          }}
        >
          Apply
        </button>
        <button type="button" class="trow__btn" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

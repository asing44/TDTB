/* Set up day loose-end banner. The prior Live block is unconfirmed (no done
   answer yet). One answer lands here: Did it records done, Dismiss records
   not-done. It replaces the inline pending text, and it reports its own
   pending entry back so the parent never narrows a property in a closure. */

export interface PendingLooseEnd {
  date: string;
  id: string;
  idea: string;
}

export interface LooseEndBannerProps {
  pending: PendingLooseEnd;
  onAnswer: (pending: PendingLooseEnd, done: boolean) => void;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-11" -> "11 Jul". A malformed date is shown as given. */
export function shortDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? m[2]}`;
}

export function LooseEndBanner(props: LooseEndBannerProps) {
  return (
    <section
      class="loose-end"
      aria-label="Loose end"
      title="Tick its Todoist task or the daily note's Live box, or answer here"
    >
      <span class="loose-end__label">Loose end</span>
      <span class="loose-end__text">
        {shortDay(props.pending.date)} · {props.pending.idea}
      </span>
      <span class="loose-end__actions">
        <button
          type="button"
          class="btn"
          onClick={() => props.onAnswer(props.pending, false)}
        >
          Dismiss
        </button>
        <button
          type="button"
          class="btn btn--primary"
          onClick={() => props.onAnswer(props.pending, true)}
        >
          Did it
        </button>
      </span>
    </section>
  );
}

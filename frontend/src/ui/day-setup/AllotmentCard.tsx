import type { Capacity } from '../../model/types';

/** Blocks are 30 minutes; "2h 15m" style, "0m" for none. */
export function allotmentDuration(blocks: number): string {
  const mins = Math.round(Math.max(0, blocks) * 30);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

interface Seg { key: string; label: string; blocks: number; color: string }

/** Presentational Set up summary of where the day goes. Same numbers the
    Capacity card reads (store `capacity`); nothing is derived beyond free =
    day capacity minus fixed time. */
export function AllotmentCard({ capacity }: { capacity: Capacity | null | undefined }) {
  if (!capacity) return null;
  const fixed = capacity.fixed + capacity.mint + capacity.anchored + capacity.habits;
  const free = Math.max(0, capacity.total - fixed);
  const segs: Seg[] = [
    { key: 'calendar', label: 'Calendar', blocks: capacity.fixed, color: 'var(--lane-cal-bar)' },
    { key: 'work', label: 'Work (Mint)', blocks: capacity.mint, color: 'var(--lane-mint-bar)' },
    { key: 'anchors', label: 'Anchors', blocks: capacity.anchored, color: 'var(--lane-anchor-bar)' },
    { key: 'habits', label: 'Habits', blocks: capacity.habits, color: 'var(--lane-habit)' },
    { key: 'free', label: 'Free for tasks', blocks: free, color: 'var(--lane-task-bar)' },
  ];
  const basis = Math.max(segs.reduce((n, s) => n + s.blocks, 0), 1);
  const summary = `${allotmentDuration(free)} left for tasks in a ${allotmentDuration(capacity.total)} day`;
  const label = segs.map((s) => `${s.label} ${allotmentDuration(s.blocks)}`).join(' · ');
  return (
    <section class="setup-section allotment-card" aria-labelledby="setup-sec-allotment">
      <div class="setup-section__head">
        <h3 id="setup-sec-allotment">Allotment</h3>
        <span class="allotment-card__summary">{summary}</span>
      </div>
      <div class="allotment-card__bar" role="img" aria-label={label}>
        {segs.map((s) => (
          <div key={s.key} class={`allotment-card__seg allotment-card__seg--${s.key}`}
            style={{ width: `${(s.blocks / basis) * 100}%`, background: s.color }} />
        ))}
      </div>
      <ul class="allotment-card__legend">
        {segs.map((s) => (
          <li key={s.key} class="allotment-card__legend-item">
            <span class="allotment-card__swatch" style={{ background: s.color }} />
            <span>{s.label}</span>
            <span class="allotment-card__value">{allotmentDuration(s.blocks)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

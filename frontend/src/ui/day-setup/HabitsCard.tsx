import { useRef, useState } from 'preact/hooks';
import { habitsView, parseHabitMinutes, withHabitEntry } from '../../model/habits';
import type { HabitOverrides, HabitsInfo } from '../../model/types';
import { HabitsDrawer, blocksText, type HabitsSaveStatus } from './HabitsDrawer';

const SAVE_FAILED = 'Could not save habits. Your previous settings were restored.';

function todayLabel(): string {
  return new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

/** Today-only planning controls. No upstream completion or assignment writes.
    With per-habit items the card is a one-line summary that opens the drawer;
    with only the aggregate it keeps the single total row inline. */
export function HabitsCard({ info, overrides, onSave, dateLabel }: {
  info?: HabitsInfo;
  overrides?: HabitOverrides;
  onSave: (value: HabitOverrides) => Promise<void>;
  dateLabel?: string;
}) {
  const view = habitsView(info, overrides);
  const [status, setStatus] = useState<HabitsSaveStatus>('idle');
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const failed = useRef<HabitOverrides | null>(null);
  const busy = status === 'saving';
  if (view.mode === 'hidden') return null;
  async function save(value: HabitOverrides) {
    setStatus('saving'); setError('');
    try { await onSave(value); failed.current = null; setStatus('saved'); }
    catch { failed.current = value; setError(SAVE_FAILED); setStatus('failed'); }
  }
  if (view.mode === 'items') {
    const off = view.rows.filter(r => !r.on).length;
    const counting = view.rows.length - off;
    return <section class="setup-section habits-card" aria-labelledby="setup-sec-habits">
      <div class="setup-section__head">
        <h3 id="setup-sec-habits">Habits</h3>
        <span>{`${counting} counting · ${view.countedMinutes} min counted · ${blocksText(view.countedMinutes)}${off ? ` · ${off} off` : ''}`}</span>
        <button type="button" class="btn" aria-haspopup="dialog" onClick={() => setOpen(true)}>Review habits</button>
      </div>
      {open && <HabitsDrawer view={view} overrides={overrides} done={info?.done ?? 0}
        dateLabel={dateLabel ?? todayLabel()} status={status} error={error}
        onSave={v => void save(v)} onRetry={() => failed.current && void save(failed.current)}
        onClose={() => setOpen(false)} />}
    </section>;
  }
  return <section class="setup-section habits-card" aria-labelledby="setup-sec-habits">
    <div class="setup-section__head">
      <h3 id="setup-sec-habits">Habits</h3>
      <span>{view.summary}</span>
      <button type="button" class="btn" disabled={busy} onClick={() => void save({items:{},total:null})}>Reset habits</button>
    </div>
    <div class="setup-section__body">
      {view.rows.map(row => <div class="habits-card__row" key={row.key}>
        <span class="habits-card__name">{row.label}{row.edited && <small> · edited</small>}</span>
        <label>{row.label} minutes
          <input type="number" min="0" max="480" step="1" value={row.minutes} disabled={busy}
            aria-label={`${row.label} minutes`}
            onBlur={e => {
              const value = parseHabitMinutes(e.currentTarget.value);
              if (value === null) {
                e.currentTarget.value = String(row.minutes);
                setError('Enter whole minutes from 0 to 480.'); return;
              }
              if (value !== row.minutes) void save(withHabitEntry(overrides,row.key,{minutes:value},row.defaultMinutes));
            }} />
        </label>
        <button type="button" role="switch" class="anchor-popover__switch all-blocks__switch" aria-checked={row.on}
          aria-label={`Count ${row.label} today`} disabled={busy}
          onClick={() => void save(withHabitEntry(overrides,row.key,{on:!row.on},row.defaultMinutes))} />
        {row.edited && <button type="button" class="btn" disabled={busy}
          onClick={() => void save(withHabitEntry(overrides,row.key,{on:true,minutes:row.defaultMinutes},row.defaultMinutes))}>Reset {row.label}</button>}
      </div>)}
      {error && <span class="field-error" role="alert">{error}</span>}
    </div>
  </section>;
}

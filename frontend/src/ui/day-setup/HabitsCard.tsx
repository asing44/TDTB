import { useState } from 'preact/hooks';
import { habitsView, parseHabitMinutes, withHabitEntry } from '../../model/habits';
import type { HabitOverrides, HabitsInfo } from '../../model/types';

/** Today-only planning controls. No upstream completion or assignment writes. */
export function HabitsCard({ info, overrides, onSave }: {
  info?: HabitsInfo;
  overrides?: HabitOverrides;
  onSave: (value: HabitOverrides) => Promise<void>;
}) {
  const view = habitsView(info, overrides);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (view.mode === 'hidden') return null;
  async function save(value: HabitOverrides) {
    setBusy(true); setError('');
    try { await onSave(value); }
    catch { setError('Could not save habits. Your previous settings were restored.'); }
    finally { setBusy(false); }
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

import { useRef, useState } from 'preact/hooks';
import { habitsView, parseHabitMinutes, withHabitEntry, type HabitRow } from '../../model/habits';
import { HABIT_GROUPS, habitGroupOf, type HabitGroup } from '../../model/habitGroups';
import type { HabitOverrides, HabitsInfo } from '../../model/types';
import { HabitsDrawer, type HabitsSaveStatus } from './HabitsDrawer';

const SAVE_FAILED = 'Could not save habits. Your previous settings were restored.';

function todayLabel(): string {
  return new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

/** Rows bucketed for the rail, in the owner's group order. */
function grouped(rows: HabitRow[]): { group: HabitGroup; rows: HabitRow[] }[] {
  return HABIT_GROUPS.map((group) => ({
    group,
    rows: rows.filter((row) => habitGroupOf(row.label) === group),
  })).filter((g) => g.rows.length > 0);
}

/** Today-only planning controls. No upstream completion or assignment writes.
    Habits are an active list: the card shows the counted summary plus one chip
    per time-of-day bucket, and "Pick habits" unfolds the per-habit pills the
    design draws ("tap one to include it today"). */
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const failed = useRef<HabitOverrides | null>(null);
  const busy = status === 'saving';
  if (view.mode === 'hidden') return null;
  async function save(value: HabitOverrides) {
    setStatus('saving'); setError('');
    try { await onSave(value); failed.current = null; setStatus('saved'); }
    catch { failed.current = value; setError(SAVE_FAILED); setStatus('failed'); }
  }
  if (view.mode === 'items') {
    const on = view.rows.filter((r) => r.on).length;
    const groups = grouped(view.rows);
    /** One save per changed row, chained so the last write wins on the server. */
    async function setRows(rows: HabitRow[], nextOn: boolean) {
      let value = overrides;
      for (const row of rows) {
        value = withHabitEntry(value, row.key, { on: nextOn }, row.defaultMinutes);
      }
      if (value) await save(value);
    }
    return <section class="setup-section habits-card" aria-labelledby="setup-sec-habits">
      <div class="setup-section__head habits-card__head">
        <h3 id="setup-sec-habits">Habits</h3>
        <span class="habits-card__summary">{`${on} of ${view.rows.length} on · ${view.countedMinutes}m`}</span>
        {groups.map(({ group, rows }) => {
          const n = rows.filter((r) => r.on).length;
          const all = n === rows.length;
          return <button type="button" key={group}
            class={`habits-chip${n === 0 ? ' habits-chip--off' : all ? ' habits-chip--on' : ' habits-chip--mixed'}`}
            aria-pressed={all}
            aria-label={`${all ? 'Turn off' : 'Turn on'} ${group} habits`}
            disabled={busy}
            onClick={() => void setRows(rows, !all)}>
            {group} {n}/{rows.length}
          </button>;
        })}
        <button type="button" class="btn habits-card__pick" aria-expanded={open}
          onClick={() => setOpen(!open)}>
          {open ? 'Hide list ▴' : 'Pick habits ▾'}
        </button>
      </div>
      {open && (
        <div class="setup-section__body habits-list">
          <p class="habits-list__hint">
            Todoist › Habits · tap one to include it today ·{' '}
            <button type="button" class="habits-list__link" disabled={busy}
              onClick={() => void setRows(view.rows, true)}>all on</button>
            {' / '}
            <button type="button" class="habits-list__link" disabled={busy}
              onClick={() => void setRows(view.rows, false)}>all off</button>
            {' · '}
            <button type="button" class="habits-list__link"
              aria-haspopup="dialog"
              onClick={() => setDrawerOpen(true)}>Edit minutes</button>
          </p>
          {groups.map(({ group, rows }) => (
            <div class="habits-list__group" key={group}>
              <span class="habits-list__label">{group.toUpperCase()}</span>
              <div class="habits-list__pills">
                {rows.map((row) => (
                  <button type="button" key={row.key}
                    class={`habit-pill${row.on ? '' : ' habit-pill--off'}`}
                    aria-pressed={row.on}
                    aria-label={`Count ${row.label} today`}
                    disabled={busy}
                    onClick={() => void save(
                      withHabitEntry(overrides, row.key, { on: !row.on }, row.defaultMinutes),
                    )}>
                    {row.label} {row.minutes}m
                  </button>
                ))}
              </div>
            </div>
          ))}
          {error && !drawerOpen && <span class="field-error" role="alert">{error}</span>}
        </div>
      )}
      {error && !open && <span class="field-error" role="alert">{error}</span>}
      {drawerOpen && <HabitsDrawer view={view} overrides={overrides}
        dateLabel={dateLabel ?? todayLabel()} status={status} error={error}
        onSave={(v) => void save(v)} onRetry={() => failed.current && void save(failed.current)}
        onClose={() => setDrawerOpen(false)} />}
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

import { render, fireEvent, cleanup, waitFor } from '@testing-library/preact';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'preact/hooks';
import { HabitsCard } from './HabitsCard';
import { HabitsDrawer } from './HabitsDrawer';
import { habitsView } from '../../model/habits';
import type { HabitOverrides } from '../../model/types';
afterEach(cleanup);
const info = { total: 5, done: 2, outstanding: 3, estMinutes: 60, items: [
  {id:'a',name:'Reading',minutes:30},{id:'b',name:'Walk',minutes:20},{id:'c',name:'Stretch',minutes:10}] };
const EMPTY: HabitOverrides = {items:{},total:null};
/** Stateful host so saved overrides flow back into the card like the store does. */
function Host({ onSave, initial = EMPTY, done = info }: { onSave: (v: HabitOverrides) => Promise<void>; initial?: HabitOverrides; done?: typeof info }) {
  const [ov, setOv] = useState(initial);
  return <HabitsCard info={done} overrides={ov} onSave={async v => { await onSave(v); setOv(v); }} />;
}
const setup = (onSave = vi.fn().mockResolvedValue(undefined), extra: Partial<Parameters<typeof Host>[0]> = {}) => {
  const r = render(<Host onSave={onSave} {...extra} />);
  const opener = r.getByRole('button', {name:'Review habits'});
  opener.focus(); fireEvent.click(opener);
  return { r, onSave };
};
const saved = (r: ReturnType<typeof render>) => waitFor(() => expect(r.getByText('✓ Saved for today')).toBeTruthy());
const sw = (r: ReturnType<typeof render>, n: string) => r.getByRole('switch', {name:`Count ${n} today`});

it('rows reflect items and header copy has no capacity text', () => {
  const { r } = setup();
  expect(sw(r,'Reading').getAttribute('aria-checked')).toBe('true');
  expect(r.getByText(/Today only · .* · 60 min counted · 2 blocks/)).toBeTruthy();
  expect(r.getByText('Done today:')).toBeTruthy();
  expect(r.queryByText(/Day capacity|\bFree\b|over capacity/i)).toBeNull();
  expect(r.queryByText('Details')).toBeNull();
});
it('off dims the row in place with chip, disabled stepper and summary', async () => {
  const { r } = setup();
  const order = () => r.getAllByRole('switch').map(s => s.getAttribute('aria-label'));
  const before = order();
  fireEvent.click(sw(r,'Walk'));
  await waitFor(() => expect(sw(r,'Walk').getAttribute('aria-checked')).toBe('false'));
  expect(order()).toEqual(before);
  expect(r.getByText('Off')).toBeTruthy();
  expect(r.getByText(/not counted today/)).toBeTruthy();
  expect((r.getByLabelText('Increase Walk minutes by 5') as HTMLButtonElement).disabled).toBe(true);
  expect((r.getByLabelText('Decrease Walk minutes by 5') as HTMLButtonElement).disabled).toBe(true);
  expect(r.getByText(/Off today:/).parentElement?.textContent).toBe('Off today: Walk — 1 habit, 20 min not counted');
});
it('bulk off writes on:false per habit and back on restores', async () => {
  const { r, onSave } = setup();
  fireEvent.click(r.getByRole('button', {name:'Turn off all 3 habits'}));
  expect(onSave).toHaveBeenLastCalledWith({items:{a:{on:false},b:{on:false},c:{on:false}},total:null});
  await saved(r);
  fireEvent.click(await r.findByRole('button', {name:'Turn all back on'}));
  expect(onSave).toHaveBeenLastCalledWith({items:{},total:null});
});
it('stepper changes by 5 and clamps at 0 and 480', async () => {
  const { r, onSave } = setup(undefined, { done: {...info, items:[{id:'c',name:'Stretch',minutes:3},{id:'a',name:'Reading',minutes:478}]} });
  fireEvent.click(r.getByLabelText('Increase Reading minutes by 5'));
  expect(onSave).toHaveBeenLastCalledWith({items:{a:{minutes:480}},total:null});
  await saved(r);
  fireEvent.click(r.getByLabelText('Decrease Stretch minutes by 5'));
  expect(onSave).toHaveBeenLastCalledWith({items:{a:{minutes:480},c:{minutes:0}},total:null});
});
it('editor applies presets, typed minutes, rejects out of range and resets', async () => {
  const { r, onSave } = setup();
  fireEvent.click(r.getByLabelText('Reading: 30 minutes. Edit minutes'));
  fireEvent.click(r.getByRole('button', {name:'15'}));
  expect(onSave).toHaveBeenLastCalledWith({items:{a:{minutes:15}},total:null});
  await saved(r);
  const input = r.getByLabelText('Reading minutes today');
  fireEvent.input(input,{target:{value:'481'}}); fireEvent.blur(input);
  expect(r.getByRole('alert').textContent).toMatch(/0 to 480/);
  fireEvent.input(input,{target:{value:'45'}}); fireEvent.blur(input);
  expect(onSave).toHaveBeenLastCalledWith({items:{a:{minutes:45}},total:null});
  await saved(r);
  fireEvent.click(await r.findByRole('button', {name:'Reset to 30 min'}));
  expect(onSave).toHaveBeenLastCalledWith({items:{},total:null});
});
it('Esc and backdrop close and focus returns to the opener', () => {
  const { r } = setup();
  const opener = r.getByRole('button', {name:'Review habits'});
  fireEvent.keyDown(r.getByRole('dialog'), {key:'Escape'});
  expect(r.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(opener);
  opener.focus(); fireEvent.click(opener);
  fireEvent.click(document.querySelector('.drawer-backdrop')!);
  expect(r.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(opener);
});
it('dialog is labelled, modal, and is a bottom-sheet-capable panel', () => {
  const { r } = setup();
  const d = r.getByRole('dialog', {name:'Habits'});
  expect(d.getAttribute('aria-modal')).toBe('true');
  expect(d.classList.contains('habits-drawer')).toBe(true);
});
it('Reset day overrides clears overrides and shows count', async () => {
  const { r, onSave } = setup(undefined, { initial: {items:{a:{minutes:10},b:{on:false}},total:null} });
  const reset = r.getByRole('button', {name:'Reset day overrides (2 changed)'});
  fireEvent.click(reset);
  expect(onSave).toHaveBeenLastCalledWith({items:{},total:null});
  await waitFor(() => expect(r.getByText('✓ Saved for today')).toBeTruthy());
});
it('failure shows error and Retry re-saves the same value', async () => {
  const save = vi.fn().mockRejectedValueOnce(new Error('x')).mockResolvedValue(undefined);
  const { r } = setup(save);
  fireEvent.click(sw(r,'Reading'));
  await waitFor(() => expect(r.getByText(/previous settings were restored/)).toBeTruthy());
  expect(sw(r,'Reading').getAttribute('aria-checked')).toBe('true');
  fireEvent.click(r.getByRole('button', {name:'Retry'}));
  expect(save).toHaveBeenLastCalledWith({items:{a:{on:false}},total:null});
  await waitFor(() => expect(r.getByText('✓ Saved for today')).toBeTruthy());
});
const HabitsDrawerHarness = ({ onClose }: { onClose: () => void }) =>
  <HabitsDrawer view={habitsView(info, EMPTY)} overrides={EMPTY} done={0} dateLabel="Fri"
    status="idle" error="" onSave={() => {}} onRetry={() => {}} onClose={onClose} />;
/** Save that never resolves, so the drawer stays in its "saving" state. */
const pending = () => vi.fn().mockReturnValue(new Promise<void>(() => {}));
it('bulk stays natively enabled while saving and Esc closes from body or dialog', () => {
  for (const target of ['body', 'dialog'] as const) {
    const { r } = setup(pending());
    const opener = r.getByRole('button', {name:'Review habits'});
    const bulk = r.getByRole('button', {name:'Turn off all 3 habits'}) as HTMLButtonElement;
    bulk.focus(); fireEvent.click(bulk);
    expect(r.getByText('Saving…')).toBeTruthy();
    expect(bulk.disabled).toBe(false);
    expect(bulk.getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(bulk);
    fireEvent.keyDown(target === 'body' ? document.body : r.getByRole('dialog'), {key:'Escape'});
    expect(r.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
    cleanup();
  }
});
it('a click on aria-disabled bulk while saving is ignored', () => {
  const { r, onSave } = setup(pending());
  const bulk = r.getByRole('button', {name:'Turn off all 3 habits'});
  fireEvent.click(bulk); fireEvent.click(bulk);
  expect(onSave).toHaveBeenCalledTimes(1);
});
it('stepper tap keeps focus on the stepper while saving and Esc still closes', () => {
  const { r } = setup(pending());
  const opener = r.getByRole('button', {name:'Review habits'});
  const inc = r.getByLabelText('Increase Reading minutes by 5') as HTMLButtonElement;
  inc.focus(); fireEvent.click(inc);
  expect(inc.disabled).toBe(false);
  expect(document.activeElement).toBe(inc);
  fireEvent.keyDown(document.body, {key:'Escape'});
  expect(r.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(opener);
});
it('a stepper tapped into its limit stays focusable (aria-disabled, not native)', async () => {
  const { r } = setup(undefined, { done: {...info, items:[{id:'a',name:'Reading',minutes:478}]} });
  const inc = r.getByLabelText('Increase Reading minutes by 5') as HTMLButtonElement;
  inc.focus(); fireEvent.click(inc);
  await saved(r);
  expect(inc.disabled).toBe(false);
  expect(inc.getAttribute('aria-disabled')).toBe('true');
  expect(document.activeElement).toBe(inc);
});
it('Reset day overrides stays focusable after it clears the overrides', async () => {
  const { r } = setup(undefined, { initial: {items:{a:{minutes:10}},total:null} });
  const reset = r.getByRole('button', {name:'Reset day overrides (1 changed)'}) as HTMLButtonElement;
  reset.focus(); fireEvent.click(reset);
  await saved(r);
  const after = r.getByRole('button', {name:'Reset day overrides'}) as HTMLButtonElement;
  expect(after.disabled).toBe(false);
  expect(after.getAttribute('aria-disabled')).toBe('true');
  expect(document.activeElement).toBe(after);
});
it('Esc inside the dialog closes once; the document net does not double-fire', () => {
  const onClose = vi.fn();
  const r = render(<HabitsDrawerHarness onClose={onClose} />);
  fireEvent.keyDown(r.getByRole('dialog'), {key:'Escape'});
  expect(onClose).toHaveBeenCalledTimes(1);
});

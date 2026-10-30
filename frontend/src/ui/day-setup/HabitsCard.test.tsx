import { render, fireEvent, cleanup } from '@testing-library/preact';
import { afterEach, expect, it, vi } from 'vitest';
import { HabitsCard } from './HabitsCard';
afterEach(cleanup);
const info = { total: 1, estMinutes: 30, items: [{id:'a',name:'Reading',minutes:30}] };
const two = { total: 2, estMinutes: 40, items: [{id:'a',name:'Reading',minutes:30},{id:'b',name:'Walk',minutes:10}] };
const open = (r: ReturnType<typeof render>) => fireEvent.click(r.getByRole('button', {name:/Pick habits/}));
it('card shows a one-line summary and unfolds the habit pills inline', () => {
 const r = render(<HabitsCard info={two} onSave={vi.fn()} />);
 expect(r.getByText('2 of 2 on · 40m')).toBeTruthy();
 expect(r.queryByRole('button',{name:/Count Reading today/})).toBeNull(); // folded
 expect(r.queryByRole('dialog')).toBeNull();
 open(r);
 expect(r.queryByRole('dialog')).toBeNull(); // inline, not a drawer
 expect(r.getByRole('button',{name:'Count Reading today'})).toBeTruthy();
 expect(r.getByRole('button',{name:'Count Walk today'})).toBeTruthy();
});
it('card summary counts off habits', () => {
 const r = render(<HabitsCard info={two} overrides={{items:{b:{on:false}},total:null}} onSave={vi.fn()} />);
 expect(r.getByText('1 of 2 on · 30m')).toBeTruthy();
});
it('a pill saves a skip', () => {
 const save = vi.fn().mockResolvedValue(undefined);
 const r = render(<HabitsCard info={info} onSave={save} />); open(r);
 fireEvent.click(r.getByRole('button', {name:'Count Reading today'}));
 expect(save).toHaveBeenCalledWith({items:{a:{on:false}},total:null});
 expect(r.queryByText('Habits (total)')).toBeNull();
});
it('shows total fallback and hides empty source', () => {
 const r = render(<HabitsCard info={{...info,items:[]}} onSave={vi.fn()} />);
 expect(r.getByText('Habits (total)')).toBeTruthy();
 expect(r.getByText('1 habit · 30min counted')).toBeTruthy();
 expect(r.queryByRole('button',{name:/Pick habits/})).toBeNull();
 r.rerender(<HabitsCard info={{...info,items:[],estMinutes:0}} onSave={vi.fn()} />);
 expect(r.queryByRole('heading',{name:'Habits'})).toBeNull();
});
it('total fallback rejects empty minutes without saving', () => {
 const save = vi.fn(); const r = render(<HabitsCard info={{...info,items:[]}} onSave={save} />);
 const input = r.getByLabelText('Habits (total) minutes');
 fireEvent.input(input,{target:{value:''}}); fireEvent.blur(input);
 expect(save).not.toHaveBeenCalled(); expect(r.getByRole('alert')).toBeTruthy();
});
it('a legacy artifact keeps every habit and never shows completion', () => {
 // Old contract: `done` > 0 and `outstanding` < `total`. Grit owns completion
 // now, so the whole list stays switchable and no wording mentions it.
 const legacy = { total: 3, done: 2, outstanding: 1, estMinutes: 25,
   items: [{id:'a',name:'Reading',minutes:10},{id:'b',name:'Walk',minutes:10},{id:'c',name:'Water',minutes:5}] };
 const r = render(<HabitsCard info={legacy} onSave={vi.fn()} />);
 expect(r.getByText('3 of 3 on · 25m')).toBeTruthy();
 open(r);
 expect(r.getAllByRole('button',{name:/^Count .* today$/}).length).toBe(3);
 expect(r.queryByText(/outstanding/i)).toBeNull();
 expect(r.queryByText(/Done today/)).toBeNull();
 expect(r.getByRole('button',{name:'all off'})).toBeTruthy();
});

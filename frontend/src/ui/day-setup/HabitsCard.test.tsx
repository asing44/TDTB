import { render, fireEvent, cleanup } from '@testing-library/preact';
import { afterEach, expect, it, vi } from 'vitest';
import { HabitsCard } from './HabitsCard';
afterEach(cleanup);
const info = { total: 1, done: 0, outstanding: 1, estMinutes: 30, items: [{id:'a',name:'Reading',minutes:30}] };
const two = { total: 4, done: 2, outstanding: 2, estMinutes: 40, items: [{id:'a',name:'Reading',minutes:30},{id:'b',name:'Walk',minutes:10}] };
const open = (r: ReturnType<typeof render>) => fireEvent.click(r.getByRole('button', {name:'Review habits'}));
it('card shows a one-line summary and opens the drawer', () => {
 const r = render(<HabitsCard info={two} onSave={vi.fn()} />);
 expect(r.getByText('2 counting · 40 min counted · 2 blocks')).toBeTruthy();
 expect(r.queryByRole('switch')).toBeNull(); // no inline rows
 expect(r.queryByRole('dialog')).toBeNull();
 open(r);
 expect(r.getByRole('dialog', {name:'Habits'})).toBeTruthy();
 expect(r.getAllByRole('switch').length).toBe(2);
});
it('card summary counts off habits', () => {
 const r = render(<HabitsCard info={two} overrides={{items:{b:{on:false}},total:null}} onSave={vi.fn()} />);
 expect(r.getByText('1 counting · 30 min counted · 1 block · 1 off')).toBeTruthy();
});
it('drawer saves a skip', () => {
 const save = vi.fn().mockResolvedValue(undefined);
 const r = render(<HabitsCard info={info} onSave={save} />); open(r);
 fireEvent.click(r.getByRole('switch', {name:'Count Reading today'}));
 expect(save).toHaveBeenCalledWith({items:{a:{on:false}},total:null});
 expect(r.queryByText('Habits (total)')).toBeNull();
});
it('shows total fallback and hides empty source', () => {
 const r = render(<HabitsCard info={{...info,items:[]}} onSave={vi.fn()} />);
 expect(r.getByText('Habits (total)')).toBeTruthy();
 expect(r.queryByRole('button',{name:'Review habits'})).toBeNull();
 r.rerender(<HabitsCard info={{...info,items:[],outstanding:0,estMinutes:0}} onSave={vi.fn()} />);
 expect(r.queryByRole('heading',{name:'Habits'})).toBeNull();
});
it('total fallback rejects empty minutes without saving', () => {
 const save = vi.fn(); const r = render(<HabitsCard info={{...info,items:[]}} onSave={save} />);
 const input = r.getByLabelText('Habits (total) minutes');
 fireEvent.input(input,{target:{value:''}}); fireEvent.blur(input);
 expect(save).not.toHaveBeenCalled(); expect(r.getByRole('alert')).toBeTruthy();
});

import { render, fireEvent, cleanup } from '@testing-library/preact';
import { afterEach, expect, it, vi } from 'vitest';
import { HabitsCard } from './HabitsCard';
afterEach(cleanup);
const info = { total: 1, done: 0, outstanding: 1, estMinutes: 30, items: [{id:'a',name:'Reading',minutes:30}] };
it('renders items and saves a skip', () => {
 const save = vi.fn().mockResolvedValue(undefined);
 const r = render(<HabitsCard info={info} onSave={save} />);
 fireEvent.click(r.getByRole('switch', {name:'Count Reading today'}));
 expect(save).toHaveBeenCalledWith({items:{a:{on:false}},total:null});
 expect(r.queryByText('Habits (total)')).toBeNull();
});
it('rejects empty minutes without saving', () => {
 const save = vi.fn(); const r = render(<HabitsCard info={info} onSave={save} />);
 const input = r.getByLabelText('Reading minutes');
 fireEvent.input(input,{target:{value:''}}); fireEvent.blur(input);
 expect(save).not.toHaveBeenCalled(); expect(r.getByRole('alert')).toBeTruthy();
});
it('shows total fallback and hides empty source', () => {
 const r = render(<HabitsCard info={{...info,items:[]}} onSave={vi.fn()} />);
 expect(r.getByText('Habits (total)')).toBeTruthy();
 r.rerender(<HabitsCard info={{...info,items:[],outstanding:0,estMinutes:0}} onSave={vi.fn()} />);
 expect(r.queryByRole('heading',{name:'Habits'})).toBeNull();
});

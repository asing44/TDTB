import { render, cleanup } from '@testing-library/preact';
import { afterEach, expect, it } from 'vitest';
import { AllotmentCard, allotmentDuration } from './AllotmentCard';
afterEach(cleanup);
const cap = { total: 20, fixed: 4, anchored: 2, habits: 1, mint: 3, selected: 0, buffer: 0,
  free: 10, overassigned: false, availableForSelection: 10, remaining: '', ratio: '' } as never;
it('formats 30-minute blocks as durations', () => {
  expect(allotmentDuration(4.5)).toBe('2h 15m');
  expect(allotmentDuration(2)).toBe('1h');
  expect(allotmentDuration(0)).toBe('0m');
});
it('summarises free time and labels every segment', () => {
  const r = render(<AllotmentCard capacity={cap} />);
  expect(r.getByText('5h left for tasks in a 10h day')).toBeTruthy();
  expect(r.getByRole('img').getAttribute('aria-label')).toBe(
    'Calendar 2h · Work (Mint) 1h 30m · Anchors 1h · Habits 30m · Free for tasks 5h');
  expect(r.getByText('Free for tasks')).toBeTruthy();
});
it('renders nothing without capacity', () => {
  expect(render(<AllotmentCard capacity={null} />).container.innerHTML).toBe('');
});

it('counts shared clock time once and names it', () => {
  // Calendar 4 blk and Anchors 2 blk sit on 3 shared blk: the day pays for
  // 4 + 2 − 3 = 3 fixed blk, not 6.
  const shared = { ...(cap as object), overlap: 3, free: 10 } as never;
  const r = render(<AllotmentCard capacity={shared} />);
  expect(r.getByText('6h 30m left for tasks in a 10h day')).toBeTruthy();
  expect(r.getByText('1h 30m shared — counted once')).toBeTruthy();
  expect(r.getByRole('img').getAttribute('aria-label')).toContain('Shared 1h 30m');
});

it('shows no shared line when nothing overlaps', () => {
  const r = render(<AllotmentCard capacity={cap} />);
  expect(r.queryByText(/counted once/)).toBeNull();
});

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

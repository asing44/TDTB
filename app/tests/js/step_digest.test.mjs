// step_digest.test.mjs — node --test suite for the pure helpers in
// static/steps/digest.js (spa-overhaul T3). DOM wiring (render, the table
// factory, the refresh button) is preview-verified, not here — same split
// as wizard_logic.test.mjs / ui_kit.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const digest = require('../../legacy-static/steps/digest.js');

// -- rowOf / rowsOf -----------------------------------------------------------
test('rowOf: shapes name/deadline/urgency/path, defaults missing fields', () => {
  assert.deepEqual(
    digest._pure.rowOf({ name: 'Ship it', deadline: '2026-07-20', urgency: 3, path: 'a/b.md' }),
    { name: 'Ship it', deadline: '2026-07-20', urgency: 3, path: 'a/b.md' }
  );
  assert.deepEqual(digest._pure.rowOf({}), { name: '', deadline: '', urgency: '', path: '' });
  assert.deepEqual(digest._pure.rowOf(null), { name: '', deadline: '', urgency: '', path: '' });
});

test('rowOf: urgency 0 is preserved, not treated as missing', () => {
  assert.equal(digest._pure.rowOf({ urgency: 0 }).urgency, 0);
});

test('rowsOf: maps a list, empty/absent input yields empty list', () => {
  const rows = digest._pure.rowsOf([{ name: 'A' }, { name: 'B' }]);
  assert.deepEqual(rows.map(r => r.name), ['A', 'B']);
  assert.deepEqual(digest._pure.rowsOf([]), []);
  assert.deepEqual(digest._pure.rowsOf(undefined), []);
});

// -- countsOf ------------------------------------------------------------------
test('countsOf: assigned_count wins when present, else falls back to list length', () => {
  assert.deepEqual(digest._pure.countsOf({ assigned_count: 5, assigned: [1, 2], suggested: [1] }),
    { assigned: 5, suggested: 1 });
  assert.deepEqual(digest._pure.countsOf({ assigned: [1, 2, 3], suggested: [] }),
    { assigned: 3, suggested: 0 });
  assert.deepEqual(digest._pure.countsOf({}), { assigned: 0, suggested: 0 });
});

// -- statusText ------------------------------------------------------------------
test('statusText: renders valid_date and vault/todoist/calendar counts verbatim', () => {
  const text = digest._pure.statusText({
    digest: { valid_date: '2026-07-16' },
    source_counts: { vault: 4, todoist: 2, calendar: 1 }
  });
  assert.equal(text, 'Loaded — valid for 2026-07-16 · sources vault/todoist/calendar: 4/2/1');
});

test('statusText: missing date/counts fall back to unknown date / "?"', () => {
  assert.equal(digest._pure.statusText({}),
    'Loaded — valid for unknown date · sources vault/todoist/calendar: ?/?/?');
});

// -- warningsText -----------------------------------------------------------------
test('warningsText: absent/empty warnings ⇒ hidden', () => {
  assert.deepEqual(digest._pure.warningsText({}), { visible: false, text: '' });
  assert.deepEqual(digest._pure.warningsText({ source_warnings: [] }), { visible: false, text: '' });
});

test('warningsText: joins warnings with " — ", prefixed with a warning glyph', () => {
  assert.deepEqual(
    digest._pure.warningsText({ source_warnings: ['todoist unreachable', 'calendar stale'] }),
    { visible: true, text: '⚠ todoist unreachable — calendar stale' }
  );
});

// -- overassignedText -------------------------------------------------------------
test('overassignedText: hidden when capacity absent or not overassigned', () => {
  assert.deepEqual(digest._pure.overassignedText({}), { visible: false, text: '' });
  assert.deepEqual(digest._pure.overassignedText({ capacity: { overassigned: false } }),
    { visible: false, text: '' });
});

test('overassignedText: renders remaining + legend verbatim when overassigned', () => {
  assert.deepEqual(
    digest._pure.overassignedText({ capacity: { overassigned: true, remaining: -2, legend: '2 over' } }),
    { visible: true, text: 'OVERASSIGNED — -2 (2 over)' }
  );
});

// -- habitsText -------------------------------------------------------------------
test('habitsText: empty when habits absent or total is falsy', () => {
  assert.equal(digest._pure.habitsText({}), '');
  assert.equal(digest._pure.habitsText({ habits: { total: 0 } }), '');
});

test('habitsText: prefers server-computed capacity.habits block count', () => {
  const text = digest._pure.habitsText({
    habits: { total: 3, done: 1, outstanding: 2, est_minutes: 45 },
    capacity: { habits: 2 }
  });
  assert.equal(text, 'Habits: 1 done · 2 left (~45 min · 2 blk deducted from capacity)');
});

test('habitsText: falls back to client ceil(est_minutes/30) when capacity absent (ported verbatim)', () => {
  const text = digest._pure.habitsText({
    habits: { total: 2, done: 0, outstanding: 2, est_minutes: 40 }
  });
  assert.equal(text, 'Habits: 0 done · 2 left (~40 min · 2 blk deducted from capacity)');
});

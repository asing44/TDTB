// node --test tests for static/timeline_logic.js (G23: the manual-seed
// defects — calendar walls defaulting to 30 min, over-EOD items silently
// dropped — shipped because this logic had zero coverage). Run via
// `node --test tests/js/` or the pytest wrapper tests/test_js_logic.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const logic = require('../../static/timeline_logic.js');

// -- time parsing -----------------------------------------------------------

test('toMin24 parses 24h and AM/PM forms', () => {
  assert.equal(logic.toMin24('17:45'), 1065);
  assert.equal(logic.toMin24('5:45 PM'), 1065);
  assert.equal(logic.toMin24('12:15 AM'), 15);
  assert.equal(logic.toMin24('12:30 PM'), 750);
  assert.equal(logic.toMin24('garbage'), null);
});

test('minToHH clamps to [00:00, 23:59]', () => {
  assert.equal(logic.minToHH(0), '00:00');
  assert.equal(logic.minToHH(1065), '17:45');
  assert.equal(logic.minToHH(24 * 60 + 30), '23:59');
  assert.equal(logic.minToHH(-10), '00:00');
});

// -- durations ---------------------------------------------------------------

test('itemMinutes: blocks win, bare duration is minutes, default 1 block', () => {
  assert.equal(logic.itemMinutes({ blocks: 2 }), 60);
  assert.equal(logic.itemMinutes({ blocks: 0.5 }), 30);      // 30-min floor
  assert.equal(logic.itemMinutes({ duration: 45 }), 60);     // ceil to block
  assert.equal(logic.itemMinutes({ duration: 30 }), 30);
  assert.equal(logic.itemMinutes({}), 30);
});

test('anchoredMinutes: Duration wins, else End−Start, else 30', () => {
  assert.equal(logic.anchoredMinutes({ Duration: '30m' }), 30);
  assert.equal(logic.anchoredMinutes({ Duration: '45m' }), 60); // ceil to block
  assert.equal(logic.anchoredMinutes({ Duration: 60 }), 60);
  assert.equal(logic.anchoredMinutes({ Block: 'Gym', Start: '13:00', End: '15:00' }), 120);
  assert.equal(logic.anchoredMinutes({ Block: 'X' }), 30);
});

// 866c5dc regression: calendar busy blocks carry Start/End and never
// Duration — they must wall off their REAL span, not default to 30 min.
test('calendar wall spans End−Start, not 30-min default (866c5dc)', () => {
  const rows = logic.seedRows(
    { anchor: '09:00', effective_eod: '22:00' },
    [{ Block: 'Meeting', Start: '10:00', End: '12:00', source: 'calendar' }],
    [{ id: 'A', blocks: 4 }]   // 120 min — collides with the wall
  );
  const a = rows.find(r => r.id === 'A');
  // first-fit must jump past the wall's true end (12:00), not 10:30
  assert.equal(a.start, '12:00');
  assert.equal(a.end, '14:00');
});

// -- seedRows placement rules -------------------------------------------------

test('suppressed anchored blocks (off / skip_today) are excluded', () => {
  const rows = logic.seedRows(
    { anchor: '09:00' },
    [
      { Block: 'Skipped', Start: '10:00', Duration: '30m', skip_today: true },
      { Block: 'Off', Start: '11:00', Duration: '30m', on: false },
      { Block: 'Live', Start: '13:00', Duration: '30m' }
    ],
    []
  );
  assert.deepEqual(rows.map(r => r.id), ['Live']);
});

test('pre-anchor block walls the grid but emits no row', () => {
  const rows = logic.seedRows(
    { anchor: '09:00' },
    [{ Block: 'Early', Start: '08:30', End: '09:30' }],   // spans the anchor
    [{ id: 'A' }]                                         // 30 min
  );
  assert.ok(!rows.some(r => r.id === 'Early'));           // no placement-in-past row
  const a = rows.find(r => r.id === 'A');
  assert.equal(a.start, '09:30');                         // wall still applies
});

// 866c5dc regression: items that can't fit before midnight park in the
// overflow tail [EOD, 23:59] — never silently dropped (never-bump).
test('over-midnight items park at the overflow tail, never dropped (866c5dc)', () => {
  const rows = logic.seedRows(
    { anchor: '22:00', effective_eod: '23:00' },
    [],
    [{ id: 'A', blocks: 2 }, { id: 'B', blocks: 4 }, { id: 'C', blocks: 4 }]
  );
  assert.deepEqual(rows.map(r => r.id).sort(), ['A', 'B', 'C']);  // all present
  const b = rows.find(r => r.id === 'B');
  const c = rows.find(r => r.id === 'C');
  assert.equal(b.start, '23:00');   // parked at EOD
  assert.equal(b.end, '23:59');
  assert.deepEqual([c.start, c.end], [b.start, b.end]);  // same tail
});

test('first-fit stacks items sequentially from the anchor', () => {
  const rows = logic.seedRows(
    { anchor: '09:00', effective_eod: '22:00' },
    [],
    [{ id: 'A', blocks: 1 }, { id: 'B', blocks: 2 }]
  );
  assert.deepEqual(rows.map(r => [r.id, r.start, r.end]), [
    ['A', '09:00', '09:30'],
    ['B', '09:30', '10:30']
  ]);
});

test('_excluded assigned items are skipped', () => {
  const rows = logic.seedRows(
    { anchor: '09:00' },
    [],
    [{ id: 'A', _excluded: true }, { id: 'B' }]
  );
  assert.deepEqual(rows.map(r => r.id), ['B']);
});

// -- ui-revamp T5: chip-data mapping + totals aggregation --------------------

test('assignedIndexById: keys by id, ignores entries with no id', () => {
  const idx = logic.assignedIndexById([
    { id: 'A', source: 'todoist' }, { name: 'no id here' }, { id: 'B' }
  ]);
  assert.equal(idx.A.source, 'todoist');
  assert.equal(idx.B.id, 'B');
  assert.equal(Object.keys(idx).length, 2);
});

test('assignedIndexById: empty/missing input ⇒ empty index', () => {
  assert.deepEqual(logic.assignedIndexById(), {});
  assert.deepEqual(logic.assignedIndexById([]), {});
});

test('sourceForId: matched row returns its assigned source, absent ⇒ undefined (vault)', () => {
  const idx = logic.assignedIndexById([{ id: 'A', source: 'calendar' }, { id: 'B' }]);
  assert.equal(logic.sourceForId('A', idx), 'calendar');
  assert.equal(logic.sourceForId('B', idx), undefined);   // no source field on the item
  assert.equal(logic.sourceForId('nope', idx), undefined); // no match at all
});

test('placedDurations: matched non-backdrop rows only, unmatched rows excluded', () => {
  const idx = logic.assignedIndexById([
    { id: 'A', duration: 60 }, { id: 'B', duration: null }
  ]);
  const rows = [
    { id: 'A', start: '09:00', end: '10:00' },
    { id: 'B', start: '10:00', end: '10:30' },
    { id: 'anchor-block', start: '07:00', end: '07:30' },  // no assigned match — excluded
    { id: 'A', start: '11:00', end: '11:30', backdrop: true } // backdrop — excluded
  ];
  assert.deepEqual(logic.placedDurations(rows, idx), [60, null]);
});

test('placedDurations: empty rows/index ⇒ empty array', () => {
  assert.deepEqual(logic.placedDurations([], {}), []);
  assert.deepEqual(logic.placedDurations(), []);
});

test('buildDaySetupEcho: echoes persisted day_setup verbatim into buildCapacityQuery shape', () => {
  const echo = logic.buildDaySetupEcho(
    { anchor: '08:00', effective_eod: '21:00' },
    {
      anchor: '09:00', eod: '22:00', buffering: 'standard',
      schedulable: { minting: { on: true, n: 2 } },
      anchored: [{ id: 'gym', on: true, time: '07:00', blocks: 2 }, { id: 'walk', on: false, time: '12:00' }]
    }
  );
  assert.equal(echo.anchor, '09:00');   // persisted wins over time-frame fallback
  assert.equal(echo.eod, '22:00');
  assert.equal(echo.buffering, 'standard');
  assert.deepEqual(echo.schedRows, { minting: { on: true, n: 2 } });
  assert.deepEqual(echo.anchoredRows, [
    { id: 'gym', on: true, time: '07:00', blocks: 2, blocksChanged: true },
    { id: 'walk', on: false, time: '12:00', blocks: undefined, blocksChanged: false }
  ]);
});

test('buildDaySetupEcho: no persisted day_setup ⇒ falls back to time frame, empty overrides', () => {
  const echo = logic.buildDaySetupEcho({ anchor: '08:00', effective_eod: '21:00' }, {});
  assert.equal(echo.anchor, '08:00');
  assert.equal(echo.eod, '21:00');
  assert.equal(echo.buffering, 'minimal');
  assert.deepEqual(echo.schedRows, {});
  assert.deepEqual(echo.anchoredRows, []);
});

test('buildDaySetupEcho: missing time and day_setup ⇒ null anchor/eod', () => {
  const echo = logic.buildDaySetupEcho(null, null);
  assert.equal(echo.anchor, null);
  assert.equal(echo.eod, null);
  assert.equal(echo.buffering, 'minimal');
});

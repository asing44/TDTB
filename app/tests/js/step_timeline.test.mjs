// step_timeline.test.mjs — node --test suite for steps/timeline.js's NEW
// pure state-shaping helpers (spa-overhaul T5 port). timeline_logic.js's own
// math (toMin/seedRows/etc.) is already covered by timeline_logic.test.mjs
// and is NOT re-tested here — this file only covers logic that is new to
// the step-module port. Same require/require pattern as wizard_logic.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const stepMod = require('../../legacy-static/steps/timeline.js');
const pure = stepMod.tdtbSteps.timeline._pure;

// -- module registration ------------------------------------------------------

test('registers on tdtbSteps.timeline with render + _pure', () => {
  assert.equal(typeof stepMod.tdtbSteps.timeline.render, 'function');
  assert.equal(typeof pure, 'object');
});

// -- parseAnyTime -------------------------------------------------------------

test('parseAnyTime: 24h and 12h AM/PM forms, invalid -> null', () => {
  assert.equal(pure.parseAnyTime('09:00'), 540);
  assert.equal(pure.parseAnyTime('7:45 AM'), 465);
  assert.equal(pure.parseAnyTime('12:15 AM'), 15);
  assert.equal(pure.parseAnyTime('12:30 PM'), 750);
  assert.equal(pure.parseAnyTime('5:45 PM'), 1065);
  assert.equal(pure.parseAnyTime('garbage'), null);
  assert.equal(pure.parseAnyTime(null), null);
});

// -- snap -----------------------------------------------------------------

test('snap: rounds to the given granularity, defaults to 5', () => {
  assert.equal(pure.snap(542), 540);
  assert.equal(pure.snap(543), 545);
  assert.equal(pure.snap(547, 10), 550);
  assert.equal(pure.snap(544, 10), 540);
});

// -- fmt12 / fmt12Text ---------------------------------------------------------

test('fmt12: converts 24h HH:MM to 12h, passes through non-HH:MM', () => {
  assert.equal(pure.fmt12('00:00'), '12:00 AM');
  assert.equal(pure.fmt12('12:00'), '12:00 PM');
  assert.equal(pure.fmt12('13:30'), '1:30 PM');
  assert.equal(pure.fmt12('23:59'), '11:59 PM');
  assert.equal(pure.fmt12('—'), '—');
});

test('fmt12Text: rewrites every valid HH:MM token, leaves invalid times raw', () => {
  assert.equal(pure.fmt12Text('starts 09:00, ends 17:30'), 'starts 9:00 AM, ends 5:30 PM');
  assert.equal(pure.fmt12Text("row 'A' overlaps at 25:99"), "row 'A' overlaps at 25:99");
});

// -- isWorkout -----------------------------------------------------------

test('isWorkout: matches by id keyword or zone keyword', () => {
  assert.equal(pure.isWorkout({ id: 'Morning Workout' }), true);
  assert.equal(pure.isWorkout({ id: 'Gym', zone: 'exercise' }), true);
  assert.equal(pure.isWorkout({ id: 'Standup', zone: 'work' }), false);
  assert.equal(pure.isWorkout({ id: 'fitness check-in' }), true);
});

// -- placementRow -----------------------------------------------------------

test('placementRow: strips to {id,start,end,zone}, drops extras like _dur/backdrop', () => {
  assert.deepEqual(
    pure.placementRow({ id: 'A', start: '09:00', end: '09:30', zone: 'focus', _dur: 30, backdrop: true }),
    { id: 'A', start: '09:00', end: '09:30', zone: 'focus' }
  );
});

// -- normalizeAssigned -------------------------------------------------------

test('normalizeAssigned: id defaults to name when absent, existing id wins', () => {
  const out = pure.normalizeAssigned([{ name: 'Task A' }, { id: 'B', name: 'Task B' }]);
  assert.equal(out[0].id, 'Task A');
  assert.equal(out[1].id, 'B');
});

test('normalizeAssigned: empty/missing input -> empty array', () => {
  assert.deepEqual(pure.normalizeAssigned(), []);
  assert.deepEqual(pure.normalizeAssigned([]), []);
});

// -- computeBounds -----------------------------------------------------------

test('computeBounds: time frame wins when present and valid', () => {
  const b = pure.computeBounds({ anchor: '08:15', effective_eod: '21:45' }, [], [], 5 * 60, 23 * 60);
  assert.deepEqual(b, { start: 8 * 60, end: 22 * 60 });   // floor to hour / ceil to hour
});

test('computeBounds: falls back when time frame missing, widens for rows', () => {
  const b = pure.computeBounds(null, [{ start: '04:50', end: '05:10' }], [], 5 * 60, 23 * 60);
  assert.equal(b.start, 4 * 60);   // widened earlier by the row
  assert.equal(b.end, 23 * 60);
});

test('computeBounds: widens for anchored blocks (12h Start/End)', () => {
  const b = pure.computeBounds(
    { anchor: '09:00', effective_eod: '22:00' },
    [],
    [{ Start: '10:30 PM', End: '11:15 PM' }],
    5 * 60, 23 * 60
  );
  assert.equal(b.end, 24 * 60 - 1);   // 23:15 ceils to 24:00, clamped to 23:59
});

// -- classForRow -----------------------------------------------------------

test('classForRow: hard beats warn, quoted-id match in free-text hard_errors', () => {
  const v = { hard_errors: ["row 'A' overlaps anchored block"], warnings: [{ id: 'A', kind: 'zone' }] };
  assert.equal(pure.classForRow('A', v), 'hard');
});

test('classForRow: warn when only a matching warning id, else empty', () => {
  const v = { hard_errors: [], warnings: [{ id: 'B', kind: 'zone' }] };
  assert.equal(pure.classForRow('B', v), 'warn');
  assert.equal(pure.classForRow('C', v), '');
});

// -- dragNewStart -----------------------------------------------------------

test('dragNewStart: snaps the delta and clamps within [dayStart, dayEnd - dur]', () => {
  // origStart=540 (09:00), dur=30, pxPerMin=1.2, snap=5
  assert.equal(pure.dragNewStart(540, 30, 12, 1.2, 5, 300, 1380), 550);   // +10 min -> snapped
  assert.equal(pure.dragNewStart(540, 30, -100000, 1.2, 5, 300, 1380), 300);   // clamp to dayStart
  assert.equal(pure.dragNewStart(540, 30, 100000, 1.2, 5, 300, 1380), 1380 - 30);   // clamp to dayEnd-dur
});

// -- sortRowsByStart -----------------------------------------------------------

test('sortRowsByStart: ascending by start, does not mutate the input array', () => {
  const rows = [{ id: 'B', start: '10:00' }, { id: 'A', start: '09:00' }];
  const sorted = pure.sortRowsByStart(rows);
  assert.deepEqual(sorted.map(r => r.id), ['A', 'B']);
  assert.deepEqual(rows.map(r => r.id), ['B', 'A']);   // original untouched
});

test('sortRowsByStart: empty/missing -> empty array', () => {
  assert.deepEqual(pure.sortRowsByStart(), []);
  assert.deepEqual(pure.sortRowsByStart([]), []);
});

// -- filterRemoved -----------------------------------------------------------

test('filterRemoved: drops removed ids from assigned and digest.assigned (by id or name)', () => {
  const res = pure.filterRemoved(
    [{ id: 'A' }, { id: 'B' }],
    [{ id: 'A' }, { name: 'B' }, { id: 'C' }],
    ['A', 'B']
  );
  assert.deepEqual(res.assigned, []);
  assert.deepEqual(res.digestAssigned, [{ id: 'C' }]);
});

test('filterRemoved: non-array digestAssigned passes through unchanged', () => {
  const res = pure.filterRemoved([{ id: 'A' }], null, ['A']);
  assert.deepEqual(res.assigned, []);
  assert.equal(res.digestAssigned, null);
});

// -- buildValidatePayload -----------------------------------------------------------

test('buildValidatePayload: excludes backdrop rows, maps to placement shape', () => {
  const payload = pure.buildValidatePayload(
    [{ id: 'A', start: '09:00', end: '09:30', zone: 'focus' }, { id: 'X', start: '07:00', end: '07:30', backdrop: true }],
    [{ id: 'A' }],
    [{ Block: 'gym' }],
    { anchor: '09:00' }
  );
  assert.deepEqual(payload.sequence, [{ id: 'A', start: '09:00', end: '09:30', zone: 'focus' }]);
  assert.deepEqual(payload.assigned, [{ id: 'A' }]);
  assert.deepEqual(payload.anchored_blocks, [{ Block: 'gym' }]);
  assert.deepEqual(payload.config, { anchor: '09:00' });
});

test('buildValidatePayload: missing inputs default to empty', () => {
  const payload = pure.buildValidatePayload();
  assert.deepEqual(payload, { sequence: [], assigned: [], anchored_blocks: [], config: {} });
});

// -- buildCommitStagePayload -----------------------------------------------------------

test('buildCommitStagePayload: full commit stash, non-backdrop rows dict-wrapped', () => {
  const digest = { valid_date: '2026-07-16', assigned: [] };
  const config = { Defaults: {} };
  const anchored = [{ Block: 'Wall', Start: '09:00', End: '10:00' }];
  const payload = pure.buildCommitStagePayload([
    { id: 'A', start: '09:00', end: '09:30', zone: 'focus' },
    { id: 'X', start: '07:00', end: '07:30', backdrop: true }
  ], digest, config, anchored);
  assert.deepEqual(payload, {
    digest,
    sequence: { sequence: [{ id: 'A', start: '09:00', end: '09:30', zone: 'focus' }] },
    config,
    anchored_blocks: anchored
  });
});

test('buildCommitStagePayload: empty/backdrop rows and missing context default safely', () => {
  assert.deepEqual(pure.buildCommitStagePayload([{ id: 'X', backdrop: true }]),
    { digest: null, sequence: { sequence: [] }, config: {}, anchored_blocks: [] });
  assert.deepEqual(pure.buildCommitStagePayload(),
    { digest: null, sequence: { sequence: [] }, config: {}, anchored_blocks: [] });
});

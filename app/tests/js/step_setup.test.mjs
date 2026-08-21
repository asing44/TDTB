// step_setup.test.mjs — node --test suite for the Day Setup step's pure
// logic (spa-overhaul T2). DOM wiring (render()) is preview-verified, not
// unit-tested, per the wizard_logic.test.mjs pattern this file copies.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const setup = require('../../legacy-static/steps/setup.js');
const pure = setup._pure;

// -- toMinutes ----------------------------------------------------------------

test('toMinutes parses HH:MM to minutes since midnight', () => {
  assert.equal(pure.toMinutes('09:30'), 570);
  assert.equal(pure.toMinutes('00:00'), 0);
  assert.equal(pure.toMinutes('23:59'), 1439);
});

test('toMinutes rejects empty/garbage input', () => {
  assert.equal(pure.toMinutes(null), null);
  assert.equal(pure.toMinutes(undefined), null);
  assert.equal(pure.toMinutes(''), null);
  assert.equal(pure.toMinutes('bogus'), null);
});

// -- to24h ----------------------------------------------------------------------

test('to24h normalizes 12h strings to 24h HH:MM', () => {
  assert.equal(pure.to24h('5:45 PM'), '17:45');
  assert.equal(pure.to24h('5:45pm'), '17:45');
  assert.equal(pure.to24h('5:45 AM'), '05:45');
  assert.equal(pure.to24h('12:00 am'), '00:00');
  assert.equal(pure.to24h('12:00 pm'), '12:00');
});

test('to24h passes through bare 24h strings and rejects junk', () => {
  assert.equal(pure.to24h('17:45'), '17:45');
  assert.equal(pure.to24h(''), null);
  assert.equal(pure.to24h(null), null);
  assert.equal(pure.to24h('not a time'), null);
});

// -- buildAnchoredRows: past-anchor default-skip -------------------------------

function pi(specs, overrides) {
  return {
    config: { 'Anchored Lifestyle Blocks': specs },
    day_setup: { anchored: overrides || [] }
  };
}

test('buildAnchoredRows defaults on with no anchor set', () => {
  const rows = pure.buildAnchoredRows(pi([{ Block: 'Gym', Start: '06:00', End: '07:00' }]), '');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].on, true);
  assert.equal(rows[0].defaultSkipped, false);
});

test('buildAnchoredRows default-skips a window block whose End is before the anchor', () => {
  const rows = pure.buildAnchoredRows(pi([{ Block: 'Gym', Start: '06:00', End: '07:00' }]), '08:00');
  assert.equal(rows[0].on, false);
  assert.equal(rows[0].defaultSkipped, true);
  assert.equal(rows[0].skip_today, true);
});

test('buildAnchoredRows default-skips a point-in-time block whose Start is before the anchor', () => {
  const rows = pure.buildAnchoredRows(pi([{ Block: 'Meds', Start: '06:00' }]), '08:00');
  assert.equal(rows[0].on, false);
  assert.equal(rows[0].defaultSkipped, true);
});

test('buildAnchoredRows keeps a block on when its window is still ahead of the anchor', () => {
  const rows = pure.buildAnchoredRows(pi([{ Block: 'Gym', Start: '09:00', End: '10:00' }]), '08:00');
  assert.equal(rows[0].on, true);
  assert.equal(rows[0].defaultSkipped, false);
});

test('buildAnchoredRows: an explicit override.on wins over the past-anchor default-skip', () => {
  const rows = pure.buildAnchoredRows(
    pi([{ Block: 'Gym', Start: '06:00', End: '07:00' }], [{ id: 'Gym', on: true }]),
    '08:00'
  );
  assert.equal(rows[0].on, true);
  assert.equal(rows[0].defaultSkipped, false);
});

test('buildAnchoredRows: override.skip_today forces off regardless of anchor', () => {
  const rows = pure.buildAnchoredRows(
    pi([{ Block: 'Gym', Start: '09:00', End: '10:00' }], [{ id: 'Gym', skip_today: true }]),
    '08:00'
  );
  assert.equal(rows[0].on, false);
});

test('buildAnchoredRows: a persisted blocks override marks blocksChanged true', () => {
  const rows = pure.buildAnchoredRows(
    pi([{ Block: 'Gym', Start: '06:00', End: '07:00', Duration: '1h' }], [{ id: 'Gym', blocks: 3 }]),
    ''
  );
  assert.equal(rows[0].blocks, 3);
  assert.equal(rows[0].blocksChanged, true);
});

test('buildAnchoredRows: no Duration on the spec falls back to 1 block, untouched', () => {
  const rows = pure.buildAnchoredRows(pi([{ Block: 'Meds', Start: '06:00' }]), '');
  assert.equal(rows[0].blocks, 1);
  assert.equal(rows[0].blocksChanged, false);
});

test('buildAnchoredRows: Duration on the spec parses to blocks (30-min units, ceil)', () => {
  const rows = pure.buildAnchoredRows(pi([{ Block: 'Gym', Start: '06:00', End: '07:00', Duration: '45m' }]), '');
  assert.equal(rows[0].blocks, 2);
});

// -- buildSchedRows -------------------------------------------------------------

const MONDAY = new Date('2026-07-20T08:00:00'); // a weekday
const SATURDAY = new Date('2026-07-18T08:00:00'); // a weekend

test('buildSchedRows: minting defaults on for a weekday with time left', () => {
  const rows = pure.buildSchedRows({}, '08:00', '17:00', MONDAY);
  assert.equal(rows.minting.on, true);
  assert.equal(rows.minting.n, 2);
  assert.equal(rows.qt.on, true);
  assert.equal(rows.shivery.on, false);
});

test('buildSchedRows: minting defaults off on a weekend', () => {
  const rows = pure.buildSchedRows({}, '08:00', '17:00', SATURDAY);
  assert.equal(rows.minting.on, false);
});

test('buildSchedRows: minting defaults off when eod has already passed the anchor', () => {
  const rows = pure.buildSchedRows({}, '17:00', '08:00', MONDAY);
  assert.equal(rows.minting.on, false);
});

test('buildSchedRows: a persisted override wins over the computed default', () => {
  const rows = pure.buildSchedRows(
    { day_setup: { schedulable: { minting: { on: false, n: 5 }, shivery: { on: true } } } },
    '08:00', '17:00', MONDAY
  );
  assert.equal(rows.minting.on, false);
  assert.equal(rows.minting.n, 5);
  assert.equal(rows.shivery.on, true);
});

// -- buildConfirmPayload --------------------------------------------------------

test('buildConfirmPayload shapes the /day-setup body from form state', () => {
  const body = pure.buildConfirmPayload({
    anchor: '08:00', eod: '17:00', buffering: 'standard',
    schedRows: { minting: { on: true, n: 2 }, qt: { on: false, n: 0 }, shivery: { on: true, n: 1 } },
    anchoredRows: [{ id: 'Gym', on: true, time: '09:00', blocks: 2 }],
    captures: { intention: 'ship it', megan_nicety: '', stoic_intention: 'patience' }
  });
  assert.deepEqual(body, {
    anchor: '08:00', eod: '17:00', buffering: 'standard',
    schedulable: {
      minting: { on: true, n: 2 },
      qt: { on: false, n: 0 },
      shivery: { on: true, n: 1 }
    },
    anchored: [{ id: 'Gym', on: true, skip_today: false, time: '09:00', blocks: 2 }],
    captures: { intention: 'ship it', megan_nicety: '', stoic_intention: 'patience' }
  });
});

test('buildConfirmPayload: skip_today derives as the inverse of on', () => {
  const body = pure.buildConfirmPayload({
    anchor: '', eod: '', buffering: 'off',
    schedRows: {},
    anchoredRows: [{ id: 'Gym', on: false, time: '', blocks: 1 }],
    captures: {}
  });
  assert.equal(body.anchored[0].skip_today, true);
  assert.equal(body.anchor, null);
  assert.equal(body.eod, null);
});

test('buildConfirmPayload: missing captures default to empty strings', () => {
  const body = pure.buildConfirmPayload({ buffering: 'minimal', schedRows: {}, anchoredRows: [] });
  assert.deepEqual(body.captures, { intention: '', megan_nicety: '', stoic_intention: '' });
});

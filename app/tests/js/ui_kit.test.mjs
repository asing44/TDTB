// ui_kit.test.mjs — node --test suite for the pure helpers in
// static/ui_kit.js (ui-revamp T1). DOM factories (chipEl, renderError) and
// kitFetch's network path are exercised via preview-verify, not here —
// except kitFetch's error-message contract, testable with a stubbed fetch.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const kit = require('../../static/ui_kit.js');

// -- sourceOf ---------------------------------------------------------------
test('sourceOf: absent source ⇒ vault', () => {
  assert.equal(kit.sourceOf({ name: 'x' }), 'vault');
  assert.equal(kit.sourceOf(null), 'vault');
});

test('sourceOf: known sources pass through, unknown falls back to vault', () => {
  assert.equal(kit.sourceOf({ source: 'todoist' }), 'todoist');
  assert.equal(kit.sourceOf({ source: 'calendar' }), 'calendar');
  assert.equal(kit.sourceOf({ source: 'schedulable' }), 'schedulable');
  assert.equal(kit.sourceOf({ source: 'mystery' }), 'vault');
});

// -- parseDurationMinutes -----------------------------------------------------
test('parseDurationMinutes: numeric minutes, h/m strings, bare numbers', () => {
  assert.equal(kit.parseDurationMinutes(45), 45);
  assert.equal(kit.parseDurationMinutes('30m'), 30);
  assert.equal(kit.parseDurationMinutes('1h'), 60);
  assert.equal(kit.parseDurationMinutes('1h30m'), 90); // G27: h-format must not read as 1 min
  assert.equal(kit.parseDurationMinutes('90'), 90);
});

test('parseDurationMinutes: null/zero/garbage ⇒ null', () => {
  assert.equal(kit.parseDurationMinutes(null), null);
  assert.equal(kit.parseDurationMinutes(0), null);
  assert.equal(kit.parseDurationMinutes('nonsense'), null);
});

// -- blocksOf -----------------------------------------------------------------
test('blocksOf: blocks field wins, duration ceils to blocks, no min-1 clamp', () => {
  assert.equal(kit.blocksOf({ blocks: 2.5 }), 2.5);
  assert.equal(kit.blocksOf({ duration: 45 }), 2);       // ceil(45/30)
  assert.equal(kit.blocksOf({ duration: '1h30m' }), 3);
  assert.equal(kit.blocksOf({ Duration: '30m' }), 1);     // anchored-spec casing
  assert.equal(kit.blocksOf({ name: 'no cost' }), null);  // unknown ⇒ null, not 0 or 1
});

test('blocksOf: Todoist duration-label fallback when native duration null', () => {
  assert.equal(kit.blocksOf({ duration: null, labels: ['🍅30min'] }), 1);
  assert.equal(kit.blocksOf({ duration: null, labels: ['ctx', '🚀10min'] }), 1);
  assert.equal(kit.blocksOf({ duration: 60, labels: ['🍅30min'] }), 2); // native wins
  assert.equal(kit.blocksOf({ duration: null, labels: ['ctx'] }), null);
});

// -- fmtDuration --------------------------------------------------------------
test('fmtDuration: h-format durations, incl. fractional blocks', () => {
  assert.equal(kit.fmtDuration(1), '30m');
  assert.equal(kit.fmtDuration(2), '1h');
  assert.equal(kit.fmtDuration(3), '1h30m');
  assert.equal(kit.fmtDuration(0), '0m');
  assert.equal(kit.fmtDuration(0.5), '15m');   // fractional block ⇒ sub-30min minutes
  assert.equal(kit.fmtDuration(2.5), '1h15m'); // fractional block past the hour mark
  assert.equal(kit.fmtDuration(4), '2h');      // exact-hour boundary, no trailing "0m"
});

// -- fmtCost / fmtDuration ------------------------------------------------------
test('fmtCost: "2 blk · 1h" shape', () => {
  assert.equal(kit.fmtCost(2), '2 blk · 1h');
  assert.equal(kit.fmtCost(1), '1 blk · 30m');
  assert.equal(kit.fmtCost(3), '3 blk · 1h30m');
  assert.equal(kit.fmtCost(0), '0 blk · 0m');
});

test('fmtCost: fractional blocks (adjust.html steppers move in 0.5 increments)', () => {
  assert.equal(kit.fmtCost(2.5), '2.5 blk · 1h15m');
  assert.equal(kit.fmtCost(0.5), '0.5 blk · 15m');
});

test('fmtCost: null/NaN ⇒ empty string (unknown cost renders as nothing)', () => {
  assert.equal(kit.fmtCost(null), '');
  assert.equal(kit.fmtCost(undefined), '');
  assert.equal(kit.fmtCost(NaN), '');
});

test('costOf: item convenience wrapper', () => {
  assert.equal(kit.costOf({ duration: 60 }), '2 blk · 1h');
  assert.equal(kit.costOf({ name: 'no cost' }), '');
});

// -- kitFetch error contract -----------------------------------------------------
test('kitFetch: non-OK response rejects with named endpoint + status + detail', async () => {
  globalThis.fetch = () => Promise.resolve({
    ok: false, status: 502,
    json: () => Promise.resolve({ detail: 'upstream sad' })
  });
  await assert.rejects(
    kit.kitFetch('/plan-inputs'),
    (err) => err.message === 'GET /plan-inputs → HTTP 502: upstream sad'
  );
});

test('kitFetch: network failure rejects with named endpoint', async () => {
  globalThis.fetch = () => Promise.reject(new Error('connection refused'));
  await assert.rejects(
    kit.kitFetch('/day-setup', { method: 'POST' }),
    (err) => err.message === 'POST /day-setup → connection refused'
  );
});

test('kitFetch: token option becomes X-TDTB-Token header', async () => {
  let seen = null;
  globalThis.fetch = (url, opts) => {
    seen = opts;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: 1 }) });
  };
  const out = await kit.kitFetch('/day-setup', { method: 'POST', token: 'tok123' });
  assert.equal(out.ok, 1);
  assert.equal(seen.headers['X-TDTB-Token'], 'tok123');
  assert.equal(seen.token, undefined);
});

// -- buildCapacityQuery (ui-revamp T3) -----------------------------------------
test('buildCapacityQuery: shapes day_setup + selected from UI state', () => {
  const q = kit.buildCapacityQuery({
    anchor: '09:00', eod: '22:00', buffering: 'minimal',
    schedRows: { minting: { on: true, n: 2 }, qt: { on: false, n: 1 } },
    anchoredRows: [
      { id: 'gym', on: true, time: '07:00', blocks: 2, blocksChanged: false },
      { id: 'walk', on: false, time: '12:00', blocks: 1, blocksChanged: true }
    ],
    assigned: [
      { duration: '1h', _included: true },
      { duration: null, _included: true },
      { duration: '30m', _included: false }
    ]
  });
  assert.deepEqual(q.day_setup.schedulable, {
    minting: { on: true, n: 2 }, qt: { on: false, n: 1 }
  });
  assert.deepEqual(q.day_setup.anchored, [
    { id: 'gym', on: true, skip_today: false, time: '07:00' },        // blocks omitted — unchanged
    { id: 'walk', on: false, skip_today: true, time: '12:00', blocks: 1 } // blocks included — changed
  ]);
  assert.deepEqual(q.selected, ['1h', null]);  // excluded row dropped, null passthrough
  assert.equal(q.day_setup.anchor, '09:00');
  assert.equal(q.day_setup.eod, '22:00');
  assert.equal(q.day_setup.buffering, 'minimal');
});

test('buildCapacityQuery: empty/missing state produces empty-but-shaped payload', () => {
  const q = kit.buildCapacityQuery();
  assert.deepEqual(q, {
    day_setup: { anchor: null, eod: null, buffering: null, schedulable: {}, anchored: [] },
    selected: []
  });
});

// -- costOf true-minutes display (2026-07-17 LOOTS report) --------------------
test('costOf: time part shows true minutes, not block-rounded lie', () => {
  assert.equal(kit.costOf({ duration: 5 }), '1 blk · 5m');       // NOT "30m"
  assert.equal(kit.costOf({ duration: 45 }), '2 blk · 45m');     // NOT "1h"
  assert.equal(kit.costOf({ duration: 60 }), '2 blk · 1h');      // exact fit
  assert.equal(kit.costOf({ duration: '1h30m' }), '3 blk · 1h30m');
});

test('costOf: label-derived minutes also show true value', () => {
  assert.equal(kit.costOf({ labels: ['🚀10min'] }), '1 blk · 10m');
});

test('costOf: explicit blocks override shows block-derived time', () => {
  // stepper/retime set blocks — a size decision; stale source minutes lose
  assert.equal(kit.costOf({ blocks: 2, duration: 5 }), '2 blk · 1h');
  assert.equal(kit.costOf({ blocks: 1 }), '1 blk · 30m');
});

test('costOf: unknown cost renders empty', () => {
  assert.equal(kit.costOf({ name: 'no cost' }), '');
});

// step_commit.test.mjs — node --test suite for the pure logic in
// static/steps/commit.js (spa-overhaul T6). DOM wiring (render, button
// handlers, live/shadow network flow) is preview-verified, not here — this
// covers the id->source map builder, error classification (409/422), and
// commit-report summarization exported via commit._pure.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const kit = require('../../static/ui_kit.js');
const commitStep = require('../../static/steps/commit.js');
const pure = commitStep._pure;

// -- itemKey ------------------------------------------------------------------

test('itemKey: id wins, then name, then Block, else null', () => {
  assert.equal(pure.itemKey({ id: 'a1', name: 'A' }), 'a1');
  assert.equal(pure.itemKey({ name: 'A' }), 'A');
  assert.equal(pure.itemKey({ Block: 'gym' }), 'gym');
  assert.equal(pure.itemKey({}), null);
  assert.equal(pure.itemKey(null), null);
});

// -- buildSourceMap -------------------------------------------------------------

test('buildSourceMap: keys off digest.assigned first, falls back to anchored_blocks', () => {
  const stash = {
    digest: {
      assigned: [
        { id: 't1', source: 'todoist' },
        { name: 'vault-task', source: undefined }
      ]
    },
    anchored_blocks: [
      { Block: 'gym', source: 'schedulable' },
      { id: 't1', source: 'calendar' } // assigned already claimed t1 — must NOT override
    ]
  };
  const map = pure.buildSourceMap(stash, kit);
  assert.equal(map.t1, 'todoist');
  assert.equal(map['vault-task'], 'vault');
  assert.equal(map.gym, 'schedulable');
});

test('buildSourceMap: empty/missing stash produces an empty map', () => {
  assert.deepEqual(pure.buildSourceMap({}, kit), {});
  assert.deepEqual(pure.buildSourceMap(null, kit), {});
});

// -- chipHtml -------------------------------------------------------------------

test('chipHtml: known source renders its label/class, unknown falls back to vault', () => {
  assert.equal(pure.chipHtml('todoist', kit), '<span class="chip chip-todoist">todoist</span>');
  assert.equal(pure.chipHtml('mystery', kit), '<span class="chip chip-vault">vault</span>');
});

// -- fmt12 ------------------------------------------------------------------------

test('fmt12: 24h HH:MM -> 12h with AM/PM, non-HHMM passes through', () => {
  assert.equal(pure.fmt12('09:00'), '9:00 AM');
  assert.equal(pure.fmt12('00:00'), '12:00 AM');
  assert.equal(pure.fmt12('12:30'), '12:30 PM');
  assert.equal(pure.fmt12('23:59'), '11:59 PM');
  assert.equal(pure.fmt12('—'), '—');
});

// -- esc --------------------------------------------------------------------------

test('esc: escapes the five HTML-significant characters', () => {
  assert.equal(pure.esc('<a>&"\''), '&lt;a&gt;&amp;&quot;&#39;');
  assert.equal(pure.esc(null), '');
});

// -- parseCommitError (409 in-flight / 422 plan-refused) ---------------------------
// Message shape from ui_kit.js kitFetch: "<METHOD> <url> -> HTTP <status>[: <detail>]".

test('parseCommitError: 409 live-commit-in-flight classifies retry-later', () => {
  const msg = 'POST /commit?mode=live → HTTP 409: live commit already in flight — retry after it returns';
  const parsed = pure.parseCommitError(msg);
  assert.equal(parsed.status, 409);
  assert.equal(parsed.kind, 'retry-later');
  assert.equal(parsed.detail, 'live commit already in flight — retry after it returns');
});

test('parseCommitError: 422 plan-refused keeps the FULL blast-radius detail intact', () => {
  const detail = 'plan refused: cannot plan 2 item(s) — refusing entire commit: 1 todoist writes + ' +
    '1 calendar writes blocked. Unplannable: step1/Foo: todoist surface unavailable (would write blind — ' +
    'refusing); step2/Bar: conflict (missing)';
  const msg = 'POST /commit?mode=live → HTTP 422: ' + detail;
  const parsed = pure.parseCommitError(msg);
  assert.equal(parsed.status, 422);
  assert.equal(parsed.kind, 'plan-refused');
  assert.equal(parsed.detail, detail); // nothing truncated
});

test('parseCommitError: other statuses classify as generic error', () => {
  const parsed = pure.parseCommitError('POST /commit?mode=shadow → HTTP 502: shadow state error: boom');
  assert.equal(parsed.status, 502);
  assert.equal(parsed.kind, 'error');
  assert.equal(parsed.detail, 'shadow state error: boom');
});

test('parseCommitError: no HTTP status (network failure) still returns something renderable', () => {
  const parsed = pure.parseCommitError('POST /commit?mode=live → connection refused');
  assert.equal(parsed.status, null);
  assert.equal(parsed.kind, 'error');
  assert.equal(parsed.detail, 'POST /commit?mode=live → connection refused');
});

test('parseCommitError: null/undefined message degrades to empty string, not a throw', () => {
  assert.equal(pure.parseCommitError(null).detail, '');
  assert.equal(pure.parseCommitError(undefined).detail, '');
});

// -- summarizeReport -----------------------------------------------------------------
// Shape from orchestrate.run_orchestrated: {ok, resumed, today, surfaces,
// landed, failed, verify_failures}; surfaces[key] = {status, created,
// updated, noops, note|error}.

test('summarizeReport: per-surface counts + verify_failures survive the reduction', () => {
  const report = {
    ok: true, resumed: false, today: '2026-07-16',
    surfaces: {
      todoist: { status: 'ok', created: ['a', 'b'], updated: [], noops: ['c'], note: null },
      calendar: { status: 'ok', created: [], updated: ['d'], noops: [], note: 'fine' },
      vault: { status: 'ok', created: [], updated: [], noops: [], note: 'no intents' }
    },
    landed: ['todoist:step1 created=2 updated=0 noops=1'],
    failed: [],
    verify_failures: []
  };
  const s = pure.summarizeReport(report);
  assert.equal(s.ok, true);
  assert.equal(s.today, '2026-07-16');
  assert.equal(s.resumed, false);
  assert.deepEqual(s.surfaces.find(x => x.key === 'todoist'),
    { key: 'todoist', status: 'ok', created: 2, updated: 0, noops: 1, note: null });
  assert.deepEqual(s.surfaces.find(x => x.key === 'calendar'),
    { key: 'calendar', status: 'ok', created: 0, updated: 1, noops: 0, note: 'fine' });
  assert.equal(s.landedCount, 1);
  assert.equal(s.failedCount, 0);
  assert.deepEqual(s.verifyFailures, []);
});

test('summarizeReport: failed surfaces surface their error as note, verify_failures pass through', () => {
  const report = {
    ok: false, resumed: true, today: '2026-07-16',
    surfaces: {
      todoist: { status: 'failed', created: [], updated: [], noops: [], error: 'todoist: client unavailable' }
    },
    landed: [],
    failed: ['todoist: client unavailable'],
    verify_failures: ['todoist/step1: expected due 09:00, got 09:30']
  };
  const s = pure.summarizeReport(report);
  assert.equal(s.ok, false);
  assert.equal(s.resumed, true);
  assert.equal(s.failedCount, 1);
  assert.deepEqual(s.failed, ['todoist: client unavailable']);
  assert.equal(s.surfaces[0].note, 'todoist: client unavailable');
  assert.deepEqual(s.verifyFailures, ['todoist/step1: expected due 09:00, got 09:30']);
});

test('summarizeReport: missing/empty report degrades to a shaped-but-empty summary', () => {
  const s = pure.summarizeReport(null);
  assert.equal(s.ok, false);
  assert.equal(s.today, null);
  assert.deepEqual(s.surfaces, []);
  assert.equal(s.landedCount, 0);
  assert.equal(s.failedCount, 0);
  assert.deepEqual(s.verifyFailures, []);
});

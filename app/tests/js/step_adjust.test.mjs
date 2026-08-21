// step_adjust.test.mjs — node --test suite for the pure logic in
// static/steps/adjust.js (spa-overhaul T4). Same harness pattern as
// wizard_logic.test.mjs: require() the UMD-ish module (Node's top-level
// `this` in a CJS file is `module.exports`, so the IIFE's `root.tdtbSteps`
// assignment lands on what require() returns) and exercise `_pure` — no DOM,
// no fetch, no window.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const mod = require('../../legacy-static/steps/adjust.js');
const adjust = mod.tdtbSteps.adjust;
const P = adjust._pure;
const kit = require('../../legacy-static/ui_kit.js');

test('module registers render + _pure on window.tdtbSteps.adjust', () => {
  assert.equal(typeof adjust.render, 'function');
  assert.equal(typeof P, 'object');
});

// -- clampBlocks --------------------------------------------------------------

test('clampBlocks snaps to nearest 0.5 and clamps to [0, 8]', () => {
  assert.equal(P.clampBlocks(1.24), 1);
  assert.equal(P.clampBlocks(1.26), 1.5);
  assert.equal(P.clampBlocks(-3), 0);
  assert.equal(P.clampBlocks(50), 8);
});

test('clampBlocks rejects non-numeric input', () => {
  assert.equal(P.clampBlocks(NaN), null);
  assert.equal(P.clampBlocks('2'), null);
  assert.equal(P.clampBlocks(null), null);
  assert.equal(P.clampBlocks(undefined), null);
});

// -- stepOp ---------------------------------------------------------------

test('stepOp: first press on an auto (no blocks) item lands at 0.5', () => {
  assert.deepEqual(P.stepOp({ name: 'X' }, 0.5), { op: 'retime', id: 'X', args: { blocks: 0.5 } });
});

test('stepOp: floors at 0.5, never goes to 0 or negative', () => {
  assert.deepEqual(P.stepOp({ name: 'X', blocks: 0.5 }, -0.5).args, { blocks: 0.5 });
  assert.deepEqual(P.stepOp({ name: 'X', blocks: 0 }, -0.5).args, { blocks: 0.5 });
});

test('stepOp: steps up from an explicit duration', () => {
  assert.deepEqual(P.stepOp({ name: 'X', blocks: 1 }, 0.5).args, { blocks: 1.5 });
});

// -- findItem ---------------------------------------------------------------

function freshState() {
  return {
    assigned: [
      { name: 'A1', path: 'a1.md' },
      { name: 'A2', path: 'a2.md', _excluded: true }
    ],
    suggested: [
      { name: 'S1', path: 's1.md' }
    ]
  };
}

test('findItem: matches by name or path, assigned before suggested', () => {
  const state = freshState();
  assert.equal(P.findItem(state, 'A1').item.name, 'A1');
  assert.equal(P.findItem(state, 'a1.md').item.name, 'A1');
  assert.equal(P.findItem(state, 'S1').item.name, 'S1');
});

test('findItem: unknown id returns null', () => {
  assert.equal(P.findItem(freshState(), 'nope'), null);
});

// -- applyOne / applyOps -----------------------------------------------------

test('applyOne add: moves a suggested item into assigned', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'add', id: 'S1', args: {} });
  assert.equal(report.status, 'applied');
  assert.equal(state.suggested.length, 0);
  assert.equal(state.assigned.length, 3);
  assert.equal(state.assigned[2].name, 'S1');
});

test('applyOne add: re-includes an already-excluded assigned item without duplicating it', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'add', id: 'A2', args: {} });
  assert.equal(report.status, 'applied');
  assert.equal(state.assigned.length, 2);
  assert.equal(state.assigned[1]._excluded, false);
});

test('applyOne add: applies an optional blocks hint', () => {
  const state = freshState();
  P.applyOne(state, { op: 'add', id: 'S1', args: { blocks: 2 } });
  assert.equal(state.assigned[2].blocks, 2);
});

test('applyOne retime: sets a clamped blocks value', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'retime', id: 'A1', args: { blocks: 3.24 } });
  assert.equal(report.status, 'applied');
  assert.equal(state.assigned[0].blocks, 3);
});

test('applyOne retime: skips when blocks is not numeric', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'retime', id: 'A1', args: {} });
  assert.equal(report.status, 'skipped');
  assert.equal(report.reason, 'retime needs numeric blocks');
});

test('applyOne deassign: flags _excluded and clears any note', () => {
  const state = freshState();
  state.assigned[0]._note = 'stale';
  const report = P.applyOne(state, { op: 'deassign', id: 'A1', args: {} });
  assert.equal(report.status, 'applied');
  assert.equal(state.assigned[0]._excluded, true);
  assert.equal('_note' in state.assigned[0], false);
});

test('applyOne complete: flags _excluded with a completion-not-recorded note', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'complete', id: 'A1', args: {} });
  assert.equal(report.status, 'applied');
  assert.equal(state.assigned[0]._excluded, true);
  assert.match(state.assigned[0]._note, /NOT recorded/);
});

test('applyOne remove: splices the row out entirely', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'remove', id: 'A1', args: {} });
  assert.equal(report.status, 'applied');
  assert.equal(state.assigned.length, 1);
  assert.equal(state.assigned[0].name, 'A2');
});

test('applyOne: unknown item is skipped, never throws', () => {
  const state = freshState();
  const report = P.applyOne(state, { op: 'retime', id: 'ghost', args: { blocks: 1 } });
  assert.equal(report.status, 'skipped');
  assert.equal(report.reason, 'unknown item');
});

test('applyOne: malformed op (no .op string) is skipped, never throws', () => {
  const report = P.applyOne(freshState(), { id: 'A1' });
  assert.equal(report.status, 'skipped');
  assert.equal(report.reason, 'malformed op');
  assert.equal(P.applyOne(freshState(), null).status, 'skipped');
});

test('applyOne: unrecognized op name is skipped, never throws', () => {
  const report = P.applyOne(freshState(), { op: 'teleport', id: 'A1', args: {} });
  assert.equal(report.status, 'skipped');
  assert.match(report.reason, /unknown op/);
});

test('applyOps: batches applyOne, one report entry per op, partial success survives a bad op', () => {
  const state = freshState();
  const report = P.applyOps(state, [
    { op: 'deassign', id: 'A1', args: {} },
    { op: 'retime', id: 'ghost', args: { blocks: 1 } }
  ]);
  assert.equal(report.length, 2);
  assert.equal(report[0].status, 'applied');
  assert.equal(report[1].status, 'skipped');
  assert.equal(state.assigned[0]._excluded, true);
});

test('applyOps: empty/undefined ops list is a no-op', () => {
  assert.deepEqual(P.applyOps(freshState(), []), []);
  assert.deepEqual(P.applyOps(freshState(), undefined), []);
});

// -- summarizeReport (ops-report shape) --------------------------------------

test('summarizeReport: empty report renders the "no change" nudge', () => {
  const s = P.summarizeReport([]);
  assert.equal(s.applied, 0);
  assert.equal(s.skipped, 0);
  assert.equal(s.level, 'warn');
  assert.match(s.text, /No change/);
});

test('summarizeReport: all-applied renders ok with no skip mention', () => {
  const s = P.summarizeReport([{ status: 'applied' }, { status: 'applied' }]);
  assert.equal(s.applied, 2);
  assert.equal(s.skipped, 0);
  assert.equal(s.level, 'ok');
  assert.equal(s.text, '2 change(s) applied.');
});

test('summarizeReport: mixed applied/skipped renders warn with a skip count', () => {
  const s = P.summarizeReport([{ status: 'applied' }, { status: 'skipped' }, { status: 'skipped' }]);
  assert.equal(s.applied, 1);
  assert.equal(s.skipped, 2);
  assert.equal(s.level, 'warn');
  assert.equal(s.text, '1 change(s) applied, 2 skipped (unknown item).');
});

// -- shouldBlockApply (budget-gate predicate) --------------------------------

test('shouldBlockApply: a clean gate (loaded, budget open, non-empty ask) is not blocked', () => {
  const g = P.shouldBlockApply({ busy: false, budgetSpent: false, hasToken: true, instruction: 'drop the suit' });
  assert.equal(g.blocked, false);
  assert.equal(g.reason, null);
});

test('shouldBlockApply: in-flight request wins over every other reason', () => {
  const g = P.shouldBlockApply({ busy: true, budgetSpent: true, hasToken: false, instruction: '' });
  assert.equal(g.blocked, true);
  assert.equal(g.reason, 'busy');
});

test('shouldBlockApply: budget-spent blocks even with a valid instruction + token', () => {
  const g = P.shouldBlockApply({ busy: false, budgetSpent: true, hasToken: true, instruction: 'add Guitar' });
  assert.equal(g.blocked, true);
  assert.equal(g.reason, 'budget-spent');
});

test('shouldBlockApply: no token yet (digest still loading) blocks', () => {
  const g = P.shouldBlockApply({ busy: false, budgetSpent: false, hasToken: false, instruction: 'add Guitar' });
  assert.equal(g.blocked, true);
  assert.equal(g.reason, 'not-loaded');
});

test('shouldBlockApply: empty/whitespace-only instruction blocks', () => {
  assert.equal(P.shouldBlockApply({ hasToken: true, instruction: '' }).reason, 'empty-instruction');
  assert.equal(P.shouldBlockApply({ hasToken: true, instruction: '   ' }).reason, 'empty-instruction');
  assert.equal(P.shouldBlockApply({ hasToken: true, instruction: undefined }).reason, 'empty-instruction');
});

// -- classifyAdjustError -------------------------------------------------------

test('classifyAdjustError: 429 classifies as budget', () => {
  const c = P.classifyAdjustError(new Error('POST /adjust → HTTP 429: billed budget spent (4/4) for 2026-07-16'));
  assert.equal(c.status, 429);
  assert.equal(c.kind, 'budget');
});

test('classifyAdjustError: 502 classifies as judgment', () => {
  const c = P.classifyAdjustError(new Error('POST /adjust → HTTP 502: judgment error: SDK timeout'));
  assert.equal(c.status, 502);
  assert.equal(c.kind, 'judgment');
});

test('classifyAdjustError: any other status (or none) classifies as other', () => {
  assert.equal(P.classifyAdjustError(new Error('POST /adjust → HTTP 400: bad token')).kind, 'other');
  assert.equal(P.classifyAdjustError(new Error('POST /adjust → network error')).kind, 'other');
  assert.equal(P.classifyAdjustError(new Error('POST /adjust → network error')).status, null);
});

test('classifyAdjustError: message passes through verbatim for rendering', () => {
  const err = new Error('POST /adjust → HTTP 502: judgment error: boom');
  assert.equal(P.classifyAdjustError(err).message, err.message);
});

// -- buildDigestPayload -------------------------------------------------------

test('buildDigestPayload: strips client-only fields, keeps present optional fields', () => {
  const state = {
    valid_date: '2026-07-16',
    assigned: [{ name: 'A1', path: 'a1.md', types: ['task'], urgency: '2-med', deadline: '2026-08-01', blocks: 1.5, _excluded: false, _note: 'x' }],
    suggested: [{ name: 'S1', path: 's1.md' }]
  };
  const payload = P.buildDigestPayload(state);
  assert.equal(payload.valid_date, '2026-07-16');
  assert.deepEqual(payload.assigned[0], {
    name: 'A1', path: 'a1.md', types: ['task'], urgency: '2-med', deadline: '2026-08-01', blocks: 1.5
  });
  assert.deepEqual(payload.suggested[0], { name: 'S1', path: 's1.md' });
});

test('buildDigestPayload: omits optional fields that are absent rather than sending null', () => {
  const payload = P.buildDigestPayload({ valid_date: null, assigned: [{ name: 'A1', path: null }], suggested: [] });
  assert.deepEqual(payload.assigned[0], { name: 'A1', path: null });
  assert.equal('blocks' in payload.assigned[0], false);
});

// -- urgencyText ---------------------------------------------------------------

test('urgencyText: normalizes a stringified-list frontmatter shape', () => {
  assert.equal(P.urgencyText("['2-med']"), '2-med');
  assert.equal(P.urgencyText('3-high'), '3-high');
  assert.equal(P.urgencyText(null), '');
});

// -- blocksTextOf (uses the real ui_kit, not a mock) -------------------------

test('blocksTextOf: an explicit blocks hint wins and formats via kit.fmtCost', () => {
  assert.equal(P.blocksTextOf(kit, { blocks: 2 }), kit.fmtCost(2));
});

test('blocksTextOf: falls back to kit.costOf when no blocks hint is set', () => {
  const item = { duration: '45m' };
  assert.equal(P.blocksTextOf(kit, item), kit.costOf(item));
});

test('blocksTextOf: "auto" when neither resolves to a real number', () => {
  assert.equal(P.blocksTextOf(kit, {}), 'auto');
});

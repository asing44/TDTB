// wizard_logic.test.mjs — node --test suite for the SPA wizard state machine
// (spa-overhaul T1). Pure logic only; app.html DOM wiring is preview-verified.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const wiz = require('../../static/wizard_logic.js');

test('step order matches the morning ritual', () => {
  assert.deepEqual(wiz.STEPS.map(s => s.id),
    ['setup', 'digest', 'adjust', 'timeline', 'commit']);
});

test('nextStep walks forward and stops at commit', () => {
  assert.equal(wiz.nextStep('setup'), 'digest');
  assert.equal(wiz.nextStep('digest'), 'adjust');
  assert.equal(wiz.nextStep('commit'), null);
  assert.equal(wiz.nextStep('nope'), null);
});

test('adjust is skippable via opts', () => {
  assert.equal(wiz.nextStep('digest', { skipAdjust: true }), 'timeline');
  assert.equal(wiz.nextStep('digest', { skipAdjust: false }), 'adjust');
});

test('prevStep walks back and stops at setup', () => {
  assert.equal(wiz.prevStep('digest'), 'setup');
  assert.equal(wiz.prevStep('setup'), null);
});

test('entryNotice: commit warns without a staged sequence, quiet with one', () => {
  assert.match(wiz.entryNotice('commit', {}), /No staged sequence/);
  assert.equal(wiz.entryNotice('commit', { hasSequence: true }), null);
});

test('entryNotice: timeline nudges when Day Setup unconfirmed', () => {
  assert.match(wiz.entryNotice('timeline', {}), /Day Setup not confirmed/);
  assert.equal(wiz.entryNotice('timeline', { hasDaySetup: true }), null);
});

test('entryNotice: never blocks other steps', () => {
  for (const id of ['setup', 'digest', 'adjust']) {
    assert.equal(wiz.entryNotice(id, {}), null);
  }
});

test('stepFromHash parses known steps, rejects junk', () => {
  assert.equal(wiz.stepFromHash('#/timeline'), 'timeline');
  assert.equal(wiz.stepFromHash('#setup'), 'setup');
  assert.equal(wiz.stepFromHash('#/bogus'), null);
  assert.equal(wiz.stepFromHash(''), null);
  assert.equal(wiz.stepFromHash(undefined), null);
});

test('hashForStep round-trips through stepFromHash', () => {
  for (const s of wiz.STEPS) {
    assert.equal(wiz.stepFromHash(wiz.hashForStep(s.id)), s.id);
  }
});

test('dayPhase: fresh / mid-ritual / post-commit', () => {
  assert.equal(wiz.dayPhase({}), 'fresh');
  assert.equal(wiz.dayPhase({ hasDaySetup: true }), 'mid-ritual');
  assert.equal(wiz.dayPhase({ hasDaySetup: true, commitDone: true }), 'post-commit');
});

test('landingStep maps phase to step', () => {
  assert.equal(wiz.landingStep({}), 'setup');
  assert.equal(wiz.landingStep({ hasDaySetup: true }), 'digest');
  assert.equal(wiz.landingStep({ hasDaySetup: true, hasSequence: true }), 'timeline');
  assert.equal(wiz.landingStep({ hasDaySetup: true, commitDone: true }), 'commit');
});

test('budgetLabel renders server numbers verbatim', () => {
  assert.equal(wiz.budgetLabel({ spent: 2, cap: 4, remaining: 2 }), 'billed 2/4');
  assert.equal(wiz.budgetLabel({ spent: 4, cap: 4, remaining: 0 }),
    'billed 4/4 — budget spent');
  assert.equal(wiz.budgetLabel(null), 'budget: —');
});

test('budgetSpent only when remaining is 0', () => {
  assert.equal(wiz.budgetSpent({ spent: 4, cap: 4, remaining: 0 }), true);
  assert.equal(wiz.budgetSpent({ spent: 3, cap: 4, remaining: 1 }), false);
  assert.equal(wiz.budgetSpent(null), false);
});

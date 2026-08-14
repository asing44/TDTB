// wizard_logic.js — pure wizard state machine for the TDTB SPA (spa-overhaul
// T1). Same UMD shape as timeline_logic.js/ui_kit.js: zero DOM, everything
// node-testable (tests/js/wizard_logic.test.mjs). The shell (app.html) owns
// all DOM wiring and data fetching; this module owns step order, progression
// gating, hash routing, and day-phase detection.
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else root.tdtbWizard = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Morning-ritual order (plan locked decision 2). Adjust is skippable.
  var STEPS = [
    { id: 'setup',    title: 'Day Setup', skippable: false },
    { id: 'digest',   title: 'Digest',    skippable: false },
    { id: 'adjust',   title: 'Adjust',    skippable: true },
    { id: 'timeline', title: 'Timeline',  skippable: false },
    { id: 'commit',   title: 'Commit',    skippable: false }
  ];

  var IDS = STEPS.map(function (s) { return s.id; });

  function stepIndex(id) { return IDS.indexOf(id); }

  function stepById(id) {
    var i = stepIndex(id);
    return i === -1 ? null : STEPS[i];
  }

  // -- progression -----------------------------------------------------------

  function nextStep(currentId, opts) {
    var i = stepIndex(currentId);
    if (i === -1 || i === STEPS.length - 1) return null;
    var next = STEPS[i + 1];
    if (next.skippable && opts && opts.skipAdjust) {
      return i + 2 < STEPS.length ? STEPS[i + 2].id : null;
    }
    return next.id;
  }

  function prevStep(currentId) {
    var i = stepIndex(currentId);
    if (i <= 0) return null;
    return STEPS[i - 1].id;
  }

  // Gating (plan LD2/LD6): deep links are allowed anywhere — a wizard that
  // traps the user re-creates the five-view problem — but the shell renders a
  // non-blocking notice when prerequisites look absent. Only `commit` has a
  // hard data prerequisite: a staged sequence to diff. NOTHING here ever
  // auto-fires a billed call (G23: entering timeline/adjust idles until an
  // explicit button).
  function entryNotice(stepId, state) {
    state = state || {};
    if (stepId === 'commit' && !state.hasSequence) {
      return 'No staged sequence yet — propose one on the Timeline step first.';
    }
    if (stepId === 'timeline' && !state.hasDaySetup) {
      return 'Day Setup not confirmed yet — capacity numbers may be defaults.';
    }
    return null;
  }

  // -- hash routing -----------------------------------------------------------

  function stepFromHash(hash) {
    var m = /^#\/?([a-z-]+)/.exec(hash || '');
    if (m && stepIndex(m[1]) !== -1) return m[1];
    return null; // caller picks the day-phase default
  }

  function hashForStep(id) { return '#/' + id; }

  // -- day-phase detection -----------------------------------------------------
  // Where should a fresh page-load land? Pure function of observable state;
  // the shell supplies the inputs (day_setup echo from /plan-inputs, staged
  // sequence + commit flag from sessionStorage).
  //   fresh       → no confirmed Day Setup → land on setup
  //   mid-ritual  → Day Setup confirmed, no commit yet → land on digest
  //                 (or timeline when a sequence is already staged)
  //   post-commit → commit reported ok → land on commit (summary state)
  function dayPhase(state) {
    state = state || {};
    if (state.commitDone) return 'post-commit';
    if (state.hasDaySetup) return 'mid-ritual';
    return 'fresh';
  }

  function landingStep(state) {
    var phase = dayPhase(state);
    if (phase === 'post-commit') return 'commit';
    if (phase === 'mid-ritual') return (state && state.hasSequence) ? 'timeline' : 'digest';
    return 'setup';
  }

  // -- billed budget (LD6) -----------------------------------------------------
  // Renders from GET /billed-ledger verbatim — never a client-side count.
  function budgetLabel(ledger) {
    if (!ledger || typeof ledger.spent !== 'number') return 'budget: —';
    return 'billed ' + ledger.spent + '/' + ledger.cap +
      (ledger.remaining === 0 ? ' — budget spent' : '');
  }

  function budgetSpent(ledger) {
    return !!ledger && typeof ledger.remaining === 'number' && ledger.remaining <= 0;
  }

  return {
    STEPS: STEPS,
    stepIndex: stepIndex,
    stepById: stepById,
    nextStep: nextStep,
    prevStep: prevStep,
    entryNotice: entryNotice,
    stepFromHash: stepFromHash,
    hashForStep: hashForStep,
    dayPhase: dayPhase,
    landingStep: landingStep,
    budgetLabel: budgetLabel,
    budgetSpent: budgetSpent
  };
});

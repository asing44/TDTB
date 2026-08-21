// steps/timeline.js — TDTB SPA step module: Timeline view port (spa-overhaul
// T5, port source: static/timeline.html, 724 ln). Contract (T1): register
// { render(el, ctx) } on window.tdtbSteps. ctx = {state, kit, wizard, token,
// goto(stepId), persist(), refreshStatus()}.
//
// Reuses timeline_logic.js (toMin/minToHH/seedRows/assignedIndexById/
// sourceForId/placedDurations/buildDaySetupEcho) verbatim via `logic` below —
// NEVER duplicated. ui_kit.js (kitFetch/chipEl/sourceOf/buildCapacityQuery/
// renderError) reused via ctx.kit.
//
// G23: render() must NEVER auto-fire POST /sequence. A fresh mount with no
// restorable stash idles on "Propose sequence (1 billed call)" until an
// explicit click (or the LLM-free Manual layout). G14: the Propose button
// carries an in-flight guard. Budget gate (spa-overhaul LD6): Propose checks
// GET /billed-ledger (via ctx.wizard.budgetSpent) before firing, and a 429
// from the server itself renders as a distinct "budget spent" message, not a
// generic error.
//
// Pure, NEW-to-this-port state-shaping helpers (not already in
// timeline_logic.js) are exported under `_pure` for
// tests/js/step_timeline.test.mjs — same pattern as wizard_logic.js.
(function (root) {
  'use strict';

  // Resolve timeline_logic.js's pure module either via Node require (test
  // harness) or the browser global (already loaded by app.html before this
  // script — see app.html's <script> order). Never duplicate its functions.
  var logic = (typeof module !== 'undefined' && module.exports)
    ? require('../timeline_logic.js')
    : root.tdtbLogic;

  // -- pure helpers (state-shaping only; NOT duplicating timeline_logic.js) --

  // Anchored/busy block times arrive in two shapes: config rows use "7:45 AM"
  // 12h, calendar busy blocks use "09:00" 24h. Returns minutes or null.
  function parseAnyTime(raw) {
    if (raw == null) return null;
    var s = String(raw).trim();
    var m = s.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
    if (!m) return null;
    var h = +m[1], min = +m[2];
    if (m[3]) {
      var pm = m[3].toUpperCase() === 'PM';
      if (h === 12) h = pm ? 12 : 0;
      else if (pm) h += 12;
    }
    return h * 60 + min;
  }

  function snap(min, gran) {
    gran = gran || 5;
    return Math.round(min / gran) * gran;
  }

  // DISPLAY-ONLY 12h formatter. Never mutates row.start/row.end — those stay
  // "HH:MM" 24h (POST payload + sessionStorage stash + drag math all rely on it).
  function fmt12(hhmm) {
    if (!/^\d{1,2}:\d{2}$/.test(hhmm)) return hhmm;   // guard "—"/blank
    var p = hhmm.split(':'), h = +p[0], mer = h < 12 ? 'AM' : 'PM';
    var h12 = h % 12; if (h12 === 0) h12 = 12;
    return h12 + ':' + p[1] + ' ' + mer;
  }

  // DISPLAY-ONLY: rewrite every valid HH:MM token inside a backend string
  // (validation warnings/hard-errors from sequence.py, which speak 24h) to
  // 12h. Strict token match leaves an invalid time raw so the reader still
  // sees the bad value. Never mutates row data.
  function fmt12Text(s) {
    return String(s).replace(/\b([01]?\d|2[0-3]):[0-5]\d\b/g, fmt12);
  }

  var WORKOUT_KW = ['workout', 'exercise', 'fitness'];
  function isWorkout(row) {
    var id = (row.id || '').toLowerCase();
    if (WORKOUT_KW.some(function (k) { return id.indexOf(k) >= 0; })) return true;
    var z = (row.zone || '').toLowerCase();
    return WORKOUT_KW.indexOf(z) >= 0;
  }

  // Placement rows only — 🟡 backdrop zone framing is visual, never
  // validated, committed, or persisted as a placement.
  function placementRow(r) { return { id: r.id, start: r.start, end: r.end, zone: r.zone }; }

  // T1 contract: validator keys assigned_by_id on `id`; digest items carry
  // `name`. Set id = name so by-id matching lines up with sequence row ids.
  function normalizeAssigned(assigned) {
    return (assigned || []).map(function (a) {
      var copy = {}; for (var k in a) if (Object.prototype.hasOwnProperty.call(a, k)) copy[k] = a[k];
      if (copy.id == null) copy.id = a.name;
      return copy;
    });
  }

  // Canvas bounds: [anchor, effective_eod] padded to the hour, widened to
  // any row/busy block outside it (backdrop Trinoor rows start mid-morning).
  // Pure version of timeline.html's recomputeBounds — no module-level
  // mutable DAY_START/DAY_END, so it's safe across repeated mounts.
  function computeBounds(time, rows, anchored, fallbackStart, fallbackEnd) {
    var lo = fallbackStart, hi = fallbackEnd;
    var t = time || {};
    var a = t.anchor ? logic.toMin(t.anchor) : null;
    var e = t.effective_eod ? logic.toMin(t.effective_eod) : null;
    if (a != null && e != null && e > a) { lo = a; hi = e; }
    (rows || []).forEach(function (r) {
      var s = logic.toMin(r.start), en = logic.toMin(r.end);
      if (s != null && !isNaN(s)) lo = Math.min(lo, s);
      if (en != null && !isNaN(en)) hi = Math.max(hi, en);
    });
    (anchored || []).forEach(function (b) {
      var s = parseAnyTime(b.Start), en = parseAnyTime(b.End);
      if (s != null && en != null && en > s) { lo = Math.min(lo, s); hi = Math.max(hi, en); }
    });
    return {
      start: Math.max(0, Math.floor(lo / 60) * 60),
      end: Math.min(24 * 60 - 1, Math.ceil(hi / 60) * 60)
    };
  }

  // Per-block coloring: which rows are flagged by the last /validate-sequence
  // response. hard_errors are free-text server strings (no structured id).
  function classForRow(id, validation) {
    var v = validation || { hard_errors: [], warnings: [] };
    var hard = (v.hard_errors || []).some(function (m) { return typeof m === 'string' && m.indexOf("'" + id + "'") >= 0; });
    if (hard) return 'hard';
    var warn = (v.warnings || []).some(function (w) { return w.id === id; });
    return warn ? 'warn' : '';
  }

  // Pure drag math: pointer delta -> new snapped start, clamped to
  // [dayStart, dayEnd - dur]. Pulled out of the pointermove closure so it's
  // node-testable without a DOM.
  function dragNewStart(origStart, dur, dy, pxPerMin, snapGran, dayStart, dayEnd) {
    var raw = origStart + dy / pxPerMin;
    var snapped = snap(raw, snapGran);
    return Math.max(dayStart, Math.min(dayEnd - dur, snapped));
  }

  // Chronological start order is a HARD rule — non-mutating (returns a new
  // array; the caller assigns it back to state.rows).
  function sortRowsByStart(rows) {
    return (rows || []).slice().sort(function (a, b) { return logic.toMin(a.start) - logic.toMin(b.start); });
  }

  // Removing a row must also drop the item from assigned (never-bump check)
  // and from digest.assigned (a future commit read would carry it verbatim).
  function filterRemoved(assigned, digestAssigned, removedIds) {
    var gone = {};
    (removedIds || []).forEach(function (id) { gone[id] = true; });
    var newAssigned = (assigned || []).filter(function (a) { return !gone[a.id]; });
    var newDigestAssigned = Array.isArray(digestAssigned)
      ? digestAssigned.filter(function (a) { return !gone[a.id != null ? a.id : a.name]; })
      : digestAssigned;
    return { assigned: newAssigned, digestAssigned: newDigestAssigned };
  }

  // POST /validate-sequence body — backdrop rows excluded (never validated).
  function buildValidatePayload(rows, assigned, anchored, config) {
    return {
      sequence: (rows || []).filter(function (r) { return r && !r.backdrop; }).map(placementRow),
      assigned: assigned || [],
      anchored_blocks: anchored || [],
      config: config || {}
    };
  }

  // The wizard-level staged-sequence shape (ctx.state.sequence): the full
  // commit stash the Commit step POSTs — {digest, sequence:{sequence:[...]},
  // config, anchored_blocks}, the same shape commit.html's tdtb.commit.inputs
  // carried (sequence is a dict wrapper, per the /commit body contract).
  function buildCommitStagePayload(rows, digest, config, anchored) {
    return {
      digest: digest || null,
      sequence: { sequence: (rows || []).filter(function (r) { return r && !r.backdrop; }).map(placementRow) },
      config: config || {},
      anchored_blocks: anchored || []
    };
  }

  var PURE = {
    parseAnyTime: parseAnyTime,
    snap: snap,
    fmt12: fmt12,
    fmt12Text: fmt12Text,
    isWorkout: isWorkout,
    placementRow: placementRow,
    normalizeAssigned: normalizeAssigned,
    computeBounds: computeBounds,
    classForRow: classForRow,
    dragNewStart: dragNewStart,
    sortRowsByStart: sortRowsByStart,
    filterRemoved: filterRemoved,
    buildValidatePayload: buildValidatePayload,
    buildCommitStagePayload: buildCommitStagePayload
  };

  // -- style injection (once per page load) -----------------------------------
  // Touch-only scope is steps/timeline.js — this view's canvas/block/findings
  // CSS has no home in app.html or ui_kit.css, so it ships inline here.
  // Scoped under .tl-timeline so it can never collide with a sibling step's
  // classes (present or future).
  var STYLE_ID = 'tdtb-step-timeline-style';
  function ensureStyle() {
    if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '.tl-timeline .tl-actions { margin: 0.75rem 0; display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }',
      '.tl-timeline button:disabled { opacity: 0.5; cursor: not-allowed; }',
      '.tl-timeline .tl-layout { display: flex; gap: 1rem; align-items: flex-start; }',
      '.tl-timeline .tl-canvas-wrap { flex: 1 1 auto; max-height: 70vh; overflow-y: auto; border: 1px solid #ddd; border-radius: 6px; }',
      '.tl-timeline .tl-canvas { position: relative; margin-left: 52px; }',
      '.tl-timeline .tl-hourline { position: absolute; left: -52px; right: 0; border-top: 1px solid #eee; font-size: 11px; color: #999; padding-left: 2px; box-sizing: border-box; }',
      '.tl-timeline .tl-hourline.half { border-top-style: dashed; border-top-color: #f2f2f2; }',
      '.tl-timeline .tl-block {',
      '  position: absolute; left: 4px; right: 8px; box-sizing: border-box;',
      '  background: #dbeafe; border: 2px solid #60a5fa; border-radius: 5px;',
      '  padding: 2px 6px; font-size: 12px; overflow: hidden; cursor: grab; user-select: none;',
      '  touch-action: none;',   // claim the vertical-pan gesture (drag, not scroll)
      '}',
      '.tl-timeline .tl-block.dragging { cursor: grabbing; opacity: 0.85; z-index: 5; }',
      '.tl-timeline .tl-block.warn { border-color: #d97706; background: #fef3c7; }',
      '.tl-timeline .tl-block.hard { border-color: #dc2626; background: #fee2e2; }',
      '.tl-timeline .tl-block .tl-bmeta { color: #555; font-size: 11px; }',
      '.tl-timeline .tl-block.workout { box-shadow: inset 3px 0 0 #7c3aed; }',
      '.tl-timeline .tl-block.busy { background: #f1f1f1; border-color: #ccc; color: #666; cursor: default; pointer-events: none; z-index: 0; }',
      '.tl-timeline .tl-block.backdrop { background: rgba(240, 200, 60, 0.12); border: 1px dashed #d0a92e; color: #9a7d1c; cursor: default; pointer-events: none; z-index: 0; }',
      '.tl-timeline .tl-rmbtn { position: absolute; top: 2px; right: 4px; border: none; background: transparent; color: inherit; opacity: 0.35; font-size: 14px; line-height: 1; padding: 2px 4px; cursor: pointer; }',
      '.tl-timeline .tl-block:hover .tl-rmbtn { opacity: 0.85; }',
      '.tl-timeline .tl-rmbtn:hover { opacity: 1; }',
      '.tl-timeline .tl-side { flex: 0 0 260px; font-size: 13px; }',
      '.tl-timeline .tl-findings { border: 1px solid #ddd; border-radius: 6px; padding: 0.5rem 0.75rem; margin-bottom: 0.75rem; }',
      '.tl-timeline .tl-findings h2 { font-size: 13px; margin: 0 0 0.35rem; }',
      '.tl-timeline .tl-finding { padding: 3px 0; border-bottom: 1px solid #f0f0f0; }',
      '.tl-timeline .tl-finding.hard { color: #b91c1c; }',
      '.tl-timeline .tl-finding.warn { color: #b45309; }',
      '.tl-timeline .ok { color: #15803d; }',
      '.tl-timeline .tl-hint { font-size: 12px; color: #888; }',
      '.tl-timeline #tl-totals { margin-top: 2px; }',
      '@media (max-width: 640px) {',
      '  .tl-timeline .tl-layout { flex-direction: column; align-items: stretch; }',
      '  .tl-timeline .tl-side { flex: 1 1 auto; width: 100%; }',
      '  .tl-timeline .tl-canvas-wrap { max-height: 60vh; }',
      '  .tl-timeline .tl-actions { flex-wrap: wrap; }',
      '}',
      '@media (prefers-color-scheme: dark) {',
      '  .tl-timeline .tl-canvas-wrap, .tl-timeline .tl-findings { border-color: #444; }',
      '  .tl-timeline .tl-hourline { border-top-color: #333; color: #777; }',
      '  .tl-timeline .tl-hourline.half { border-top-color: #2a2a2a; }',
      '  .tl-timeline .tl-block { background: #1e3a5f; border-color: #3b82f6; }',
      '  .tl-timeline .tl-block.busy { background: #2a2a2a; border-color: #555; color: #999; }',
      '  .tl-timeline .tl-block.backdrop { background: rgba(240, 200, 60, 0.08); border-color: #8a7422; color: #b99a2e; }',
      '  .tl-timeline .tl-block .tl-bmeta { color: #b0b0b0; }',
      '  .tl-timeline .tl-block.warn { background: #422006; border-color: #d97706; }',
      '  .tl-timeline .tl-block.hard { background: #450a0a; border-color: #dc2626; }',
      '  .tl-timeline .tl-finding { border-bottom-color: #333; }',
      '}'
    ].join('\n');
    document.head.appendChild(style);
  }

  // Cross-mount timer guard: a pending debounced /validate-sequence call from
  // a PREVIOUS mount (before a nav-away) is cancelled the instant this step
  // re-mounts, so it never fires against detached state.
  var pendingValidateTimer = null;

  var PX_PER_MIN = 1.2;   // canvas scale
  var SNAP = 5;           // minute snap granularity

  // Working-state persistence keys (unchanged from timeline.html so a future
  // adjust.js port's handoff stays wire-compatible).
  var TL_STASH_KEY = 'tdtb.timeline.rows';
  var ADJ_STASH_KEY = 'tdtb.adjust.assigned';

  // -- render -----------------------------------------------------------------
  function render(el, ctx) {
    ensureStyle();
    var kit = ctx.kit;
    var wiz = ctx.wizard;

    if (pendingValidateTimer) { clearTimeout(pendingValidateTimer); pendingValidateTimer = null; }

    el.innerHTML =
      '<div class="tl-timeline">' +
        '<p id="tl-status" class="status">Loading…</p>' +
        '<div id="tl-actions" class="tl-actions" hidden>' +
          '<button id="tl-propose" class="btn" type="button">Propose sequence (1 billed call)</button>' +
          '<button id="tl-manual" class="btn" type="button" title="Skip the LLM: stack assigned items from the anchor, then drag into shape">Manual layout</button>' +
          '<span id="tl-okline"></span>' +
        '</div>' +
        '<div id="tl-layout" class="tl-layout" hidden>' +
          '<div class="tl-canvas-wrap"><div id="tl-canvas" class="tl-canvas"></div></div>' +
          '<div class="tl-side">' +
            '<div class="tl-findings">' +
              '<h2>Validation</h2>' +
              '<div id="tl-findings-list"><span class="ok">No issues.</span></div>' +
              '<div id="tl-totals" class="cost"></div>' +
            '</div>' +
            '<div class="tl-findings">' +
              '<h2>How to use</h2>' +
              '<div class="tl-hint">Drag a block up/down to shift its start (snaps to 5 min). Amber = soft warning (still commit-able); red = hard error (blocks commit).</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';

    var statusEl = el.querySelector('#tl-status');
    var actionsEl = el.querySelector('#tl-actions');
    var layoutEl = el.querySelector('#tl-layout');
    var canvasEl = el.querySelector('#tl-canvas');
    var findingsEl = el.querySelector('#tl-findings-list');
    var totalsEl = el.querySelector('#tl-totals');
    var okLineEl = el.querySelector('#tl-okline');
    var proposeBtn = el.querySelector('#tl-propose');
    var manualBtn = el.querySelector('#tl-manual');

    // -- local (per-mount) state ------------------------------------------------
    var state = {
      token: (ctx.state && ctx.state.token) || ctx.token || null,
      digest: null,
      config: {},
      time: null,
      anchored: [],
      assigned: [],      // normalized: id = name
      daySetup: {},       // /plan-inputs' persisted day_setup echo (totals line)
      rows: [],           // [{id,start,end,zone,_dur}]
      removed: [],        // ids removed on the timeline — kept out of assigned too
      validation: { ok: true, hard_errors: [], warnings: [] },
      _proposing: false,
      _seedGen: 0
    };
    var bounds = { start: 5 * 60, end: 23 * 60 };

    // -- working-state persistence ----------------------------------------------
    function readAdjustAssigned() {
      try {
        var raw = sessionStorage.getItem(ADJ_STASH_KEY);
        if (!raw) return null;
        var s = JSON.parse(raw);
        if (!s || !Array.isArray(s.assigned) || !s.assigned.length) return null;
        var vd = state.digest && state.digest.valid_date;
        if (!vd || s.valid_date !== vd) return null;   // day rollover -> ignore
        return s.assigned;
      } catch (e) { return null; }
    }
    function clearStash(key) { try { sessionStorage.removeItem(key); } catch (e) { /* best-effort */ } }

    function persistRows() {
      try {
        var vd = state.digest && state.digest.valid_date;
        if (vd) {
          sessionStorage.setItem(TL_STASH_KEY, JSON.stringify({
            valid_date: vd,
            rows: state.rows.map(function (r) {
              var row = placementRow(r);
              if (r.backdrop) row.backdrop = true;
              return row;
            }),
            removed: state.removed
          }));
        }
      } catch (e) { /* sessionStorage unavailable — persistence is best-effort */ }
      stageForWizard();   // "the source view persists" -> also stage for the wizard
    }

    // ui-revamp/T5 wizard integration: whenever the timeline's own rows-stash
    // persists (a successful proposal, manual-layout seed, drag-end, or
    // removal), mirror the current non-backdrop rows into the shared wizard
    // state so the Commit step (and the entryNotice gate) sees them.
    function stageForWizard() {
      ctx.state.sequence = buildCommitStagePayload(
        state.rows, state.digest, state.config, state.anchored);
      ctx.state.hasSequence = ctx.state.sequence.sequence.sequence.length > 0;
      ctx.persist();
      ctx.refreshStatus();
    }

    function readStashRows() {
      try {
        var raw = sessionStorage.getItem(TL_STASH_KEY);
        if (!raw) return null;
        var s = JSON.parse(raw);
        if (!s || !Array.isArray(s.rows) || !s.rows.length) return null;
        var vd = state.digest && state.digest.valid_date;
        if (!vd || s.valid_date !== vd) return null;
        return { rows: s.rows, removed: Array.isArray(s.removed) ? s.removed : [] };
      } catch (e) { return null; }
    }

    // -- budget gate (spa-overhaul LD6) ------------------------------------------
    function updateProposeAvailability() {
      var spent = !!(ctx.state.ledger && wiz.budgetSpent(ctx.state.ledger));
      proposeBtn.disabled = state._proposing || spent;
      proposeBtn.title = spent
        ? 'Billed budget spent for today — try Manual layout instead'
        : 'Fetch a fresh /sequence proposal (1 billed call)';
    }

    // -- canvas rendering ---------------------------------------------------------
    function renderCanvas() {
      bounds = computeBounds(state.time, state.rows, state.anchored, 5 * 60, 23 * 60);
      canvasEl.innerHTML = '';
      var totalMin = bounds.end - bounds.start;
      canvasEl.style.height = (totalMin * PX_PER_MIN) + 'px';
      var assignedIndex = logic.assignedIndexById(state.assigned);

      for (var h = Math.ceil(bounds.start / 60); h <= bounds.end / 60; h++) {
        var line = document.createElement('div');
        line.className = 'tl-hourline';
        line.style.top = ((h * 60 - bounds.start) * PX_PER_MIN) + 'px';
        line.textContent = fmt12((h < 10 ? '0' : '') + h + ':00');
        canvasEl.appendChild(line);
        var half = h * 60 + 30;
        if (half < bounds.end) {
          var hl = document.createElement('div');
          hl.className = 'tl-hourline half';
          hl.style.top = ((half - bounds.start) * PX_PER_MIN) + 'px';
          canvasEl.appendChild(hl);
        }
      }

      // Anchored + calendar busy blocks render as non-draggable gray walls
      // behind the sequence rows. Duration-only blocks (End "—") are skipped.
      (state.anchored || []).forEach(function (b) {
        var s = parseAnyTime(b.Start), e = parseAnyTime(b.End);
        if (s == null || e == null || e <= s) return;
        var blockEl = document.createElement('div');
        blockEl.className = 'tl-block busy';
        blockEl.style.top = ((s - bounds.start) * PX_PER_MIN) + 'px';
        blockEl.style.height = Math.max(14, (e - s) * PX_PER_MIN) + 'px';
        var meta = document.createElement('div');
        meta.className = 'tl-bmeta';
        meta.textContent = (b.source === 'calendar' ? '📅 ' : '⚓ ') + (b.Block || 'block') +
          ' · ' + fmt12(logic.minToHH(s)) + '–' + fmt12(logic.minToHH(e));
        meta.appendChild(kit.chipEl(kit.sourceOf(b)));
        blockEl.appendChild(meta);
        canvasEl.appendChild(blockEl);
      });

      state.rows.forEach(function (row, i) {
        var blockEl = document.createElement('div');
        blockEl.className = 'tl-block';
        if (row.backdrop) {
          // 🟡 Trinoor zone framing — permeable backdrop, not a placement:
          // dimmed, behind everything, never draggable, no source chip.
          blockEl.className = 'tl-block backdrop';
          positionBlock(blockEl, row);
          var bdMeta = document.createElement('div');
          bdMeta.className = 'tl-bmeta';
          bdMeta.textContent = row.id + ' · ' + fmt12(row.start) + '–' + fmt12(row.end);
          blockEl.appendChild(bdMeta);
          canvasEl.appendChild(blockEl);
          return;
        }
        if (isWorkout(row)) blockEl.className += ' workout';
        blockEl.dataset.idx = i;
        positionBlock(blockEl, row);
        var kind = classForRow(row.id, state.validation);
        if (kind) blockEl.className += ' ' + kind;
        var nameLine = document.createElement('div');
        var strong = document.createElement('strong');
        strong.textContent = row.id;
        nameLine.appendChild(strong);
        nameLine.appendChild(kit.chipEl(logic.sourceForId(row.id, assignedIndex)));
        blockEl.appendChild(nameLine);
        var rowMeta = document.createElement('div');
        rowMeta.className = 'tl-bmeta';
        rowMeta.textContent = fmt12(row.start) + '–' + fmt12(row.end) + ' · ' + (row.zone || 'any');
        blockEl.appendChild(rowMeta);
        var rm = document.createElement('button');
        rm.className = 'tl-rmbtn';
        rm.type = 'button';
        rm.textContent = '×';
        rm.title = 'Remove from plan';
        // pointerdown starts the drag on the block — swallow it so × is a click
        rm.addEventListener('pointerdown', function (ev) { ev.stopPropagation(); });
        rm.addEventListener('click', function (ev) { ev.stopPropagation(); removeRow(row.id); });
        blockEl.appendChild(rm);
        attachDrag(blockEl, row);
        canvasEl.appendChild(blockEl);
      });
    }

    function positionBlock(blockEl, row) {
      var s = logic.toMin(row.start), e = logic.toMin(row.end);
      blockEl.style.top = ((s - bounds.start) * PX_PER_MIN) + 'px';
      blockEl.style.height = Math.max(16, (e - s) * PX_PER_MIN) + 'px';
    }

    // -- drag interaction -----------------------------------------------------
    function attachDrag(blockEl, row) {
      blockEl.addEventListener('pointerdown', function (ev) {
        ev.preventDefault();
        try { blockEl.setPointerCapture(ev.pointerId); } catch (e) { /* capture unsupported — drag still tracks via listeners */ }
        blockEl.classList.add('dragging');
        var startY = ev.clientY;
        var origStart = logic.toMin(row.start);
        var dur = row._dur;

        function onMove(e) {
          var newStart = dragNewStart(origStart, dur, e.clientY - startY, PX_PER_MIN, SNAP, bounds.start, bounds.end);
          row.start = logic.minToHH(newStart);
          row.end = logic.minToHH(newStart + dur);
          positionBlock(blockEl, row);
          blockEl.querySelector('.tl-bmeta').textContent = fmt12(row.start) + '–' + fmt12(row.end) + ' · ' + (row.zone || 'any');
        }
        function onUp(e) {
          blockEl.classList.remove('dragging');
          try { blockEl.releasePointerCapture(ev.pointerId); } catch (e) { /* no capture to release */ }
          blockEl.removeEventListener('pointermove', onMove);
          blockEl.removeEventListener('pointerup', onUp);
          resortRows();
          persistRows();       // survive nav — keep the stash (and wizard stage) in sync
          scheduleValidate();
        }
        blockEl.addEventListener('pointermove', onMove);
        blockEl.addEventListener('pointerup', onUp);
      });
    }

    function resortRows() {
      state.rows = sortRowsByStart(state.rows);
      renderCanvas();
    }

    // -- row removal ------------------------------------------------------------
    function applyRemovals() {
      if (!state.removed.length) return;
      var res = filterRemoved(state.assigned, state.digest && state.digest.assigned, state.removed);
      state.assigned = res.assigned;
      if (state.digest) state.digest.assigned = res.digestAssigned;
    }

    function removeRow(id) {
      state.rows = state.rows.filter(function (r) { return r.id !== id; });
      state.removed.push(id);
      applyRemovals();
      persistRows();
      renderCanvas();
      scheduleValidate();
    }

    function scheduleValidate() {
      if (pendingValidateTimer) clearTimeout(pendingValidateTimer);
      pendingValidateTimer = setTimeout(runValidate, 250);
    }

    // -- validation -----------------------------------------------------------
    function runValidate() {
      var payload = buildValidatePayload(state.rows, state.assigned, state.anchored, state.config);
      kit.kitFetch('/validate-sequence', {
        method: 'POST',
        token: state.token,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
        .then(function (v) { state.validation = v; renderValidation(); renderCanvas(); updateTotals(); })
        .catch(function (err) { statusEl.textContent = 'Validation failed: ' + err.message; statusEl.className = 'status error'; });
    }

    function renderValidation() {
      var v = state.validation;
      var assignedIndex = logic.assignedIndexById(state.assigned);
      findingsEl.innerHTML = '';
      if (v.ok && !v.warnings.length) {
        findingsEl.innerHTML = '<span class="ok">No issues.</span>';
      } else {
        (v.hard_errors || []).forEach(function (m) {
          var d = document.createElement('div'); d.className = 'tl-finding hard'; d.textContent = '⛔ ' + fmt12Text(m); findingsEl.appendChild(d);
        });
        (v.warnings || []).forEach(function (w) {
          var d = document.createElement('div'); d.className = 'tl-finding warn';
          d.textContent = '⚠ ' + (w.id ? w.id + ': ' : '') + fmt12Text(w.detail || w.kind);
          if (w.id) { d.appendChild(kit.chipEl(logic.sourceForId(w.id, assignedIndex))); }
          findingsEl.appendChild(d);
        });
      }
      okLineEl.textContent = v.ok
        ? (v.warnings.length ? v.warnings.length + ' warning(s) — sequence staged' : 'All clear')
        : v.hard_errors.length + ' hard error(s) — fix before Commit';
      okLineEl.className = v.ok ? 'ok' : '';
    }

    // -- sidebar totals -----------------------------------------------------------
    // Σ blocks placed / free — GET /capacity-preview is the ONLY source of
    // these numbers; this never sums block costs itself. day_setup is an
    // echo of the persisted values (buildDaySetupEcho) so the preview call
    // is a no-op merge server-side; `selected` carries the durations of
    // rows currently placed on the canvas (placedDurations). Workaround
    // documented in timeline_logic.js — preserved verbatim here.
    function updateTotals() {
      if (!kit) return;   // defensive: preview harness may not load ui_kit.js
      var assignedIndex = logic.assignedIndexById(state.assigned);
      var echo = logic.buildDaySetupEcho(state.time, state.daySetup);
      echo.assigned = logic.placedDurations(state.rows, assignedIndex).map(function (d) {
        return { duration: d, _included: true };
      });
      var q = kit.buildCapacityQuery(echo);
      var url = '/capacity-preview?day_setup=' + encodeURIComponent(JSON.stringify(q.day_setup)) +
                '&selected=' + encodeURIComponent(JSON.stringify(q.selected));
      kit.kitFetch(url)
        .then(function (resp) {
          totalsEl.className = 'cost';
          totalsEl.textContent = 'Selected: ' + resp.segments.selected + ' blk · Free: ' + resp.free + ' blk' +
            (resp.over > 0 ? ' (over by ' + resp.over + ')' : '');
        })
        .catch(function (err) { kit.renderError(totalsEl, err); });
    }

    // -- load flow ------------------------------------------------------------
    function ensureToken() {
      if (state.token) return Promise.resolve();
      return kit.kitFetch('/session-token').then(function (d) {
        state.token = d.token;
        ctx.state.token = d.token;   // keep the shared shell state in sync too
      });
    }

    function ingestProposal(proposal) {
      state.rows = (proposal.sequence || []).map(function (r) {
        var row = { id: r.id, start: r.start, end: r.end, zone: r.zone || 'any', _dur: logic.toMin(r.end) - logic.toMin(r.start) };
        if (r.backdrop) row.backdrop = true;   // Step D′ framing must survive ingest
        return row;
      });
      resortRows();
      // seed validation from the proposal's own warnings; then re-validate live.
      state.validation = { ok: true, hard_errors: [], warnings: proposal.warnings || [] };
      renderValidation();
      actionsEl.hidden = false;
      layoutEl.hidden = false;
      statusEl.textContent = 'Loaded ' + state.rows.length + ' block(s) for ' +
        (state.digest && state.digest.valid_date ? state.digest.valid_date : 'today') + '.';
      persistRows();   // capture the just-loaded plan (and stage for the wizard)
      runValidate();
    }

    function fetchPlanInputs() {
      return kit.kitFetch('/plan-inputs').then(function (pi) {
        state.digest = pi.digest;
        state.config = pi.config || {};
        state.time = pi.time || null;
        state.anchored = pi.anchored_blocks || [];
        state.assigned = normalizeAssigned((pi.digest && pi.digest.assigned) || []);
        state.daySetup = pi.day_setup || {};   // totals-line echo source
      });
    }

    function fetchSequence() {
      return fetch('/sequence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-TDTB-Token': state.token },
        body: JSON.stringify({ assigned: state.assigned, config: state.config, anchored_blocks: state.anchored })
      }).then(function (r) {
        if (r.status === 429) {
          return r.json().catch(function () { return {}; }).then(function (e) {
            var err = new Error('budget spent' + (e && e.detail ? ': ' + e.detail : ''));
            err.budgetSpent = true;
            throw err;
          });
        }
        if (r.status === 422) return r.json().then(function (e) { throw new Error('POST /sequence → HTTP 422 rejected: ' + JSON.stringify(e.detail)); });
        if (!r.ok) throw new Error('POST /sequence → HTTP ' + r.status);
        return r.json();
      });
    }

    // load(seed, opts): when seed is given (preview), use its proposal/inputs
    // and skip the /plan-inputs + /sequence fetches; token + /validate-sequence
    // still run against the live server. opts.forceRepropose skips the stash
    // restore and always re-calls /sequence (the Propose/Re-propose button).
    // Normal mounts restore a same-day stash if present. G23: a fresh mount
    // with NO stash and no forceRepropose never calls /sequence — it idles.
    function load(seed, opts) {
      opts = opts || {};
      statusEl.className = 'status';
      statusEl.textContent = 'Fetching session token…';
      ensureToken().then(function () {
        if (seed) {
          state.digest = seed.digest || { valid_date: 'preview', assigned: seed.assigned || [] };
          state.config = seed.config || {};
          state.anchored = seed.anchored_blocks || [];
          state.assigned = normalizeAssigned(seed.assigned || (state.digest.assigned) || []);
          state.time = seed.time || null;
          state.daySetup = seed.day_setup || {};
          ingestProposal(seed.proposal);
          return;
        }
        statusEl.textContent = 'Gathering plan inputs…';
        fetchPlanInputs()
          .then(function () {
            // Re-propose is the deliberate reset: discard both the adjust-view
            // handoff and any in-progress drags, reverting to the raw digest plan.
            if (opts.forceRepropose) { clearStash(ADJ_STASH_KEY); clearStash(TL_STASH_KEY); }
            var adj = opts.forceRepropose ? null : readAdjustAssigned();
            if (adj) { state.assigned = normalizeAssigned(adj); }
            var restored = opts.forceRepropose ? null : readStashRows();
            if (restored) {
              statusEl.textContent = 'Restored your in-progress plan.';
              state.removed = restored.removed;
              applyRemovals();
              ingestProposal({ sequence: restored.rows, warnings: [] });
              return;
            }
            state.removed = [];
            if (!opts.forceRepropose) {
              // G23: a fresh mount with no stash must NOT auto-fire /sequence.
              // Idle until the user explicitly clicks Propose (or Manual layout).
              actionsEl.hidden = false;
              proposeBtn.textContent = 'Propose sequence (1 billed call)';
              statusEl.textContent = 'No plan yet — Propose sequence (1 billed call) or Manual layout.';
              updateProposeAvailability();
              return;
            }
            // Budget gate (LD6): block the billed call client-side too — the
            // server 429s regardless, but this avoids a doomed round-trip.
            if (ctx.state.ledger && wiz.budgetSpent(ctx.state.ledger)) {
              statusEl.textContent = 'Budget spent — no more billed calls today.';
              statusEl.className = 'status error';
              actionsEl.hidden = false;
              updateProposeAvailability();
              return;
            }
            statusEl.textContent = 'Proposing sequence… (or click Manual layout to skip the wait)';
            actionsEl.hidden = false;   // Manual layout usable while the LLM call runs
            var gen = state._seedGen = (state._seedGen || 0) + 1;   // manual seed bumps this — late proposal discards itself
            // G14: disable the propose button while a request is in flight —
            // a double-click fired two concurrent billed judgment calls.
            state._proposing = true;
            proposeBtn.disabled = true;
            return fetchSequence().then(function (p) {
              if (gen === state._seedGen) ingestProposal(p);
            }).catch(function (err) {
              if (err && err.budgetSpent) {
                // Distinct from a generic failure — the server itself hit the cap.
                statusEl.textContent = 'Budget spent — no more billed calls today.';
                statusEl.className = 'status error';
                ctx.refreshStatus().then(updateProposeAvailability);
              } else {
                statusEl.textContent = 'Failed: ' + err.message; statusEl.className = 'status error';
                // Failed attempts still spend the server ledger (retries are
                // billed) — resync the header so it never shows stale 0/4.
                ctx.refreshStatus();
              }
            }).finally(function () {
              state._proposing = false;
              proposeBtn.disabled = false;
              updateProposeAvailability();
              proposeBtn.textContent = 'Re-propose (1 billed call)';
            });
          })
          .catch(function (err) {
            statusEl.textContent = 'Failed: ' + err.message; statusEl.className = 'status error';
            actionsEl.hidden = false;   // keep Manual layout reachable on /sequence failure
          });
      }).catch(function (err) { statusEl.textContent = 'Failed: ' + err.message; statusEl.className = 'status error'; });
    }

    // -- manual layout (LLM-free seed) ----------------------------------------
    // Deterministic fallback when /sequence is slow, 422s, or the billed
    // budget is spent: anchored blocks land at their configured times
    // (validator passthrough), assigned items first-fit from the anchor
    // around them. User drags into shape; the same /validate-sequence path
    // applies. Math lives in timeline_logic.js (pure, node-tested, G23) —
    // this shell only owns state + rendering.
    function manualSeed() {
      var rows = logic.seedRows(state.time, state.anchored, state.assigned);
      state.removed = [];
      state._seedGen = (state._seedGen || 0) + 1;   // invalidate any in-flight /sequence
      ingestProposal({ sequence: rows, warnings: ['manual layout — LLM-free seed; drag into shape'] });
    }

    proposeBtn.addEventListener('click', function () {
      if (state._proposing) return;   // G14: in-flight guard, belt to the disabled attr
      load(null, { forceRepropose: true });
    });

    manualBtn.addEventListener('click', function () {
      if (state.digest) { manualSeed(); return; }
      // /sequence (or plan-inputs) failed before anything loaded — refetch inputs, then seed
      ensureToken().then(fetchPlanInputs).then(manualSeed)
        .catch(function (err) { statusEl.textContent = 'Failed: ' + err.message; statusEl.className = 'status error'; });
    });

    // Preview seam, scoped to this mount (not a global — avoids collisions
    // with sibling step ports' own preview hooks). Inert in normal use;
    // window.__TDTB_PREVIEW_TIMELINE__ bypasses ONLY the /sequence LLM call —
    // token fetch and /validate-sequence still hit the live (deterministic) server.
    el.tdtbLoad = load;
    load(root.__TDTB_PREVIEW_TIMELINE__ || null);
  }

  root.tdtbSteps = root.tdtbSteps || {};
  root.tdtbSteps['timeline'] = { render: render, _pure: PURE };
})(typeof self !== 'undefined' ? self : this);

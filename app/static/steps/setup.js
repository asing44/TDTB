// steps/setup.js — TDTB SPA Day Setup step (spa-overhaul T2: port of
// static/index.html into the wizard step contract). Same UMD shape as
// ui_kit.js/wizard_logic.js/timeline_logic.js: pure logic (time parsing,
// anchored/schedulable row derivation incl. past-anchor default-skip, the
// /day-setup payload builder) is node-testable via `_pure`; render(el, ctx)
// is thin DOM wiring, preview-verified rather than unit-tested.
//
// Capacity numbers are backend-verbatim (GET /capacity-preview) — this file
// does NO block-count arithmetic beyond bar-segment % widths, same contract
// as the source view (G27 died by construction; don't reintroduce it here).
// render() never auto-fires POST /sequence or /adjust (G23) — the only POST
// here is /day-setup, the explicit confirm action this step exists for.
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else {
    root.tdtbSteps = root.tdtbSteps || {};
    root.tdtbSteps['setup'] = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // -- time helpers (ported verbatim from index.html) ------------------------
  function toMinutes(hhmm) {
    if (!hhmm) return null;
    var parts = String(hhmm).split(':');
    var h = parseInt(parts[0], 10), m = parseInt(parts[1], 10);
    if (isNaN(h) || isNaN(m)) return null;
    return h * 60 + m;
  }

  // Convert "5:45 PM" / "17:45" / "5:45pm" style strings to "HH:MM" 24h.
  function to24h(s) {
    if (!s) return null;
    s = String(s).trim();
    var m = s.match(/^(\d{1,2}):(\d{2})\s*(am|pm)?$/i);
    if (!m) return null;
    var h = parseInt(m[1], 10), mins = m[2];
    var ap = m[3] ? m[3].toLowerCase() : null;
    if (ap === 'pm' && h < 12) h += 12;
    if (ap === 'am' && h === 12) h = 0;
    return (h < 10 ? '0' + h : String(h)) + ':' + mins;
  }

  // -- anchored lifestyle blocks: past-anchor default-skip calc ---------------
  // Pure port of index.html's buildAnchoredRows, parameterized on anchorVal
  // instead of reading the DOM directly.
  function buildAnchoredRows(pi, anchorVal) {
    pi = pi || {};
    var cfg = pi.config || {};
    var specs = cfg['Anchored Lifestyle Blocks'] || cfg['anchored_blocks'] || [];
    var overrides = {};
    ((pi.day_setup && pi.day_setup.anchored) || []).forEach(function (o) {
      overrides[o.id] = o;
    });
    var anchorMin = toMinutes(anchorVal);

    return specs.map(function (spec) {
      var id = spec.Block || spec.id || spec.name || '(unnamed)';
      var startRaw = spec.Start;
      var endRaw = spec.End;
      var start24 = to24h(startRaw) || startRaw;
      var end24 = to24h(endRaw) || endRaw;
      // display-only block count (no min-1 clamp) — fall back to 1 when no
      // duration is present at all, matching the source row default.
      var blocks = blocksOfDuration(spec.Duration || spec.duration);
      if (blocks == null) blocks = 1;

      var override = overrides[id] || {};
      // a persisted override already reflects an explicit past choice —
      // treat it as "changed" so it keeps riding the capacity-preview query;
      // otherwise blocksChanged only flips true when the stepper is used.
      var blocksChanged = false;
      if (override.blocks != null) { blocks = override.blocks; blocksChanged = true; }
      var timeVal = override.time || start24 || '';
      var on = override.on != null ? override.on : true;
      var defaultSkipped = false;

      var startMin = toMinutes(start24);
      var endMin = toMinutes(end24);
      var isWindow = start24 && end24;

      if (override.on == null) {
        // no explicit override — apply past-anchor default-skip rule
        if (anchorMin != null) {
          if (isWindow) {
            if (endMin != null && endMin < anchorMin) { on = false; defaultSkipped = true; }
          } else if (startMin != null && startMin < anchorMin) {
            on = false; defaultSkipped = true;
          }
        }
      }
      if (override.skip_today) on = false;

      return {
        id: id,
        name: id,
        on: on,
        skip_today: !on,
        time: timeVal,
        blocks: blocks,
        blocksChanged: blocksChanged,
        defaultSkipped: defaultSkipped
      };
    });
  }

  // Minimal local duration parser (mirrors ui_kit.js's parseDurationMinutes so
  // this file doesn't reach into ui_kit internals for one helper). Same
  // rounding contract: ceil to 30-min blocks, no min-1 clamp.
  function blocksOfDuration(dur) {
    if (dur == null) return null;
    var min;
    if (typeof dur === 'number') {
      min = dur > 0 ? dur : null;
    } else {
      var s = String(dur).trim();
      var h = s.match(/(\d+)\s*h/i);
      var m = s.match(/(\d+)\s*m/i);
      if (h || m) min = (h ? +h[1] * 60 : 0) + (m ? +m[1] : 0);
      else { var n = parseFloat(s); min = (!isNaN(n) && n > 0) ? n : null; }
    }
    return min == null ? null : Math.ceil(min / 30);
  }

  // -- schedulable rows ---------------------------------------------------------
  // Pure port of index.html's buildSchedRows. `now` is injectable so the
  // weekend/eod-passed defaulting is node-testable without a real clock.
  function buildSchedRows(pi, anchorVal, eodVal, now) {
    pi = pi || {};
    var ds = pi.day_setup || {};
    var override = ds.schedulable || {};
    var anchorMin = toMinutes(anchorVal);
    var eodMin = toMinutes(eodVal);
    var eodPassed = (anchorMin != null && eodMin != null && eodMin <= anchorMin);
    var d = now || new Date();
    var isWeekend = (d.getDay() === 0 || d.getDay() === 6);

    var mintingDefaultOn = !isWeekend && !eodPassed;

    var rows = {
      minting: { on: mintingDefaultOn, n: 2 },
      qt: { on: true, n: 1 },
      shivery: { on: false, n: 1 }
    };
    ['minting', 'qt', 'shivery'].forEach(function (k) {
      if (override[k]) {
        if (override[k].on != null) rows[k].on = override[k].on;
        if (override[k].n != null) rows[k].n = override[k].n;
      }
    });
    return rows;
  }

  var SCHED_LABELS = { minting: 'Minting', qt: 'Quick Tasks', shivery: 'Shivery Jigs' };

  // -- POST /day-setup payload builder ----------------------------------------
  // Pure port of confirmDaySetup's inline body construction.
  function buildConfirmPayload(form) {
    form = form || {};
    var schedRows = form.schedRows || {};
    var anchoredRows = form.anchoredRows || [];
    var captures = form.captures || {};
    function row(k) {
      var r = schedRows[k];
      return { on: !!(r && r.on), n: r ? r.n : 0 };
    }
    return {
      anchor: form.anchor || null,
      eod: form.eod || null,
      buffering: form.buffering,
      schedulable: { minting: row('minting'), qt: row('qt'), shivery: row('shivery') },
      anchored: anchoredRows.map(function (r) {
        return { id: r.id, on: r.on, skip_today: !r.on, time: r.time, blocks: r.blocks };
      }),
      captures: {
        intention: captures.intention || '',
        megan_nicety: captures.megan_nicety || '',
        stoic_intention: captures.stoic_intention || ''
      }
    };
  }

  // -- DOM helpers --------------------------------------------------------------
  function mk(tag, cls, doc) {
    var d = doc || document;
    var e = d.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  // Local styles for rules index.html carried inline that ui_kit.css doesn't
  // define (ui_kit.css/.js are shared-owned — not edited by this port).
  // Scoped under .step-setup so it can't leak into other step modules.
  var STYLE = '' +
    '.step-setup h2 { margin: 1.2rem 0 0.4rem; }\n' +
    '.step-setup label { font-size: 13px; font-weight: 600; }\n' +
    '.step-setup input[type=time], .step-setup select { font-size: 13px; padding: 3px 6px; }\n' +
    '.step-setup .day-frame { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 0.4rem; }\n' +
    '.step-setup .day-frame > div { display: flex; flex-direction: column; gap: 2px; }\n' +
    '.step-setup .bar-wrap { margin: 0.6rem 0 1rem; }\n' +
    '.step-setup .bar-track { display: flex; height: 20px; border-radius: 4px; overflow: hidden; background: #eee; border: 1px solid #ccc; }\n' +
    '.step-setup .bar-track > div { height: 100%; }\n' +
    '.step-setup .seg-fixed { background: #999; }\n' +
    '.step-setup .seg-anch { background: #7a9fd6; }\n' +
    '.step-setup .seg-habit { background: #b28ade; }\n' +
    '.step-setup .seg-sched { background: #4caf7d; }\n' +
    '.step-setup .seg-buf { background: #e0c458; }\n' +
    '.step-setup .seg-free { background: transparent; }\n' +
    '.step-setup .bar-lbl { font-size: 13px; font-weight: 600; margin-top: 4px; }\n' +
    '.step-setup .bar-lbl.over { color: #a33; }\n' +
    '.step-setup .bar-legend { font-size: 12px; color: #666; margin-top: 2px; }\n' +
    '.step-setup .bar-status { margin-top: 4px; }\n' +
    '.step-setup .rows { list-style: none; padding: 0; margin: 0 0 1rem; }\n' +
    '.step-setup .row { display: flex; align-items: center; gap: 0.6rem; padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 13px; }\n' +
    '.step-setup .row .main { flex: 1 1 auto; min-width: 0; }\n' +
    '.step-setup .row .name { font-weight: 600; }\n' +
    '.step-setup .row.skipped .cost { color: #aab; }\n' +
    '.step-setup .row.skipped { opacity: 0.55; }\n' +
    '.step-setup .row .n { width: 2.4em; text-align: center; }\n' +
    '.step-setup .capbox { display: flex; align-items: center; gap: 0.5rem; }\n' +
    '.step-setup .capbox input[type=text] { width: 100%; box-sizing: border-box; font-size: 13px; font-family: inherit; padding: 6px; }\n' +
    '.step-setup .confirm-wrap { margin: 1.2rem 0; display: flex; align-items: center; gap: 0.8rem; }\n' +
    '.step-setup .confirm-status { font-size: 13px; }\n' +
    '.step-setup .confirm-status.error { color: #a33; }\n' +
    '.step-setup .confirm-status.ok { color: #2a7; }\n' +
    '.step-setup .config-viewer { margin-top: 2rem; border-top: 1px solid #ddd; padding-top: 0.6rem; }\n' +
    '.step-setup .config-viewer summary { cursor: pointer; font-size: 13px; font-weight: 600; }\n' +
    '.step-setup .viewer-caption { color: #888; font-size: 12px; margin: 0.3rem 0 0.6rem; }\n' +
    '.step-setup .section { border: 1px solid #ddd; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 0.75rem; }\n' +
    '.step-setup .section h2 { font-size: 15px; margin: 0; }\n' +
    '.step-setup .validation { border: 1px solid #ddd; border-radius: 6px; padding: 0.75rem 1rem; }\n' +
    '.step-setup .validation.valid { border-color: #2a7; }\n' +
    '.step-setup .validation.invalid { border-color: #a33; }\n' +
    '.step-setup .config-viewer ul { margin: 0.25rem 0; padding-left: 1.25rem; }\n' +
    '.step-setup .config-viewer code { background: #f2f2f2; padding: 0 3px; }\n' +
    '@media (prefers-color-scheme: dark) {\n' +
    '  .step-setup .section, .step-setup .validation, .step-setup .config-viewer { border-color: #444 !important; }\n' +
    '  .step-setup .config-viewer code { background: #333; }\n' +
    '  .step-setup .row { border-color: #444; }\n' +
    '  .step-setup .bar-track { background: #333; border-color: #555; }\n' +
    '  .step-setup .bar-legend { color: #aaa; }\n' +
    '  .step-setup .viewer-caption { color: #aaa; }\n' +
    '}\n';

  // -- render -------------------------------------------------------------------
  function render(el, ctx) {
    var kit = ctx.kit;
    var token = ctx.token;

    var wrap = mk('div', 'step-setup');
    var styleEl = document.createElement('style');
    styleEl.textContent = STYLE;
    wrap.appendChild(styleEl);

    var statusEl = mk('div', 'status'); statusEl.textContent = 'Loading…';
    var warnEl = mk('div', 'banner-error');
    var noTimeEl = mk('div', 'banner-error');

    // -- day frame: anchor / eod / buffering
    var dayFrame = mk('div', 'day-frame'); dayFrame.hidden = true;
    var anchorEl = document.createElement('input'); anchorEl.type = 'time';
    var eodEl = document.createElement('input'); eodEl.type = 'time';
    var bufEl = document.createElement('select');
    ['minimal', 'standard', 'off'].forEach(function (v) {
      var o = document.createElement('option'); o.value = v; o.textContent = v; bufEl.appendChild(o);
    });
    function frameField(labelText, inputEl) {
      var d = document.createElement('div');
      var lab = document.createElement('label'); lab.textContent = labelText;
      d.appendChild(lab); d.appendChild(inputEl);
      return d;
    }
    dayFrame.appendChild(frameField('Anchor', anchorEl));
    dayFrame.appendChild(frameField('EOD', eodEl));
    dayFrame.appendChild(frameField('Buffering', bufEl));

    var eodNoteEl = mk('div', 'banner-warn');

    // -- capacity bar
    var barWrap = mk('div', 'bar-wrap'); barWrap.hidden = true;
    var barTrack = mk('div', 'bar-track');
    var segFixedEl = mk('div', 'seg-fixed');
    var segAnchEl = mk('div', 'seg-anch');
    var segHabitEl = mk('div', 'seg-habit');
    var segSchedEl = mk('div', 'seg-sched');
    var segBufEl = mk('div', 'seg-buf');
    var segFreeEl = mk('div', 'seg-free');
    [segFixedEl, segAnchEl, segHabitEl, segSchedEl, segBufEl, segFreeEl].forEach(function (s) {
      barTrack.appendChild(s);
    });
    var barLblEl = mk('div', 'bar-lbl');
    var barLegendEl = mk('div', 'bar-legend');
    var overBannerEl = mk('div', 'banner-error');
    var barStatusEl = mk('div', 'status bar-status');
    barWrap.appendChild(barTrack);
    barWrap.appendChild(barLblEl);
    barWrap.appendChild(barLegendEl);
    barWrap.appendChild(overBannerEl);
    barWrap.appendChild(barStatusEl);

    var h2Anchored = document.createElement('h2'); h2Anchored.textContent = 'Anchored Lifestyle Blocks';
    var anchoredListEl = mk('ul', 'rows');

    var h2Sched = document.createElement('h2'); h2Sched.textContent = 'Schedulable';
    var schedListEl = mk('ul', 'rows');

    var h2Captures = document.createElement('h2'); h2Captures.textContent = 'Captures';
    var capIntentionEl = document.createElement('input'); capIntentionEl.type = 'text'; capIntentionEl.placeholder = 'Intention for today';
    var capMeegyEl = document.createElement('input'); capMeegyEl.type = 'text'; capMeegyEl.placeholder = 'For Meegy';
    var capStoicEl = document.createElement('input'); capStoicEl.type = 'text'; capStoicEl.placeholder = 'Stoic focus';
    function capbox(inputEl) { var d = mk('div', 'capbox'); d.appendChild(inputEl); return d; }

    var confirmBtn = document.createElement('button'); confirmBtn.textContent = 'Confirm day setup';
    var confirmStatusEl = mk('span', 'confirm-status');
    var confirmWrap = mk('div', 'confirm-wrap');
    confirmWrap.appendChild(confirmBtn);
    confirmWrap.appendChild(confirmStatusEl);

    // -- config viewer (folded, ported from prior index.html)
    var details = mk('details', 'config-viewer');
    var summary = document.createElement('summary'); summary.textContent = 'Config viewer';
    var caption = mk('p', 'viewer-caption');
    caption.textContent = 'raw Presets/config.md — reference only, edits happen in the vault';
    var bootstrapEl = mk('div', 'banner-warn'); bootstrapEl.hidden = true;
    bootstrapEl.innerHTML = '<strong>Bootstrap needed</strong> — no config file found at ' +
      '<code>00 - META/Skill-Configs/tdtb-bridger.md</code>.';
    var sectionsEl = document.createElement('div');
    var validationEl = document.createElement('div');
    details.appendChild(summary);
    details.appendChild(caption);
    details.appendChild(bootstrapEl);
    details.appendChild(sectionsEl);
    details.appendChild(validationEl);

    wrap.appendChild(statusEl);
    wrap.appendChild(warnEl);
    wrap.appendChild(noTimeEl);
    wrap.appendChild(dayFrame);
    wrap.appendChild(eodNoteEl);
    wrap.appendChild(barWrap);
    wrap.appendChild(h2Anchored);
    wrap.appendChild(anchoredListEl);
    wrap.appendChild(h2Sched);
    wrap.appendChild(schedListEl);
    wrap.appendChild(h2Captures);
    wrap.appendChild(capbox(capIntentionEl));
    wrap.appendChild(capbox(capMeegyEl));
    wrap.appendChild(capbox(capStoicEl));
    wrap.appendChild(confirmWrap);
    wrap.appendChild(details);
    el.appendChild(wrap);

    // -- local view state (fresh per render — a step revisit re-fetches, same
    // as the source view's single page load)
    var local = {
      pi: null,
      anchoredRows: [],
      schedRows: { minting: { on: true, n: 2 }, qt: { on: true, n: 1 }, shivery: { on: false, n: 1 } },
      assigned: []
    };

    // -- capacity bar wiring (verbatim server numbers; ui-revamp contract)
    var capacityTimer = null;
    function scheduleCapacityUpdate() {
      if (capacityTimer) clearTimeout(capacityTimer);
      capacityTimer = setTimeout(updateBar, 150); // debounce toggle bursts
    }

    function updateBar() {
      capacityTimer = null;
      var q = kit.buildCapacityQuery({
        anchor: anchorEl.value, eod: eodEl.value, buffering: bufEl.value,
        schedRows: local.schedRows, anchoredRows: local.anchoredRows, assigned: local.assigned
      });
      var url = '/capacity-preview?day_setup=' + encodeURIComponent(JSON.stringify(q.day_setup)) +
        '&selected=' + encodeURIComponent(JSON.stringify(q.selected));
      kit.kitFetch(url).then(renderCapacity).catch(function (err) { kit.renderError(barStatusEl, err); });
    }

    function renderCapacity(resp) {
      barStatusEl.textContent = '';
      barStatusEl.className = 'status bar-status';

      var seg = resp.segments || {};
      var total = resp.total || 0;
      function pctWidth(v) { return (total > 0 ? (100 * Math.max(0, v || 0) / total) : 0) + '%'; }

      segFixedEl.style.width = pctWidth(seg.fixed);
      segFixedEl.title = 'Fixed — calendar commitments: ' + seg.fixed + ' blk';
      segAnchEl.style.width = pctWidth(seg.anchored);
      segAnchEl.title = 'Anchored — lifestyle blocks: ' + seg.anchored + ' blk';
      segHabitEl.style.width = pctWidth(seg.habits);
      segHabitEl.title = 'Habits: ' + seg.habits + ' blk';
      segSchedEl.style.width = pctWidth(seg.selected);
      segSchedEl.title = 'Selected — assigned + schedulables: ' + seg.selected + ' blk';
      segBufEl.style.width = pctWidth(seg.buffer);
      segBufEl.title = 'Buffer (buffering choice): ' + seg.buffer + ' blk';
      segFreeEl.style.width = pctWidth(resp.free);
      segFreeEl.title = 'Free: ' + resp.free + ' blk';

      barLblEl.textContent = 'Budget: ' + resp.remaining;
      barLblEl.className = 'bar-lbl' + (resp.overassigned ? ' over' : '');
      barLegendEl.textContent = resp.legend;

      if (resp.over > 0) {
        overBannerEl.style.display = 'block';
        overBannerEl.textContent = '⚠ Over capacity by ' + resp.over + ' blk — trim Selected or extend EOD';
      } else {
        overBannerEl.style.display = 'none';
        overBannerEl.textContent = '';
      }
    }

    // -- anchored lifestyle blocks list
    function renderAnchored() {
      anchoredListEl.innerHTML = '';
      if (!local.anchoredRows.length) {
        var li0 = mk('li', 'empty'); li0.textContent = 'None.';
        anchoredListEl.appendChild(li0);
        return;
      }
      local.anchoredRows.forEach(function (row) {
        var li = mk('li', 'row' + (!row.on ? ' skipped' : ''));

        var main = mk('div', 'main');
        var name = mk('div', 'name');
        name.textContent = row.name + (row.defaultSkipped && !row.on ? ' — skipped (past anchor)' : '');
        main.appendChild(name);

        var cost = mk('div', 'cost');
        cost.textContent = kit.fmtCost(row.blocks);
        main.appendChild(cost);

        var timeInput = document.createElement('input');
        timeInput.type = 'time';
        timeInput.value = row.time || '';
        timeInput.addEventListener('change', function () {
          row.time = timeInput.value;
          scheduleCapacityUpdate();
        });

        var toggle = document.createElement('input');
        toggle.type = 'checkbox';
        toggle.checked = row.on;
        toggle.title = 'On / skip today';
        toggle.addEventListener('change', function () {
          row.on = toggle.checked;
          row.skip_today = !row.on;
          renderAnchored();
          scheduleCapacityUpdate();
        });

        var minus = document.createElement('button');
        minus.type = 'button'; minus.textContent = '−';
        minus.addEventListener('click', function () {
          row.blocks = Math.max(0, row.blocks - 1);
          row.blocksChanged = true;
          renderAnchored();
          scheduleCapacityUpdate();
        });

        var nEl = mk('span', 'n'); nEl.textContent = row.blocks;

        var plus = document.createElement('button');
        plus.type = 'button'; plus.textContent = '+';
        plus.addEventListener('click', function () {
          row.blocks = Math.min(16, row.blocks + 1);
          row.blocksChanged = true;
          renderAnchored();
          scheduleCapacityUpdate();
        });

        li.appendChild(main);
        li.appendChild(timeInput);
        li.appendChild(minus);
        li.appendChild(nEl);
        li.appendChild(plus);
        li.appendChild(toggle);
        anchoredListEl.appendChild(li);
      });
    }

    // -- schedulable rows
    function renderSched() {
      schedListEl.innerHTML = '';
      ['minting', 'qt', 'shivery'].forEach(function (k) {
        var row = local.schedRows[k];
        var li = mk('li', 'row' + (!row.on ? ' skipped' : ''));

        var main = mk('div', 'main');
        var name = mk('div', 'name'); name.textContent = SCHED_LABELS[k];
        main.appendChild(name);

        var cost = mk('div', 'cost'); cost.textContent = kit.fmtCost(row.n);
        main.appendChild(cost);

        var toggle = document.createElement('input');
        toggle.type = 'checkbox';
        toggle.checked = row.on;
        toggle.addEventListener('change', function () {
          row.on = toggle.checked;
          renderSched();
          scheduleCapacityUpdate();
        });

        var minus = document.createElement('button');
        minus.type = 'button'; minus.textContent = '−';
        minus.addEventListener('click', function () {
          row.n = Math.max(0, row.n - 1);
          renderSched();
          scheduleCapacityUpdate();
        });

        var n = mk('span', 'n'); n.textContent = row.n;

        var plus = document.createElement('button');
        plus.type = 'button'; plus.textContent = '+';
        plus.addEventListener('click', function () {
          row.n = Math.min(8, row.n + 1);
          renderSched();
          scheduleCapacityUpdate();
        });

        li.appendChild(main);
        li.appendChild(toggle);
        li.appendChild(minus);
        li.appendChild(n);
        li.appendChild(plus);
        schedListEl.appendChild(li);
      });
    }

    [anchorEl, eodEl, bufEl].forEach(function (inputEl) {
      inputEl.addEventListener('change', function () {
        local.anchoredRows = buildAnchoredRows(local.pi || {}, anchorEl.value);
        renderAnchored();
        scheduleCapacityUpdate();
      });
    });

    // -- load ---------------------------------------------------------------
    function loadPlan() {
      statusEl.className = 'status';
      statusEl.textContent = 'Loading plan inputs…';

      kit.kitFetch('/plan-inputs')
        .then(function (pi) {
          local.pi = pi;
          var warnings = pi.source_warnings || [];
          warnEl.style.display = warnings.length ? 'block' : 'none';
          warnEl.textContent = warnings.length ? ('⚠ ' + warnings.join(' — ')) : '';

          var time = pi.time || {};
          var ds = pi.day_setup || {};
          anchorEl.value = ds.anchor || time.anchor || '';
          eodEl.value = ds.eod || time.effective_eod || '';
          bufEl.value = ds.buffering || 'minimal'; // server contract default (G27 fix)

          if (time.eod_note) {
            eodNoteEl.style.display = 'block';
            eodNoteEl.textContent = time.eod_note;
          } else {
            eodNoteEl.style.display = 'none';
          }

          if (time.no_time_left) {
            noTimeEl.style.display = 'block';
            noTimeEl.textContent = 'No schedulable time left today (EOD ≤ start). Nothing to plan.';
            dayFrame.hidden = false;
            barWrap.hidden = true;
            statusEl.textContent = 'Loaded.';
            local.assigned = ((pi.digest || {}).assigned || []).map(function (r) {
              return { name: r.name, duration: r.duration, _included: false };
            });
            local.anchoredRows = buildAnchoredRows(pi, anchorEl.value);
            local.schedRows = buildSchedRows(pi, anchorEl.value, eodEl.value);
            renderAnchored();
            renderSched();
            return;
          }
          noTimeEl.style.display = 'none';
          dayFrame.hidden = false;
          barWrap.hidden = false;

          local.assigned = ((pi.digest || {}).assigned || []).map(function (r) {
            return { name: r.name, duration: r.duration, _included: true };
          });

          local.anchoredRows = buildAnchoredRows(pi, anchorEl.value);
          local.schedRows = buildSchedRows(pi, anchorEl.value, eodEl.value);

          capIntentionEl.value = ds.intention || '';
          capMeegyEl.value = ds.megan_nicety || '';
          capStoicEl.value = ds.stoic_intention || '';

          renderAnchored();
          renderSched();
          updateBar();
          statusEl.textContent = 'Loaded.';
        })
        .catch(function (err) { kit.renderError(statusEl, err); });
    }

    // -- confirm --------------------------------------------------------------
    function confirmDaySetup() {
      if (!token) {
        confirmStatusEl.textContent = 'Not loaded yet.';
        confirmStatusEl.className = 'confirm-status error';
        return;
      }
      var body = buildConfirmPayload({
        anchor: anchorEl.value, eod: eodEl.value, buffering: bufEl.value,
        schedRows: local.schedRows, anchoredRows: local.anchoredRows,
        captures: {
          intention: capIntentionEl.value,
          megan_nicety: capMeegyEl.value,
          stoic_intention: capStoicEl.value
        }
      });

      confirmBtn.disabled = true;
      confirmStatusEl.textContent = 'Confirming…';
      confirmStatusEl.className = 'confirm-status';

      // kitFetch (not a hand-rolled fetch) so a failure carries the same
      // "POST /day-setup → HTTP xxx: detail" naming every other endpoint
      // gets — only the success path is endpoint-specific (re_included copy,
      // wizard state flip) hence the "manual handler" around it.
      kit.kitFetch('/day-setup', {
        method: 'POST',
        token: token,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
        .then(function (data) {
          var reInc = data.re_included || [];
          confirmStatusEl.textContent = 'Confirmed.' + (reInc.length ? ' Re-included: ' + reInc.join(', ') + '.' : '');
          confirmStatusEl.className = 'confirm-status ok';
          confirmBtn.disabled = false;

          ctx.state.hasDaySetup = true;
          ctx.state.daySetup = body;
          ctx.refreshStatus();

          // Source view linked to a separate digest.html page; the wizard
          // has no separate pages, so this becomes an in-place step jump
          // via ctx.goto instead of an <a href>.
          var goLink = document.createElement('a');
          goLink.href = '#';
          goLink.textContent = ' Go to digest →';
          goLink.addEventListener('click', function (e) {
            e.preventDefault();
            ctx.goto('digest');
          });
          confirmStatusEl.appendChild(goLink);
        })
        .catch(function (err) {
          confirmStatusEl.textContent = 'Confirm failed: ' + err.message;
          confirmStatusEl.className = 'confirm-status error';
          confirmBtn.disabled = false;
        });
    }
    confirmBtn.addEventListener('click', confirmDaySetup);

    // -- config viewer (folded, ported from prior index.html) -----------------
    function loadConfig() {
      kit.kitFetch('/config')
        .then(function (data) {
          if (data.bootstrap_needed) {
            bootstrapEl.hidden = false;
            return;
          }
          (data.sections || []).forEach(function (name) {
            var div = mk('div', 'section');
            var h2 = document.createElement('h2'); h2.textContent = name;
            div.appendChild(h2);
            sectionsEl.appendChild(div);
          });

          var v = data.validation;
          if (v) {
            var div = mk('div', 'validation ' + (v.valid ? 'valid' : 'invalid'));
            var h2 = document.createElement('h2');
            h2.textContent = v.valid ? 'Config valid' : 'Config invalid';
            div.appendChild(h2);

            function renderList(label, items) {
              if (!items || !items.length) return;
              var p = document.createElement('p'); p.textContent = label + ':';
              div.appendChild(p);
              var ul = document.createElement('ul');
              items.forEach(function (item) {
                var li = document.createElement('li');
                li.textContent = typeof item === 'string' ? item : JSON.stringify(item);
                ul.appendChild(li);
              });
              div.appendChild(ul);
            }
            renderList('Missing sections', v.missing_sections);
            renderList('Missing keys', v.missing_keys);
            renderList('Malformed rows', v.malformed_rows);

            validationEl.appendChild(div);
          }
        })
        .catch(function (err) { kit.renderError(sectionsEl, err); });
    }

    loadPlan();
    loadConfig();
  }

  return {
    render: render,
    _pure: {
      toMinutes: toMinutes,
      to24h: to24h,
      buildAnchoredRows: buildAnchoredRows,
      buildSchedRows: buildSchedRows,
      buildConfirmPayload: buildConfirmPayload
    }
  };
});

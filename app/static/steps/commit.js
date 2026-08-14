// steps/commit.js — TDTB SPA step module (spa-overhaul T6).
// Port of static/commit.html into the wizard step contract: register
// { render(el, ctx) } on window.tdtbSteps.commit; render must NEVER
// auto-fire a billed call (G23) — POST /commit (shadow AND live) is NOT
// billed (plan locked decision 5/T6 rules), but live still WRITES
// externally, so the live call only ever fires from the explicit confirmed
// click, never automatically.
//
// ctx contract (app.html, T1): { state, kit, wizard, token, goto(stepId),
// persist(), refreshStatus() }. ctx.state.sequence is assumed to carry the
// same shape static/commit.html called `stash` — {digest, sequence, config,
// anchored_blocks} — staged by the (not-yet-ported, T5) timeline step.
// ctx.state.commitDone flags a landed live commit; app.html's own persist()
// only serializes {sequence, commitDone} to sessionStorage, so a live
// commit's full report is kept ONLY in-memory on ctx.state.commitReport —
// it survives step re-entry within the same page load, not a hard refresh.
//
// Same UMD shape as wizard_logic.js/ui_kit.js/timeline_logic.js: pure logic
// (id->source map, error classification, report summarization) is
// node-testable via require(); render() is DOM-only and preview-verified.
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.tdtbSteps = root.tdtbSteps || {};
    root.tdtbSteps['commit'] = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // -- pure: escaping / formatting --------------------------------------------

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // DISPLAY-ONLY 12h formatter (ported verbatim from commit.html). Stored
  // r.start/r.end/m.time stay "HH:MM" 24h — they are the commit/validate
  // payload. Guard passes non-HHMM ("—", blank) through untouched.
  function fmt12(hhmm) {
    if (!/^\d{1,2}:\d{2}$/.test(hhmm)) return hhmm;
    var p = hhmm.split(':'), h = +p[0], mer = h < 12 ? 'AM' : 'PM';
    var h12 = h % 12; if (h12 === 0) h12 = 12;
    return h12 + ':' + p[1] + ' ' + mer;
  }

  // -- pure: id -> source map builder ------------------------------------------
  // Sequence rows carry only {id, start, end, zone} — `source` lives on the
  // digest.assigned / anchored_blocks items the timeline step stashed
  // alongside them (T5/T16 contract: sequence row id == assigned item id,
  // falling back to name; busy/anchored blocks key off id or Block). Build
  // an id -> source lookup once so every staged row gets the same chip the
  // other four views render.
  function itemKey(item) {
    if (!item) return null;
    if (item.id != null) return item.id;
    if (item.name != null) return item.name;
    if (item.Block != null) return item.Block;
    return null;
  }

  function buildSourceMap(stash, kit) {
    var map = {};
    var s = stash || {};
    ((s.digest && s.digest.assigned) || []).forEach(function (a) {
      var k = itemKey(a);
      if (k != null) map[k] = kit.sourceOf(a);
    });
    (s.anchored_blocks || []).forEach(function (b) {
      var k = itemKey(b);
      if (k != null && map[k] == null) map[k] = kit.sourceOf(b);
    });
    return map;
  }

  // String-HTML twin of kit.chipEl (this view builds tables via innerHTML,
  // not DOM nodes) — same SOURCES label/class map, so all five views agree.
  function chipHtml(source, kit) {
    var key = kit.SOURCES[source] ? source : 'vault';
    var s = kit.SOURCES[key];
    return '<span class="chip ' + s.cls + '">' + esc(s.label) + '</span>';
  }

  var CLASS_PILL = {
    'would-create': 'create', 'would-update': 'update', 'no-op': 'noop',
    'conflict': 'conflict', 'unavailable': 'unavailable'
  };

  // -- pure: commit error classification ---------------------------------------
  // kit.kitFetch's rejection message is "<METHOD> <url> -> HTTP <status>[:
  // <detail>]" (ui_kit.js locked decision 6). New backend behaviors this
  // task renders: 409 "live commit already in flight" (transient, retry
  // later) and 422 "plan refused: ..." (G29b blast-radius refusal — lists
  // EVERY unplannable item + a blast-radius line). Both carry their full
  // detail in `detail`; callers must render it whole, not truncate it.
  function parseCommitError(message) {
    var msg = String(message == null ? '' : message);
    var m = /HTTP (\d+)(?::\s*([\s\S]*))?$/.exec(msg);
    if (!m) return { status: null, kind: 'error', detail: msg, message: msg };
    var status = +m[1];
    var detail = (m[2] || '').trim();
    var kind = status === 409 ? 'retry-later' : status === 422 ? 'plan-refused' : 'error';
    return { status: status, kind: kind, detail: detail, message: msg };
  }

  // -- pure: commit report summarization ---------------------------------------
  // orchestrate.run_orchestrated's report: {ok, resumed, today, surfaces,
  // landed, failed, verify_failures}; surfaces[key] = {status, created,
  // updated, noops, note|error, verify_failures}. Reduces to what the
  // compact post-commit summary renders: per-surface landed counts +
  // verify_failures (bake-in-protocol gating signal — never drop these).
  function summarizeReport(report) {
    var r = report || {};
    var surfacesObj = r.surfaces || {};
    var surfaces = Object.keys(surfacesObj).map(function (key) {
      var s = surfacesObj[key] || {};
      return {
        key: key,
        status: s.status || null,
        created: (s.created || []).length,
        updated: (s.updated || []).length,
        noops: (s.noops || []).length,
        note: s.note || s.error || null
      };
    });
    return {
      ok: !!r.ok,
      today: r.today || null,
      resumed: !!r.resumed,
      surfaces: surfaces,
      landedCount: (r.landed || []).length,
      failedCount: (r.failed || []).length,
      failed: r.failed || [],
      verifyFailures: r.verify_failures || []
    };
  }

  // -- DOM: scoped styles (view-specific rules live here per app.html's own
  // top-of-file comment; shared primitives stay in ui_kit.css) --------------
  var STYLE_ID = 'tdtb-commit-step-styles';
  function ensureStyles(doc) {
    if (doc.getElementById(STYLE_ID)) return;
    var style = doc.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '.commit-step .pill { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 11px; font-weight: 600; }',
      '.commit-step .pill.create { background: #dcfce7; color: #166534; }',
      '.commit-step .pill.update { background: #fef3c7; color: #92400e; }',
      '.commit-step .pill.noop { background: #f1f5f9; color: #475569; }',
      '.commit-step .pill.conflict, .commit-step .pill.unavailable { background: #fee2e2; color: #991b1b; }',
      '.commit-step .counts span { margin-right: 0.9rem; font-size: 13px; }',
      '.commit-step .card { border: 1px solid #ccc; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; }',
      '.commit-step .badge { display: inline-block; padding: 2px 9px; border-radius: 5px; font-weight: 600; font-size: 13px; }',
      '.commit-step .badge.ok { background: #dcfce7; color: #166534; }',
      '.commit-step .badge.fail { background: #fee2e2; color: #991b1b; }',
      // Error/warn banners render the FULL backend detail string (409
      // in-flight retry / 422 blast-radius refusal) — pre-wrap, no
      // nowrap/ellipsis, so nothing gets truncated.
      '.commit-step .warnbanner, .commit-step .errbanner { border-radius: 6px; padding: 0.5rem 0.75rem; font-size: 13px; margin-bottom: 1rem; white-space: pre-wrap; }',
      '.commit-step .warnbanner { background: #fef3c7; color: #92400e; border: 1px solid #d97706; }',
      '.commit-step .errbanner { background: #fee2e2; color: #991b1b; border: 1px solid #c0392b; }',
      '.commit-step code { background: rgba(127,127,127,0.15); padding: 0 3px; }',
      '.commit-step button[disabled] { opacity: 0.5; cursor: not-allowed; }',
      '@media (max-width: 480px) { .commit-step table { display: block; overflow-x: auto; white-space: nowrap; } }',
      '@media (prefers-color-scheme: dark) { .commit-step .card { border-color: #444; } .commit-step .pill.noop { background: #334155; color: #cbd5e1; } }'
    ].join('\n');
    doc.head.appendChild(style);
  }

  // -- DOM: staged-plan + shadow/live actions ----------------------------------

  function renderStagedState(el, ctx, doc, stash) {
    var kit = ctx.kit;
    var rows = (stash.sequence && stash.sequence.sequence) || [];
    var sourceMap = buildSourceMap(stash, kit);
    var assignedCount = (stash.digest && (stash.digest.assigned_count != null
      ? stash.digest.assigned_count
      : (stash.digest.assigned || []).length)) || 0;

    var rowsHtml = rows.map(function (r) {
      var src = sourceMap[r.id] || 'vault';
      return '<tr><td>' + esc(r.id) + chipHtml(src, kit) + '</td><td>' +
        esc(fmt12(r.start)) + '</td><td>' + esc(fmt12(r.end)) + '</td><td>' +
        esc(r.zone) + '</td></tr>';
    }).join('');

    el.innerHTML =
      '<p id="commitStatus" class="status">Plan loaded. Preview before committing.</p>' +
      '<div class="card">' +
        '<h2>Staged plan</h2>' +
        '<div><strong>' + rows.length + '</strong> sequenced block(s) &middot; <strong>' +
          assignedCount + '</strong> assigned &middot; valid for ' +
          esc((stash.digest && stash.digest.valid_date) || 'today') + '</div>' +
        '<table><thead><tr><th>Block</th><th>Start</th><th>End</th><th>Zone</th></tr></thead>' +
        '<tbody>' + rowsHtml + '</tbody></table>' +
      '</div>' +
      '<div id="commitActions">' +
        '<button id="shadowBtn" class="btn">Preview (shadow &mdash; no writes)</button> ' +
        '<button id="liveBtn" class="btn" disabled title="Run a shadow preview first">Commit for real (live) &rarr;</button>' +
      '</div>' +
      '<div id="commitErr"></div>' +
      '<div id="shadowPanel" class="card" style="display:none">' +
        '<h2>Shadow preview <span style="font-weight:400;font-size:12px;opacity:0.7">(writes nothing)</span></h2>' +
        '<div id="shadowCounts" class="counts"></div>' +
        '<table id="shadowTable"></table>' +
        '<div id="shadowUnavailable" style="font-size:12px;color:#991b1b"></div>' +
      '</div>';

    var statusEl = el.querySelector('#commitStatus');
    var shadowBtn = el.querySelector('#shadowBtn');
    var liveBtn = el.querySelector('#liveBtn');
    var errEl = el.querySelector('#commitErr');
    var shadowPanel = el.querySelector('#shadowPanel');
    var shadowCountsEl = el.querySelector('#shadowCounts');
    var shadowTableEl = el.querySelector('#shadowTable');
    var shadowUnavailEl = el.querySelector('#shadowUnavailable');

    var previewed = false;

    function commitBody() {
      return { digest: stash.digest, sequence: stash.sequence, config: stash.config || {} };
    }

    function postCommit(mode) {
      return kit.kitFetch('/commit?mode=' + mode, {
        method: 'POST',
        token: ctx.token,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(commitBody())
      });
    }

    function renderShadowInto(diff) {
      var counts = diff.counts || {};
      shadowCountsEl.innerHTML =
        '<span><span class="pill create">' + (counts['would-create'] || 0) + ' create</span></span>' +
        '<span><span class="pill update">' + (counts['would-update'] || 0) + ' update</span></span>' +
        '<span><span class="pill noop">' + (counts['no-op'] || 0) + ' no-op</span></span>' +
        '<span><span class="pill conflict">' + (counts['conflict'] || 0) + ' conflict</span></span>';
      var html = '<thead><tr><th>Step</th><th>System</th><th>Action</th><th>Name</th><th>Time</th><th></th></tr></thead><tbody>';
      (diff.entries || []).forEach(function (e) {
        var m = e.manifest || {};
        html += '<tr><td>' + esc(m.step) + '</td><td>' + esc(m.system) + '</td><td>' +
          esc(m.action) + '</td><td>' + esc(m.name) + '</td><td>' + esc(fmt12(m.time || '—')) + '</td>' +
          '<td><span class="pill ' + (CLASS_PILL[e.classification] || 'noop') + '">' +
          esc(e.classification) + '</span></td></tr>';
      });
      html += '</tbody>';
      shadowTableEl.innerHTML = html;
      var unavail = diff.unavailable_surfaces || [];
      shadowUnavailEl.textContent = unavail.length ? 'Unavailable surfaces: ' + unavail.join(', ') : '';
      shadowPanel.style.display = 'block';
      previewed = true;
      liveBtn.disabled = false;
      liveBtn.title = 'Writes to real surfaces';
    }

    // 409 (in-flight) / 422 (plan-refused) get an endpoint-named red banner
    // that renders the FULL detail string (T6 rule: legibly, not truncated)
    // plus a short human framing; anything else falls back to the raw
    // kitFetch message.
    function renderCommitError(err) {
      var parsed = parseCommitError(err && err.message);
      var label, cls;
      if (parsed.kind === 'retry-later') {
        label = 'Live commit already in flight elsewhere — nothing written here. Wait a moment and retry.';
        cls = 'warnbanner';
      } else if (parsed.kind === 'plan-refused') {
        label = 'Commit refused — nothing was written. Every listed item is blocking the whole commit:';
        cls = 'errbanner';
      } else {
        label = 'Commit failed:';
        cls = 'errbanner';
      }
      errEl.innerHTML = '<div class="' + cls + '"><strong>' + esc(label) + '</strong><br>' +
        esc(parsed.detail || parsed.message || String(err)) + '</div>';
    }

    shadowBtn.addEventListener('click', function () {
      errEl.innerHTML = '';
      statusEl.className = 'status'; statusEl.textContent = 'Running shadow preview…';
      postCommit('shadow')
        .then(function (diff) {
          renderShadowInto(diff);
          statusEl.className = 'status';
          statusEl.textContent = 'Shadow preview complete — nothing written.';
        })
        .catch(function (err) {
          kit.renderError(statusEl, err);
          renderCommitError(err);
        });
    });

    liveBtn.addEventListener('click', function () {
      if (!previewed) return;
      if (!window.confirm('Commit for real? This writes to Todoist, the vault, and the calendar.')) return;
      errEl.innerHTML = '';
      statusEl.className = 'status'; statusEl.textContent = 'Committing (live)…';
      liveBtn.disabled = true;
      postCommit('live')
        .then(function (report) {
          // Post-commit summary state: flip the flag, persist it (app.html's
          // persist() only carries {sequence, commitDone} — the report
          // itself lives only in-memory on ctx.state.commitReport), then
          // render the compact summary in place of the staged-plan view.
          ctx.state.commitDone = true;
          ctx.state.commitReport = report;
          ctx.persist();
          renderPostCommit(el, ctx, doc, report);
        })
        .catch(function (err) {
          renderCommitError(err);
          statusEl.className = 'status';
          statusEl.textContent = 'Live commit failed — plan is still staged, nothing written.';
          liveBtn.disabled = false;
        });
    });
  }

  function renderEmptyState(el, ctx, doc) {
    var p = doc.createElement('p');
    p.className = 'status';
    p.textContent = 'No plan staged. Build and adjust one on the Timeline step, then come back to commit.';
    el.appendChild(p);
    var b = doc.createElement('button');
    b.className = 'btn';
    b.textContent = 'Go to Timeline';
    b.onclick = function () { ctx.goto('timeline'); };
    el.appendChild(b);
  }

  // -- DOM: post-commit summary state -------------------------------------------
  // Fires immediately after a successful live commit (report passed in), and
  // on any re-entry into the commit step while ctx.state.commitDone is set
  // (report read back from ctx.state.commitReport — in-memory only).
  function renderPostCommit(el, ctx, doc, freshReport) {
    var kit = ctx.kit;
    var report = freshReport || ctx.state.commitReport;
    el.innerHTML = '';

    if (!report) {
      // commitDone survived (sessionStorage, via app.html's persist()) but
      // the in-memory report did not (hard page reload). Nothing here
      // re-fires a billed call — POST /commit is not billed in either mode
      // (T6 rule) — so a manual shadow re-fetch to inspect the current diff
      // is fine; it just isn't auto-fired on render (G23 spirit: no
      // surprise network calls on step entry).
      var p = doc.createElement('p');
      p.className = 'status';
      p.textContent = 'Commit already completed earlier this session. The detailed report ' +
        "wasn't kept across a page reload, but the plan is still committed.";
      el.appendChild(p);

      if (ctx.state.sequence) {
        var btn = doc.createElement('button');
        btn.className = 'btn';
        btn.textContent = 'Preview current diff (shadow — no writes)';
        btn.onclick = function () {
          var stash = ctx.state.sequence;
          btn.disabled = true;
          kit.kitFetch('/commit?mode=shadow', {
            method: 'POST',
            token: ctx.token,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ digest: stash.digest, sequence: stash.sequence, config: stash.config || {} })
          }).then(function (diff) {
            renderShadowStandalone(el, doc, diff);
          }).catch(function (err) {
            kit.renderError(p, err);
            btn.disabled = false;
          });
        };
        el.appendChild(btn);
      }
      return;
    }

    var summary = summarizeReport(report);
    var html = '<div class="card">' +
      '<h2>Commit complete</h2>' +
      '<p><span class="badge ' + (summary.ok ? 'ok' : 'fail') + '">' + (summary.ok ? 'OK' : 'FAILED') + '</span> &middot; ' +
      esc(summary.today || '') + (summary.resumed ? ' &middot; resumed' : '') + '</p>' +
      '<table><thead><tr><th>Surface</th><th>Status</th><th>Created</th><th>Updated</th><th>No-op</th><th>Note</th></tr></thead><tbody>';
    summary.surfaces.forEach(function (s) {
      html += '<tr><td>' + esc(s.key) + '</td><td>' + esc(s.status) + '</td><td>' + s.created +
        '</td><td>' + s.updated + '</td><td>' + s.noops + '</td><td>' + esc(s.note || '') + '</td></tr>';
    });
    html += '</tbody></table>' +
      '<p style="font-size:12px">landed: ' + summary.landedCount + ' &middot; failed: ' + summary.failedCount + '</p>';
    if (summary.failedCount) {
      html += '<div class="warnbanner">Failures: ' + esc(summary.failed.join('; ')) +
        '. Re-run with resume to retry only the failed surfaces.</div>';
    }
    if (summary.verifyFailures.length) {
      html += '<div class="errbanner"><strong>Verify failures</strong> (blocks bake-in PASS):<br>' +
        esc(summary.verifyFailures.join('; ')) + '</div>';
    }
    html += '</div>';
    el.innerHTML = html;
  }

  function renderShadowStandalone(el, doc, diff) {
    var counts = diff.counts || {};
    var html = '<div class="card"><h2>Current diff (shadow &mdash; writes nothing)</h2>' +
      '<div class="counts">' +
      '<span class="pill create">' + (counts['would-create'] || 0) + ' create</span> ' +
      '<span class="pill update">' + (counts['would-update'] || 0) + ' update</span> ' +
      '<span class="pill noop">' + (counts['no-op'] || 0) + ' no-op</span> ' +
      '<span class="pill conflict">' + (counts['conflict'] || 0) + ' conflict</span>' +
      '</div><table><thead><tr><th>Step</th><th>System</th><th>Action</th><th>Name</th><th>Time</th><th></th></tr></thead><tbody>';
    (diff.entries || []).forEach(function (e) {
      var m = e.manifest || {};
      html += '<tr><td>' + esc(m.step) + '</td><td>' + esc(m.system) + '</td><td>' +
        esc(m.action) + '</td><td>' + esc(m.name) + '</td><td>' + esc(fmt12(m.time || '—')) + '</td>' +
        '<td><span class="pill ' + (CLASS_PILL[e.classification] || 'noop') + '">' + esc(e.classification) + '</span></td></tr>';
    });
    html += '</tbody></table></div>';
    el.insertAdjacentHTML('beforeend', html);
  }

  // -- render entry point -------------------------------------------------------

  function render(el, ctx) {
    var doc = el.ownerDocument || document;
    ensureStyles(doc);
    el.innerHTML = '';
    if (el.className.indexOf('commit-step') === -1) {
      el.className = (el.className ? el.className + ' ' : '') + 'commit-step';
    }

    var state = ctx.state;
    if (state.commitDone) {
      renderPostCommit(el, ctx, doc, null);
      return;
    }

    var stash = state.sequence;
    if (!stash) {
      renderEmptyState(el, ctx, doc);
      return;
    }

    renderStagedState(el, ctx, doc, stash);
  }

  return {
    render: render,
    _pure: {
      esc: esc,
      fmt12: fmt12,
      itemKey: itemKey,
      buildSourceMap: buildSourceMap,
      chipHtml: chipHtml,
      parseCommitError: parseCommitError,
      summarizeReport: summarizeReport,
      CLASS_PILL: CLASS_PILL
    }
  };
});

// steps/digest.js — TDTB SPA step module (spa-overhaul T3: port of
// digest.html). Contract: register { render(el, ctx) } on window.tdtbSteps.
// render must NEVER auto-fire a billed call (G23) — GET /plan-inputs is the
// free, deterministic single-assembly endpoint (source_counts, digest,
// source_warnings, capacity, habits all ride together); it is NOT the
// token-guarded POST /digest (that returns bare tiers only, no
// warnings/capacity/habits, and is used elsewhere in the pipeline) — using
// it here would silently drop the OVERASSIGNED banner and habits line.
// Same UMD shape as wizard_logic.js/ui_kit.js: pure row/text shaping lives
// in `_pure` (node-tested, tests/js/step_digest.test.mjs); render() is thin
// DOM wiring, preview-verified only.
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else {
    root.tdtbSteps = root.tdtbSteps || {};
    root.tdtbSteps['digest'] = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // -- pure: row shaping (source digest.html renderTable body cells) ----------
  function rowOf(item) {
    item = item || {};
    return {
      name: item.name || '',
      deadline: item.deadline || '',
      urgency: item.urgency != null ? item.urgency : '',
      path: item.path || ''
    };
  }

  function rowsOf(items) { return (items || []).map(rowOf); }

  // -- pure: tier counts --------------------------------------------------------
  function countsOf(data) {
    data = data || {};
    return {
      assigned: data.assigned_count != null ? data.assigned_count : (data.assigned || []).length,
      suggested: (data.suggested || []).length
    };
  }

  // -- pure: status line ----------------------------------------------------
  function statusText(pi) {
    pi = pi || {};
    var data = pi.digest || {};
    var counts = pi.source_counts || {};
    return 'Loaded — valid for ' + (data.valid_date || 'unknown date')
      + ' · sources vault/todoist/calendar: '
      + (counts.vault != null ? counts.vault : '?') + '/'
      + (counts.todoist != null ? counts.todoist : '?') + '/'
      + (counts.calendar != null ? counts.calendar : '?');
  }

  // -- pure: source-degrade banner (locked decision 3: never look like an
  // empty source) ----------------------------------------------------------
  function warningsText(pi) {
    var warnings = (pi && pi.source_warnings) || [];
    return {
      visible: warnings.length > 0,
      text: warnings.length ? ('⚠ ' + warnings.join(' — ')) : ''
    };
  }

  // -- pure: OVERASSIGNED advisory (ui-parity T10) --------------------------
  function overassignedText(pi) {
    var cap = (pi && pi.capacity) || null;
    if (cap && cap.overassigned) {
      return { visible: true, text: 'OVERASSIGNED — ' + cap.remaining + ' (' + cap.legend + ')' };
    }
    return { visible: false, text: '' };
  }

  // -- pure: habits-as-capacity line (ui-parity T10) ------------------------
  // Block count prefers the server-computed capacity.habits (backend-
  // verbatim); falls back to the same client ceil the source view used when
  // capacity is absent — ported verbatim (source: digest.html L119-125),
  // not a new arithmetic path.
  function habitsText(pi) {
    pi = pi || {};
    if (!(pi.habits && pi.habits.total)) return '';
    var cap = pi.capacity || null;
    var blk = cap ? cap.habits : Math.ceil((pi.habits.est_minutes || 0) / 30);
    return 'Habits: ' + pi.habits.done + ' done · '
      + pi.habits.outstanding + ' left (~' + pi.habits.est_minutes + ' min · '
      + blk + ' blk deducted from capacity)';
  }

  var _pure = {
    rowOf: rowOf,
    rowsOf: rowsOf,
    countsOf: countsOf,
    statusText: statusText,
    warningsText: warningsText,
    overassignedText: overassignedText,
    habitsText: habitsText
  };

  // -- DOM: tiered table (browser only; source chip + cost cell on every row,
  // locked decision 3/4) -----------------------------------------------------
  function renderTable(wrap, items, kit) {
    wrap.innerHTML = '';
    if (!items || !items.length) {
      var p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'None.';
      wrap.appendChild(p);
      return;
    }
    var table = document.createElement('table');
    var thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Name</th><th>Cost</th><th>Deadline</th><th>Urgency</th><th>Path</th></tr>';
    table.appendChild(thead);
    var tbody = document.createElement('tbody');
    items.forEach(function (item) {
      var row = rowOf(item);
      var tr = document.createElement('tr');

      var nameTd = document.createElement('td');
      nameTd.textContent = row.name;
      nameTd.appendChild(kit.chipEl(kit.sourceOf(item)));
      tr.appendChild(nameTd);

      var costTd = document.createElement('td');
      costTd.className = 'cost';
      costTd.textContent = kit.costOf(item);
      tr.appendChild(costTd);

      [row.deadline, row.urgency, row.path].forEach(function (val) {
        var td = document.createElement('td');
        td.textContent = val;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  // -- render(el, ctx) ----------------------------------------------------
  function render(el, ctx) {
    var kit = ctx.kit;

    el.innerHTML =
      '<button type="button" id="digest-refresh" class="btn">Refresh</button>' +
      '<div id="digest-status" class="status">Loading…</div>' +
      '<div id="digest-warnings" class="banner-error" style="display:none"></div>' +
      '<div id="digest-overassigned" class="banner-error" style="display:none"></div>' +
      '<div id="digest-habits"></div>' +
      '<h2>Assigned (<span id="digest-assignedCount">0</span>)</h2>' +
      '<div id="digest-assignedWrap"></div>' +
      '<h2>Suggested (<span id="digest-suggestedCount">0</span>)</h2>' +
      '<div id="digest-suggestedWrap"></div>';

    var statusEl = el.querySelector('#digest-status');
    var warnEl = el.querySelector('#digest-warnings');
    var overEl = el.querySelector('#digest-overassigned');
    var habEl = el.querySelector('#digest-habits');
    // source digest.html scopes #habits' muted style via its own <style>
    // block; step modules can't add rules to ui_kit.css, so match it inline.
    habEl.style.fontSize = '0.85rem';
    habEl.style.opacity = '0.75';
    habEl.style.margin = '0.6rem 0';
    var assignedCountEl = el.querySelector('#digest-assignedCount');
    var suggestedCountEl = el.querySelector('#digest-suggestedCount');
    var assignedWrap = el.querySelector('#digest-assignedWrap');
    var suggestedWrap = el.querySelector('#digest-suggestedWrap');

    function load() {
      statusEl.className = 'status';
      statusEl.textContent = 'Loading plan inputs…';

      // GET /plan-inputs is tokenless (deterministic assembly, no cache/
      // run-state write) — same free call source digest.html fires on load.
      kit.kitFetch('/plan-inputs')
        .then(function (pi) {
          var data = pi.digest || {};

          statusEl.textContent = statusText(pi);

          var warn = warningsText(pi);
          warnEl.style.display = warn.visible ? 'block' : 'none';
          warnEl.textContent = warn.text;

          var over = overassignedText(pi);
          overEl.style.display = over.visible ? 'block' : 'none';
          overEl.textContent = over.text;

          habEl.textContent = habitsText(pi);

          var counts = countsOf(data);
          assignedCountEl.textContent = counts.assigned;
          suggestedCountEl.textContent = counts.suggested;
          renderTable(assignedWrap, data.assigned, kit);
          renderTable(suggestedWrap, data.suggested, kit);
        })
        .catch(function (err) { kit.renderError(statusEl, err); });
    }

    el.querySelector('#digest-refresh').addEventListener('click', load);
    load();
  }

  return { render: render, _pure: _pure };
});

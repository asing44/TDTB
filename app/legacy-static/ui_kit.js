// ui_kit.js — shared pure UI helpers for the five TDTB views (ui-revamp T1).
// Same UMD shape as timeline_logic.js: everything except the two thin DOM
// factories is pure and node-testable (tests/js/ui_kit.test.mjs).
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else root.tdtbKit = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // -- source badges ---------------------------------------------------------
  // `source` values in payloads today: 'todoist' | 'calendar' | 'schedulable'
  // | absent ⇒ vault (ui-revamp locked decision 3).
  var SOURCES = {
    vault:       { label: 'vault',    cls: 'chip-vault' },
    todoist:     { label: 'todoist',  cls: 'chip-todoist' },
    calendar:    { label: 'calendar', cls: 'chip-calendar' },
    schedulable: { label: 'sched',    cls: 'chip-schedulable' }
  };

  function sourceOf(item) {
    var s = item && item.source;
    return SOURCES[s] ? s : 'vault';
  }

  // DOM factory (browser only): compact color+text chip, never emoji-only.
  function chipEl(source, doc) {
    var d = doc || document;
    var key = SOURCES[source] ? source : 'vault';
    var span = d.createElement('span');
    span.className = 'chip ' + SOURCES[key].cls;
    span.textContent = SOURCES[key].label;
    return span;
  }

  // -- cost formatting ---------------------------------------------------------
  // Blocks are 30-minute units everywhere; placement rounding lives
  // server-side, so this is display-only math: ceil to match
  // _blocks_of_minutes, no min-1 clamp (0-cost renders as 0).
  function parseDurationMinutes(dur) {
    if (dur == null) return null;
    if (typeof dur === 'number') return dur > 0 ? dur : null;
    var s = String(dur).trim();
    var h = s.match(/(\d+)\s*h/i);
    var m = s.match(/(\d+)\s*m/i);
    if (h || m) return (h ? +h[1] * 60 : 0) + (m ? +m[1] : 0);
    var n = parseFloat(s);
    return !isNaN(n) && n > 0 ? n : null;
  }

  function minutesOf(item) {
    if (!item) return null;
    var min = parseDurationMinutes(item.duration != null ? item.duration : item.Duration);
    if (min == null && item.labels && item.labels.length) {
      // Todoist duration labels ("🍅30min", "🚀10min") are the canonical
      // duration source when the native duration field is null.
      for (var i = 0; i < item.labels.length; i++) {
        var m = String(item.labels[i]).match(/(\d+)\s*min/i);
        if (m) { min = +m[1]; break; }
      }
    }
    return min;
  }

  function blocksOf(item) {
    if (!item) return null;
    if (item.blocks != null && !isNaN(+item.blocks)) return +item.blocks;
    var min = minutesOf(item);
    return min == null ? null : Math.ceil(min / 30);
  }

  function fmtMinutes(m) {
    m = Math.round(m);
    if (m < 60) return m + 'm';
    return m % 60 === 0 ? (m / 60) + 'h' : Math.floor(m / 60) + 'h' + (m % 60) + 'm';
  }

  function fmtDuration(blocks) {
    return fmtMinutes(blocks * 30);
  }

  // "2 blk · 1h" (ui-revamp locked decision 4); null blocks → '' (unknown
  // cost renders as nothing, not a fake 0).
  function fmtCost(blocks) {
    if (blocks == null || isNaN(+blocks)) return '';
    var b = +blocks;
    var bs = String(b).replace(/\.0$/, '');
    return bs + ' blk · ' + fmtDuration(b);
  }

  // Per-item cost: block count from capacity math, but the time part shows the
  // item's TRUE minutes when known — a 5m task reads "1 blk · 5m", not the
  // block-rounded "30m" lie (2026-07-17 LOOTS report).
  function costOf(item) {
    var b = blocksOf(item);
    if (b == null || isNaN(+b)) return '';
    // An explicit blocks override (stepper/retime) is a size decision — the
    // time part follows the blocks, not the stale source minutes.
    if (item && item.blocks != null && !isNaN(+item.blocks)) return fmtCost(b);
    var min = minutesOf(item);
    if (min == null) return fmtCost(b);
    var bs = String(+b).replace(/\.0$/, '');
    return bs + ' blk · ' + fmtMinutes(min);
  }

  // -- fetch helper -------------------------------------------------------------
  // Named-endpoint failures (locked decision 6): every rejection carries
  // "<METHOD> <url> → <reason>" so views can render it verbatim — no dead
  // "Loading…" ends.
  function kitFetch(url, opts) {
    var o = opts || {};
    var method = (o.method || 'GET').toUpperCase();
    if (o.token) {
      o.headers = o.headers || {};
      o.headers['X-TDTB-Token'] = o.token;
      delete o.token;
    }
    return fetch(url, o).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (e) {
          var d = e && e.detail;
          // FastAPI 422s carry object/array details — stringify anything
          // non-string so errors never render "[object Object]".
          var detail = d ? ': ' + (typeof d === 'string' ? d : JSON.stringify(d)) : '';
          throw new Error(method + ' ' + url + ' → HTTP ' + res.status + detail);
        }, function () {
          throw new Error(method + ' ' + url + ' → HTTP ' + res.status);
        });
      }
      return res.json();
    }, function (err) {
      throw new Error(method + ' ' + url + ' → ' + (err && err.message ? err.message : 'network error'));
    });
  }

  // DOM helper (browser only): render a failure into a status element.
  function renderError(el, err) {
    el.textContent = '✗ ' + (err && err.message ? err.message : String(err));
    el.className = 'status error';
  }

  // -- capacity-preview query builder (ui-revamp T3) ---------------------------
  // Pure: turns current Day Setup UI state into the {day_setup, selected}
  // pair GET /capacity-preview expects. No block arithmetic here — durations
  // parse server-side (capacity.py); this only shapes the request.
  function buildCapacityQuery(state) {
    var s = state || {};
    var schedRows = s.schedRows || {};
    var schedulable = {};
    Object.keys(schedRows).forEach(function (k) {
      var r = schedRows[k] || {};
      schedulable[k] = { on: !!r.on, n: r.n };
    });

    var anchored = (s.anchoredRows || []).map(function (r) {
      var row = { id: r.id, on: !!r.on, skip_today: !r.on, time: r.time || null };
      // blocks is omitted unless this row's stepper was actually touched
      // this session (or already carried a persisted override on load) —
      // otherwise the server falls back to the spec's own Duration.
      if (r.blocksChanged) { row.blocks = r.blocks; }
      return row;
    });

    var selected = (s.assigned || [])
      .filter(function (a) { return a && a._included !== false; })
      .map(function (a) { return (a && a.duration != null) ? a.duration : null; });

    return {
      day_setup: {
        anchor: s.anchor || null,
        eod: s.eod || null,
        buffering: s.buffering || null,
        schedulable: schedulable,
        anchored: anchored
      },
      selected: selected
    };
  }

  return {
    SOURCES: SOURCES,
    sourceOf: sourceOf,
    chipEl: chipEl,
    parseDurationMinutes: parseDurationMinutes,
    blocksOf: blocksOf,
    fmtDuration: fmtDuration,
    fmtCost: fmtCost,
    costOf: costOf,
    kitFetch: kitFetch,
    renderError: renderError,
    buildCapacityQuery: buildCapacityQuery
  };
});

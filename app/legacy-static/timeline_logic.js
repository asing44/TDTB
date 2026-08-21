// timeline_logic.js — pure timeline math shared by timeline.html (browser)
// and tests/js/timeline_logic.test.mjs (node --test). No DOM, no fetch, no
// state — everything here is testable in isolation (G23: the manual-seed
// defects shipped because this logic lived untested inside timeline.html).
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) module.exports = factory();
  else root.tdtbLogic = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function toMin(hhmm) { var p = String(hhmm).split(':'); return (+p[0]) * 60 + (+p[1]); }

  function minToHH(m) {
    m = Math.max(0, Math.min(24 * 60 - 1, m));
    var h = Math.floor(m / 60), mm = m % 60;
    return (h < 10 ? '0' : '') + h + ':' + (mm < 10 ? '0' : '') + mm;
  }

  // Start may be "5:45 PM" (config) or "17:45" — reuse to24-style parse.
  function toMin24(v) {
    var s = String(v).trim();
    var ap = s.match(/(\d{1,2}):(\d{2})\s*(AM|PM)/i);
    if (ap) {
      var h = (+ap[1]) % 12 + (/pm/i.test(ap[3]) ? 12 : 0);
      return h * 60 + (+ap[2]);
    }
    var p = s.match(/^(\d{1,2}):(\d{2})$/);
    return p ? (+p[1]) * 60 + (+p[2]) : null;
  }

  function itemMinutes(a) {
    if (a.blocks != null) return Math.max(30, Math.round(a.blocks * 30));
    if (typeof a.duration === 'number' && a.duration > 0) return Math.ceil(a.duration / 30) * 30;
    return 30;
  }

  // Anchored/busy span length in minutes — mirrors _spec_blocks: the
  // Duration field ("30m"/"60m"/int minutes) wins when present (window
  // blocks consume their duration, not their whole Start–End window),
  // else End−Start (calendar busy blocks carry Start/End, never
  // Duration), else 30.
  function anchoredMinutes(b) {
    var m = String(b.Duration == null ? '' : b.Duration).match(/^(\d+)/);
    if (m && +m[1] > 0) return Math.ceil((+m[1]) / 30) * 30;
    var s = b.Start != null ? toMin24(b.Start) : null;
    var e = b.End != null ? toMin24(b.End) : null;
    if (s != null && e != null && e > s) return e - s;
    return 30;
  }

  // Manual-layout seed (LLM-free): anchored blocks land at their configured
  // times (validator passthrough), assigned items first-fit from the anchor
  // around them. Returns the row list; the caller owns state/rendering.
  function seedRows(time, anchored, assigned) {
    var t = time || {};
    var anchor = t.anchor ? toMin(t.anchor) : 8 * 60;
    var busy = [];
    var rows = [];
    (anchored || []).forEach(function (b) {
      if (b.on === false || b.skip_today) return;
      var s = b.Start != null ? toMin24(b.Start) : null;
      if (s == null) return;
      var durMin = anchoredMinutes(b);
      // pre-anchor rows would hard-error as placement-in-past — keep them
      // out of the sequence, but their span still walls off the grid.
      if (s >= anchor) {
        rows.push({ id: String(b.Block || b.id || b.name), start: minToHH(s), end: minToHH(s + durMin) });
      }
      if (s + durMin > anchor) busy.push([Math.max(s, anchor), s + durMin]);
    });
    busy.sort(function (x, y) { return x[0] - y[0]; });
    var midnight = 24 * 60 - 1;
    var eod = t.effective_eod ? toMin(t.effective_eod) : midnight;
    var cur = anchor;
    (assigned || []).forEach(function (a) {
      if (a._excluded) return;
      var need = itemMinutes(a);
      // first-fit: skip over anchored spans
      for (var i = 0; i < busy.length; i++) {
        if (cur < busy[i][1] && cur + need > busy[i][0]) cur = busy[i][1];
      }
      // Every assigned item stays IN the sequence: a missing row is a HARD
      // never-bump error the user can't drag away (it isn't on the board),
      // while past_eod / overflow overlaps are soft. Rows that would run
      // past midnight park in the overflow tail at [EOD, 23:59] — start
      // >= EOD keeps anchored overlaps SOFT (G16), stacked tail rows may
      // overlap each other (validator only checks vs anchored), and the
      // truncated footprint is fine (validate-sequence has no duration
      // check). User drags them into shape.
      if (cur + need > midnight) {
        var s = Math.max(anchor, Math.min(eod, midnight - 1));
        rows.push({ id: a.id, start: minToHH(s), end: minToHH(midnight) });
        return; // cur unchanged — every later item parks in the same tail
      }
      rows.push({ id: a.id, start: minToHH(cur), end: minToHH(cur + need) });
      cur += need;
    });
    return rows;
  }

  // -- ui-revamp T5: chip-data mapping + totals aggregation (pure) -----------
  // Timeline's placed rows (from /sequence or manual-seed) carry only
  // {id,start,end,zone} — the `source` field lives on the digest.assigned
  // items, not the sequence row. These helpers bridge that gap without any
  // DOM/fetch so they're node-testable (the ui_kit.js pattern).

  // id -> assigned item, for O(1) lookup by row.id.
  function assignedIndexById(assigned) {
    var idx = {};
    (assigned || []).forEach(function (a) {
      if (a && a.id != null) idx[a.id] = a;
    });
    return idx;
  }

  // Source chip value for a placed row: looks up the matching assigned item
  // and returns its `source` field (undefined -> ui_kit's sourceOf treats
  // that as vault). Rows with no assigned match (e.g. anchored-block rows
  // from manual-seed) also render as vault, which is correct — they're
  // config-sourced, not todoist/calendar.
  function sourceForId(id, assignedIndex) {
    var a = (assignedIndex || {})[id];
    return a && a.source;
  }

  // Σ blocks placed (ui-revamp T5 locked decision: backend renders this
  // verbatim, never client-recomputed). This is NOT that arithmetic — it
  // only picks which durations to send to GET /capacity-preview's `selected`
  // param: one entry per non-backdrop row that has a matching assigned item.
  // Rows with no assigned match (anchored-block rows) are excluded — their
  // capacity is already counted in the anchored segment, so including them
  // here would double-count. A matched row with no duration passes `null`
  // through (server default: 1 block, same as any other unsized selected
  // item) rather than being dropped, since it IS still a placed item.
  function placedDurations(rows, assignedIndex) {
    return (rows || [])
      .filter(function (r) { return r && !r.backdrop && (assignedIndex || {})[r.id]; })
      .map(function (r) {
        var a = assignedIndex[r.id];
        return a.duration != null ? a.duration : null;
      });
  }

  // Echoes /plan-inputs' persisted day_setup (+ time frame fallback) into the
  // shape ui_kit.js's buildCapacityQuery expects. Timeline has no Day Setup
  // UI of its own, so this is a passthrough, not a live edit: sending the
  // already-persisted values back is a no-op merge server-side, avoiding the
  // /capacity-preview override-with-null footgun (main.py's overrides merge
  // replaces a key whenever it's present, even when the value is null/{}/[]).
  function buildDaySetupEcho(time, daySetup) {
    var t = time || {};
    var ds = daySetup || {};
    var schedRows = {};
    Object.keys(ds.schedulable || {}).forEach(function (k) {
      var r = ds.schedulable[k] || {};
      schedRows[k] = { on: !!r.on, n: r.n };
    });
    var anchoredRows = (ds.anchored || []).map(function (o) {
      return {
        id: o.id,
        on: !!o.on,
        time: o.time || null,
        blocks: o.blocks,
        blocksChanged: o.blocks != null   // echo verbatim only when persisted had a value
      };
    });
    return {
      anchor: ds.anchor || t.anchor || null,
      eod: ds.eod || t.effective_eod || null,
      buffering: ds.buffering || 'minimal',
      schedRows: schedRows,
      anchoredRows: anchoredRows
    };
  }

  return {
    toMin: toMin,
    minToHH: minToHH,
    toMin24: toMin24,
    itemMinutes: itemMinutes,
    anchoredMinutes: anchoredMinutes,
    seedRows: seedRows,
    assignedIndexById: assignedIndexById,
    sourceForId: sourceForId,
    placedDurations: placedDurations,
    buildDaySetupEcho: buildDaySetupEcho
  };
});

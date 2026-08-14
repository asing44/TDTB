// steps/adjust.js — TDTB SPA step module (spa-overhaul T4: port of
// adjust.html). Contract: register { render(el, ctx) } on
// window.tdtbSteps['adjust']; ctx = {state, kit, wizard, token, goto,
// persist, refreshStatus} (app.html T1 shell).
//
// render() NEVER auto-fires a billed call (G23) — POST /adjust only ever
// fires from the explicit Apply click, gated + in-flight-guarded below.
// Budget gate sources GET /billed-ledger via ctx.state.ledger /
// ctx.wizard.budgetSpent — the server's number (G24) — replacing
// adjust.html's old client-side localStorage call counter, which is dead:
// server-side enforcement supersedes it entirely.
//
// Pure logic (ops engine, budget-gate predicate, error classification,
// ops-report shape) lives in the `_pure` export below and is node-tested in
// tests/js/step_adjust.test.mjs, same harness pattern as
// tests/js/wizard_logic.test.mjs (Node's top-level `this` in a CJS module is
// `module.exports`, so `require()` sees the same object this IIFE builds).
(function (root) {
  'use strict';

  // ---------------------------------------------------------------------
  // Pure ops engine — ported verbatim from adjust.html's applyOps/applyOne/
  // findItem/stepOp/clampBlocks (T2/T3/T4 of the retired MVP plan). Only
  // extraction changed: no DOM, no globals, node-testable in isolation.
  // ---------------------------------------------------------------------

  function clampBlocks(n) {
    if (typeof n !== 'number' || isNaN(n)) return null;
    n = Math.round(n * 2) / 2; // snap to nearest 0.5
    return Math.max(0, Math.min(8, n));
  }

  function cloneItem(item) {
    var copy = {};
    for (var k in item) if (Object.prototype.hasOwnProperty.call(item, k)) copy[k] = item[k];
    return copy;
  }

  // Stable identity is the vault path (unique); name is the fallback (also
  // how /adjust ops and button-emitted ops key items). Searches assigned
  // first, then suggested.
  function findItem(state, id) {
    function scan(arr) {
      for (var i = 0; i < arr.length; i++) {
        if (arr[i].name === id || arr[i].path === id) return { item: arr[i], arr: arr, idx: i };
      }
      return null;
    }
    return scan(state.assigned) || scan(state.suggested);
  }

  // ±0.5 stepper press. Auto (no blocks) treats the base as 0 so the first
  // press lands at 0.5; explicit durations floor at 0.5 (a 0-block item is
  // meaningless — reset to auto via Remove+Add).
  function stepOp(item, delta) {
    var base = (item.blocks == null) ? 0 : item.blocks;
    var target = base + delta;
    if (target < 0.5) target = 0.5;
    return { op: 'retime', id: item.name, args: { blocks: target } };
  }

  // Op semantics (today-only, no vault write): deassign/complete flag
  // `_excluded` (reversible, greyed); remove splices the row out; add moves
  // suggested->assigned (or re-includes an excluded assigned row); retime
  // sets a `blocks` duration hint (clamped 0-8 in 0.5 steps). Never throws —
  // an unknown id / malformed op is skipped and reported.
  function applyOne(state, op) {
    if (!op || typeof op.op !== 'string') return { op: op, status: 'skipped', reason: 'malformed op' };
    var id = op.id;
    var args = op.args || {};

    if (op.op === 'add') {
      var loc = findItem(state, id);
      if (!loc) return { op: op, status: 'skipped', reason: 'unknown item' };
      var b = args.blocks != null ? clampBlocks(args.blocks) : null;
      if (loc.arr === state.assigned) {
        loc.item._excluded = false; // re-including an excluded item
        delete loc.item._note;
        if (b != null) loc.item.blocks = b;
        return { op: op, status: 'applied', reason: 'already assigned' };
      }
      loc.arr.splice(loc.idx, 1); // suggested -> assigned
      loc.item._excluded = false;
      delete loc.item._note;
      if (b != null) loc.item.blocks = b;
      state.assigned.push(loc.item);
      return { op: op, status: 'applied' };
    }

    var found = findItem(state, id);
    if (!found) return { op: op, status: 'skipped', reason: 'unknown item' };
    var item = found.item;

    if (op.op === 'retime') {
      var nb = clampBlocks(args.blocks);
      if (nb == null) return { op: op, status: 'skipped', reason: 'retime needs numeric blocks' };
      item.blocks = nb;
      return { op: op, status: 'applied' };
    }
    if (op.op === 'deassign') {
      item._excluded = true;
      delete item._note;
      return { op: op, status: 'applied' };
    }
    if (op.op === 'complete') {
      item._excluded = true;
      item._note = 'excluded — completion NOT recorded anywhere';
      return { op: op, status: 'applied' };
    }
    if (op.op === 'remove') {
      found.arr.splice(found.idx, 1);
      return { op: op, status: 'applied' };
    }
    return { op: op, status: 'skipped', reason: 'unknown op "' + op.op + '"' };
  }

  // Apply a batch; returns a per-op report. Pure — no re-render here (the
  // DOM wiring below re-renders once after calling this).
  function applyOps(state, ops) {
    return (ops || []).map(function (op) { return applyOne(state, op); });
  }

  // -- ops-report shape (rendered in the free-text notice) -------------------
  function summarizeReport(report) {
    if (!report || !report.length) {
      return {
        applied: 0, skipped: 0, level: 'warn',
        text: 'No change — try naming the item (e.g. "drop New Suit").'
      };
    }
    var applied = report.filter(function (r) { return r.status === 'applied'; }).length;
    var skipped = report.length - applied;
    return {
      applied: applied, skipped: skipped, level: skipped ? 'warn' : 'ok',
      text: applied + ' change(s) applied' +
        (skipped ? ', ' + skipped + ' skipped (unknown item)' : '') + '.'
    };
  }

  // -- budget-gate predicate (G24) -------------------------------------------
  // `budgetSpent` is supplied by the caller from ctx.wizard.budgetSpent(
  // ctx.state.ledger) — the server's GET /billed-ledger number, never
  // re-derived here (that would reintroduce the old client-counter drift
  // class this port is explicitly retiring). Priority: an in-flight request
  // wins (can't double-submit), then the hard budget gate, then the softer
  // not-loaded-yet / empty-instruction UX checks.
  function shouldBlockApply(opts) {
    opts = opts || {};
    if (opts.busy) return { blocked: true, reason: 'busy' };
    if (opts.budgetSpent) return { blocked: true, reason: 'budget-spent' };
    if (!opts.hasToken) return { blocked: true, reason: 'not-loaded' };
    if (!opts.instruction || !String(opts.instruction).trim()) return { blocked: true, reason: 'empty-instruction' };
    return { blocked: false, reason: null };
  }

  // -- error classification ---------------------------------------------------
  // kit.kitFetch's rejection carries "METHOD url → HTTP <status>[: detail]"
  // (ui_kit.js, not edited here) — parse the status back out so a 429
  // (budget spent server-side, e.g. a race with another tab) and a 502
  // (judgment/SDK failure) render as distinct states, never one generic red
  // banner.
  function classifyAdjustError(err) {
    var message = (err && err.message) || String(err);
    var m = /HTTP (\d+)/.exec(message);
    var status = m ? +m[1] : null;
    var kind = status === 429 ? 'budget' : status === 502 ? 'judgment' : 'other';
    return { status: status, kind: kind, message: message };
  }

  // -- /adjust request payload -------------------------------------------------
  // The CURRENT working state, so ops resolve against what the user sees
  // (not the original digest). Client-only fields (_excluded/_note) are
  // stripped; blocks rides along so "make it longer" has duration context.
  function buildDigestPayload(state) {
    function clean(list) {
      return (list || []).map(function (it) {
        var o = { name: it.name, path: it.path };
        if (it.types != null) o.types = it.types;
        if (it.urgency != null) o.urgency = it.urgency;
        if (it.deadline != null) o.deadline = it.deadline;
        if (it.blocks != null) o.blocks = it.blocks;
        return o;
      });
    }
    return { valid_date: state.valid_date, assigned: clean(state.assigned), suggested: clean(state.suggested) };
  }

  // Urgency arrives either clean ("2-med") or as a stringified list
  // ("['2-med']") depending on the source note's frontmatter shape.
  // Display-only normalization — never mutates the stored value.
  function urgencyText(u) {
    if (u == null) return '';
    var s = String(u);
    var m = s.match(/([0-9]-[a-z]+)/i);
    return m ? m[1] : s;
  }

  // Per-row cost readout ("2 blk · 1h"). An explicit client-side blocks hint
  // (stepper/retime ops) wins; otherwise falls back to the kit's
  // duration-derived estimate; 'auto' when neither resolves.
  function blocksTextOf(kit, item) {
    if (item.blocks != null) return kit.fmtCost(item.blocks) || 'auto';
    return kit.costOf(item) || 'auto';
  }

  var _pure = {
    clampBlocks: clampBlocks,
    cloneItem: cloneItem,
    findItem: findItem,
    stepOp: stepOp,
    applyOne: applyOne,
    applyOps: applyOps,
    summarizeReport: summarizeReport,
    shouldBlockApply: shouldBlockApply,
    classifyAdjustError: classifyAdjustError,
    buildDigestPayload: buildDigestPayload,
    urgencyText: urgencyText,
    blocksTextOf: blocksTextOf
  };

  // ---------------------------------------------------------------------
  // DOM render (browser only) — thin wiring over the pure engine above.
  // ---------------------------------------------------------------------

  // Scoped styles the shared ui_kit.css doesn't carry (row/action layout
  // was previously page-local `<style>` in adjust.html). Injected once
  // (id-guarded) rather than touching ui_kit.css, which is out of scope for
  // this port.
  var STYLE_ID = 'tdtb-step-adjust-styles';
  function ensureStyles(doc) {
    if (doc.getElementById(STYLE_ID)) return;
    var style = doc.createElement('style');
    style.id = STYLE_ID;
    style.textContent =
      '.adj-toolbar { margin-bottom: 1rem; display: flex; gap: 0.5rem; align-items: center; }\n' +
      '.adj-rows { list-style: none; padding: 0; margin: 0 0 1.5rem; }\n' +
      '.adj-row { display: flex; align-items: center; gap: 0.6rem; padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 13px; }\n' +
      '.adj-row .main { flex: 1 1 auto; min-width: 0; }\n' +
      '.adj-row .name { font-weight: 600; }\n' +
      '.adj-row .meta { color: #777; font-size: 12px; }\n' +
      '.adj-row .dur { flex: 0 0 auto; color: #555; font-variant-numeric: tabular-nums; min-width: 4.5em; text-align: right; }\n' +
      '.adj-row .actions { flex: 0 0 auto; display: flex; gap: 0.25rem; align-items: center; }\n' +
      '.adj-row .actions button { padding: 2px 7px; font-size: 12px; line-height: 1.4; }\n' +
      '.adj-row.excluded { opacity: 0.55; }\n' +
      '.adj-row.excluded .name { text-decoration: line-through; }\n' +
      '.adj-row .note { color: #b26b00; font-size: 12px; }\n' +
      '.adj-ft-counter.budget-spent { font-weight: 600; }\n' +
      '@media (prefers-color-scheme: dark) {\n' +
      '  .adj-row { border-color: #444; }\n' +
      '  .adj-row .meta, .adj-row .dur { color: #aaa; }\n' +
      '  .adj-row .note { color: #e0a955; }\n' +
      '}';
    doc.head.appendChild(style);
  }

  function render(el, ctx) {
    var doc = el.ownerDocument || document;
    var kit = ctx.kit, wizard = ctx.wizard, state = ctx.state;

    ensureStyles(doc);

    // Working copy lives on ctx.state.adjust so Back/Next between steps
    // preserves in-progress edits within this session; only an explicit
    // "Refresh from digest" click re-pulls /plan-inputs. This is also the
    // handoff surface a future Timeline-step port (T5) reads from — mirrors
    // adjust.html's old sessionStorage stash, now in-memory since the SPA
    // never does a full page navigation between steps.
    var local = state.adjust || (state.adjust = {
      valid_date: null, assigned: [], suggested: [], _loaded: false
    });
    var undoSnap = null;
    var busy = false;

    el.innerHTML = '';

    var toolbar = doc.createElement('div');
    toolbar.className = 'adj-toolbar';
    var refreshBtn = doc.createElement('button');
    refreshBtn.type = 'button'; refreshBtn.textContent = 'Refresh from digest';
    var undoBtn = doc.createElement('button');
    undoBtn.type = 'button'; undoBtn.textContent = 'Undo'; undoBtn.disabled = true;
    toolbar.appendChild(refreshBtn);
    toolbar.appendChild(undoBtn);
    el.appendChild(toolbar);

    var statusEl = doc.createElement('div');
    statusEl.className = 'status';
    statusEl.textContent = 'Loading…';
    el.appendChild(statusEl);

    var warnEl = doc.createElement('div');
    warnEl.className = 'banner-error';
    warnEl.style.display = 'none';
    el.appendChild(warnEl);

    var section = doc.createElement('section');
    var label = doc.createElement('label');
    label.textContent = 'Ask for changes';
    label.style.cssText = 'font-size:13px;font-weight:600;display:block;';
    var textarea = doc.createElement('textarea');
    textarea.rows = 2;
    textarea.placeholder = 'e.g. drop the suit, 2 blocks for Rowe’s redesign, add Guitar';
    textarea.style.cssText = 'width:100%;box-sizing:border-box;font-size:13px;font-family:inherit;padding:6px;margin:0.3rem 0;';
    var row = doc.createElement('div');
    row.style.cssText = 'display:flex;gap:0.6rem;align-items:center;flex-wrap:wrap;';
    var submitBtn = doc.createElement('button');
    submitBtn.type = 'button'; submitBtn.textContent = 'Apply (1 billed call)';
    var counterEl = doc.createElement('span');
    counterEl.className = 'adj-ft-counter';
    counterEl.style.fontSize = '12px';
    row.appendChild(submitBtn);
    row.appendChild(counterEl);
    var noticeEl = doc.createElement('div');
    noticeEl.style.cssText = 'font-size:12px;margin-top:0.35rem;min-height:1em;';
    section.appendChild(label);
    section.appendChild(textarea);
    section.appendChild(row);
    section.appendChild(noticeEl);
    el.appendChild(section);

    var h2a = doc.createElement('h2');
    var assignedCountEl = doc.createElement('span');
    assignedCountEl.textContent = '0';
    h2a.appendChild(doc.createTextNode('Assigned ('));
    h2a.appendChild(assignedCountEl);
    h2a.appendChild(doc.createTextNode(')'));
    el.appendChild(h2a);
    var assignedListEl = doc.createElement('ul');
    assignedListEl.className = 'adj-rows';
    el.appendChild(assignedListEl);

    var h2b = doc.createElement('h2');
    var suggestedCountEl = doc.createElement('span');
    suggestedCountEl.textContent = '0';
    h2b.appendChild(doc.createTextNode('Suggested ('));
    h2b.appendChild(suggestedCountEl);
    h2b.appendChild(doc.createTextNode(')'));
    el.appendChild(h2b);
    var suggestedListEl = doc.createElement('ul');
    suggestedListEl.className = 'adj-rows';
    el.appendChild(suggestedListEl);

    // -- row rendering --------------------------------------------------------

    function blocksText(item) { return blocksTextOf(kit, item); }

    function mkBtn(label, title, onClick) {
      var b = doc.createElement('button');
      b.type = 'button';
      b.textContent = label;
      if (title) b.title = title;
      b.addEventListener('click', onClick);
      return b;
    }

    function renderRows(listEl, items, kind) {
      listEl.innerHTML = '';
      if (!items || !items.length) {
        var li0 = doc.createElement('li');
        li0.className = 'empty';
        li0.textContent = 'None.';
        listEl.appendChild(li0);
        return;
      }
      items.forEach(function (item) {
        var li = doc.createElement('li');
        li.className = 'adj-row' + (item._excluded ? ' excluded' : '');
        li.dataset.id = item.path || item.name || '';

        var main = doc.createElement('div');
        main.className = 'main';
        var name = doc.createElement('div');
        name.className = 'name';
        name.textContent = item.name || '(unnamed)';
        name.appendChild(kit.chipEl(kit.sourceOf(item), doc));
        var meta = doc.createElement('div');
        meta.className = 'meta';
        var bits = [];
        if (item.types && item.types.length) bits.push(item.types.join('/'));
        var ut = urgencyText(item.urgency);
        if (ut) bits.push(ut);
        if (item.deadline) bits.push('due ' + item.deadline);
        meta.textContent = bits.join(' · ');
        main.appendChild(name);
        main.appendChild(meta);
        if (item._note) {
          var note = doc.createElement('div');
          note.className = 'note';
          note.textContent = item._note;
          main.appendChild(note);
        }

        var dur = doc.createElement('div');
        dur.className = 'dur step';
        dur.textContent = blocksText(item);

        var actions = doc.createElement('div');
        actions.className = 'actions';

        var pinned = item.source === 'todoist' && item.is_recurring;
        if (pinned) {
          var pin = doc.createElement('span');
          pin.className = 'note';
          pin.textContent = '🔒 pinned · recurring';
          actions.appendChild(pin);
        } else if (kind === 'assigned') {
          if (item._excluded) {
            actions.appendChild(mkBtn('On', 'Include in today’s plan', function () {
              act({ op: 'add', id: item.name, args: {} });
            }));
          } else {
            actions.appendChild(mkBtn('−', 'Shorter (−0.5 block)', function () {
              act(stepOp(item, -0.5));
            }));
            actions.appendChild(mkBtn('+', 'Longer (+0.5 block)', function () {
              act(stepOp(item, 0.5));
            }));
            actions.appendChild(mkBtn('Off', 'Exclude from today (not written to vault)', function () {
              act({ op: 'deassign', id: item.name, args: {} });
            }));
            actions.appendChild(mkBtn('Remove', 'Drop from today’s plan', function () {
              act({ op: 'remove', id: item.name, args: {} });
            }));
          }
        } else {
          actions.appendChild(mkBtn('Add', 'Add to today’s plan', function () {
            act({ op: 'add', id: item.name, args: {} });
          }));
        }

        li.appendChild(main);
        li.appendChild(dur);
        li.appendChild(actions);
        listEl.appendChild(li);
      });
    }

    function renderAll() {
      assignedCountEl.textContent = local.assigned.length;
      suggestedCountEl.textContent = local.suggested.length;
      renderRows(assignedListEl, local.assigned, 'assigned');
      renderRows(suggestedListEl, local.suggested, 'suggested');
    }

    // -- single-level undo ------------------------------------------------
    function snapshot() {
      undoSnap = { assigned: local.assigned.map(cloneItem), suggested: local.suggested.map(cloneItem) };
      undoBtn.disabled = false;
    }

    function act(op) {
      snapshot();
      applyOps(local, [op]);
      renderAll();
    }

    undoBtn.addEventListener('click', function () {
      if (!undoSnap) return;
      local.assigned = undoSnap.assigned;
      local.suggested = undoSnap.suggested;
      undoSnap = null;
      undoBtn.disabled = true;
      renderAll();
    });

    // -- budget gate / counter ---------------------------------------------
    function renderBudgetCounter() {
      var spent = wizard.budgetSpent(state.ledger);
      counterEl.textContent = wizard.budgetLabel(state.ledger) +
        (spent ? ' — free-text budget spent. Buttons above are still free.'
               : ' (free-text is API-billed; buttons above are free)');
      counterEl.style.color = spent ? 'var(--err, #c0392b)' : '#888';
      counterEl.classList.toggle('budget-spent', spent);
      // Don't clobber the button while a request is genuinely in flight —
      // setBusy owns that state and re-applies this gate on release.
      if (submitBtn.textContent !== 'Thinking…') {
        submitBtn.disabled = spent;
        submitBtn.title = spent ? 'Daily free-text budget spent — try again tomorrow' : '';
      }
    }

    // G14-pattern in-flight guard: disable Apply for the request duration so
    // a double-click can't fire two concurrent billed calls.
    function setBusy(v) {
      busy = v;
      textarea.disabled = v;
      submitBtn.textContent = v ? 'Thinking…' : 'Apply (1 billed call)';
      if (v) { submitBtn.disabled = true; } else { renderBudgetCounter(); }
    }

    function loadDigest() {
      statusEl.className = 'status';
      statusEl.textContent = 'Loading plan inputs…';
      kit.kitFetch('/plan-inputs').then(function (pi) {
        var data = pi.digest || {};
        var warnings = pi.source_warnings || [];
        warnEl.style.display = warnings.length ? 'block' : 'none';
        warnEl.textContent = warnings.length ? ('⚠ ' + warnings.join(' — ')) : '';
        local.valid_date = data.valid_date || null;
        // fresh working copies — shallow-clone so client-only fields
        // (_excluded/_note/blocks) never leak into a re-fetch comparison.
        local.assigned = (data.assigned || []).map(cloneItem);
        local.suggested = (data.suggested || []).map(cloneItem);
        local._loaded = true;
        undoSnap = null; undoBtn.disabled = true; // stale after a re-fetch
        statusEl.textContent = 'Loaded — valid for ' + (local.valid_date || 'unknown date') + '.';
        renderAll();
        renderBudgetCounter();
      }).catch(function (err) { kit.renderError(statusEl, err); });
    }

    // -- free-text /adjust (billed call) ------------------------------------
    function submitFreetext() {
      var spent = wizard.budgetSpent(state.ledger);
      var instruction = (textarea.value || '').trim();
      var gate = shouldBlockApply({
        busy: busy, budgetSpent: spent, hasToken: !!ctx.token, instruction: instruction
      });
      if (gate.blocked) {
        if (gate.reason === 'budget-spent') {
          noticeEl.textContent = 'Daily free-text budget spent — button actions above still work.';
          noticeEl.style.color = 'var(--err, #c0392b)';
        } else if (gate.reason === 'not-loaded') {
          noticeEl.textContent = 'Not loaded yet — wait for the digest.';
          noticeEl.style.color = '#a33';
        } else if (gate.reason === 'empty-instruction') {
          noticeEl.textContent = 'Type an ask first.';
          noticeEl.style.color = '#a33';
        }
        renderBudgetCounter();
        return;
      }

      setBusy(true);
      noticeEl.textContent = 'Asking the judgment layer…';
      noticeEl.style.color = '#888';

      kit.kitFetch('/adjust', {
        method: 'POST',
        token: ctx.token,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: instruction, digest: buildDigestPayload(local) })
      }).then(function (plan) {
        var ops = (plan && plan.ops) || [];
        if (!ops.length) {
          var empty = summarizeReport([]);
          noticeEl.textContent = empty.text;
          noticeEl.style.color = '#b26b00';
          setBusy(false);
          return;
        }
        snapshot(); // free-text is undoable too
        var report = applyOps(local, ops);
        renderAll();
        var summary = summarizeReport(report);
        noticeEl.textContent = summary.text;
        noticeEl.style.color = summary.level === 'warn' ? '#b26b00' : '#2a7';
        textarea.value = '';
        setBusy(false);
        ctx.refreshStatus(); // G24: pull the post-charge ledger number
      }).catch(function (err) {
        var cls = classifyAdjustError(err);
        noticeEl.textContent = cls.kind === 'budget'
          ? 'Budget spent server-side: ' + cls.message
          : cls.message;
        noticeEl.style.color = 'var(--err, #c0392b)';
        setBusy(false);
        // A 429 here means another tab/session spent the ledger mid-flight —
        // resync the header bar and this step's gate either way.
        ctx.refreshStatus();
      });
    }

    refreshBtn.addEventListener('click', loadDigest);
    submitBtn.addEventListener('click', submitFreetext);
    // Cmd/Ctrl+Enter submits from the textarea.
    textarea.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); submitFreetext(); }
    });

    // -- boot this step visit -------------------------------------------------
    renderBudgetCounter();
    if (typeof ctx.refreshStatus === 'function') {
      ctx.refreshStatus().then(renderBudgetCounter).catch(function () { /* best-effort */ });
    }
    if (local._loaded) {
      statusEl.textContent = 'Loaded — valid for ' + (local.valid_date || 'unknown date') + '.';
      renderAll();
    } else {
      loadDigest();
    }
  }

  root.tdtbSteps = root.tdtbSteps || {};
  root.tdtbSteps['adjust'] = { render: render, _pure: _pure };
})(typeof self !== 'undefined' ? self : this);

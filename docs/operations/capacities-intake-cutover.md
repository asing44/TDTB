# Capacities Intake Cutover Checklist (U4 S8)

**Status:** NOT PERFORMED. This document is a preparation checklist only. No
machine-local config was edited, no service was restarted, no provider call or
live source write was made, and no cutover has happened. Every step below is an
attended action for the operator.

**Scope:** switching the planning digest between the legacy Capacities read
(`sources.capacities_intake: "legacy"`, the default) and the direct cached
read (`"direct"`). The legacy builder, adapter, and artifact path stay in place
throughout, so the switch is reversible at any time.

**Offline evidence for this checklist:** `app/tests/test_capacities_intake_rollback.py`
exercises the `direct → legacy → direct` flip with local fakes only. It pins
that `direct` serves the published cache and never the live builder or artifact
Capacities rows, that `legacy` restores the original rows and response shape,
that the generation/cache/selection store survive the flip, and that a missing
direct snapshot stays `refresh_required` with no legacy fallback. The tests are
characterization (the behavior already ships); they pass on the pinned source
and each was killed by a mutation that disabled the direct branch.

---

## 1. Prerequisites

Confirm all of the following before touching any config.

- [ ] The intended source revision is built and green: backend pytest passes,
      `npm test`/`npm run typecheck` pass, and both bundles
      (`npm run build:mockup`, `npm run build:prod`) were rebuilt from source
      and read back. The committed served bundle is
      `app/static/cockpit/assets/index-*.js` (referenced by
      `app/static/cockpit/index.html`); the mockup preview is
      `mockups/cockpit/assets/index-*.js`.
- [ ] The `capacities_intake` switch, the offline projector, the selection
      endpoints, and the commit trust boundary have all been reviewed (R-E and
      the standalone S6 adversarial review).
- [ ] A complete direct-refresh generation has been published (section 2).
- [ ] The rollback path is understood: `legacy` is the default, so removing the
      key also rolls back (section 6).
- [ ] The operator is present for the whole window (config edit, restart, and
      any live Refresh are each attended — section 5).

## 2. Cache generation and structure readiness

The direct intake reads only the published cache. It refuses (no rows) unless a
complete generation with a matching structure contract and readable members
exists. Verify readiness read-only:

```bash
curl -fsS http://127.0.0.1:8746/capacities/refresh/status
```

- [ ] `configured` is `true` (a Capacities source record exists).
- [ ] `snapshot.present` is `true` and `snapshot.generation` is non-zero.
- [ ] `snapshot.type_check_times` covers every configured structure.
- [ ] `snapshot.installed_at` is recent enough for the operator's intent.
- [ ] `coverage`/`warnings` are understood. A completed refresh is not
      automatically full coverage.

The direct path's own readiness contract, seen on `GET /plan-inputs` once
`direct` is active, is the additive `capacities_intake` block:

- `state: "ok"` — a complete generation, contract, and readable members; rows
  are served.
- `state: "degraded"` — rows are served with review warnings.
- `state: "refresh_required"` — no generation, no contract, or a damaged
  member; no Capacities rows, and no fallback to legacy or the artifact.
- `state: "not_configured"` — no source record.
- `state: "unavailable"` — warnings and no Capacities rows served.

Do not switch to `direct` while the expected state is `refresh_required` or
`not_configured`. Publish a complete Refresh first (attended, token-guarded,
billed — section 5), then re-check.

## 3. The config switch

The switch is the single key `sources.capacities_intake` in the app config
document (`$TDTB_HOME/config.json` when `TDTB_HOME` is set, otherwise
`~/.config/tdtb/config.json`).

- `"legacy"` (default) — the existing read path, unchanged.
- `"direct"` — the offline cache projection.

Only the exact string `"direct"` selects direct. Any other value (misspelled,
wrong case, a non-string, or an unusable document) resolves to `legacy` and is
reported by the config warning surface, so a typo can never switch intake by
accident.

```json
{
  "version": 1,
  "sources": {
    "mode": "artifact",
    "capacities_intake": "direct"
  }
}
```

- [ ] The edit is made by hand on the operator's machine, never by a test or
      an automated agent.
- [ ] Only `sources.capacities_intake` changes; leave `sources.mode` and every
      other section as the operator intends.
- [ ] The operator confirms there is no in-flight commit (a restart is never
      taken during a commit).

## 4. UI telemetry to confirm after the switch

Once `direct` is active, `GET /plan-inputs` carries the additive
`capacities_intake` block, and the cockpit projects it typed (S7): state,
`generation`, `installed_at`, `type_check_times`, `coverage`, and the
`unassigned_candidates` list (identity, name, review reasons, selected). The
readiness rail prefers the structured block over warning text for coverage, so
a lingering warning cannot read as success.

- [ ] `capacities_intake.mode` is `"direct"` and `state` is `ok` (or a
      knowingly accepted `degraded`).
- [ ] `generation`/`installed_at` match the generation checked in section 2.
- [ ] Any `unassigned_candidates` are expected; a selected candidate is
      promoted to the assigned surface.
- [ ] `legacy` responses have NO `capacities_intake` block and keep today's
      shape.

## 5. Calendar-only Commit safety

Capacities rows are calendar-only. At commit the server admits a Capacities row
only when its index entry is complete, its identity is still in the current
selection store, any review reasons were acknowledged, and no hard exclusion
has caught it since indexing. A row that fails any of these is refused with
`422 commit refused: Capacities rows not admitted: …`, and nothing is written.

- [ ] The Step B plan body omits Capacities rows (they are calendar-only).
- [ ] The commit manifest emits calendar writes only for Capacities rows. **If
      it could emit any non-calendar write for a Capacities row, STOP** (O6
      gate) and do not commit.
- [ ] `POST /commit` is token-guarded and is only ever sent on Adam's explicit
      instruction.

## 6. Rollback

Rollback is a single attended config edit plus restart; nothing is deleted.

- [ ] Set `sources.capacities_intake` back to `"legacy"` (or remove the key —
      the default is `legacy`).
- [ ] Restart the service (attended, section 5) and re-read with GET only.
- [ ] Verify the legacy behavior is restored: `/plan-inputs` has no
      `capacities_intake` block, the legacy rows are served, and the digest
      shape matches the pre-cutover response.
- [ ] Verify the direct cache and selection store are intact (the flip does
      not touch them): the same generation is present, and a stored selection
      is still stored and honored if `direct` is re-selected.

The offline drill in `app/tests/test_capacities_intake_rollback.py` is the
deterministic rehearsal of exactly this path. Run it from the repository root:

```bash
cd app
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_capacities_intake_rollback.py -q
```

## 7. Attended gates (each requires the operator)

- **Config edit** — writing `capacities_intake` is a machine-local source
  change. Attended only.
- **Restart** — restarting the live `:8746` service is an attended deployment
  action. It is allowed only when backend/frontend version skew is the reason,
  never during a commit, and must be followed by GET-only proof of the expected
  contract.
- **Live Refresh/Rescan** — `POST /capacities/refresh/start` is token-guarded,
  billed, and reaches the provider. Attended only.
- **Selections** — `POST /capacities/selections` is token-guarded and writes
  the machine-local selection store. Direct-intake only.
- **Commit** — `POST /commit` is token-guarded and writes live sources.
  Calendar-only for Capacities rows (section 5).

## 8. Cutover record (to be completed at the attended window)

- [ ] Operator and date: ______________________
- [ ] Pre-cutover generation: __________________
- [ ] Config key written: `sources.capacities_intake` = ____________
- [ ] Restart performed (GET-only proof attached): ____________
- [ ] Post-cutover `capacities_intake.state`: ____________
- [ ] Rollback rehearsed or accepted: ____________

No cutover has been performed as of this checklist's creation.

# 2026-10-13 — Preview / Revalidate failure visibility

User-directed, narrow fix so a failing Build preview or Revalidate stops being
silent. Scope is **error visibility only**. This does **not** touch the
staleness/commit gates, does not implement the architect's clock snapshot
identity change, and does not change any backend. The clock-anchor cause of the
user's *actual* stale-plan rejection remains **separately unconfirmed and not
solved** by this change.

## Settled causes (from source)

1. `frontend/src/store/store.ts` — `SHADOW_FAIL` reduced to
   `{ shadowPhase: "none", liveArmed: false }` and dropped `a.error`, so a
   failed preview was indistinguishable from "nothing happened".
2. `frontend/src/store/controller.ts` — `revalidate()` awaited
   `validateSequence` with no `catch`; a rejected call escaped as an unhandled
   promise rejection (callers use `void this.revalidate()`), so the UI showed
   nothing and the plan stayed silently stale.
3. `shadowPreview()`'s fixed-input read was already inside the existing `try`
   (`fixedInputsStillValid()` → `readFixedInputs()`), so it already routed to
   `SHADOW_FAIL`; the visibility fix in (1) is what was missing. Verified by
   reading the controller body — no structural move was required.

## Change

- `AppState` gains `shadowError: string | null`, default `null` in
  `initialState`. The message is defensive-free (it carries the existing
  server/read error text verbatim; no private state is dumped).
- The reducer records the message on `SHADOW_FAIL` and clears it on
  `SHADOW_START`, `SHADOW_OK`, a new plan (`SEQUENCE_OK`), drift invalidation
  (`FINGERPRINT_MISMATCH` and the `SOURCE_REFRESH_OK` drift branch), a stale
  plan edit (`staleShadow` stale branch), and a same-date `SESSION_RESTORED`.
  It is never written to the session blob (`persist.ts`), so a reload never
  restores a transient preview error.
- `alerts()` emits `shadowError` as an `error` alert, which the existing
  `FooterBanners` roll-up already renders as a blocking pill with an on-demand
  detail popover.
- `revalidate()` catches a rejected `validateSequence` and dispatches
  `VALIDATED` with `ok: false` and `hardErrors: ["Revalidation failed: <msg>"]`,
  but **only when the request token is still current** — a superseded rejection
  is ignored exactly like a superseded success. This keeps the plan `dirty`,
  keeps `canLiveCommit` false, and surfaces the exact reason through the same
  alert/approval roll-up as any other hard error.

Every prior gate is retained: source-health, defect acceptance (LD 24),
fingerprint/drift (LD 17), shadow blockers, ARM_LIVE, `canLiveCommit`,
approval-drawer hard errors.

## Non-goals (unchanged)

- No clock snapshot identity change; no change to staleness or commit gates.
- No backend change (`SHADOW_FAIL`/`revalidate` are frontend concerns here).
- No paid call, budget reset, live preview, commit, restart, or source write.

## Verification actually run

Frontend, from `frontend/`:

- TDD red: new store block 6 failed / 50 skipped; new controller block 5 failed
  / 62 skipped (rejections escaped as unhandled `Error`; `shadowError` was
  `undefined`).
- Green focused: `npx vitest run src/store/store.test.ts
  src/store/controller.test.ts` — 2 files, **123 passed**.
- Full suite: `npm test` — **91 files passed, 1 skipped; 1409 passed,
  6 skipped** (baseline before this change was 1398 passed).
- `npm run typecheck` — exit 0.
- `npm run build:mockup` and `npm run build:prod` — both succeeded.
  - `mockups/cockpit/index.html` → `assets/index-BvideELu.js` (302.87 kB) +
    `assets/index-G0kDBygb.css` (unchanged content hash).
  - `app/static/cockpit/index.html` → `assets/index-RCZyyddb.js` (314.86 kB) +
    `assets/index-G0kDBygb.css`.
  - Read-back: both bundles contain the new literal
    `Revalidation failed: ${String(e` (1 occurrence each).
- Existing UI error seam: `npx vitest run src/ui/components.test.tsx -t "alert"`
  — 5 passed. Source integration: `frontend/src/ui/FooterBanners.tsx:87`
  consumes `alerts(s)` into the blocking/warning pills; the existing
  `components.test.tsx` "alerts render as floating pills" test already covers
  the `VALIDATED`-with-`hardErrors` path that a rejected revalidation now uses.

Fixture visual (mockup build, `#sequenced`, static server on `127.0.0.1:8877`,
`window.__tdtb` debug handle; zero provider calls):

- Preview failure driven through the **real controller**: dispatch
  `ACCEPT_DEFECTS`, `adapter.simulateSourceFailure()`, `await
  controller.shadowPreview()` → `shadowError = "calendar source read failed —
  preview/commit blocked"`, `shadowPhase = "none"`. Captured at 900x1000 and
  1500x1000.
- Revalidation failure driven through the **real controller**:
  `adapter.validateSequence` mocked to reject, `await controller.revalidate()`
  → `validation.ok = false`, `hardErrors = ["Revalidation failed: validate
  endpoint down"]`, `seqPhase = "dirty"`. Captured at 900x1000 and 1500x1000.
- Both viewports show the blocking pill (`⛔ 1 blocking`) and the inline red
  bar with the exact message; the footer reads "1 blocking issue — fix rows or
  resquence." and the billed primary action is refused.
- Screenshots: `/tmp/tdtb-preview-errors/shots/{preview,revalidation}-error-{900,1500}.png`.
- Static-server access log shows only same-origin `index.html`/JS/CSS GETs
  (plus a favicon 404) — no provider or billed endpoint was contacted.

## Gaps / not verified here

- The fixture visual is the mockup build, not the live `:8746` runtime.
- The backend `planning.py`/pytest suite was **not** run: a concurrent
  backend-only writer owns `app/planning.py` and its tests.
- The user's actual "immediate stale-plan rejection" cause is **not** confirmed
  fixed; only the discarded preview error and the ignored revalidate rejection
  are now visible. The clock-anchor hypothesis remains open.
- No commit, staging, restart, or live action was performed.

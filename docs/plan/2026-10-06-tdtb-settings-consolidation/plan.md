# Settings consolidation — approved plan

Status: **approved**, variant A. Branch `TDTB/settings-consolidation`.
Supersedes nothing; consolidation only. No settings semantics change.

## Goal

Consolidate the cockpit's proliferating settings-like drawers behind one
settings surface with a consistent shell. The operator chose this goal over
density, hierarchy, and responsive work, and approved **variant A** with
**panel drafts retained in memory**.

## Current state

Render order (`frontend/src/ui/App.tsx`): loading/error guards → Rail → main
(committed banner, ExecutionView, compact CalendarImpact, Queue) → footer
(FooterBanners, ActionDock) → conditional overlays.

| Surface | Gate flag | Openers |
|---|---|---|
| Day setup | `ui.setupOpen` | ActionDock (always visible), ReadinessStrip Setup/Captures chips |
| Capacities settings | `ui.capacitiesSettingsOpen` | ReadinessStrip chip |
| Tag exclusions | `ui.tagExclusionSettingsOpen` | ReadinessStrip chip |
| Commit approval / Results | `ui.approvalOpen` | ActionDock |
| BlockEditor | `ui.editorItem` (+ `editorIntent`) | Queue row |
| AnchoredEditor | `ui.editorAnchor` | retained mount; no production opener remains |

All six use `useDialog` (opener capture, first-control focus, Tab wrap,
Escape close, focus restore) but **not** a shared component shell — each
repeats backdrop and dialog markup. UI patches merge independently, so
multiple settings drawers can currently coexist. The hook's "three surfaces"
comment is stale.

## Categories (confirmed)

- **Settings-like — consolidate:** Day setup, Capacities, Tag exclusions.
  Qualification: Setup is *day-scoped operational configuration*; Capacities
  and tags are *versioned local policies*. Same housing, different species.
- **Safety gate — leave distinct:** Approval is the shadow → arm → live-write
  gate and also renders Results.
- **Contextual editors — leave distinct:** BlockEditor and AnchoredEditor act
  on a specific item.

## Variant A — context-preserving tabbed drawer

```
Cockpit remains visible | Settings                    [x]
                        | Day setup | Capacities | Tags
                        | Selected panel's existing cards
                        | Existing panel-specific actions
```

**Entry/navigation.** Replace the two policy chips with one Settings chip
defaulting to Day setup. Keep the dock/rail Day setup shortcuts; Captures
selects Day setup's Captures section directly. Tabs switch panels without
opening another drawer.

**State/deep links.** `settingsPanel: null | day | capacities | tags`, plus
optional `settingsSection`. Push one owned history entry on open, replace
panel navigation, close via owned-history back or parameter removal.
Back/Forward restores navigation; unknown values open nothing.

**Accessibility.** One named modal, one backdrop, one close control; tablist
with selected state, panel relationships, arrow-key navigation. `useDialog`
stays mounted across switches. Programmatic shortcuts focus the destination
heading; closing restores the original opener. Only active controls enter the
focus trap; the cockpit background is isolated while the modal owns focus.

## Implementation boundaries

- Extract panel content from the three drawers into one `SettingsSurface` and
  `SettingsShell`. Keep existing controller calls, payloads, validation, and
  styles. Do **not** generalize the Approval or editor shells.
- **Preserve distinct save lifecycles.** Setup and Capacities save then close;
  tag exclusions stays open and reports refresh outcome; live micro-adventure
  actions persist immediately. No global Save, no combined transaction.
- One canonical settings destination. Normalize legacy `*Open` writes into it
  and expose one-hot compatibility flags; retain exported drawer facades so
  existing tests stay meaningful. App mounts exactly one settings host.
  Remove compatibility only in a separately reviewed follow-up.
- **URL parameters carry navigation only — never drafts.** Preserve unrelated
  parameters and fixture hashes: `frontend/src/main.tsx` already uses hash
  changes to select/reboot mockup scenarios. Do not use `#settings`.
- Approval and editor state stay independent.

## Do not re-propose

The adopted layout decisions are test-locked. The authoritative list is the
test suites themselves — read them, do not work from this summary:

`locked-contract.test.tsx`, `feedback10`, `feedback13`, `feedback14`,
`feedback15`, `feedback16`, `feedback17`, `feedback18`, `panels`,
`compact-cockpit`, `a11y`, `row-spacing.test.ts`.

Broadly: cockpit composition (assigned-only "Today's work", compact calendar
disclosure, execution before planning after commit); row language and actions
(direct Mark complete / Leave as-is; removal, Delete, Adjust time, Unschedule
behind More); the ±15-minute duration contract; reachability (Day setup always
directly available and primary when unconfirmed; Results keeps its
applicable-state reachability and incomplete-commit danger entry); Setup
content order (Frame → optional Mint sessions → Anchored blocks → Live
micro-adventure → Captures); calendar/approval hierarchy; and the
accessibility/layout floors (named controls, modal semantics, focus
restoration, contrast, reduced motion, 44px targets, row spacing).

Explicitly excluded: a settings-only menu that hides Day setup; a nonmodal
inline accordion replacing its dialog; a wizard that splits or reorders Setup
cards; relocating Results into Settings; restoring retired cockpit surfaces.

**Evidence correction:** FEEDBACK-07 proposed 24-hour display and FEEDBACK-10
repeated the claim, but FEEDBACK-13 and the current tests require **12-hour
AM/PM**. The later contract wins.

## Verification

1. Keep every existing suite passing; do not weaken assertions to accommodate
   consolidation.
2. New tests: one settings host; every shortcut and deep link; invalid URLs;
   Back/Forward; fixture-hash preservation; panel draft lifecycle (retained
   across switches, re-initialized on reopen); reopen from current state;
   async errors/conflicts; unchanged save/refresh behavior; untouched
   approval/editor state.
3. New keyboard tests: focus, Escape, Tab, return-focus for every panel and
   across switching; names, relationships, live announcements, background
   isolation, both themes, reduced motion, existing narrow-width behavior.
4. Backend pytest including bootstrap tests; frontend `npm test`,
   `npm run typecheck`, `npm run build:mockup`, `npm run build:prod`; read back
   emitted HTML/JS/CSS and referenced assets. Mockup fixture mode and
   production API mode stay distinct.
5. Fixture/mocked scratch walkthrough only — zero billed calls, zero real
   source writes. No restart or probe of the live `:8746` service.

## Non-goals

No cockpit-body restyling, density or hierarchy redesign, or responsive
overhaul. No approval/editor changes beyond keeping them distinct. No new
settings values, backend semantics, provider discovery, or global save.

## Decisions recorded

- Variant A approved over variant B (full-screen workspace, ~4–6 days).
- Unsaved drafts retained in memory across panel switches; discarded on shell
  close; reopening re-initializes fresh.

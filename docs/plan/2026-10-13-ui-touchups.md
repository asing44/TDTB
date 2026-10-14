# 2026-10-13 — UI touch-ups (from the mockup audit)

Source: visual audit against `docs/plan/2026-10-09-capacities-refresh-ui/reference/`
(screenshots in `/tmp/tdtb-touchups-20261013/shots/`, after-fix captures in
`/tmp/tdtb-uitouchups/shots/`; /tmp is not durable).

## Changed (frontend only)

1. **More menu overlap (>=1500px)** — the `.qrow__actions .alloc-verb` height cap no
   longer applies to `.row-more__menu` items; detail line no longer collides with Delete.
2. **More tooltip** — `Tooltip` takes a `suppress` prop; the More trigger passes it while
   the menu is open.
3. **Preview writes drawer** — right padding clears the close button; empty state now
   reads "No preview yet. Build one to see the exact writes." with a Build preview button
   (disabled unless the controller allows a shadow preview).
4. **Alert pills** — moved into footer flow so they no longer cover content.
5. **Footer at 900px** — tighter wrap/gaps; Copy prompt and the Day budget chip kept.
   Still three rows in the conflict state, where the "Can't sequence until sources are
   healthy" message adds a line.
8. **Connections inputs / disclose button** — inputs use the shared input styling, spacing
   under the Discover header, `.capacity__disclose` styled as a link button.

## Verification

- `npx vitest run`: 1416 passed | 6 skipped; `npx tsc --noEmit`: clean.
- `npm run build:mockup` and `npm run build:prod` succeed.
- After-fix screenshots viewed for More menu (1500), conflict footer (900) and the empty
  drawer (900). Connections inputs, dark mode and the remaining states were not re-viewed.

## Not changed

- Copy prompt stays (operator request); the budget chip stays, so the budget appears in
  the chip and in the button sub-label.
- Row density at 900px, always-visible row actions, and the P3 drift items are untouched.
- The reported "Exclude today / Ex…" clipping did not reproduce at 1500 or 900.

## Icon row actions (2026-10-13, follow-up)

Operator request: "row button functionality same but with icons instead of words but
still tooltip hover". The row verbs keep every handler, keyboard shortcut, disabled/busy
state, data attribute and wrapper class; only the face changes.

### Changed (frontend only)

- New `frontend/src/ui/RowIcon.tsx`: a dependency-free inline-SVG `RowIcon` set (check,
  eye, eye-off, clock, undo, ellipsis). 16px viewBox 24, stroke-based with
  `stroke="currentColor"`, `aria-hidden`, so the accessible name stays on the button.
- `frontend/src/ui/Queue.tsx`: `StagingVerbs` faces are now `check` (Mark complete) and
  `undo` (Leave as-is); the Exclude/Include toggle is `eye-off`/`eye`; Exact duration is
  `clock`; the More trigger is `ellipsis`. The More-menu items keep their word labels.
  The `aria-label`s, `Tooltip` label+detail, the `suppress` prop, the `qrow__tip--*`
  anchors, and the `planbtn`/`alloc-verb`/`row-more__trigger` classes are unchanged.
- `frontend/src/app.css`: appended square icon-button sizing. Base is 44px square (the
  44px touch floor); at `>=1500px` it is a deliberate 32px square, right-aligned in the
  reserved 500px action track (`justify-content: flex-end`). The sizing is scoped
  `:not(.row-more__item)` so the word-labelled More-menu items keep their stacked height
  (the `.qrow__actions .alloc-verb` CSS trap).

### Protected-test updates (conscious)

- `frontend/src/ui/row-actions.test.tsx`: removed the visible-word assertions for
  Exclude today / Include today / Exact duration and the on-face scope notes; they now
  assert the unchanged `aria-label`, an `svg` face, an empty face text, and that the
  hover/focus tooltip still carries label + detail. Added an icon-face describe block.
- `frontend/src/ui/row-actions-plan.test.ts`: replaced the `>=1500px` 13px / 6px-10px /
  31px sizing pins with 32px-square + 1px border + 6px radius, and added a
  `:not(.row-more__item)` scoping guard and a right-alignment guard.
- `frontend/src/ui/components.test.tsx`: the `/Mark complete/i` `textContent` check now
  asserts the completion button's `aria-label`.
- `frontend/src/ui/feedback10.test.tsx` (More-menu item) is untouched — it still asserts
  the word-labelled items.

### Verification

- `npx vitest run`: 1432 passed | 6 skipped (baseline 1425 | 6; +7 new tests).
- `npx tsc --noEmit`: clean.
- `npm run build:mockup` and `npm run build:prod` succeed.

### Not verified

- Not visually verified: no screenshots or Playwright in this unit. Icon legibility,
  spacing, dark-mode contrast and the `>=1500px` cluster alignment are deferred to the
  visuals unit.

## Footer/header compaction (2026-10-13, follow-up)

Context: item 5 above left the footer three rows tall in the conflict state (the
"Can't sequence until sources are healthy" line added a row), and at 900x1000 the
header wrapped to two rows whenever the source pill read "Sources degraded · commit
blocked". Goal: at most two footer rows in every state at <=1100px and a single-row
header with degraded sources.

### Changed (frontend only)

- `frontend/src/app.css`: appended a new `@media (max-width: 1100px)` block at the end of
  the file (the last such block, so it wins the cascade over the earlier gap-tightening
  layer). The footer controls row and both clusters are `flex-wrap: nowrap` with
  `min-width: 0`, so the left cluster (back link + Day budget chip) and the right cluster
  (Copy prompt, Revalidate, Sequence & commit) stay on one row; `.footer .btn__sub`
  collapses; and `.footer__blocked` becomes a single truncating line (`white-space:
  nowrap`, `overflow: hidden`, `text-overflow: ellipsis`) inside that same row, so it
  never adds a third row. The header `.app-header__right` is nowrap and the pill compacts
  and ellipsizes; Retry and Connections get smaller padding and `white-space: nowrap`.
  No `visibility` hiding and no primary control is hidden; keyboard reachability is
  untouched.
- `frontend/src/ui/Header.tsx`: wraps the pill text in `.health-pill__text` so it can
  ellipsize while the existing `title`/`aria-label` keep the full freshness hint.
- `frontend/src/ui/ActionDock.tsx`: the Sequence & commit sub-label shortens to
  `1 billed call` (the chip already shows `Day budget: n/4 calls left`; the billed-call
  disclosure itself is unchanged). Revalidate gains `title="deterministic · free"` and
  Copy prompt `title="local plan context · export stays Commit-only"`, so the collapsed
  sub-labels keep their full text on the control.
- New `frontend/src/ui/footer-header-compact-1100.test.ts`: CSS-contract tests pinning the
  nowrap clusters, collapsed sub-labels, the truncating status line, the single-row
  header, and the shortened sub-label text.

### Protected-test updates (conscious)

- `frontend/src/ui/footer.test.tsx` (line ~48) and `frontend/src/ui/components.test.tsx`
  (line ~85): the billed-note pin changes from `/1 billed call · 4 left today/` to the
  shortened `"1 billed call"` — the conscious update permitted by the compaction brief.

### Verification

- `npx vitest run`: 1443 passed | 6 skipped (baseline 1432 | 6; +11 new tests).
- `npx tsc --noEmit`: clean.
- `npm run build:mockup` and `npm run build:prod` succeed.

### Not verified

- Not visually verified: no screenshots or Playwright in this unit (root does the
  visuals). The <=1100px one/two-row behavior is asserted against the CSS contract only.

## Polish pass (2026-10-13, follow-up)

Root viewed the 900px renders after the footer/header and anchor-actions units and
reported three remaining defects. This pass fixes them with presentation only: no control
is hidden and no behavior changes.

### Changed (frontend only)

- `frontend/src/app.css` (footer, in the existing last `@media (max-width: 1100px)`
  block):
  - `.footer__back` is `flex: none; white-space: nowrap`, so "← Day setup" no longer
    wraps to two lines.
  - `.footer__blocked` is `flex: 1 1 auto; min-width: 0` (single truncating line kept),
    so the status message owns the leftover width.
  - `.footer__budget` becomes `display: inline-block; flex: 0 6 auto; min-width: 0;
    overflow: hidden; text-overflow: ellipsis`, so the Day budget chip yields its width
    (ellipsizing while keeping `n/4`) before the status truncates.
- `frontend/src/app.css` (header, same block): `.app-header` and `.app-stepper` are
  `flex-wrap: nowrap`; the date block is `min-width: 0` with an ellipsizing frame; step
  padding tightens to `4px 8px` and separators to `10px`. Degraded state is one row at
  900px; the pill ellipsizes while its `title`/`aria-label` keep the full sentence. The
  `@media (max-width: 767px)` block restores wrapping for phones.
- `frontend/src/app.css` (anchors): `.anchor-actions .anchor-action` now mirrors the Plan
  row icon buttons (44px square, `--radius-sm`, the mixed hairline border,
  `--t-surface-2` fill) instead of the old 32px sharp squares. A new `.anchor-tile__head`
  flex row pins the actions at the right end of the chip row above 700px; the
  `@media (max-width: 699px)` block wraps them below the chip.
- `frontend/src/ui/day-setup/AnchorsCard.tsx`: wraps each chip's button + `AnchorActions`
  in `.anchor-tile__head` (the below-chip time control is unchanged).
- `frontend/src/ui/Footer.tsx`: the blocked status span gains `title={blocked}` so the
  full sentence survives truncation.
- Copy prompt and Revalidate already carry `title="local plan context · export stays
  Commit-only"` and `title="deterministic · free"` (`ActionDock.tsx`); verified present,
  so no change was needed.
- New `frontend/src/ui/anchor-actions-icons.test.ts`: CSS-contract tests pinning the row
  icon tokens, the inline chip row, and the <700px wrap.

### Protected-test updates (conscious)

- `frontend/src/ui/footer-header-compact-1100.test.ts`: added three cases — the Day setup
  link / status / budget-chip shrink order, the nowrap header + stepper, and the
  stepper/date compaction. Existing assertions are unchanged.

### Verification

- RED first: the new cases failed (9 failed | 12 passed across the two suites).
- `npx vitest run`: 1462 passed | 6 skipped (baseline 1452 | 6; +10 new tests).
- `npx tsc --noEmit`: clean (exit 0).
- `npm run build:mockup` and `npm run build:prod`: green. The generated
  `mockups/cockpit/assets/index-DaPMF8LM.css` and
  `app/static/cockpit/assets/index-DaPMF8LM.css` both contain `.anchor-tile__head`,
  `.footer__back{white-space:nowrap;flex:none}` and `.app-stepper__sep{width:10px}`.

### Still unverified

- Tooltip hover on the anchor icons (jsdom does not lay out or hover).
- Dark mode for the new hairline borders and fills.
- Touch reachability/overlap at the 44px floor on a real coarse pointer.
- No screenshots or Playwright in this unit (root does the visuals).

# TDTB Cockpit — homepage redesign brief

> Input for a design session. Produced 2026-07-27 10:40 from the live `:8746`
> morning run + Adam's annotated screenshots. Paste this into the design surface,
> design against it, bring the result back to Claude Code for implementation.

## What this screen is

The single page Adam drives his morning from. One pass, once a day, ~10 minutes:

1. **Read** what the day already contains (fixed events, anchored lifestyle
   blocks, habits, minting time).
2. **Notice** what he forgot (overdue, unassigned, deadline-passed).
3. **Allocate** — decide which of ~19 candidate rows get time today, and how
   much (30-min blocks, slider per row).
4. **Sequence** — one billed LLM call places everything.
5. **Review + commit** — read-only placement list, then write to Todoist,
   calendar, and the Obsidian vault.

Steps 1–3 are where he spends the time. Step 3 is the job. **The redesign is
about making step 3 the centre of the page.**

## Current section order (`ui/App.tsx`)

| # | Component | Renders |
|---|---|---|
| 1 | `ReadinessStrip` | date + status chips (Preset, Setup, Captures, Sources, Budget, Theme) |
| 2 | `CapacityHeadline` | "11hr 30min over · 23 blk", segment bar, pie, segment legend |
| 3 | `AlertSummary` | "1 warning" disclosure → "Overassigned" |
| 4 | `ForgotStrip` | YOU MAY HAVE FORGOTTEN — 8 rows + Assign / Not today |
| 5 | `ExecutionView` | "Today" header, work allotment, error banner, NOW / NEXT cards |
| 6 | `PlacementList` | null until a sequence exists |
| 7 | `.allocator > Queue` | capacity bar, NEEDS PLACEMENT (19), the rows |
| 8 | `MobileAgenda` | `display:none` on desktop |
| 9 | `.edit-day > Timeline` | behind a disclosure; deleted at T13 |
| 10 | `ActionDock` | sticky footer — Copy prompt · Auto sequence |

## Problems to solve

1. **Capacity is 600px from allocation.** The pie + segment legend + warning sit
   at position 2; the rows they describe are at position 7. Adam changes a
   slider and the number that moved is off-screen. He wants the capacity read-out
   adjacent to the rows. (A thin "11hr 30min over · 23 blk" bar already sits atop
   the Queue — so the question is what the pie/legend adds, and where.)

2. **Row controls are marooned.** `.qrow__body { flex: 1 }` pushes the slider and
   six buttons to the far right edge. At ≥1280px the name and its own duration
   slider are ~900px apart, with nothing between them. Reads as two unrelated
   columns.

3. **Badges don't encode urgency.** `4-crit` and `vault · project` are visually
   identical pills. The tier logic is correct — `.badge--ucrit` applies a
   `color-mix(#ef4444 22%, surface)` tint — but 22% at 10px is invisible. Adam
   cannot scan for what's critical.

4. **Six identical outline buttons per row.** `⊘ ⤵ 📎 Done Defer Delete`, all
   44×44+ border-only, no grouping, no weight difference between *Done* and
   *Delete*. 19 rows × 6 = 114 identical targets.

5. **NOW / NEXT is dead space pre-sequence.** Before sequencing, NOW reads
   "Clear · No scheduled block" every morning. Adam struck it out entirely.

6. **The error banner has no home.** Runtime-action failures render mid-
   `ExecutionView`, between the allotment line and the NOW card.

7. **Duration is asked twice.** One `BlockEditor` modal serves two buttons:
   **⤵ Place at…** (start-focused, only on unplaced rows) and **✎ Exact
   duration** (5-minute shaping). Because it is one modal it always shows both
   Start and Duration — so placing a row re-asks for the duration its own
   slider already set, one click to the left. Either the modal adapts to its
   caller, or start becomes an inline row control and the modal is only ever
   about fine duration.

## Hard constraints

- **Preact + plain CSS.** No Tailwind, no component library, no CSS-in-JS.
  `app.css` + `tokens.css`, hand-written.
- **Theme-aware, both directions.** Light and dark are equal citizens; a
  `Theme: Auto` chip toggles. Every colour must come from a token.
- **44px minimum tap targets** on every row control — already enforced and
  verified; do not regress it.
- **Mobile 375px matters.** `MobileAgenda` renders there; no horizontal overflow.
- **The Timeline canvas is being deleted** (T13). Do not design around it, and
  do not reintroduce drag-to-place — post-placement correction moves to BusyCal
  by locked decision.
- **Sequencing costs a real billed call**, 5/day. The Auto sequence button must
  stay unmistakable and hard to hit by accident.
- **The row set is ~19 items on a normal day**, up to ~40. Design for scrolling,
  not for 4 rows.

## Design tokens (`frontend/src/tokens.css`)

```
                     LIGHT       DARK
surface              #fdfcf9     #191917
surface-2            #f1efe8     #232320
text                 #2a2a27     #e8e6e0
muted                #5f5e5a     #a3a19a
border               #d9d7cd     #3a3934
badge-neutral-bg     #d3d1c7     #3a3934
badge-neutral-text   #444441     #c9c7c0
```

Segment / semantic colours (shared across themes, used by the pie, the segment
bar, and the row accent stripe):

```
minting   #f59e0b   overflow/overassigned  #ef4444
```

Segment categories, in the order the engine emits them:
**Fixed · Anchored · Habits · Mint · Selected · Static · Live · Unallocated**

## What one row carries

```
Rowe's T-shirt Redesign 2026          [====o-----] 30min   ⊘ ⤵ 📎  Done Defer Delete
vault · project   4-crit   due in 4d
```

- **name** — wraps to two lines before clipping (long vault titles are common)
- **source badge** — `vault · project`, `vault · task`, `todoist`, `vault · press`
- **urgency badge** — `4-crit` / `3-high` / `p1` / `p2` … four tiers, or absent
- **due label** — `due today`, `overdue 4d`, `due in 4d`; three tones
- **accent stripe** — 3px left edge, coloured by source
- **duration slider** — 30-min steps, the primary allocation control
- **⊘** exclude today · **⤵** defer · **📎** pin
- **Done · Defer · Delete** — staging verbs, journaled and undoable

Rows group under **NEEDS PLACEMENT (n)** / **SCHEDULED (n)** / **EXCLUDED TODAY (n)**.

## Success test

Adam opens the page at 07:00, and without scrolling up or hunting:

- sees how overcommitted he is **while** dragging a slider,
- can scan 19 rows and spot the critical ones in one pass,
- can allocate a row's time and dispatch a verb without crossing the screen,
- and reaches Auto sequence knowing the number he's committing to.

## Deliverable wanted back

A static mockup of the page at desktop width — section order, row anatomy,
capacity treatment, button grouping/hierarchy, badge system — in light and dark.
Component boundaries can shift freely; the token palette and the constraints
above cannot.

# ChatGPT handoff — TDTB cockpit homepage mockup

> Paste everything below the line into a fresh ChatGPT conversation. It is
> self-contained: ChatGPT has no access to the repo, the vault, or the running
> app, so nothing here may assume it. Optionally also upload the four annotated
> screenshots — they show the current state and the complaints in situ.
>
> What comes back gets rebuilt in `mockups/cockpit/` against the fixture
> harness, then ported to Preact components.

---

You are designing a redesign of the single screen I use to plan my day. I will
hand your output to an engineer to implement, so it must be buildable, not
merely pretty.

## Deliverable — read this first, it constrains everything else

**One self-contained HTML file.** Inline `<style>`, no build step, no external
requests. It opens in a browser and *is* the mockup.

- **Hand-written plain CSS only.** No Tailwind, no CSS framework, no CDN, no
  utility classes. The real app is Preact + hand-written CSS, and your CSS gets
  ported nearly line-for-line — a utility-class mockup is worthless to me.
- **Use my CSS custom properties verbatim** (defined below), so the port is a
  copy. Introduce new tokens only if you genuinely need them, and list any you
  add in a comment block at the top.
- **Both themes.** Implement light and dark via `@media (prefers-color-scheme: dark)`
  AND a `:root[data-theme="dark"]` override, plus a toggle button in the mockup
  so I can flip between them without changing OS settings.
- **Desktop-first at 1280px**, but must not break at 375px — no horizontal
  scrolling on the page body at any width.
- **Static.** Sliders and buttons need not function. Layout, hierarchy, colour,
  and state rendering are the deliverable. Do show hover/active styling.
- Use the real sample data below, all of it. Do not shorten the list to four
  tidy rows — the whole problem is that this screen carries ~19 rows of
  uneven-length real titles.

If you want to show more than one direction, give me **two or three complete
alternatives in separate HTML files**, each committing fully to its idea. Do
not hedge inside a single layout.

## What this screen is

The one page I drive my morning from. One pass, once a day, about ten minutes:

1. **Read** what the day already contains — fixed calendar events, anchored
   lifestyle blocks (Morning Routine, Lunch, Dinner, Wind Down), habits, and
   "minting" time (my term for discretionary work allotment).
2. **Notice** what I forgot — overdue items, unassigned but critical, passed
   deadlines.
3. **Allocate** — decide which of ~19 candidate rows get time today, and how
   much. Time is counted in 30-minute **blocks**. This is the job.
4. **Sequence** — one expensive LLM call places everything into the day.
5. **Review and commit** — a read-only placement list, then writes go out to
   Todoist, my calendar, and my Obsidian vault.

Steps 1–3 are where I spend the time. **Step 3 is the job. The redesign is
about making step 3 the centre of the page.**

## The current layout, top to bottom

1. **Readiness strip** — date, then status chips: `Preset Workday · automatic`,
   `Setup ✓`, `Captures 3/3`, `Sources ✓ ↻`, `Budget 4/4`, `Theme: Light`.
2. **Capacity headline** — `⚠ 11hr 30min over · 23 blk`, `49 / 26 blk`, a
   horizontal segment bar, a pie chart, and a segment legend.
3. **Alert summary** — a `1 warning ▴` disclosure opening to `Overassigned`.
4. **Forgot strip** — "YOU MAY HAVE FORGOTTEN", 8 rows, each with `Not today`
   and sometimes `Assign`.
5. **Execution view** — a "Today" header, `Work allotment · 0min used · 4h
   remaining`, an error banner slot, then two big cards: **NOW** and **NEXT**.
6. **Placement list** — empty until the day has been sequenced.
7. **The allocator** — a thin capacity bar, then `NEEDS PLACEMENT (19)` and the
   rows. Then `SCHEDULED (0)` and `EXCLUDED TODAY (0)`.
8. **Action dock** — sticky footer: `Copy prompt` and a green
   `Auto sequence · 1 billed call · 4 left today`.

## The seven problems to solve

1. **Capacity is 600px away from allocation.** The pie and legend sit at
   position 2; the rows they describe are at position 7. I move a slider and
   the number that changed is off-screen. The capacity read-out belongs where I
   am actually allocating. (A thin `11hr 30min over · 23 blk` bar already sits
   directly above the rows — so decide what the pie and legend genuinely add,
   and where they earn their place. Deleting them is a legitimate answer if
   the segment bar carries the same information.)

2. **Row controls are marooned.** The row name is flush left; the slider and
   six buttons are pinned flush right. At 1280px+ that is a ~900px gap with
   nothing in it. It reads as two unrelated columns rather than one row.

3. **Urgency is invisible.** `4-crit` and `vault · project` render as
   identical grey pills. There is a red tint applied to critical, but at ~22%
   opacity on a 10px pill it cannot be seen. I cannot scan 19 rows and find
   what is critical. Urgency has four tiers: crit, high, med, low.

4. **Six identical outline buttons per row.** `⊘  ⤵  📎  Done  Defer  Delete`
   — all the same weight, same border, no grouping, `Done` indistinguishable
   from `Delete`. Nineteen rows × six = 114 identical targets. They need
   grouping and hierarchy; destructive actions need to look destructive.

5. **NOW / NEXT is dead space.** Before sequencing — which is when I am
   actually using this screen — NOW always reads "Clear · No scheduled block",
   because nothing is placed yet. Two large cards saying nothing.

6. **The error banner has no home.** Runtime failures currently render in the
   middle of the execution view, between the allotment line and the NOW card.
   Errors need a defined, consistent place.

7. **Duration is asked for twice.** Each row has a duration slider. But
   clicking ⤵ ("Place at…") opens a modal asking for **both** Start *and*
   Duration — re-asking the duration the slider one click to the left already
   set. The same modal is also opened by ✎ ("Exact duration"), where duration
   genuinely is the point. One modal serving two jobs, so it always shows both
   fields. Solve this: either the modal adapts to which button opened it, or
   start-time becomes an inline row control and the modal is only ever about
   fine-grained duration.

## Hard constraints — these are not design choices

- **Preact + hand-written CSS.** No component library.
- **44×44px minimum tap target** on every row control. Currently met exactly;
  do not regress it. Chips in the header strip are currently ~20px tall, which
  is a real problem at 375px — fixing that is in scope.
- **~19 rows on a normal day, up to ~40.** Design for scrolling and scanning,
  not for a tidy short list.
- **No drag-to-place, ever.** A timeline canvas with drag-and-drop is being
  deleted from this app on purpose. Corrections after placement happen in a
  separate calendar app. Do not reintroduce dragging, a timeline, or a
  calendar grid.
- **Sequencing costs real money** — 5 calls per day, hard limit. The
  `Auto sequence` button must stay unmistakable and must be hard to press by
  accident.
- **Rows come from two sources**, and which one matters to me: my Obsidian
  vault, and Todoist.

## Design tokens — use these exact custom properties

```css
:root {
  --t-surface:            #fdfcf9;
  --t-surface-2:          #f1efe8;
  --t-text:               #2a2a27;
  --t-muted:              #5f5e5a;
  --t-border:             #d9d7cd;
  --t-badge-neutral-bg:   #d3d1c7;
  --t-badge-neutral-text: #444441;
}
:root[data-theme="dark"] {
  --t-surface:            #191917;
  --t-surface-2:          #232320;
  --t-text:               #e8e6e0;
  --t-muted:              #a3a19a;
  --t-border:             #3a3934;
  --t-badge-neutral-bg:   #3a3934;
  --t-badge-neutral-text: #c9c7c0;
}
/* semantic, shared across themes */
--c-minting:  #f59e0b;   /* discretionary work time */
--c-overflow: #ef4444;   /* overassigned / overdue */
```

The palette is warm off-white and warm near-black. Keep it. Accent colour is
used sparingly and meaningfully — it is not decoration.

Capacity segment categories, in engine order:
**Fixed · Anchored · Habits · Mint · Selected · Static · Live · Unallocated**

## Anatomy of one row

Every row carries, at minimum:

- **name** — real titles are long and wrap to two lines before clipping
- **source badge** — `vault · project`, `vault · task`, `vault · interval`,
  `vault · press`, or `todoist`
- **urgency badge** — `4-crit` / `3-high` / `2-med` / `1-low` for vault rows,
  `p1` / `p2` / `p3` / `p4` for Todoist rows. Sometimes absent.
- **due label** — `due today`, `due in 4d`, `overdue 4d`. Three tones.
- **a 3px coloured accent stripe** on the left edge, keyed to source
- **duration slider** — 30-minute steps, the primary allocation control,
  showing its value (`30min`, `1hr 30min`)
- **⊘** exclude today · **⤵** place at… · **✎** exact duration
- **Done · Defer · Delete** — these hit real systems and are undoable

Rows group under three headings: `NEEDS PLACEMENT (19)`, `SCHEDULED (0)`,
`EXCLUDED TODAY (0)`.

## Real data — use all of it

Header: date `2026-07-27`. Chips: `Preset Workday · automatic`, `Setup ✓`,
`Captures 3/3`, `Sources ✓ ↻`, `Budget 4/4`, `Theme: Light`.

Capacity: **`11hr 30min over · 23 blk`**, `49 / 26 blk`. Segments —
Fixed 6 blk · Anchored 9 blk · Habits 4 blk · Mint 8 blk · Selected 22 blk.
One warning: `Overassigned — 11hr 30min over · 23 blk`.

Forgot strip:

| Item | Note | Buttons |
|---|---|---|
| Payments | 4-crit and unassigned | Assign · Not today |
| Press | deadline 2026-07-26 has passed | Assign · Not today |
| Professional Development | assigned but deadline 2026-07-23 has passed | Not today |
| Reading | assigned but deadline 2026-07-25 has passed | Not today |
| Release hours | assigned but deadline 2026-07-24 has passed | Not today |
| Run PA.8 RUN-2 battery early in fresh cap window | assigned but deadline 2026-07-25 has passed | Not today |
| Stillness | assigned but deadline 2026-07-21 has passed | Not today |

Plus three read-only capture lines: `Intention — follow night time routine`,
`For Meegy — Bathrooms`, `Stoic — Negative Visualization`.

NEEDS PLACEMENT rows (all currently 30min):

| Name | Source | Urgency | Due |
|---|---|---|---|
| Clean bathrooms | todoist | p1 | due today |
| Rowe's T-shirt Redesign 2026 | vault · project | 4-crit | due in 4d |
| Braindump on professional goals currently | vault · task | 4-crit | — |
| Career Ops Pipeline | vault · project | 4-crit | — |
| Institute WALL·E-OS timeblock everyday again | vault · task | 4-crit | — |
| Semi weekly reviewing should make me answer the question | vault · task | 4-crit | — |
| Professional Development | vault · interval | 3-high | overdue 4d |
| Release hours | todoist | p2 | overdue 3d |
| Run PA.8 RUN-2 battery early in fresh cap window | todoist | p2 | overdue 2d |
| Finish stalled plans via Claude-side handoffs | vault · task | 3-high | — |
| walle-mini: replicate grep MCP (both harness entries) | todoist | p3 | overdue 2d |
| Cancel subscription | todoist | p4 | due today |
| On walle-mini: git pull + seat-bootstrap.sh to get tdtb-restart | todoist | p4 | due today |
| Weigh self | todoist · ⚡10min | p4 | due today |

Footer: `Setup confirmed. Sequence the day when ready.` · `Copy prompt / paste
into any LLM · fallback` · green `Auto sequence · 1 billed call · 4 left today`.

## The test your design has to pass

I open this at 07:00 and, without scrolling up or hunting:

- I see how overcommitted I am **while** I am dragging a slider;
- I can scan 19 rows and spot the critical ones in a single pass;
- I can set a row's time and fire an action on it without crossing the screen;
- I reach `Auto sequence` knowing exactly what number I am committing to.

## Before you write code

Tell me in a short paragraph what your central structural move is and what you
are cutting — I would rather hear "the pie goes, the segment bar absorbs it,
and capacity becomes a sticky element above the rows" than get a file and
reverse-engineer the reasoning. Then give me the HTML.

Do not ask me clarifying questions first. Make the call, state the assumption,
and show me. I will react to something concrete.

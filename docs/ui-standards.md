# UI standards

For the owner and Claude desktop design mode. Colours and spacing live in a few
central files; change them there, not in components.

## Where things live

| File | Owns |
| --- | --- |
| `frontend/src/origin-tokens.css` | Every chip colour: origin chips and Todoist label chips (light and dark) |
| `frontend/src/tokens.css` | The theme: surfaces, text, state colours, and the shared Lanes tokens (`--t-*`, `--lane-*`) |
| `frontend/src/app.css` | The "Lanes · Set up" block, scoped `.cockpit:has(.day-setup-screen)`; consumes the Lanes tokens |
| `frontend/src/ui/settings-hub.css` | Settings hub spacing: `--hub-gap`, `--hub-pad` |

## Change a chip colour

- Edit ONE custom property in `origin-tokens.css`, for example `--c-cap-task`.
- Change it in BOTH themes: the light block and the dark blocks (system dark and forced dark).
- Ink must stay at least 4.5:1 on the chip tint; `origin-chip.test.tsx` checks every token.

## Chip names

- `origin-chip--td-<name>`: Todoist project chip. `<name>` is the Todoist colour name (`berry_red`); the token is `--c-td-berry-red`.
- `label-chip--td-<name>`: Todoist label chip (10min, Hour, Habits...) in that label's own Todoist colour.
- `origin-chip--cap-<slug>`: Capacities object type. Slug is the lowercased, singular title with dashes (`Research Notes` becomes `research-note`; `Horticos` becomes `hortico`).
- `origin-chip--cap-h1` to `h8`: hash fallback for a Capacities type with no named token.
- Unknown or missing colours fall back to the neutral chip or plain tag styling.

## Specificity rule

- Colour classes set only `--chip-c`.
- The shared base rules (`.origin-chip[data-origin]` in `app.css`, `.qrow__tag.label-chip` in `origin-tokens.css`) must NOT declare `--chip-c`.
- A base rule that sets it out-specifies every colour class and turns all chips grey. A test pins this.

## Sending a design-mode edit back

- Paste the changed CSS (or a diff) to the assistant.
- The assistant ports it into the central files above, runs both test suites, rebuilds the bundle and screenshots the result.
- Do not hand-edit `app.css` for chip colours; the duplicate would be removed.

## Capacities type colours

Light value shown; the dark value is the same hue lightened 30%.

| Type | Hue | Token | Light |
| --- | --- | --- | --- |
| Task | red | `--c-cap-task` | `#dc2626` |
| Event, Day, Week, Month | blue | `--c-cap-event / day / week / month` | `#3b78e0` |
| Project | violet | `--c-cap-project` | `#7c3aed` |
| Print | magenta-violet | `--c-cap-print` | `#a63fd1` |
| Press | pink-magenta | `--c-cap-press` | `#d1349e` |
| Continual | purple | `--c-cap-continual` | `#9333ea` |
| Adventure, Trip | violet | `--c-cap-adventure / trip` | `#8b5cf6` |
| Review | slate-blue | `--c-cap-review` | `#5b73a8` |
| Meeting | blue-violet | `--c-cap-meeting` | `#5b5fdb` |
| Birthday | blue | `--c-cap-birthday` | `#2f7fd6` |
| Hortico | lime-green | `--c-cap-hortico` | `#5fae1f` |
| Place | lime | `--c-cap-place` | `#84a60f` |
| Purchase | amber | `--c-cap-purchase` | `#d99a06` |
| Peep | green | `--c-cap-peep` | `#22a05a` |
| Entity | teal-green | `--c-cap-entity` | `#14a37a` |
| Read, Film, Research Note, Atomic Note, Definition | teal-green | `--c-cap-read / film / research-note / atomic-note / definition` | `#109b84` |
| Fleeting | teal-blue | `--c-cap-fleeting` | `#0e90b0` |
| Recipe | pink | `--c-cap-recipe` | `#e0508a` |
| Area | neutral grey | `--c-cap-area` | `#7a7f87` |
| Weblink | teal | `--c-cap-weblink` | `#0f9aa8` |
| Page | green | `--c-cap-page` | `#3aa04a` |
| Whiteboard, Tag, Query | grey | `--c-cap-whiteboard / tag / query` | `#858a92` |

## Lanes look (replaces the old locked rules)

The old rules (no shadows, weights 400/500 only, small radii) no longer apply. Lanes, the owner's design, governs the Set up screen now and Plan later.

- Cards: white surface, 16px radius, one soft shadow (`--t-card-shadow`), hairline `--t-hairline`. No other shadows except the segmented-control thumb (`--t-seg-shadow`).
- Radii: card 16px, track 10px, block 7px, pills fully round (`--t-card-radius`, `--t-track-radius`, `--t-block-radius`, `--t-pill-radius`).
- Weights: 400/500 for body, 600/700 for headings, lane labels and emphasis.
- Controls are pills with a `--t-control-border`; segmented controls sit on `--t-seg` with a `--t-seg-on` thumb.
- Each lane has an ink, tint and block triple: Calendar (`--lane-cal-*`), Anchors (`--lane-anchor-*`), Mint (`--lane-mint-*`), Tasks (`--lane-task-*`), plus the habit pair (`--lane-habit*`).
- Dark is derived from light: every token has a dark value in `tokens.css` (system dark and forced dark). Text stays at least 4.5:1.
- Still no gradients. Change a Lanes colour in `tokens.css` only, in both themes.

## Plan look

The Plan screen reuses the Lanes tokens; it adds no new colours.

- Each lane group (Critical, Fixed, When there's room, Not in plan) is a tinted card with a 4px left rule and a 12px radius. Rows inside are white pill-control cards.
- Lane colours come from the ink / tint / bar tokens: `--lane-crit-*` (Critical, over-capacity ink and the overflow hatch), `--lane-cal-*` (Fixed), `--lane-task-*` (When there's room), `--lane-anchor-*` (Not in plan).
- Everything is scoped by `.cockpit:has(.queue)`, in the Plan block at the end of `app.css`; Set up stays on `.cockpit:has(.day-setup-screen)`.
- The duration pill is `[- 1hr 30min +]` in a 140px column above 1000px; below that the steppers drop out and the readout stands alone.
- Dark over-capacity text uses `--lane-crit-ink` (`plan-dark-contrast.css`); `--c-overflow` is under 4.5:1 on dark cards.

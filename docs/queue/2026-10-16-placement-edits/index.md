# Placement edits — issue draft queue (2026-10-16)

**All nine files here are DRAFT, not published. None of these was published. No GitHub issue was created, and no `gh` command was run.**

Purpose: the operator reviews these drafts and decides what to publish. Publishing is a separate, explicitly approved action under `docs/agents/issue-tracker.md`.

Order is the operator's order from this session, not a priority ranking.

## Ordered table

| # | Title | File | One-line summary | Dependency notes |
| - | ----- | ---- | ---------------- | ---------------- |
| 01 | Source health: treat generation-freshness warnings as advisory at the served row level | `01-source-health-advisory.md` | A Capacities generation-freshness warning degrades all sources and hard-locks commit; make it advisory and scope dots per source. | None. |
| 02 | Approval drawer: offer resequence/regenerate instead of an inert Retry on deterministic commit refusals | `02-retry-preview-resequence.md` | Deterministic 422 refusals show `Retry preview`, which resends an identical sequence and can never succeed. | None. |
| 03 | Overflow parking must respect anchored spans and never stack rows on anchors | `03-overflow-respects-anchors.md` | Overflow rows were stacked on anchors on 2026-10-16; anchors are not shareable and overflow must skip anchor spans. | None; diagnosis (overflow re-seating vs model placement) comes first. |
| 04 | Show a visible overlap note on rows that share time with another row | `04-overlap-note-on-rows.md` | Rows sharing time need a note naming the other row and why the overlap was explicitly granted. | 06 depends on 04's note. |
| 05 | Plan tasks: add a per-row "Keep all-day" action for date-only tasks | `05-keep-all-day-action.md` | No rule keeps date-only tasks all-day; add an explicit per-row action on the existing all-day path. | None. |
| 06 | Plan tasks: add a per-row "May overlap Mint" toggle with server-side validation | `06-may-overlap-mint-toggle.md` | Mint is a hard wall; only toggle-granted rows may overlap it, with the overlap shown explicitly. | Depends on 04. |
| 07 | Detect and display drift between committed blocks and current Calendar state | `07-drift-detection.md` | TDTB cannot see operator Calendar edits after commit; show moved/missing/added, change nothing. | None. |
| 08 | Fix capacities-assigned rule: reference assignedOn instead of the non-existent assigned property | `08-capacities-assigned-rule.md` | The rule referenced a property that does not exist and matched zero objects; includes a separate machine-local operator step. | None; repo part plus operator step. |

## Decisions log (settled by the operator this session)

- 01: generation-freshness warnings are advisory where the rows are served; per-source dots are scoped to their own source; the commit gate stays fail-closed for real read failures.
- 02: offer a resequence/regenerate affordance on deterministic refusals; transient transport failures keep Retry.
- 03: anchors are NOT shareable; overflow rows fill free gaps and skip anchor spans; what does not fit goes past end of day with a visible warning; never-bump (every item still placed).
- 04: show the overlap note naming the other row and why; applies to explicit grants only, plus Mint per-row grants from 06.
- 05: a per-row `Keep all-day` action in Plan tasks; not a rule and not a label.
- 06: only tasks with a per-row `May overlap Mint` toggle may overlap Mint; not by label, size, or rule; Mint stays a hard wall otherwise; the overlap must be shown explicitly.
- 07: detect and show drift only; adopt nothing, write nothing to Todoist or Calendar.
- 08: update the rule to `assignedOn`; op and values still to be decided; starter file is the repo part, machine-local file is an operator step.

Two decisions reverse or supersede earlier approved behavior: 03 reverses overflow exemption from anchor overlap (commit `4add629` era), stated explicitly in that draft.

## NOT queued

- Publishing any of these drafts to GitHub. Forbidden this session; separate approval required.
- Implementing any of the eight edits. These are drafts only.
- The `~/.config/tdtb/producer-rules.json` machine-local edit as a repo issue; it is an operator step listed inside 08.
- The refresh `staleConfiguration` discard behavior of 2026-10-16 (a complete 236-read result was discarded, so Purchase/Print stayed out of generation 22). It is cited as evidence in 01; no issue was drafted for it. (inferred)
- The backdrop allow-set fix itself, already landed on main at `77b2c39`; it appears in 02 only as the example refusal's resolution.

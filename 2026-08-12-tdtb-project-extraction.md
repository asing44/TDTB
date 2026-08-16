---
plan_schema: 2
plan_id: "2026-08-12-tdtb-project-extraction"
approval: superseded
execution: completed
created: "2026-08-12"
updated: "2026-08-15"
completed_date: "2026-08-15"
parent_plan: "2026-08-09-tdtb-planning-ui-reliability"
blocker_reason: "Historical governance BLOCKED state resolved and superseded: B (2026-08-09-tdtb-planning-ui-reliability) reached terminal approval=superseded at T3; A executed T1-T11 and closed with the attended cutover restart and GET-only proof at T11. Authoritative record: docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml (T1-T11 completed, closure.declared_complete=true at 2026-08-15T02:52:00Z)."
superseded_by: "docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml"
body_contract: 1
plan_lineage: {revision: 2, replan_count: 2, max_replans: 2}
---
# TDTB Project Extraction from Claudius

## Purpose / observable outcome

Move the TDTB application and project boundary out of the Claudius repository
and into the sibling `../Projects/TDTB` repository. On this seat the intended
target is `/Users/walle-mini/Repos/Projects/TDTB`; the exact target must be
resolved per seat before execution rather than inferred from an arbitrary
worktree's current directory.

The standalone repository owns the FastAPI/Preact application, its project
policy, app-specific documentation, launchd template, restart script, and
app-specific tests. Claudius continues to own shared TDTB skills, scheduled
rituals, historical plans/reports/references, and cross-repository integration
checks.

The migration has an explicit two-pass reference policy:

1. **Before extraction:** run a targeted inventory of hardcoded paths and
   ownership boundaries. Do not use a noisy full-repository `tdtb` grep as the
   pre-move inventory.
2. **After extraction:** run a broad `tdtb` sweep across the remaining Claudius
   repository and triage every result as intentional cross-repo wiring,
   historical/archive evidence, or broken/orphaned reference.

No live restart, deployment, billed judgment call, live commit, or source write
is authorized by this plan. The existing single-writer `com.walle.tdtb`
cutover is a separate attended action after the new repository is verified.

## Progress

> **Status: COMPLETED / SUPERSEDED (2026-08-15).** The authoritative execution record is
> [docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml](docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml)
> (T1-T11 completed; `closure.declared_complete: true` at `2026-08-15T02:52:00Z`), with the
> attended cutover restart and GET-only proof in
> `docs/plan/2026-08-12-tdtb-project-extraction/evidence/T11-cutover-closure.json`.
> The draft/pending/blocked narrative below is preserved as the historical record of the
> pre-execution state; it no longer reflects current status.

Resolved context (T0) — recorded as settled decisions, not an open task: the
current-seat target root `/Users/walle-mini/Repos/Projects/TDTB`, the
history-preserving transfer method (clean clone, no worktree rewrite), and the
ownership strategy (SUPERSEDE/TRANSFER, never mutual shared app ownership) are
final. T0 is complete; the remaining open work is the T1-T11 sequence below.

- [x] T1 — Targeted read-only inventory with NO ownership claim.
- [x] T2 — Freeze active UI plan (B) and capture immutable dirty-state receipt BEFORE any copy/target prep.
- [x] T3 — Formally supersede/re-home B and accept the receipt (ownership handoff point).
- [x] T4 — Acquire EXCLUSIVE app claims only AFTER T3; no app/target claim before T4.
- [x] T5 — Stage target/copy and independently verify against the receipt (receipt-compared).
- [x] T6 — Logical extraction: ownership leaves Claudius; app paths leave the operational source set (retained fallback may remain until attended cutover).
- [x] T7 — Rewire Claudius consumers, tests, audits, and documentation.
- [x] T8 — Broad post-extraction `tdtb` sweep and triage (AC-005).
- [x] T9 — Verify both repositories non-live.
- [x] T10 — Attended cutover-wait handoff (produced, NOT executed here).
- [x] T11 — Post-cutover-removal receipt/handoff (NOT authorized here).

## Current checkpoint

State: completed (superseded)
Cause: historical blocked state resolved. B reached terminal approval=superseded at T3;
A executed T1-T11 and closed with the attended canonical restart of `com.walle.tdtb`
and GET-only proof (PID, cwd, /health 200, /config 200) at T11. Authoritative closure:
`docs/plan/2026-08-12-tdtb-project-extraction/plan.yaml` (`closure.declared_complete:
true`, `2026-08-15T02:52:00Z`) and
`docs/plan/2026-08-12-tdtb-project-extraction/evidence/T11-cutover-closure.json`.
The following historical checkpoint text records the pre-execution blocked state and
is retained for provenance.
Last verified: 2026-08-12 stale-premise check confirmed the source paths and absent
target; `Scripts/test_tdtb_project_extraction.py` was added and its pre-extraction
inventory, staged Claudius check, and broad pre-extraction sweep passed.
Verified progress: target root, history method, and Playwright disposition are
resolved; the gate harness exists; no source tree, launchd plist, live service, or
external state has been changed.
Dirty owned paths: `Scripts/test_tdtb_project_extraction.py` (this plan's harness
claim) plus the existing TDTB contract-test changes owned by B. Never reset, stash,
clean, or silently reassign those paths. B's dirty T1 evidence transfers by a sealed
receipt at T2, not by copy before the freeze.
Blocked scope: physical extraction and consumer rewiring until B's app-plan claims
are released via a valid T3 supersede/re-home with an immutable receipt, and this
extraction plan receives a fresh approval PASS. No shared app ownership is permitted
at any point.
Required decision: the selected strategy is SUPERSEDE/TRANSFER, never mutual shared
ownership. Freeze B (T2) and capture an immutable dirty-state receipt, then formally
supersede/re-home B (T3) before this plan acquires exclusive app claims (T4).
Next action: freeze B and capture the immutable dirty-state receipt (T2), then
formally supersede/re-home B (T3) before acquiring exclusive app claims (T4); rerun
approval analysis and fresh review after the manifest is amended.
Resume condition: ownership is explicit, the plan-tracker approval transition is
valid (B reaches terminal approval=superseded), and a fresh plan-verifier returns PASS.

## Bounded replan delta

Revision: 2 (replan_count: 2, max_replans: 2). This is the FINAL bounded governance
replan; max_replans is NOT increased.
Reason for replan: the critic blocked every shared-ownership fallback (mutual
shared-path contract, explicit shared contract, shared app ownership) and the dirty
copy taken before a controlled freeze. Shared app-path ownership is removed; B remains
the exclusive writer/owner of app paths until a controlled T2 freeze and T3
supersede/re-home.

Changed versus revision 1: removed PATH-001/PATH-002 active target-root claims (no
target-root or app claim before T4); added the schema-valid ownership state-transition
table; added the transfer-receipt requirements; clarified T5 (staged + receipt-compared)
vs T6 (logical extraction with retained fallback); resolved the AC-005 conflict; added
the acceptance baseline contract hash. Task IDs T1-T11 are unchanged in ordering.

Preserved gates: AC-001..AC-006 are semantically preserved; only sequencing and
evidence clarifications were added. No requirement was weakened.

Risks: dirty T1 evidence must not be orphaned by a filesystem move; the T2 freeze must
capture the exact dirty state before any copy/target preparation; B must be validly
superseded before this plan claims app paths to avoid a dual-writer conflict.

Measurable progress: an immutable transfer receipt (base commit, git status, text diff,
binary diff, untracked bytes, stable hashes) exists; after re-home there is zero active
ownership overlap between the two plans.

## Surprises and discoveries

- `Tasks/tdtb-app-pilot/spec.md` already describes a future promotion to a
  proper home, including its own repository; the extraction is therefore a
  boundary change, not an unplanned rewrite.
- `Projects/TDTB/` is already the GPT-active policy/archive boundary, but it is
  currently nested inside Claudius and must be folded into the standalone root.
- The broad `tdtb` search will remain intentionally non-empty: shared skills,
  scheduled work, plans, reports, and history stay in Claudius.
- The current worktree is dirty with active TDTB contract tests. A history
  extraction must use a clean temporary clone or equivalent; it must never
  rewrite this worktree.
- `com.walle.tdtb` is launchd-managed and single-writer. Removing the old app
  before an attended cutover would break the running service.
- The extraction acceptance harness now exists at
  `Scripts/test_tdtb_project_extraction.py`; its read-only pre-extraction checks
  pass, but it is not a substitute for formal plan approval or ownership release.

## Decision log

- **Two-pass reference audit:** Do a targeted hardcoded-path/ownership inventory
  before extraction, then a broad `tdtb` sweep after extraction.
  Rationale: a full pre-move grep is dominated by valid app, plan, skill, and
  history matches; after extraction it becomes a high-signal check for broken
  cross-repository references.
  Date: 2026-08-12
- **Shared-versus-owned boundary:** Move the app and project root; retain shared
  skills, scheduled tasks, plans, reports, references, and history in Claudius.
  Rationale: Claudius remains the WALL·E-OS skill/integration host, while TDTB
  becomes the application repository.
  Date: 2026-08-12
- **No implicit live cutover:** Repository extraction and reference rewiring do
  not authorize a launchd restart, deployment, billed call, or live source write.
  Rationale: the service has a single writer and the active TDTB plan requires
  attended qualification/review gates.
  Date: 2026-08-12
- **Ownership release first:** The active planning UI/reliability plan must
  release or formally re-home its app-path claims before TDTB source paths are
  physically removed from Claudius.
  Rationale: plan-tracker ownership is the concurrency boundary, and the dirty
  T1 work must not be orphaned by a filesystem move.
  Date: 2026-08-12
- **Current-seat target only:** Use `/Users/walle-mini/Repos/Projects/TDTB` for
  this migration; defer an `adam`-seat target mapping.
  Rationale: this handoff executes on the current seat and must not invent an
  unverified second-seat filesystem contract.
  Date: 2026-08-12
- **History-preserving transfer:** Extract from a temporary clean clone or
  equivalent, preserving Git history without rewriting the dirty worktree.
  Rationale: the current checkout contains active contract work that must remain
  untouched while the standalone repository retains provenance.
  Date: 2026-08-12
- **Regenerate Playwright evidence:** Do not move `output/playwright/tdtb-*`;
  regenerate it after the standalone repository is verified.
  Rationale: generated evidence should be tied to the new repository's verified
  source rather than carried across the boundary as stale output.
  Date: 2026-08-12

## Context and orientation

### Move candidates

- `Tasks/tdtb-app-pilot/` — application source, frontend, generated bundles,
  app tests, launchd template, restart script, mockups, and pilot documentation.
- `Projects/TDTB/` — `AGENTS.md`, `README.md`, and `archive/skill-era/`; fold
  these into the standalone repository root rather than retaining a nested
  `Projects/TDTB` directory.
- `output/playwright/tdtb-*` — generated UI evidence; move only if the history
  and reproducibility decision says it is still useful. Otherwise regenerate it
  in the standalone repository.

### Claudius-owned consumers that remain or require updates

- Shared skill sources and variants: `Skills/user/tdtb-*`,
  `Configurations/gpt-stack/skills/tdtb-*`, and plugin projections.
- Scheduled and skill-side tooling: nightly precompute, gather mirrors,
  correction/observation records, and vault audits.
- Plans, reports, references, and historical handoffs under `Plans Link/`,
  `Reports/`, and `References/`.
- Cross-repository wiring: `Configurations/gpt-stack/seat-bootstrap.sh`,
  `.claude/launch.json`, `Scripts/precommit-bundle-check.py`, boundary and
  runner tests, `Scripts/structure-audit.py`, `CLAUDE.md`, and GPT-stack docs.

### Active ownership constraint

`Plans Link/2026-08-09-tdtb-planning-ui-reliability.md` (B) currently owns app
subtrees and files, including `app/main.py`, frontend source/tests, bundles,
and `spec.md`. T3 must either complete and close B or transfer its remaining work
into the standalone repository via a valid supersede/re-home (B reaches terminal
approval=superseded). No extraction may silently bypass B. Shared app ownership is
not an option.

## Verification policy

Risk: high
Fresh review: approval-and-completion

All repository work is read-only with respect to live TDTB state. This plan
does not authorize `:8746` restart, launchctl activation, `/sequence`,
`/adjust`, `/commit`, Todoist mutation, Calendar mutation, or vault mutation.

## Owned paths

Pre-transfer claims (active now, while A is draft/pending). Only the harness/test
artifact claim is active. NO target-root ownership claim exists before T4. NO app-path
claim exists before T4. A's read-only T1 inventory asserts NO ownership claim. B
remains the exclusive writer/owner of app paths until its T2 freeze and T3
supersede/re-home. No shared app ownership; no shares_with on any app claim.

```jsonl
{"claim_id":"PATH-003","namespace":"repo","path":"Scripts/test_tdtb_project_extraction.py","kind":"file","mode":"exclusive","state":"existing","shares_with":[]}
```

### Post-transfer exclusive app-claims block (NOT active before T4)

This block documents the claims A will acquire ONLY after B is validly
superseded/re-homed (T3) and T4 amends the manifest. It is not an active claim and
the plan-tracker does not index it while A is draft/pending. Each entry MUST be
realized as an exclusive claim (mode=exclusive, shares_with=[]), never shared:

- `Tasks/tdtb-app-pilot/` (tree, repo) — backend, frontend, tests, launchd template, restart script, mockups, pilot docs.
- `Projects/TDTB/` (tree, repo) — AGENTS.md, README.md, archive/skill-era; folded into the standalone root.
- `Scripts/test_tdtb_planning_ui_reliability.py` (file, repo) — active dirty app-plan test artifact.
- Any remaining active dirty TDTB path owned by B at freeze time.

These transfer to A only via the T3/T4 exclusive-claim amendment. No broad shared claim.

## Ownership state-transition table

Schema-valid values per `Scripts/plan-tracker.py`: APPROVALS = {draft, approved,
superseded}; EXECUTIONS = {pending, in-progress, blocked, completed, archived}; a
supersede requires approval=superseded AND a terminal execution (archived/completed)
AND superseded_by set; a superseded plan is excluded from the overlap index. These
transitions are governance intent only; no state changes while A is draft/pending.

| Phase | Plan B (2026-08-09-tdtb-planning-ui-reliability) | Plan A (this) | Notes |
|---|---|---|---|
| before freeze | approval=approved; execution=in-progress; exclusive app claims PATH-001..PATH-020; T1 unchecked | approval=draft; execution=pending; NO app/target claim | B is the exclusive writer/owner of app paths; A's read-only T1 inventory asserts NO claim. |
| T2 | writes PAUSED/BLOCKED without B being marked complete: approval stays approved, execution stays in-progress; dirty T1 evidence transferred by immutable receipt, not copied before freeze | capture immutable receipt of B dirty state (base commit, git status, text/binary diff, untracked bytes, stable hashes) | B is paused pending transfer; it is NOT completed and no approval is fabricated. No shared app ownership. |
| T3 | receipt accepted; B formally superseded/re-homed: approval=superseded; execution=archived; superseded_by=2026-08-12-tdtb-project-extraction; blocker_reason documents the handoff (required because T1 stays unchecked under archived). Terminal -> excluded from overlap. Immutable evidence record references the receipt. | accept receipt; T3 complete | If re-home to a new standalone plan is chosen, B still reaches terminal approval=superseded with superseded_by set to that plan. |
| T4 | n/a (B already terminal) | amend manifest with EXCLUSIVE app-path claims (the post-transfer block above, not active before T3) | A receives exclusive app claims ONLY after T3. No app or target-root claim before T4. |

## Plan of work

The authoritative gates remain AC-001..AC-006, unchanged. These tasks implement them
in the supersede/transfer ordering (no mutual shared ownership):

1. **T1 — Targeted read-only inventory with no claim.** Run the bounded pre-extraction
   path/ownership inventory. This is read-only and asserts NO app-path claim; it does
   not prepare any target. Record each match as move, remain/update, historical, or
   unresolved.
2. **T2 — Freeze active UI plan and capture immutable dirty-state receipt BEFORE any
   dirty copy or target preparation.** Set the active UI/reliability plan to a frozen
   state and capture an immutable receipt of its current dirty owned paths (base commit,
   git status, text diff, binary diff, untracked bytes, stable hashes). No copy or
   target staging occurs until this receipt exists.
3. **T3 — Formally supersede/re-home the active plan and accept the receipt.** Record the
   supersede/re-home of the active UI/reliability plan and accept the T2 receipt as the
   canonical evidence of its dirty T1 state. This is the ownership handoff point.
4. **T4 — Acquire exclusive app claims only after T3.** Now that the active plan is
   superseded/re-homed, amend this plan's manifest with EXCLUSIVE (non-shared) app-path
   and target-root claims. No app-path or target-root claim may exist before T4; the
   active plan must be superseded/re-homed (T3) first.
5. **T5 — Stage target/copy and independently verify against the receipt.** Prepare the
   sibling target repository and copy the app/project content, then compare the source
   receipt (T2) with the copied target state; abort on unexpected divergence. Evidence: the
   staged target passes its own standalone gate (`python3 Scripts/test_tdtb_project_extraction.py
   target`) AND every receipt byte-hash matches the source. Exit condition: staged target is
   independently verified and receipt-compared GREEN; T5 does not by itself remove app paths
   from Claudius.
6. **T6 — Logical extraction.** Transfer ownership of the app/project boundary to the
   standalone layout, preserving current dirty work. This is LOGICAL: ownership leaves
   Claudius and the app paths are removed from the operational source set. A clearly named,
   NON-OPERATIONAL retained fallback (the old Claudius app tree) MAY remain in place until a
   separate attended cutover; its presence is intentional retention, NOT failed verification.
   Evidence: B is superseded (T3) and the operational source set no longer lists the app
   paths. Exit condition: logical transfer complete and the retained fallback classified as
   intentional; physical deletion is deferred to T11/attended cutover.
7. **T7 — Rewire Claudius consumers.** Update shared skills, bootstrap, launchd template
   references, scratch launch config, integration tests, structure-audit registration,
   bundle checks, docs, and cross-repository path resolution.
8. **T8 — Broad post-extraction sweep.** Run the broad `tdtb` sweep over remaining Claudius
   and triage every result as intentional, historical/archive, or broken/orphaned.
9. **T9 — Verify both repositories.** Run standalone app tests/builds and Claudius
   instruction, boundary, hygiene, and diff checks with no live action.
10. **T10 — Attended cutover-wait handoff.** Produce the separate attended checklist for
    staging the new plist, restarting `com.walle.tdtb`, and GET-only health/version proof.
    Wait for that attended action; do not execute it here.
11. **T11 — Post-cutover-removal receipt/handoff (NOT authorized here).** The removal of the
    old Claudius app tree is explicitly out of scope for this plan and requires a separate
    attended approval. This task records the handoff only; it performs no removal.

## Transfer receipt requirements

The immutable dirty-state receipt is a future artifact ONLY; it is NOT created by this
replan. Path: `Plans Link/2026-08-12-tdtb-project-extraction/evidence/transfer-receipt.json`
(relative to repo root). It must contain, at minimum:

- `schema`/`version`: a stable receipt schema version (e.g. `tdtb-transfer-receipt/1`).
- `sealed_manifest_sha256`: SHA-256 over the canonical, normalized manifest JSON.
- `source_target_map`: ordered source-path -> target-path entries for every transferred item.
- `entries`: per item, classified as tracked / untracked / deleted / renamed, with:
  - `mode` (file mode bits) and `symlink` target where applicable;
  - `text_sha256` for text files and `binary_sha256` for binary files (byte-exact);
  - for renames, both `old_path` and `new_path`.
- `fail_closed`: an explicit comparison result that fails the transfer on ANY of:
  - a source path present in B but MISSING from the target;
  - a target path present but EXTRA (not in the source map);
  - any text or binary byte hash MISMATCH between source and target.
- `generated_at` and `generated_by` (the freeze command/agent) for provenance.

The receipt is captured at T2 (before any copy/target prep) and accepted at T3. It is the
sole authoritative evidence that B's dirty T1 state is preserved across the handoff.

## Execution routing

Default: OpenCode `implement` after approval; no subagents for the live/service
cutover. The migration is cross-repository and high-risk; implementation must
preserve the dirty worktree and use path-scoped operations. Planning and review
remain in the current session; acceptance belongs to a fresh plan-verifier.

## Recovery and idempotence

Never reset, clean, stash, rebase, or rewrite the active worktree. Perform any
history extraction from a temporary clean clone. The immutable T2 receipt MUST
contain: the base commit, `git status`, the text diff, the binary diff, untracked
bytes, and stable hashes. Before each move, compare the source path manifest and
dirty-file inventory against the receipt; abort on unexpected changes.

Copy/stage first, verify the target, then update consumers. Compare the source
receipt with the target state after copy; they must agree on content hashes. Keep
the old app tree until the target tests and cross-repo checks pass and an attended
service cutover is separately approved; source remaining while awaiting that
separately attended cutover is expected, NOT failed verification. A failed transfer
must leave the source tree usable and must not alter the installed launchd plist.
Re-running inventory or the post-extraction sweep is read-only and idempotent.

## Acceptance and evidence

### AC-001 — Targeted pre-extraction inventory is complete
Requirement: BEFORE any TDTB source path is moved, THE SYSTEM SHALL record the
exact move/remain/update boundary and all targeted hardcoded path consumers,
without relying on a broad full-repository `tdtb` grep.
Disposition: active
Gate cwd: repo:.
Gate command: `python3 Scripts/test_tdtb_project_extraction.py inventory`
Gate effect: test-artifacts
Expected: exit 0 with a path manifest, dirty-path preservation record, and no
unclassified targeted reference.
Failure: missing boundary, missing hardcoded consumer, or unclassified result.
Required evidence: gate

### AC-002 — Ownership is superseded/transferred (exclusive) before extraction
Requirement: THE SYSTEM SHALL supersede or formally re-home every active app-path
claim with an immutable receipt BEFORE removing the app tree from Claudius. Exclusive
(non-shared) ownership is required; the active plan must be the exclusive writer until
the controlled freeze and transfer (T2/T3).
Disposition: active
Gate cwd: repo:.
Gate command: `python3 Scripts/plan-tracker.py analyze 2026-08-12-tdtb-project-extraction --phase execution --json`
Gate effect: test-artifacts
Expected: exit 0 with no unresolved active-plan overlap or missing owned path.
Failure: an active plan still owns an extracted path without transfer evidence
(supersede/re-home).
Required evidence: gate

### AC-003 — Standalone repository is complete and independently testable
Requirement: THE TARGET REPOSITORY SHALL contain the TDTB app/project boundary
at its root, preserve current dirty work, and pass its backend/frontend/build
checks without Claudius-relative source paths. The old Claudius copy MAY remain
until a separately attended service cutover; its presence is NOT a verification failure.
Disposition: active
Gate cwd: seat:~/Repos/Projects/TDTB
Gate command: `python3 Scripts/test_tdtb_project_extraction.py target`
Gate effect: test-artifacts
Expected: exit 0 with source-map, path, backend, frontend, and bundle checks green.
Failure: missing source, lost dirty change, stale Claudius path, or failed app gate.
Required evidence: gate

### AC-004 — Claudius consumers are correctly rewired
Requirement: AFTER extraction, shared skills and integration tooling SHALL retain
their intended ownership while every operational app path resolves through the
new target-root contract.
Disposition: active
Gate cwd: repo:.
Gate command: `python3 Scripts/test_tdtb_project_extraction.py claudius`
Gate effect: test-artifacts
Expected: exit 0 with boundary, bootstrap, launch-config, bundle-check, and
structure-audit consumers green.
Failure: broken cross-repo path, stale registry claim, or shared skill moved/deleted.
Required evidence: gate

### AC-005 — Broad post-extraction sweep is fully triaged
Requirement: AFTER the app/project tree leaves Claudius logically (T6) and consumers
are rewired (T7), THE SYSTEM SHALL run a broad `tdtb` sweep over the remaining
repository and classify every result as intentional, historical/archive, or
broken/orphaned. The sweep follows the LOGICAL transfer; a retained non-operational
fallback copy (T6) is classified as intentional retention and does NOT require physical
deletion before the separate attended cutover (T10/T11).
Disposition: active
Gate cwd: repo:.
Gate command: `python3 Scripts/test_tdtb_project_extraction.py sweep`
Gate effect: test-artifacts
Expected: exit 0 with a complete sweep artifact, every match disposed, and zero
unresolved broken/orphaned references; the retained fallback is explicitly classified.
Failure: sweep omitted runtime text, a match lacks a disposition, a broken reference
remains, or the retained fallback is misclassified as broken/orphaned.
Required evidence: gate

### AC-006 — Both repositories pass non-live verification
Requirement: THE SYSTEM SHALL pass standalone app tests/builds and Claudius
instruction, boundary, hygiene, and diff checks without touching `:8746` or
real source state.
Disposition: active
Gate cwd: repo:.
Gate command: `python3 Scripts/test_tdtb_project_extraction.py verify`
Gate effect: test-artifacts
Expected: exit 0 with both repository result sets green and no live action.
Failure: any test/build/hygiene failure, live call, or source write.
Required evidence: gate

### Evidence records

```jsonl
```

### Review records

```jsonl
```

## Acceptance baseline contract

Baseline source: working-tree plan A AC-001..AC-006 material, revision 1 (the
pre-replan source). Plan A is NOT committed to git HEAD (it is an untracked working
draft), so no earlier git revision exists; HEAD cannot supply a prior version. The
baseline is therefore the current revision-1 AC block.

Method: extract the `### AC-001` .. `### AC-006` text (through the end of AC-006,
before `### Evidence records`); normalize with `splitlines()` (unifies CRLF/CR/LF),
`rstrip()` each line, then collapse 3+ consecutive newlines to 2; UTF-8 encode;
SHA-256 the bytes. Deterministic and reproducible.

Baseline SHA-256 (AC-001..AC-006, revision 1): `6ab3f79c85116facc8e6cd8745eb87b72faeaf0a33f8f36ea95185ab6a0809a4`

Revision-2 AC delta: AC-001..AC-006 are SEMANTICALLY PRESERVED. Only sequencing and
evidence clarifications were added (T1-T11 ordering, T5/T6 logical-vs-physical split,
AC-005 retained-fallback classification, AC-002 exclusive-ownership wording). No
acceptance requirement was weakened, removed, or made optional.

## Outcomes and retrospective

Completed and superseded (2026-08-15): the extraction executed through the T11
attended cutover closure, with closure evidence in
`docs/plan/2026-08-12-tdtb-project-extraction/evidence/T11-cutover-closure.json`.
Historical note: this section previously read "Pending migration" during the
pre-execution draft state; the record below is the preserved historical outcome.

## Out of scope

- Live launchd staging, `launchctl bootstrap`, restart, deployment, or service
  cutover; these require a separate attended approval and GET-only proof.
- Billed `/sequence` or `/adjust`, live `/commit`, Todoist writes, Calendar
  writes, vault writes, or real source mutation.
- Moving shared TDTB skills, scheduled rituals, historical plans/reports,
  correction records, or references merely because they contain `tdtb` text.
- Rewriting historical plans or archives solely to eliminate intentional
  references; history is classified, not erased.
- Opportunistic app redesign, behavior changes, or dependency upgrades during
  the repository-boundary migration.

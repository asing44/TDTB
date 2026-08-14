# TDTB — GPT Project Rules

**Project-ID:** `TDTB`

TDTB (The Daily Time Box) is a local planning bridge. Codex/ChatGPT is
the current change-making surface; OpenCode is supported only through the
same repository rules and safety envelope. This file is the canonical
project policy. Do not create `instructions.md` or replace this file with a
symlink.

## Required context

At the start of a TDTB task, read this file and `README.md` (the canonical
source map), plus the governing plan evidence under `docs/plan/`. The cockpit
plan (`Plans Link/2026-07-17-tdtb-cockpit-overhaul.md`) is host-owned
historical evidence in the Claudius repo, not canonical; it records the
qualification, bake-in, and retirement gates but is not an execution front.
Read the canonical and GPT skill variants when the task changes TDTB behavior.
Use `tdtb-continue` for "continue/resume TDTB" requests; it resolves the
host cockpit plan and rejects historical plans as execution fronts.

TDTB remains the review → sequence → commit bridge. Assignment stays upstream:
the cockpit consumes assigned work, fixed Calendar commitments, and the
current source contracts; it is not a second assignment or pool browser.

## Source ownership

- The canonical app lives at the repository root (`app/`, `frontend/`,
  `launchd/`, `restart-live.sh`, `spec.md`). Do not relocate it until the
  Phase 2 gates named in `README.md` are evidenced. The loaded live runtime
  still runs from the Claudius fallback copy (`Tasks/tdtb-app-pilot/`) until
  the attended cutover.
- Shared behavior belongs in the Claudius host repo:
  `Skills/user/tdtb-bridger-vault/SKILL.md`; the GPT-compatible variant
  belongs in `Configurations/gpt-stack/skills/tdtb-bridger-vault/SKILL.md`.
- Canonical plans and acceptance evidence live under `docs/plan/`. Host-owned
  historical plans remain in the Claudius repo `Plans Link/`. Installed
  launchd jobs, restart links, and other machine-local projections are not
  repo source.
- Skill-era material at the repository root is historical evidence only. Do
  not use it to re-derive current behavior when a live source or active plan
  exists. The host keeps the legacy `Projects/TDTB/archive/skill-era/` copy as
  historical evidence.
- Retirement state: the former Tune-era design/reference material is retired
  from current lookup and preserved only in the host archive; the canonical
  and GPT skill variants remain active and are not retired by this boundary.

## Safety envelope

Treat a visible `:8746` service as live even when version or port visibility
is incomplete. The live service may be inspected with bounded GET-only health,
version, and contract checks. Never send `POST /commit` without Adam's
explicit instruction. Never fire billed `POST /sequence` or `POST /adjust`
against live state during unattended or implementation work; use fixtures,
mocks, or the scratch instance on `:8790`. Do not write the real vault,
Todoist, Calendar, or other source state from a scratch walkthrough.

Restarting `:8746` is an attended deployment action. It is allowed only when
the `tdtb-continue` standing decision makes backend/frontend version skew the
reason, never during a commit, and must be followed by GET-only proof of the
expected contract. Any live source write, billed judgment call, commit, or
machine-local projection change has its own explicit approval boundary.

If host-port visibility is unavailable, report server state as unknown and
apply LIVE rules. Preserve concurrent work; do not restart, kill, or repair a
foreign process or worktree.

## Verification and completion

Use TDD for Python and frontend behavior. From the canonical repo root, run
the backend pytest suite; from `frontend/`, run `npm test`,
`npm run typecheck`, `npm run build:mockup`, and `npm run build:prod` when
the frontend or bundle is in scope. Use a scratch
`:8790` walkthrough with fixtures/mocks and assert zero billed calls and zero
real source writes. Generated bundles must be rebuilt from their source and
read back before they count as verified.

For repository-boundary changes also run the Claudius-hosted checks from
`/Users/walle-mini/Repos/Claudius` (`python3 Scripts/test_structure_audit.py`,
`python3 Scripts/instr-build.py --check`, the focused builder tests), and
`git diff --check` on owned paths. Completion requires fresh instruction
read-back, current Git/worktree evidence, the active-plan gate evidence, and
the required independent review. A local edit or static pass alone is not
completion evidence.

## Concurrent work

Use a dedicated `codex/` branch/worktree for interactive changes; `main` is
integration/review-only. Recheck branch, index, worktree, and path hashes
immediately before every mutation. Preserve foreign staged/unstaged work,
stage only owned paths, and use path-scoped commits followed by
`git show --stat HEAD`. Never reset, clean, unstage, pull, rebase, merge,
push, or delete another session's state.

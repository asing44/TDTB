# TDTB continuation — calendar persistence and judgment candidate

Focused follow-up to the [2026-10-14 handoff](/Users/walle-mini/Repos/Projects/TDTB/docs/plan/2026-10-14-handoff.md), not a replacement for its broader history or unresolved items.

## Accepted and landed

Calendar participation now updates the local setup draft before persistence. Continue no longer overwrites a good calendar override with a stale draft. Existing local start/duration edits survive.

Sequence processing now distinguishes free preflight/session-token failures, uncertain POST transport failures, and post-response processing failures. A failed independent budget refresh retains the successful plan and marks the ledger unavailable. This does not prove or repair the original four-minute provider delay.

Verification:
- Root: main backend 3,459 passed / one existing deprecation warning; frontend 1,728 passed / 6 skipped; TypeScript clean; both bundles rebuilt and their references/read-back verified.
- Root: three new calendar regressions failed against the baseline and passed against the fix.
- Independent review: no blocking findings; focused 148 tests and TypeScript passed independently.
- Fixture browser: switched a calendar event off, continued to Plan Tasks, returned to setup, and continued again. The event remained uncounted. Zero interaction requests, billed calls, or source writes. The fixture server/browser were closed.
- Off calendar events remain visible as dashed/dimmed context; their continued presence is not evidence they still count.

The source and rebuilt bundles are on `main`. The live UI was not clicked; attempted root/cockpit URL probes returned 404, so no live frontend-serving claim is made.

## Model decision — retain DeepSeek

The operator subsequently chose to keep `deepseek/deepseek-v4.1-flash`. The model-switch workstream is closed; GPT-5.4 Mini is not selected. No further qualification, routing diagnostics, credential access, or provider calls are authorized by the completed run. Existing candidate files and evidence are retained, not deleted.

Earlier in this workstream, the user approved `openai/gpt-5.4-mini` through OpenRouter, reasoning `low`, strict JSON, 4,096 output/reasoning tokens, latency-prioritized standard providers, and token-price ceilings of $0.75 input / $4.50 output per million tokens. Flex and priority OpenAI endpoints are excluded.

Candidate source, archive, tests, and snapshot are retained in the [isolated candidate worktree](/Users/walle-mini/Repos/Projects/TDTB/.worktrees/judgment-mini-20261015). They are **not merged into main**. The live health endpoint still reports `deepseek/deepseek-v4.1-flash`. No service restart or installed launchd projection change occurred.

A candidate-only compatibility correction closes Adjust's free-form `args` schema using the supported alternatives `{}` or numeric `blocks`. Other models, global schemas, prompts, and downstream validation remain unchanged. The source also retains sanitized HTTP-status evidence without logging provider bodies or credentials.

Offline evidence: child full backend suite 3,494 passed / one existing Starlette deprecation warning; root focused 115 passed. Independent static remediation review passed with no blockers; that reviewer did not run pytest. Its non-blocking note: candidate Adjust closure checks prompt identity, so a value-equal copied prompt would miss the closure; all current callers pass the module constant. No change was made for that informational note.

## Paid qualification evidence

Initial authorization: at most 21 synthetic requests and $1 token cost. After the first failure, the user explicitly authorized one resumed credential-assisted run with a cumulative cap of 22 requests and $1.

Both runs used only in-memory fixtures and the existing connection internally. No credential value was printed, placed in arguments, written to a file, or copied into repository evidence.

- Attempt one: one request, 0.242 seconds, provider/transport failure; HTTP status was not retained by the initial classifier.
- Resumed attempt: one request, 0.254 seconds, HTTP 404; stopped immediately as instructed.
- Total: two requests; conservative cumulative token-cost upper bound $0.052250. Actual provider cost was not measured.
- No model result was returned. No task-quality, Unicode-preservation, or model-inference latency claim is supported. Twenty further cases in each prepared run were skipped.
- The prepared fixture set included one Unicode-ID variant, but execution never reached it.
- Live ledger before/after the resumed run: logical day 2026-10-15, spent 0, cap 4, remaining 4.

The [committed qualification record](/Users/walle-mini/Repos/Projects/TDTB/.worktrees/judgment-mini-20261015/docs/agent-stack/qualifications/2026-10-15-tdtb-judgment-mini.json) and [blocked snapshot](/Users/walle-mini/Repos/Projects/TDTB/.worktrees/judgment-mini-20261015/docs/agent-stack/snapshots/2026-10-15-tdtb-judgment-mini.md) are retained with the candidate. Sanitized local receipts: [first run](/tmp/tdtb-judgment-mini-qualification.json) and [resumed run](/tmp/tdtb-judgment-mini-qualification-resume.json). These contain no raw prompts, model responses, error bodies, or credentials.

Public model metadata is reachable and advertises the candidate and compatible standard OpenAI endpoint. HTTP 404 does not establish which account/provider/routing restriction prevented the authenticated request. Do not label the model bad or qualified on this evidence.

## Next restartable action

No further model action: keep the existing DeepSeek route. Do not automatically resume OpenRouter diagnostics or candidate qualification. Any future model-switch effort requires a new explicit request and separate approval for credential-assisted or paid operations.

Do not merge the unselected candidate default/plist, restart the live service, alter global Pi models or today's allowance, or remove the retained candidate worktree as part of this decision.

Capacities calls-per-minute tuning remains backlog [issue #12](https://github.com/asing44/TDTB/issues/12); it was not implemented here.

## Preservation and lifecycle

Five pre-existing foreign dirty/untracked paths remain byte-for-byte unchanged: `.DS_Store`, `AGENTS.md`, `config/tdtb.json`, and the two existing handoff/mockup zip files. No cleanup, push, credential rotation, or source-system write occurred.

Calendar worktree: [retained integration context](/Users/walle-mini/Repos/Projects/TDTB/.worktrees/calendar-sequence-state-20261015), branch `TDTB/calendar-sequence-state-20261015`; source is integrated, generated bundles verified, dependency symlink retained. Named worktree: out of scope for automatic janitor removal.

Candidate worktree: [retained unmerged source](/Users/walle-mini/Repos/Projects/TDTB/.worktrees/judgment-mini-20261015), branch `TDTB/judgment-mini-20261015`; disposition retained/unselected. Its blocked qualification evidence is historical, not active work. It is not janitor-eligible.

```text
accepted frontend source HEAD: 6d41122fe17c8e6e0663a8c8bbab3ab6dcc0ba8f
candidate source HEAD: 02d8478d6f716a83de7ab421768b4530e128a0fc
candidate observed HEAD, including final evidence: 8467145d3573474c29d69284db122f01d8b2d4c5
```

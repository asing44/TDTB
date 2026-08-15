"""judgment.py — the four Agent SDK judgment calls (T11).

Per the tdtb-bridger-vault skill semantics (the original spec.md § "Judgment | Claude
Agent SDK" is fallback-only historical, preserved under Claudius Tasks/tdtb-app-pilot
until attended removal) and Plans Link/2026-07-12-tdtb-app-pilot-build.md T11: exactly
four judgment calls per run, Sonnet-tier, no tools/no filesystem access — pure
text-in/JSON-out. Every call is schema-validated; on parse/validation failure, ONE retry with
the error appended to the prompt, then ``JudgmentError``.

Prompt content mirrors the tdtb-bridger-vault skill's phase semantics (never invents new
invariants):
  - ``audit_pipeline``   → SKILL.md § "0.9 Assignment-Pipeline Audit" (non-blocking report card)
  - ``suggest_digest``   → SKILL.md § "Phase 2 — Day Digest" Suggestions (≤5, ranked)
  - ``adjust_freetext``  → SKILL.md § "Phase 3 — Adjustments" (chat-driven ops)
  - ``propose_sequence`` → SKILL.md § "Phase 4 — Sequence" (placement passes, zones,
    latest_start, never-bump) — including the standing rule (userPreferences.md § Tool
    Routing): NEVER a morning (before 12:00) workout block, except a Press micro-adventure
    whose TDTB config explicitly sets ``before_work``.

Call budget: a module-level ``RunContext`` counter asserts <=4 calls per run (spec § "0
tokens on deterministic steps, ≤4 Agent SDK invocations per run").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("tdtb.judgment")

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
import config_reader
import placement_rules
from sequence import _press_before_work_ids
from sequence import is_workout_item as _sequence_is_workout_item

MODEL = "claude-sonnet-4-6"
EFFORT = "low"  # judgment calls are structured JSON transforms, not deep reasoning

# Provider switch (2026-07-17, Adam): judgment runs over OpenRouter's HTTP API
# by default — the Claude Agent SDK's CLI subprocess wedged silently (zero
# stderr in 120s) under launchd with real-size prompts, and OpenRouter
# decouples per-call cost from the Anthropic plan quota. "sdk" restores the
# old path. The billed-call ledger (G24) charges per attempt either way.
PROVIDER = os.environ.get("TDTB_JUDGMENT_PROVIDER", "openrouter")
# The qualified production route is OpenAI GPT-5.6 Luna. The launchd plist
# pins this explicitly, but the code default must agree so a missing or stale
# service environment cannot silently fall back to the retired DeepSeek Flash
# route. DeepSeek variants remain explicit overrides for qualification only.
DEFAULT_OPENROUTER_MODEL = "openai/gpt-5.6-luna"


def _configured_openrouter_model(environ: Mapping[str, str] | None = None) -> str:
    values = os.environ if environ is None else environ
    return values.get("TDTB_JUDGMENT_MODEL") or DEFAULT_OPENROUTER_MODEL


OPENROUTER_MODEL = _configured_openrouter_model()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OPENCODE_AUTH = Path.home() / ".local/share/opencode/auth.json"
MAX_CALLS_PER_RUN = 4
# G15: hard ceiling on one query — a wedged SDK subprocess otherwise hangs the
# route forever.
#
# 2026-07-27: raised 120 -> 300. The old comment called 120s "generous vs the
# observed ~1-3 min happy path", which it is not — it sat INSIDE that range, so
# an ordinary slow-but-healthy call got killed at the ceiling. That is
# expensive in a way a timeout normally is not: propose_sequence runs with
# max_attempts=1 (the single-billed-call contract), so the attempt is charged
# to the day's ledger before the request starts, and OpenRouter may well finish
# the inference server-side after we have stopped listening. The user pays and
# gets nothing, with no retry to recover it. A ceiling exists to catch a WEDGE,
# so it belongs well past the slowest healthy call, not near the median.
QUERY_TIMEOUT_S = 300.0
# Ring buffer of CLI-child stderr kept per query for failure forensics.
STDERR_TAIL_LINES = 40
# G22: pause before retrying a transient SDK/process failure so a crashed CLI
# child isn't immediately re-spawned into the same condition (observed
# fast-fail: two attempts dead in ~1s total).
TRANSIENT_RETRY_DELAY_S = 2.0
MAX_SUGGESTIONS = 5
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class JudgmentError(Exception):
    """Raised when a judgment call fails to produce a schema-valid response
    after one retry, or when the per-run call budget is exceeded."""


class BudgetExceededError(Exception):
    """Raised by a RunContext ``charge`` hook when the persistent per-day
    billed-call ledger is spent (G24). Deliberately NOT a JudgmentError:
    routes map it to 429, never the 502 judgment-failure path, and the
    retry loop must never swallow it."""


# ---------------------------------------------------------------------------
# Call budget
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    """Per-run call counter. One instance per TDTB run; pass explicitly into
    every judgment call so the <=4 bound is enforced across the whole run,
    not just within a single process-wide singleton (tests build multiple
    contexts without interference)."""

    calls_made: int = 0
    # Qualification-only signal: retries are billed attempts even though
    # calls_made preserves the historical four-judgment-operation contract.
    attempts_made: int = 0
    max_calls: int = MAX_CALLS_PER_RUN
    # G24: optional persistent-ledger hook, invoked once per REAL SDK attempt
    # (a validation retry is a second billed call). Raises BudgetExceededError
    # when the day's budget is spent; wired by main.py routes.
    charge: Any = None

    def consume(self, label: str) -> None:
        if self.calls_made >= self.max_calls:
            raise JudgmentError(
                f"call budget exceeded: {label} would be call "
                f"{self.calls_made + 1}, max is {self.max_calls}"
            )
        self.calls_made += 1


# ---------------------------------------------------------------------------
# SDK plumbing
# ---------------------------------------------------------------------------

def _sdk_options(system_prompt: str, stderr_cb: Any = None) -> ClaudeAgentOptions:
    """Options shared by every judgment call: Sonnet-tier, no tools, single turn."""
    return ClaudeAgentOptions(
        model=MODEL,
        effort=EFFORT,
        system_prompt=system_prompt,
        tools=[],
        allowed_tools=[],
        max_turns=1,
        # Pure text-in/JSON-out: skip the user's global Claude Code config —
        # without this every call spawns the full session stack (MCP servers,
        # plugins, hooks), which dominated wall time in the T11 live run.
        setting_sources=[],
        mcp_servers={},
        strict_mcp_config=True,
        # The CLI child's stderr is the only forensic surface when a query
        # wedges (2026-07-17 launchd timeouts were undiagnosable without it).
        stderr=stderr_cb,
    )


def _openrouter_key() -> str:
    """OPENROUTER_API_KEY env, else the opencode auth store (the seat's
    canonical OpenRouter credential home per gpt-stack)."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    try:
        data = json.loads(_OPENCODE_AUTH.read_text(encoding="utf-8"))
        entry = data.get("openrouter") or {}
        key = entry.get("key") or entry.get("apiKey")
    except (OSError, json.JSONDecodeError):
        key = None
    if not key:
        raise JudgmentError(
            "no OpenRouter key: set OPENROUTER_API_KEY or run "
            "`opencode auth login openrouter`"
        )
    return key


async def _run_query_openrouter(system_prompt: str, user_prompt: str) -> str:
    """One OpenRouter chat completion — plain HTTPS, no subprocess to wedge."""
    key = _openrouter_key()
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # Qualification gate (2026-07-19): pin the provider to strict output
        # support rather than accepting a router fallback that ignores it.
        "response_format": _response_format_for(system_prompt),
        "provider": {"require_parameters": True},
    }
    # structured-JSON transform: near-deterministic output wanted. But GPT-5
    # family endpoints REJECT temperature, and with require_parameters that
    # becomes a 404 "no endpoints found" for the whole request (verified
    # 2026-07-27: identical payload succeeds on openai/gpt-5.6-luna the moment
    # temperature is dropped). Determinism for those models rides on the
    # strict response_format instead.
    if not OPENROUTER_MODEL.startswith("openai/"):
        payload["temperature"] = 0.2
    # M3 must explicitly honor reasoning-off before it can qualify. If its
    # selected provider rejects this required parameter, qualification fails
    # rather than silently spending hidden reasoning tokens.
    if OPENROUTER_MODEL == "minimax/minimax-m3":
        payload["reasoning"] = {"effort": "none"}
    async def _request() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=QUERY_TIMEOUT_S) as client:
            resp = await client.post(
                OPENROUTER_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.is_error:
                # httpx's own message is the status line plus a link to MDN,
                # which says nothing about WHY. OpenRouter puts the actual
                # reason in the body — 2026-07-27 a 403 turned out to be the
                # key's monthly spend cap, and the surfaced error gave no hint
                # of that. The body never contains the key.
                detail = resp.text[:400].strip().replace("\n", " ")
                raise RuntimeError(
                    f"OpenRouter HTTP {resp.status_code}"
                    + (f" — {detail}" if detail else "")
                )
            return resp.json()

    # httpx's timeout is inactivity-based, so a trickling provider can keep
    # resetting it indefinitely. Match the SDK path's G15 wall-clock ceiling.
    try:
        data = await asyncio.wait_for(_request(), timeout=QUERY_TIMEOUT_S)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        # A bare asyncio.TimeoutError stringifies to "" — the caller reports
        # the class name and the operator learns nothing (2026-07-27: the
        # surfaced error was the single word "TimeoutError"). Name the model
        # and the ceiling that fired, since those are the two things you
        # change in response.
        raise TimeoutError(
            f"no response from {OPENROUTER_MODEL} within {QUERY_TIMEOUT_S:.0f}s "
            f"(TDTB_JUDGMENT_MODEL / QUERY_TIMEOUT_S) — the billed attempt is "
            f"already charged and is not retried"
        ) from exc
    if "error" in data:
        # OpenRouter can 200 with an error body (e.g. provider-side failures)
        err = data["error"]
        raise RuntimeError(f"OpenRouter error {err.get('code')}: {err.get('message')}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"OpenRouter response missing content: {str(data)[:200]}") from exc
    if not content:
        raise ValueError("OpenRouter returned empty content")
    return content


async def _run_query(system_prompt: str, user_prompt: str) -> str:
    """Fire one judgment query via the configured provider, return raw text."""
    if PROVIDER == "openrouter":
        return await _run_query_openrouter(system_prompt, user_prompt)
    return await _run_query_sdk(system_prompt, user_prompt)


async def _run_query_sdk(system_prompt: str, user_prompt: str) -> str:
    """Legacy Agent SDK path (PROVIDER=sdk), kept as fallback.

    No tools are attached (pure text in, JSON out) so the only work here is
    draining the async iterator and joining text.
    """
    stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

    async def _drain() -> str:
        chunks: list[str] = []
        options = _sdk_options(system_prompt, stderr_cb=stderr_tail.append)
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "".join(chunks)

    try:
        # G15: without a ceiling a wedged CLI subprocess hangs the route forever.
        return await asyncio.wait_for(_drain(), timeout=QUERY_TIMEOUT_S)
    except BaseException:
        # Surface the child's stderr — the only forensic evidence on a wedge.
        if stderr_tail:
            logger.warning(
                "judgment CLI stderr tail (%d lines):\n%s",
                len(stderr_tail), "\n".join(stderr_tail),
            )
        else:
            logger.warning("judgment CLI produced no stderr before failure")
        raise


# G22: the SDK replaces the CLI's non-zero-exit ProcessError with
# "Claude Code returned an error result: <subtype>", and a crashed child can
# report the self-contradictory subtype "success". These are transport
# failures, not model output — retrying them with a "your response failed
# validation" prompt lies to the model and burns the retry on a doomed re-ask.
_TRANSIENT_MARKERS = (
    "error result",          # SDK's ProcessError replacement text
    "processerror",
    "exit code",
    "timed out",             # asyncio.TimeoutError str() is empty; matched by type below
    "connection",
    "server error",          # httpx HTTPStatusError 5xx text (OpenRouter path)
    "too many requests",
    "429",
)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, OSError)):
        return True
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    return any(m in str(exc).lower() for m in _TRANSIENT_MARKERS)


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(raw: str) -> Any:
    """Parse a JSON object/array out of raw model text.

    Tries the whole string first, then a fenced ```json block, then the
    outermost {...} / [...] span — models frequently wrap JSON in prose or
    a code fence despite instructions.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    fence = _FENCE_RE.search(raw)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except (json.JSONDecodeError, TypeError):
                continue

    raise ValueError(f"no JSON object/array found in response: {raw[:200]!r}")


async def _call_and_validate(
    ctx: RunContext,
    label: str,
    system_prompt: str,
    user_prompt: str,
    validate: Any,
    *,
    max_attempts: int = 2,
) -> Any:
    """Shared call + parse + validate + one-retry machinery for all four calls.

    Retry semantics (G22): a parse/validation failure retries once with the
    error appended so the model can correct itself; a transient SDK/transport
    failure retries once with the ORIGINAL prompt after a short delay — the
    model never saw the first attempt, so validation feedback would be a lie.
    """
    ctx.consume(label)
    last_error: str | None = None
    last_transient = False
    for attempt in range(1, max_attempts + 1):
        ctx.attempts_made += 1
        prompt = user_prompt
        if attempt == 2 and not last_transient:
            prompt = (
                f"{user_prompt}\n\n"
                f"Your previous response failed validation with this error:\n"
                f"{last_error}\n"
                f"Return ONLY corrected JSON matching the schema — no prose, no markdown fence."
            )
        # G24: charge the persistent ledger per real SDK attempt, OUTSIDE the
        # try — a spent budget must abort the call, not trigger the retry arm.
        if ctx.charge is not None:
            ctx.charge(f"{label} attempt {attempt}")
        t0 = time.monotonic()
        try:
            raw = await _run_query(system_prompt, prompt)
            parsed = _extract_json(raw)
            validate(parsed)
            logger.info("%s attempt %d ok in %.1fs", label, attempt, time.monotonic() - t0)
            return parsed
        except Exception as exc:  # noqa: BLE001 — any parse/validation/SDK failure retries once
            last_transient = _is_transient(exc)
            last_error = str(exc) or type(exc).__name__
            if last_transient and "error result: success" in last_error:
                # self-contradictory SDK string — name what actually happened
                last_error = (
                    "SDK subprocess exited non-zero while reporting subtype "
                    "'success' (transient CLI crash): " + last_error
                )
            logger.warning(
                "%s attempt %d failed in %.1fs (%s): %s",
                label, attempt, time.monotonic() - t0,
                "transient" if last_transient else "validation", last_error,
            )
            if attempt < max_attempts and last_transient:
                await asyncio.sleep(TRANSIENT_RETRY_DELAY_S)
    raise JudgmentError(f"{label} failed after {max_attempts} attempt(s): {last_error}")


def _run_sync(coro: Any) -> Any:
    """Run an async judgment coroutine from a sync FastAPI route handler."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    else:
        # Already inside a running loop (e.g. an async test) — the caller
        # should await the coroutine directly instead of calling the sync
        # wrapper in that context.
        raise RuntimeError(
            "_run_sync called from within a running event loop; "
            "await the underlying async function instead"
        )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _require_keys(d: dict, keys: tuple[str, ...], where: str) -> None:
    if not isinstance(d, dict):
        raise ValueError(f"{where}: expected an object, got {type(d).__name__}")
    missing = [k for k in keys if k not in d]
    if missing:
        raise ValueError(f"{where}: missing keys {missing}")


def _is_hhmm(value: Any) -> bool:
    return isinstance(value, str) and bool(_HHMM_RE.match(value))


# ---------------------------------------------------------------------------
# Call #1 — audit_pipeline (Phase 0.9)
# ---------------------------------------------------------------------------

AUDIT_SYSTEM_PROMPT = """You are the TDTB pipeline auditor — call #1 of a strict 4-call \
judgment budget in a deterministic day-planning app.

Role: produce a non-blocking report card on the day's pipeline data, mirroring the \
tdtb-bridger-vault skill's "0.9 Assignment-Pipeline Audit" phase:
- Base-health: flag inconsistent status-exclusion sets between pool/assigned data if evident.
- Unassigned candidates: consume the already-supplied pool of structurally admitted items \
and exclude anything already assigned. Do not apply a second critical/7-day/overdue \
membership filter or any legacy composite score to select, filter, rank, or explain \
candidates. Show up to five in deterministic order: deadline, urgency, return, stable name. \
Render deadline, urgency, and return as separate reasons; blank metadata is unknown/unset. \
This is a non-blocking upstream assignment nudge: never add unassigned work directly to \
today's plan. It is not a composite winner. An offer may set assigned true upstream only.
- Stale assigned: from assigned items, flag any with a deadline long past, or clearly stale \
(no recent activity signal in the data).
- Count sanity: flag any pool_count/assigned_count mismatch against the actual list lengths, \
or any item present in both pool and assigned lists that looks contradictory.

This is a REPORT, never a gate — nothing here blocks the run. Output ONLY a single JSON \
object (no prose, no markdown fence) with this exact shape:

{
  "anomalies": [{"kind": "base_health"|"count_sanity"|"other", "message": "<=200 char string"}],
  "unassigned_candidates": [{"name": "string", "path": "string", "reason": "<=140 char string"}],
  "stale_assigned": [{"name": "string", "path": "string", "reason": "<=140 char string"}]
}

Cap unassigned_candidates and stale_assigned at 5 entries each. Empty lists are valid when \
nothing qualifies — never invent findings."""


def _validate_audit_report(d: Any) -> None:
    _require_keys(d, ("anomalies", "unassigned_candidates", "stale_assigned"), "AuditReport")
    for key in ("anomalies", "unassigned_candidates", "stale_assigned"):
        if not isinstance(d[key], list):
            raise ValueError(f"AuditReport.{key}: expected a list")
    if len(d["unassigned_candidates"]) > 5:
        raise ValueError("AuditReport.unassigned_candidates: exceeds 5")
    if len(d["stale_assigned"]) > 5:
        raise ValueError("AuditReport.stale_assigned: exceeds 5")
    for a in d["anomalies"]:
        _require_keys(a, ("kind", "message"), "AuditReport.anomalies[]")
        if len(a["message"]) > 200:
            raise ValueError("AuditReport.anomalies[].message: exceeds 200 chars")
    for c in d["unassigned_candidates"]:
        _require_keys(c, ("name", "path", "reason"), "AuditReport.unassigned_candidates[]")
        if len(c["reason"]) > 140:
            raise ValueError("AuditReport.unassigned_candidates[].reason: exceeds 140 chars")
    for s in d["stale_assigned"]:
        _require_keys(s, ("name", "path", "reason"), "AuditReport.stale_assigned[]")
        if len(s["reason"]) > 140:
            raise ValueError("AuditReport.stale_assigned[].reason: exceeds 140 chars")


AuditReport = dict  # JSON-shape alias; see _validate_audit_report for the contract.


async def audit_pipeline_async(run_data: dict, ctx: RunContext) -> "AuditReport":
    user_prompt = (
        "Audit this run's pipeline data. Return ONLY the JSON object described in your "
        "system prompt.\n\nrun_data:\n" + json.dumps(run_data, default=str)
    )
    return await _call_and_validate(
        ctx, "audit_pipeline", AUDIT_SYSTEM_PROMPT, user_prompt, _validate_audit_report
    )


def audit_pipeline(run_data: dict, ctx: RunContext | None = None) -> "AuditReport":
    ctx = ctx or RunContext()
    return _run_sync(audit_pipeline_async(run_data, ctx))


# ---------------------------------------------------------------------------
# Call #2 — suggest_digest (Phase 2)
# ---------------------------------------------------------------------------

DIGEST_SYSTEM_PROMPT = """You are the TDTB digest suggester — call #2 of a strict 4-call \
judgment budget in a deterministic day-planning app.

Role: from the "suggested" pool of a deterministically-ranked digest (already assigned items \
are excluded), pick AT MOST 5 items worth a one-line nudge, mirroring the tdtb-bridger-vault \
skill's Phase 2 "Suggestions" (summit items not yet assigned, past-deadline attention items, \
overdue/due-today intervals, capacity warnings). Do not re-rank beyond the order given — the \
ranking is already deterministic backend logic; you are selecting and writing the reason text.

Assign each suggestion a tier from: "summit", "attention", "interval", "capacity", "other".

Output ONLY a single JSON object (no prose, no markdown fence) with this exact shape:

{
  "suggestions": [
    {"id": "string (item name or path from the input)", "reason": "<=140 char string", "tier": "summit"|"attention"|"interval"|"capacity"|"other"}
  ]
}

suggestions MUST have at most 5 entries. Every id MUST reference an item actually present in \
the digest's suggested pool — never invent an item."""


def _validate_digest_suggestions(d: Any) -> None:
    _require_keys(d, ("suggestions",), "DigestSuggestions")
    if not isinstance(d["suggestions"], list):
        raise ValueError("DigestSuggestions.suggestions: expected a list")
    if len(d["suggestions"]) > MAX_SUGGESTIONS:
        raise ValueError(
            f"DigestSuggestions.suggestions: {len(d['suggestions'])} exceeds max "
            f"{MAX_SUGGESTIONS}"
        )
    valid_tiers = {"summit", "attention", "interval", "capacity", "other"}
    for s in d["suggestions"]:
        _require_keys(s, ("id", "reason", "tier"), "DigestSuggestions.suggestions[]")
        if len(s["reason"]) > 140:
            raise ValueError("DigestSuggestions.suggestions[].reason: exceeds 140 chars")
        if s["tier"] not in valid_tiers:
            raise ValueError(f"DigestSuggestions.suggestions[].tier: invalid {s['tier']!r}")


DigestSuggestions = dict


async def suggest_digest_async(digest: dict, config: dict, ctx: RunContext) -> "DigestSuggestions":
    user_prompt = (
        "Select at most 5 digest suggestions. Return ONLY the JSON object described in your "
        "system prompt.\n\ndigest:\n" + json.dumps(digest, default=str)
        + "\n\nconfig:\n" + json.dumps(config, default=str)
    )
    return await _call_and_validate(
        ctx, "suggest_digest", DIGEST_SYSTEM_PROMPT, user_prompt, _validate_digest_suggestions
    )


def suggest_digest(digest: dict, config: dict, ctx: RunContext | None = None) -> "DigestSuggestions":
    ctx = ctx or RunContext()
    return _run_sync(suggest_digest_async(digest, config, ctx))


# ---------------------------------------------------------------------------
# Call #3 — adjust_freetext (Phase 3)
# ---------------------------------------------------------------------------

ADJUST_SYSTEM_PROMPT = """You are the TDTB free-text adjustment translator — call #3 of a \
strict 4-call judgment budget in a deterministic day-planning app.

Role: translate one free-text user instruction into structured ops, mirroring the \
tdtb-bridger-vault skill's Phase 3 chat commands ("drop X", "add X", "N blocks for X", \
"complete X", "deassign X", "remove X", "N blocks for X" / retime asks). Valid ops:
- "complete": mark an item done (id required)
- "deassign": clear an assigned item's assigned flag (id required)
- "remove": archive/drop an item from today's plan (id required)
- "add": pull an item into today's plan (id required; optional args.blocks)
- "retime": change an item's duration/blocks (id required; args.blocks required, a number \
0-8 in 0.5 increments per the skill's 15-min granularity)

Every id MUST reference an item actually present in the supplied digest — never invent an \
item. If the instruction is ambiguous or references an item not in the digest, return an \
empty ops list rather than guessing.

Output ONLY a single JSON object (no prose, no markdown fence) with this exact shape:

{
  "ops": [
    {"op": "complete"|"deassign"|"remove"|"add"|"retime", "id": "string", "args": {}}
  ]
}

args is an object (may be empty {}); for "retime" it MUST include "blocks" (a number)."""


def _validate_adjustment_plan(d: Any) -> None:
    _require_keys(d, ("ops",), "AdjustmentPlan")
    if not isinstance(d["ops"], list):
        raise ValueError("AdjustmentPlan.ops: expected a list")
    valid_ops = {"complete", "deassign", "remove", "add", "retime"}
    for o in d["ops"]:
        _require_keys(o, ("op", "id", "args"), "AdjustmentPlan.ops[]")
        if o["op"] not in valid_ops:
            raise ValueError(f"AdjustmentPlan.ops[].op: invalid {o['op']!r}")
        if not isinstance(o["args"], dict):
            raise ValueError("AdjustmentPlan.ops[].args: expected an object")
        if o["op"] == "retime" and not isinstance(o["args"].get("blocks"), (int, float)):
            raise ValueError("AdjustmentPlan.ops[].args.blocks: required numeric for retime")


AdjustmentPlan = dict


async def adjust_freetext_async(instruction: str, digest: dict, ctx: RunContext) -> "AdjustmentPlan":
    user_prompt = (
        f"Translate this instruction into ops. Return ONLY the JSON object described in your "
        f"system prompt.\n\ninstruction: {instruction!r}\n\ndigest:\n"
        + json.dumps(digest, default=str)
    )
    return await _call_and_validate(
        ctx, "adjust_freetext", ADJUST_SYSTEM_PROMPT, user_prompt, _validate_adjustment_plan
    )


def adjust_freetext(instruction: str, digest: dict, ctx: RunContext | None = None) -> "AdjustmentPlan":
    ctx = ctx or RunContext()
    return _run_sync(adjust_freetext_async(instruction, digest, ctx))


# ---------------------------------------------------------------------------
# Call #4 — propose_sequence (Phase 4)
# ---------------------------------------------------------------------------

SEQUENCE_SYSTEM_PROMPT = """You are the TDTB sequencing proposer — call #4 of a strict 4-call \
judgment budget in a deterministic day-planning app.

Role: propose start/end times for assigned items around fixed anchored blocks, mirroring the \
tdtb-bridger-vault skill's Phase 4 placement passes: calendar events and hard/window anchored \
blocks are fixed (place assigned items around them, never overlapping a non-permeable anchored \
block); zone preferences (before_work / work_hours / after_work / evening / weekend / any) are \
soft constraints — prefer a compatible window, flag "zone_violation" only when none exists; a \
latest_start on an item means it must start at or before that time, flagged \
"latest_start_violation" if forced later; never drop an item (never-bump) — every assigned item \
gets a slot even if it means a flagged violation.

TIME FRAME — the day is already in progress. config.time carries the live frame: \
"anchor" is the earliest schedulable moment (now, rounded up) and "effective_eod" is the \
hard-stop-aware end of day. EVERY assigned item's start MUST be at or after config.time.anchor — \
never schedule into the past. Fit the whole sequence between anchor and effective_eod; if the \
items cannot all fit, still place every item (never-bump) in order after the anchor, letting the \
overflow run past effective_eod (each such row earns a "past EOD" warning downstream) — \
overflowing late is acceptable, starting before the anchor is not. Effective EOD is a soft \
boundary: late blocks may run past it. Times remain same-day HH:MM, so a block that would cross \
midnight ends at 23:59 instead; never emit an early-morning end after a late-evening start. \
Overflow items that cannot fit in free gaps park at or after effective_eod \
even if that overlaps a late anchored block — in the overflow tail (start >= effective_eod) \
such overlaps are tolerated and merely flagged; BEFORE effective_eod, never overlap a \
non-permeable anchored block.

DURATIONS ARE FIXED INPUTS BY DEFAULT: every assigned item's duration is its "blocks" field × \
30 minutes; an item with no "blocks" field is exactly 1 block = 30 minutes. Each sequence row's \
end minus start MUST equal that duration unless the metadata-derived calendar-companion rule \
provides an explicit event span. That rule preserves the source estimate as metadata while \
using the matching event's full interval for placement. You choose WHERE an item goes, never \
how LONG it is, except for that one deterministic companion rule. Anchored blocks keep their \
configured Duration exactly except when the same-day 23:59 boundary truncates a late block.

PLACEMENT QUALITY (soft preferences, applied after every hard rule above): \
(1) BATCH SHALLOW WORK — place 1-block (30 min) items adjacent to each other in runs, \
not scattered between larger items; task-switching between deep and shallow work is the \
cost to minimize. (2) PROTECT DEEP WORK — give each multi-block (>= 2 blocks) item the \
largest uninterrupted free window available; never sandwich a deep item tightly between \
two anchored blocks when a wider window exists. (3) ENERGY PLACEMENT — when the anchor is \
before 12:00 (a full-day plan): analytic/deep items go late morning, shallow/admin items \
go in the 13:00-15:00 trough, lighter/creative items late afternoon. When the anchor is \
12:00 or later, skip the morning rule but still prefer shallow/admin earliest and deep \
work in the widest remaining window. These preferences NEVER override the hard rules, \
zone compatibility, or an item's fixed duration.

WORKOUT CO-LOCATION: when a fixed calendar event is itself a gym/workout/exercise event, \
every workout-typed item (assigned item OR anchored workout block) MUST be placed \
overlapping that event's window (same gym trip — they happen together), starting at or \
after the event's start. This co-location rule beats a workout block's configured Start \
time, and it is the one case where overlapping a calendar event is correct. (If the workout \
event starts before 12:00, the STANDING RULE below still wins — no before-noon placement.)

OVERLAP POLICY: config.overlap_permissions_raw is the verbatim configured policy and \
config.resolved_zones lists today's non-consuming Template zones. Template zones are \
permeable backdrops, never capacity or walls. When you intentionally overlap a placed \
primary row with an anchored companion under that policy, emit one overlap_grant with the \
exact stable IDs, exact intervals, concise reason, and copy \
config.planning_config_fingerprint exactly. Do not grant hard-safety exceptions.

TASK BUNDLING: overlap between two movable tasks is also allowed when reasoning justifies \
their compatibility — a driving errand can ride adjacent to or overlap another trip (e.g. \
"Return coffee burr grind" rides the Haircut trip), a passive item can overlap an active \
one, and compatible outside chores may bundle into one named context block (e.g. "chores \
outside"). Prefer bundling compatible errands/driving items over scattering them. For \
EVERY reasoned task-task overlap emit one overlap_grant per overlapping pair — exact \
stable IDs, exact intervals, a concise reason that names the context block when bundling, \
and config.planning_config_fingerprint copied exactly. An overlap without a grant is a \
defect the user must review. Bundling never changes an item's fixed duration unless the \
metadata-derived calendar-companion rule explicitly supplies the event span, and never \
overrides a hard rule.

METADATA-AWARE PLACEMENT: `relates_to`, `tags`, `labels`, `mint_session`, and \
`placement_window` are supplied as item metadata. Follow the derived semantic rules in the \
user payload. A selected Mint session is a HARD placement wall: only the Mint row itself \
occupies its exact window, and no other assigned row may overlap it — place movable work \
around every selected Mint interval, never inside one. Keep related parent/child rows and \
grouped systems rows as separate sequence entries. Parent children must remain inside their \
parent interval. All systems-tagged rows share one start. A unique same-activity calendar \
companion uses the event's exact span. Emit an exact overlap_grant for every required \
relationship.

STANDING RULE — apply this before every other placement choice: NEVER place a workout block \
(any item whose type/tag indicates a workout, exercise, or fitness block) starting before \
12:00 (noon). Workout blocks go in the afternoon or evening only. The ONLY exception is a \
Press micro-adventure item whose TDTB config explicitly sets zone "before_work" — that one \
item may be placed before noon. Every other workout-typed item that would otherwise be pushed \
before noon must instead be scheduled at or after 12:00, even if that means flagging a \
zone_violation or latest_start_violation elsewhere in the sequence.

Output ONLY a single JSON object (no prose, no markdown fence) with this exact shape:

{
  "sequence": [
    {"id": "string", "start": "HH:MM", "end": "HH:MM", "zone": "string"}
  ],
  "overlap_grants": [
    {"primary_id": "string", "companion_id": "string",
     "primary_interval": {"start": "HH:MM", "end": "HH:MM"},
     "companion_interval": {"start": "HH:MM", "end": "HH:MM"},
     "reason": "string", "planning_config_fingerprint": "string"}
  ]
}

Times are 24-hour "HH:MM" strings. Every assigned item and anchored_block passed to you MUST \
appear exactly once in sequence, in chronological start-time order. Never invent an item id."""


def _validate_sequence_proposal(
    d: Any,
    config: dict | None = None,
    assigned: list | None = None,
    anchored_blocks: list | None = None,
) -> None:
    _require_keys(d, ("sequence",), "SequenceProposal")
    if not isinstance(d["sequence"], list):
        raise ValueError("SequenceProposal.sequence: expected a list")
    if not d["sequence"]:
        raise ValueError("SequenceProposal.sequence: must not be empty")
    grants = d.setdefault("overlap_grants", [])
    if not isinstance(grants, list):
        raise ValueError("SequenceProposal.overlap_grants: expected a list")
    for grant in grants:
        _require_keys(
            grant,
            ("primary_id", "companion_id", "primary_interval",
             "companion_interval", "reason", "planning_config_fingerprint"),
            "SequenceProposal.overlap_grants[]",
        )
        for key in ("primary_interval", "companion_interval"):
            interval = grant[key]
            _require_keys(interval, ("start", "end"), f"overlap_grants[].{key}")
            if not _is_hhmm(interval["start"]) or not _is_hhmm(interval["end"]):
                raise ValueError(f"overlap_grants[].{key}: invalid interval")

    press_before_work_ids: set[str] = _press_before_work_ids(config)
    anchor = str(((config or {}).get("time") or {}).get("anchor") or "")
    # Duration budgets: blocks × 30, default 1 block. The model places items,
    # it never sizes them (shakedown 2026-07-14: Press inflated to 75 min).
    # Cap-only (<=) — same-day truncation of late blocks is legal.
    budgets: dict[str, int] = {}
    for item in assigned or []:
        name = item.get("name") if isinstance(item, dict) else None
        if not name:
            continue
        blocks = item.get("blocks")
        n = blocks if isinstance(blocks, (int, float)) and blocks > 0 else 1
        budgets[str(name)] = int(n * 30)
    constraints = placement_rules.derive_constraints(
        assigned or [], anchored_blocks or []
    )
    duration_overrides = placement_rules.effective_duration_overrides(constraints)

    for row in d["sequence"]:
        _require_keys(row, ("id", "start", "end", "zone"), "SequenceProposal.sequence[]")
        if not _is_hhmm(row["start"]):
            raise ValueError(f"SequenceProposal.sequence[].start: not HH:MM {row['start']!r}")
        if not _is_hhmm(row["end"]):
            raise ValueError(f"SequenceProposal.sequence[].end: not HH:MM {row['end']!r}")
        # Same-day contract: a clearly intended midnight rollover is not a
        # validation retry. Truncate it at the final minute. Keep this narrow
        # so ordinary reversed ranges (14:00-13:00) remain invalid.
        if row["start"] >= "18:00" and row["end"] <= "06:00":
            row["end"] = "23:59"
        if row["end"] <= row["start"]:
            raise ValueError(
                f"SequenceProposal.sequence[]: end {row['end']!r} not after start "
                f"{row['start']!r} for {row['id']!r}"
            )
        is_workout = _sequence_is_workout_item(
            {"id": row["id"], "zone": row.get("zone", "")}
        )
        # 2026-07-21 (Adam, T14): past-anchor placement no longer fails schema
        # validation — every schema retry is a BILLED call, and the 07-21 run
        # burned 2 on a single past row. The prompt still forbids it
        # (earliest_start per item, G32); a violation now flows through to
        # validate_sequence as a soft placement warning / LD24 acceptable
        # defect instead of blocking the proposal.
        if is_workout and row["start"] < "12:00" and row["id"] not in press_before_work_ids:
            raise ValueError(
                f"SequenceProposal.sequence[]: workout block {row['id']!r} placed at "
                f"{row['start']!r} — before noon is forbidden except a Press "
                f"before_work exception"
            )
        budget = duration_overrides.get(str(row["id"])) or budgets.get(str(row["id"]))
        if budget is not None:
            sh, sm = int(row["start"][:2]), int(row["start"][3:5])
            eh, em = int(row["end"][:2]), int(row["end"][3:5])
            span = (eh * 60 + em) - (sh * 60 + sm)
            if span > budget:
                # G21: name the exact fix — the retry prompt echoes this
                # verbatim, so a prescriptive end time beats restating the rule
                # (live 2026-07-16: 'Press (Todoist)' re-stretched 30→90 on
                # every attempt against the rule-only message).
                em_fix = (sh * 60 + sm) + budget
                if str(row["id"]) in duration_overrides:
                    raise ValueError(
                        f"SequenceProposal.sequence[]: {row['id']!r} must use its "
                        f"matched calendar event span of {budget} min; set end to "
                        f"exactly {em_fix // 60:02d}:{em_fix % 60:02d}"
                    )
                raise ValueError(
                    f"SequenceProposal.sequence[]: {row['id']!r} spans {span} min but its "
                    f"duration_minutes is {budget} — durations are fixed inputs, never "
                    f"lengthened. Keep start {row['start']!r} and set end to exactly "
                    f"{em_fix // 60:02d}:{em_fix % 60:02d} (or move the item, keeping "
                    f"end - start = {budget} min)"
                )

    semantic_errors = placement_rules.validate_constraints(
        d,
        constraints,
        planning_config_fingerprint=(config or {}).get("planning_config_fingerprint"),
    )
    if semantic_errors:
        raise ValueError("SequenceProposal semantic placement: " + "; ".join(semantic_errors))

    # Chronological order is derivable from the (validated) start times, so
    # normalize instead of rejecting — a mis-ordered but otherwise valid
    # proposal was burning the one retry (T11 live 502, 2026-07-14).
    d["sequence"].sort(key=lambda r: tuple(int(p) for p in r["start"].split(":")))


SequenceProposal = dict


def placement_context_instruction(config: dict | None) -> str:
    """The `## Placement Context` prose block, framed for the sequence prompt.

    Allocator-rewrite T5 / locked decision 10: wellbeing and placement
    preferences are config prose, not a subsystem — Adam writes rules like
    "no screen-heavy work after 21:00" in the config note and they ride into
    the ONE billed sequence call verbatim. Prompt-side only: nothing here
    validates, scores, or blocks a proposal, so an absent or empty section
    degrades to the exact pre-T5 prompt (empty string, no separator).

    Framed as PREFERENCES, not hard constraints, on purpose — a placement
    rule that fought a real constraint (a fixed anchor, a duration budget)
    would burn the single no-retry call on an unsatisfiable proposal.
    """
    section = (config or {}).get(config_reader.PLACEMENT_CONTEXT_SECTION)
    raw = section.get("_body") if isinstance(section, dict) else None
    body = config_reader.sanitize_placement_context(str(raw or ""))
    if not body:
        return ""
    return (
        "\n\nPLACEMENT CONTEXT (the user's own words — treat as strong "
        "PREFERENCES that shape placement where the hard rules leave a choice; "
        "never violate an anchor, a duration_minutes budget, or the overlap "
        "policy to satisfy one):\n" + body
    )


def placement_semantic_instruction(
    assigned: list[dict[str, Any]], anchored_blocks: list[dict[str, Any]]
) -> str:
    """Render deterministic placement constraints for the one model call."""
    lines: list[str] = []
    for constraint in placement_rules.derive_constraints(assigned, anchored_blocks):
        kind = constraint["kind"]
        if kind == "parent_child":
            lines.append(
                f"Parent/child pair: {constraint['child_id']} is a sub-item of "
                f"{constraint['parent_id']}. Start the child with the parent when "
                "possible, keep the child fully within the parent's interval, and "
                "emit one exact overlap_grant."
            )
        elif kind == "systems_group":
            lines.append(
                "Systems block: " + ", ".join(constraint["item_ids"]) + ". Give every "
                "systems-tagged row the same start time; keep separate rows and each "
                "row's own duration, with one exact overlap_grant per pair."
            )
        elif kind == "calendar_companion":
            interval = constraint["event_interval"]
            lines.append(
                f"Same-activity companion: {constraint['item_id']} matches calendar "
                f"event {constraint['event_id']}. Use exactly {interval['start']}-"
                f"{interval['end']} for the activity, treating the event span as its "
                "effective duration, and emit an exact overlap_grant."
            )

    if not lines:
        return ""
    return (
        "\n\nSEMANTIC PLACEMENT RULES (deterministic constraints derived from item "
        "metadata):\n- " + "\n- ".join(lines)
    )


async def propose_sequence_async(
    assigned: list, config: dict, anchored_blocks: list, ctx: RunContext
) -> "SequenceProposal":
    # G21: spell each item's fixed length out as duration_minutes so the model
    # copies a number instead of deriving blocks × 30 (the derivation is where
    # the lengthening failures happened).
    def _minutes(item: dict) -> int:
        blocks = item.get("blocks")
        n = blocks if isinstance(blocks, (int, float)) and blocks > 0 else 1
        return int(n * 30)

    anchor = str(((config or {}).get("time") or {}).get("anchor") or "")
    earliest_start = anchor if _is_hhmm(anchor) else None
    prompt_assigned = [
        {
            **item,
            "duration_minutes": _minutes(item),
            **({"earliest_start": earliest_start} if earliest_start else {}),
        }
        if isinstance(item, dict) else item
        for item in assigned
    ]
    anchor_instruction = ""
    if earliest_start:
        anchor_instruction = (
            f"\n\nLIVE-ANCHOR CHECK: every assigned item has earliest_start "
            f"{earliest_start!r}. For every row, start >= earliest_start. For example, "
            f"with earliest_start {earliest_start}, 12:00 is invalid even for a "
            f"before_work item; place it at {earliest_start} or later."
        )
    user_prompt = (
        "Propose a sequence. Return ONLY the JSON object described in your system prompt.\n\n"
        "Each assigned item carries duration_minutes — every sequence row's end minus start "
        "MUST equal that item's duration_minutes exactly unless a derived calendar-companion "
        "rule supplies an explicit event span."
        + anchor_instruction
        + placement_context_instruction(config)
        + placement_semantic_instruction(assigned, anchored_blocks)
        + "\n\n"
        "assigned:\n" + json.dumps(prompt_assigned, default=str)
        + "\n\nconfig:\n" + json.dumps(config, default=str)
        + "\n\nanchored_blocks:\n" + json.dumps(anchored_blocks, default=str)
    )

    def _validate(d: Any) -> None:
        _validate_sequence_proposal(d, config, assigned, anchored_blocks)

    return await _call_and_validate(
        ctx, "propose_sequence", SEQUENCE_SYSTEM_PROMPT, user_prompt, _validate,
        max_attempts=1,
    )


def propose_sequence(
    assigned: list, config: dict, anchored_blocks: list, ctx: RunContext | None = None
) -> "SequenceProposal":
    ctx = ctx or RunContext()
    return _run_sync(propose_sequence_async(assigned, config, anchored_blocks, ctx))


# ---------------------------------------------------------------------------
# OpenRouter strict structured-output contracts (T11 qualification)
# ---------------------------------------------------------------------------

def _strict_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}


_STRING = {"type": "string"}
_NO_EXTRA = {"additionalProperties": False}

JUDGMENT_RESPONSE_SCHEMAS = {
    AUDIT_SYSTEM_PROMPT: _strict_schema("audit_report", {
        "type": "object", "properties": {
            "anomalies": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["base_health", "count_sanity", "other"]},
                "message": {"type": "string", "maxLength": 200},
            }, "required": ["kind", "message"], **_NO_EXTRA}},
            "unassigned_candidates": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
                "name": _STRING, "path": _STRING, "reason": {"type": "string", "maxLength": 140},
            }, "required": ["name", "path", "reason"], **_NO_EXTRA}},
            "stale_assigned": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {
                "name": _STRING, "path": _STRING, "reason": {"type": "string", "maxLength": 140},
            }, "required": ["name", "path", "reason"], **_NO_EXTRA}},
        }, "required": ["anomalies", "unassigned_candidates", "stale_assigned"], **_NO_EXTRA,
    }),
    DIGEST_SYSTEM_PROMPT: _strict_schema("digest_suggestions", {
        "type": "object", "properties": {"suggestions": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "properties": {
                "id": _STRING, "reason": {"type": "string", "maxLength": 140},
                "tier": {"type": "string", "enum": ["summit", "attention", "interval", "capacity", "other"]},
            }, "required": ["id", "reason", "tier"], **_NO_EXTRA,
        }}}, "required": ["suggestions"], **_NO_EXTRA,
    }),
    ADJUST_SYSTEM_PROMPT: _strict_schema("adjustment_plan", {
        "type": "object", "properties": {"ops": {"type": "array", "items": {
            "type": "object", "properties": {
                "op": {"type": "string", "enum": ["complete", "deassign", "remove", "add", "retime"]},
                "id": _STRING, "args": {"type": "object"},
            }, "required": ["op", "id", "args"], **_NO_EXTRA,
        }}}, "required": ["ops"], **_NO_EXTRA,
    }),
    SEQUENCE_SYSTEM_PROMPT: _strict_schema("sequence_proposal", {
        "type": "object", "properties": {"sequence": {"type": "array", "items": {
            "type": "object", "properties": {
                "id": _STRING, "start": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"},
                "end": {"type": "string", "pattern": "^[0-2][0-9]:[0-5][0-9]$"}, "zone": _STRING,
            }, "required": ["id", "start", "end", "zone"], **_NO_EXTRA,
        }}, "overlap_grants": {"type": "array", "items": {
            "type": "object", "properties": {
                "primary_id": _STRING, "companion_id": _STRING,
                "primary_interval": {"type": "object", "properties": {
                    "start": _STRING, "end": _STRING,
                }, "required": ["start", "end"], **_NO_EXTRA},
                "companion_interval": {"type": "object", "properties": {
                    "start": _STRING, "end": _STRING,
                }, "required": ["start", "end"], **_NO_EXTRA},
                "reason": _STRING, "planning_config_fingerprint": _STRING,
            }, "required": ["primary_id", "companion_id", "primary_interval",
                            "companion_interval", "reason",
                            "planning_config_fingerprint"], **_NO_EXTRA,
        }}}, "required": ["sequence", "overlap_grants"], **_NO_EXTRA,
    }),
}


def _response_format_for(system_prompt: str) -> dict[str, Any]:
    try:
        return JUDGMENT_RESPONSE_SCHEMAS[system_prompt]
    except KeyError as exc:
        raise ValueError("no strict response schema for judgment prompt") from exc

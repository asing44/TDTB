/* SetupDrawer — editable day setup: anchor/EOD/buffering, anchored block
   on/skip/time overrides, and the three captures (Intention, For Meegy,
   Stoic — locked decision 10). Saves session/day-scoped state only. */

import { useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { display12h, formatBlockAmount, formatDurationMinutes } from "../model/time";
import {
  initialMintSessionIds as resolveInitialMintSessionIds,
  mintMinutesForSessionIds,
  mintSessionIdsForMinutes,
  wallFreeMintSessionIds,
  MINT_SESSION_MINUTES,
} from "../model/mint";
import type { AnchoredOverride, Buffering, DaySetup } from "../model/types";
import {
  anchoredBlocks,
  anchoredOverrideOf,
  validateAnchoredOverride,
} from "../model/anchored";
import { calendarWalls } from "../model/overflow";
import { effectiveAnchoredBlocks } from "../store/store";

export function SetupDrawer() {
  const s = useAppState();
  const { controller, store } = useApp();
  const availableMintSessions = s.inputs?.daySemantics.mintSessions ?? [];
  const savedMintOverride = s.daySetup.schedulable?.minting;
  const savedAllotment =
    typeof s.daySetup.workAllotmentMinutes === "number"
      ? s.daySetup.workAllotmentMinutes
      : s.inputs?.daySemantics.effectiveAllotmentMinutes ?? 0;
  const initialMintAnchor = s.daySetup.anchor ?? s.inputs?.time.anchor;
  // FEEDBACK-28: Mint choices are filtered against the current effective
  // fixed/work calendar walls — the August 17 incident selected Mint
  // 15:00-15:30 over the OPPD fixed wall at 15:00. The wall set is the same
  // non-permeable calendar walls the overflow scan already respects.
  const effectiveMintWalls = calendarWalls(effectiveAnchoredBlocks(s));
  const initialMintSessionIds = resolveInitialMintSessionIds(
    availableMintSessions,
    savedMintOverride,
    savedAllotment,
    initialMintAnchor,
    effectiveMintWalls,
  );
  const initialDraft: DaySetup = { ...s.daySetup };
  if (availableMintSessions.length > 0) {
    initialDraft.schedulable = {
      ...initialDraft.schedulable,
      minting: {
        ...(initialDraft.schedulable?.minting ?? {}),
        on: initialDraft.schedulable?.minting?.on !== false && initialMintSessionIds.length > 0,
        n: initialMintSessionIds.length,
        sessions: initialMintSessionIds,
      },
    };
  }
  const [draft, setDraft] = useState<DaySetup>(initialDraft);
  const automaticPreset = "__automatic__";
  const [preset, setPreset] = useState(
    s.daySetup.dayPreset ?? automaticPreset,
  );
  const [workAllotment, setWorkAllotment] = useState(String(
    availableMintSessions.length > 0
      ? initialMintSessionIds.length * MINT_SESSION_MINUTES
      : savedAllotment,
  ));
  const [allotmentMode, setAllotmentMode] = useState<"preserve" | "override" | "reset">(
    Object.prototype.hasOwnProperty.call(s.daySetup, "workAllotmentMinutes")
      ? s.daySetup.workAllotmentMinutes === null ? "reset" : "preserve"
      : "preserve",
  );
  const [saving, setSaving] = useState(false);
  const [liveCustom, setLiveCustom] = useState("");
  const close = () => store.dispatch({ type: "UI", patch: { setupOpen: false } });
  const dialog = useDialog(close);

  if (!s.ui.setupOpen || !s.inputs) return null;

  const mintAnchor = draft.anchor ?? s.inputs.time.anchor;
  const mintOverride = draft.schedulable?.minting;
  const selectedMintSessionIds =
    mintOverride?.on === false
      ? []
      : Array.isArray(mintOverride?.sessions)
        ? wallFreeMintSessionIds(availableMintSessions, mintOverride.sessions, effectiveMintWalls)
        : mintSessionIdsForMinutes(availableMintSessions, Number(workAllotment), mintAnchor, effectiveMintWalls);

  const setMintSelection = (ids: string[], anchor = mintAnchor) => {
    const sessions = wallFreeMintSessionIds(availableMintSessions, ids, effectiveMintWalls);
    const minutes = mintMinutesForSessionIds(availableMintSessions, sessions);
    setWorkAllotment(String(minutes));
    setAllotmentMode("override");
    setDraft((current) => ({
      ...current,
      anchor: anchor === mintAnchor ? current.anchor : anchor,
      schedulable: {
        ...current.schedulable,
        minting: {
          ...(current.schedulable?.minting ?? {}),
          on: sessions.length > 0,
          n: sessions.length,
          sessions,
        },
      },
    }));
  };

  // T19: free Live overrides — each control persists immediately via
  // /day-setup {micro_adventure}; never billed, never a history write.
  const micro = s.inputs.microAdventure;
  const shuffleLive = async () => {
    const pool = micro.pool;
    if (pool.length < 2) return;
    const idx = pool.findIndex((p) => p.id === micro.pick?.id);
    const next = pool[(idx + 1) % pool.length];
    if (next && next.id !== micro.pick?.id) await controller.setMicroAdventure(next);
  };

  const editableAnchors = effectiveAnchoredBlocks(s).filter((a) => a.kind !== "calendar");
  const sourceAnchor = (id: string) => s.inputs!.anchored.find((a) => a.id === id)!;
  const overrideOf = (id: string): AnchoredOverride => {
    const block = sourceAnchor(id);
    return anchoredOverrideOf(block, draft.anchored[id]);
  };

  const patchAnchored = (id: string, patch: Partial<AnchoredOverride>) =>
    setDraft((d) => ({
      ...d,
      anchored: { ...d.anchored, [id]: { ...overrideOf(id), ...patch } },
    }));

  const toggleMintSession = (id: string) => {
    const next = selectedMintSessionIds.includes(id)
      ? selectedMintSessionIds.filter((value) => value !== id)
      : [...selectedMintSessionIds, id];
    setMintSelection(next);
  };

  const setMintAllotment = (minutes: number) => {
    if (availableMintSessions.length === 0) {
      setWorkAllotment(String(minutes));
      setAllotmentMode("override");
      return;
    }
    setMintSelection(mintSessionIdsForMinutes(availableMintSessions, minutes, mintAnchor, effectiveMintWalls));
  };

  const setMintAnchor = (anchor: string) => {
    if (availableMintSessions.length === 0) {
      setDraft((current) => ({ ...current, anchor }));
      return;
    }
    const currentMinutes = mintMinutesForSessionIds(
      availableMintSessions,
      selectedMintSessionIds,
    );
    setMintSelection(
      mintSessionIdsForMinutes(availableMintSessions, currentMinutes, anchor, effectiveMintWalls),
      anchor,
    );
  };

  const save = async () => {
    if (anchorErrors.length > 0 || allotmentError) return;
    setSaving(true);
    const next: DaySetup = {
      ...draft,
      confirmed: true,
      dayPreset: preset === automaticPreset ? null : preset,
    };
    delete next.workAllotmentMinutes;
    if (availableMintSessions.length > 0) {
      // In session mode, checked rows and the daily Mint total are one value.
      // Persist the selected 30-minute total even when an older saved setup
      // carried a mismatched allotment. FEEDBACK-28: wall-conflicting rows are
      // dropped here too — the saved choice must never overlap a fixed/work
      // commitment, even when the draft was seeded from stale state.
      const sessions = wallFreeMintSessionIds(
        availableMintSessions,
        selectedMintSessionIds,
        effectiveMintWalls,
      );
      const minutes = mintMinutesForSessionIds(availableMintSessions, sessions);
      next.workAllotmentMinutes = minutes;
      next.schedulable = {
        ...next.schedulable,
        minting: {
          ...(next.schedulable?.minting ?? {}),
          on: minutes > 0,
          n: sessions.length,
          sessions,
        },
      };
    } else {
      if (allotmentMode === "reset") next.workAllotmentMinutes = null;
      if (allotmentMode === "override") next.workAllotmentMinutes = Number(workAllotment);
    }
    try {
      await controller.saveDaySetup(next);
      close();
    } finally {
      setSaving(false);
    }
  };

  const configAllotment = (presetName: string): number => {
    const semantics = s.inputs!.daySemantics;
    const selected = presetName === automaticPreset
      ? semantics.selectedPreset
      : semantics.availablePresets.find((candidate) => candidate.name === presetName) ?? null;
    return selected?.workAllotmentMinutes ?? semantics.defaultAllotmentMinutes;
  };
  const allotmentNumber = availableMintSessions.length > 0
    ? mintMinutesForSessionIds(availableMintSessions, selectedMintSessionIds)
    : Number(workAllotment);
  const allotmentError =
    workAllotment.trim() === "" ||
    !Number.isInteger(allotmentNumber) ||
    allotmentNumber < 0 ||
    allotmentNumber % 15 !== 0;
  const allotmentLabel = allotmentError
    ? "—"
    : allotmentNumber === 0
      ? "off"
      : formatDurationMinutes(allotmentNumber);

  const anchorErrors = editableAnchors.flatMap((a) =>
    validateAnchoredOverride(sourceAnchor(a.id), overrideOf(a.id), s.inputs!.time).errors.map(
      (error) => `${a.name}: ${error}`,
    ),
  );

  return (
    <>
      <div class="drawer-backdrop" onClick={close} />
      <div
        class="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Day setup"
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}
      >
        <button class="iconbtn drawer__close" onClick={close} aria-label="Close setup">
          ✕
        </button>
        <h2>Day setup</h2>

        {/* FEEDBACK-16: each section is a distinct card (setup-section) in a
            fixed scan order; Frame groups every day-frame field together. */}
        <section class="setup-section" aria-labelledby="setup-sec-frame">
          <div class="setup-section__head">
            <h3 id="setup-sec-frame">Frame</h3>
          </div>
          <div class="setup-section__body">
        <div class="field">
          <label for="setup-preset">Day preset</label>
          <select
            id="setup-preset"
            value={preset}
            onChange={(e) => {
              const value = (e.target as HTMLSelectElement).value;
              setPreset(value);
              const configured = configAllotment(value);
              if (availableMintSessions.length > 0) {
                setMintAllotment(configured);
              } else {
                setWorkAllotment(String(configured));
                setAllotmentMode("reset");
              }
            }}
          >
            <option value={automaticPreset}>
              Automatic — {s.inputs.daySemantics.selectedPreset?.name ?? "config default"}
            </option>
            {s.inputs.daySemantics.availablePresets.map((candidate) => (
              <option value={candidate.name} key={candidate.name}>{candidate.name}</option>
            ))}
          </select>
        </div>
        <div class="field">
          <label for="setup-work-allotment">
            {availableMintSessions.length > 0 ? "Mint allotment" : "Work allotment"}
          </label>
          {/* With concrete Mint sessions, the allotment is the checked-session
              total. Moving the slider and checking a row update each other. */}
          <input
            id="setup-work-allotment"
            type="range"
            class="field__range"
            min={0}
            max={availableMintSessions.length > 0 ? availableMintSessions.length * MINT_SESSION_MINUTES : 720}
            step={availableMintSessions.length > 0 ? MINT_SESSION_MINUTES : 15}
            value={allotmentNumber || 0}
            aria-valuetext={allotmentLabel}
            onInput={(e) => {
              setMintAllotment(Number((e.target as HTMLInputElement).value));
            }}
          />
          <span class="field__range-value">{allotmentLabel}</span>
          <span class="field__hint">
            {availableMintSessions.length > 0
              ? `${selectedMintSessionIds.length} of ${availableMintSessions.length} sessions checked · each session is 30min`
              : "0 disables Mint for today · 12hr max"}
          </span>
          {allotmentError && (
            <span class="field-error" role="alert">Use nonnegative 15-minute increments.</span>
          )}
          <button
            type="button"
            class="btn"
            onClick={() => {
              const configured = configAllotment(preset);
              if (availableMintSessions.length > 0) {
                setMintAllotment(configured);
              } else {
                setWorkAllotment(String(configured));
                setAllotmentMode("reset");
              }
            }}
          >
            Reset to config
          </button>
        </div>
        <div class="field">
          <label for="setup-anchor">Start (anchor)</label>
          <input
            id="setup-anchor"
            type="time"
            step={900}
            value={draft.anchor ?? s.inputs.time.anchor}
            onInput={(e) => setMintAnchor((e.target as HTMLInputElement).value)}
          />
        </div>
        <div class="field">
          <label for="setup-eod">End of day</label>
          <input
            id="setup-eod"
            type="time"
            step={900}
            value={draft.eod ?? s.inputs.time.configEod}
            onInput={(e) =>
              setDraft({ ...draft, eod: (e.target as HTMLInputElement).value })
            }
          />
        </div>
        <div class="field">
          <label for="setup-buffering">Buffering</label>
          <select
            id="setup-buffering"
            value={draft.buffering}
            onChange={(e) =>
              setDraft({
                ...draft,
                buffering: (e.target as HTMLSelectElement).value as Buffering,
              })
            }
          >
            <option value="standard">Standard (20%)</option>
            <option value="minimal">Minimal (11%)</option>
            <option value="off">Off</option>
          </select>
        </div>
          </div>
        </section>
        {availableMintSessions.length > 0 && (
          <section class="setup-section" aria-labelledby="setup-sec-mint">
            <div class="setup-section__head">
              <h3 id="setup-sec-mint">Mint sessions</h3>
            </div>
            <div class="setup-section__body">
            <p class="field__hint">
              Check the sessions for Mint. The allotment above stays in sync, so you can use either control.
            </p>
            {availableMintSessions.map((session) => {
              const checked = selectedMintSessionIds.includes(session.id);
              return (
                <label class="mint-session-row" key={session.id}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleMintSession(session.id)}
                    aria-label={`${checked ? "Disable" : "Enable"} ${session.name}`}
                  />
                  <span>{session.name}</span>
                  <span>{display12h(session.start)}–{display12h(session.end)}</span>
                </label>
              );
            })}
            </div>
          </section>
        )}

        <section class="setup-section" aria-labelledby="setup-sec-anchored">
          <div class="setup-section__head">
            <h3 id="setup-sec-anchored">Anchored blocks</h3>
          </div>
          <div class="setup-section__body">
        {editableAnchors.map((a) => {
          const o = overrideOf(a.id);
          const active = o.on && !o.skipToday;
          const blocks = anchoredBlocks(sourceAnchor(a.id), o);
          const findings = validateAnchoredOverride(sourceAnchor(a.id), o, s.inputs!.time);
          return (
            <div key={a.id} class="anchored-row">
              <div class="anchored-row__main">
                <span class="anchored-row__name">{a.name}</span>
                <span class="anchored-row__time">
                  {display12h(o.time ?? a.start)} · {blocks === 0 ? "Background · 0min" : formatBlockAmount(blocks)}
                </span>
                {findings.errors.map((error) => <span class="field-error" role="alert">{error}</span>)}
                {findings.warnings.map((warning) => (
                  <span class="field-warning" role="status">{warning}</span>
                ))}
              </div>
              <div class="anchored-row__controls">
                <input
                  type="time"
                  step={1800}
                  value={o.time ?? a.start ?? ""}
                  aria-label={`${a.name} start override`}
                  onInput={(e) =>
                    patchAnchored(a.id, { time: (e.target as HTMLInputElement).value })
                  }
                />
                <div class="stepper stepper--compact">
                  <button
                    type="button"
                    onClick={() => patchAnchored(a.id, { blocks: Math.max(0, blocks - 1) })}
                    disabled={blocks <= 0}
                    aria-label={`Shorten ${a.name}`}
                  >−</button>
                  <span aria-live="polite">{formatBlockAmount(blocks)}</span>
                  <button
                    type="button"
                    onClick={() => patchAnchored(a.id, { blocks: blocks + 1 })}
                    aria-label={`Lengthen ${a.name}`}
                  >+</button>
                </div>
                <button
                  class={`toggle ${active ? "toggle--on" : ""}`}
                  onClick={() =>
                    patchAnchored(a.id, active ? { skipToday: true } : { on: true, skipToday: false })
                  }
                  aria-pressed={active}
                >
                  {active ? "On" : "Skipped"}
                </button>
              </div>
            </div>
          );
        })}
          </div>
        </section>

        <section class="setup-section" aria-labelledby="setup-sec-live">
          <div class="setup-section__head">
            <h3 id="setup-sec-live">Live micro-adventure</h3>
          </div>
          <div class="setup-section__body">
        <p class="live-micro__status" aria-live="polite">
          {micro.pick ? (
            <span>
              🌱 {micro.pick.idea}
              {micro.pick.category ? ` · ${micro.pick.category}` : ""}
              {" "}
              <em>({micro.source === "override" ? "your pick" : "auto"})</em>
            </span>
          ) : (
            <span>No micro-adventure today</span>
          )}
          {micro.streak > 0 && <span> · streak {micro.streak}</span>}
        </p>
        {micro.pendingConfirm && (
          <p class="live-micro__pending">
            {micro.pendingConfirm.date}'s 🌱 {micro.pendingConfirm.idea} is
            unconfirmed — tick its Todoist task or the daily note's Live box.
          </p>
        )}
        <div class="field">
          <label for="live-pick">Pick from pool</label>
          <select
            id="live-pick"
            value={micro.pick?.id ?? ""}
            onChange={(e) => {
              const id = (e.target as HTMLSelectElement).value;
              const idea = micro.pool.find((p) => p.id === id);
              if (idea) void controller.setMicroAdventure(idea);
            }}
          >
            {micro.pick && !micro.pool.some((p) => p.id === micro.pick!.id) && (
              <option value={micro.pick.id}>{micro.pick.idea}</option>
            )}
            {micro.pool.map((p) => (
              <option key={p.id} value={p.id}>
                {p.idea}
              </option>
            ))}
          </select>
        </div>
        <div class="field">
          <label for="live-custom">Custom idea</label>
          <input
            id="live-custom"
            type="text"
            value={liveCustom}
            onInput={(e) => setLiveCustom((e.target as HTMLInputElement).value)}
          />
          <button
            class="btn"
            disabled={!liveCustom.trim()}
            onClick={() => {
              const text = liveCustom.trim();
              if (!text) return;
              void controller.setMicroAdventure({ id: "custom", idea: text, category: "custom" });
              setLiveCustom("");
            }}
          >
            Set custom
          </button>
        </div>
        <div class="live-micro__actions">
          <button
            class="btn"
            onClick={() => void shuffleLive()}
            disabled={micro.pool.length < 2}
          >
            Shuffle
          </button>
          {micro.source === "override" && (
            <button class="btn" onClick={() => void controller.setMicroAdventure(null)}>
              Reset to auto
            </button>
          )}
        </div>
          </div>
        </section>

        <section class="setup-section" aria-labelledby="setup-sec-captures">
          <div class="setup-section__head">
            <h3 id="setup-sec-captures">Captures</h3>
          </div>
          <div class="setup-section__body">
        {/* FEEDBACK-13 (obs 1 + 2): the primary daily capture gets an explicit
            one-focus helper and a distinct, readable textarea treatment
            (surface/border/focus/spacing states defined in app.css). */}
        <div class="field">
          <label for="cap-intention">Intention</label>
          <textarea
            id="cap-intention"
            class="cap-intention"
            aria-describedby="cap-intention-hint"
            placeholder="One thing to focus on today"
            value={draft.captures.intention}
            onInput={(e) =>
              setDraft({
                ...draft,
                captures: { ...draft.captures, intention: (e.target as HTMLTextAreaElement).value },
              })
            }
          />
          <p class="field__hint" id="cap-intention-hint">
            One thing to focus on today.
          </p>
        </div>
        <div class="field">
          <label for="cap-meegy">For Meegy</label>
          <textarea
            id="cap-meegy"
            value={draft.captures.forMeegy}
            onInput={(e) =>
              setDraft({
                ...draft,
                captures: { ...draft.captures, forMeegy: (e.target as HTMLTextAreaElement).value },
              })
            }
          />
        </div>
        <div class="field">
          <label for="cap-stoic">Stoic</label>
          <textarea
            id="cap-stoic"
            value={draft.captures.stoic}
            onInput={(e) =>
              setDraft({
                ...draft,
                captures: { ...draft.captures, stoic: (e.target as HTMLTextAreaElement).value },
              })
            }
          />
        </div>
          </div>
        </section>

        <div class="editor__actions setup__actions">
          <button class="btn" onClick={close}>
            Cancel
          </button>
          <button class="btn btn--primary" onClick={save} disabled={saving || anchorErrors.length > 0 || allotmentError}>
            {saving ? "Saving…" : "Save day setup"}
          </button>
        </div>
      </div>
    </>
  );
}

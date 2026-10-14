/* DaySetupPanel — editable day setup: anchor/EOD/buffering, anchored block
   on/skip/time overrides, the Mint selection, and the three captures
   (Intention, For Meegy, Stoic — locked decision 10). Saves session/day-scoped
   state only.

   U12 (KTD10/KTD13): the promoted Set up day screen renders the mockup's
   two-column composition — a Day frame card with the R20a drag strip, an
   Anchors card, and a Morning prompts card — while the settings drawer keeps
   the legacy sectioned composition so its long-standing suites stay intact.
   `variant` selects the layout; every controller, wire contract, and Mint/wall
   rule below is shared.

   The body is mounted by SettingsShell for the life of the shell (whether or
   not this tab is selected), so unsaved drafts survive panel switches; the
   shell discards it on close and reopening re-initializes from current state.
   The dialog chrome, focus management, and close affordance belong to the
   shell. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { display12h, formatBlockAmount, formatDurationMinutes, isValidHHMM, toMinutes } from "../model/time";
import {
  explicitMintCapMinutes,
  fillMintSessionIds,
  initialMintSessionIds as resolveInitialMintSessionIds,
  mintMinutesForSessionIds,
  mintSessionIdsForMinutes,
  wallFreeMintSessionIds,
  MINT_SESSION_MINUTES,
  MINT_DEFAULT_BLOCKS,
  MINT_DEFAULT_MINUTES,
} from "../model/mint";
import type { AnchoredOverride, Buffering, DaySetup } from "../model/types";
import {
  anchorFrameStart,
  anchoredBlocks,
  anchoredOverrideOf,
  validateAnchoredOverride,
} from "../model/anchored";
import { calendarWalls } from "../model/overflow";
import { dayFrame } from "../model/dayFrame";
import { effectiveAnchoredBlocks } from "../store/store";
import { displayAnchored } from "../model/calendarEvents";
import { DayStrip } from "./day-setup/DayStrip";
import { AnchorPopover, type AnchorTimeState } from "./day-setup/AnchorPopover";
import { PromptsCard } from "./day-setup/PromptsCard";
import { LooseEndBanner } from "./day-setup/LooseEndBanner";
import { onSetupContinue, onSetupUndo } from "./day-setup/commands";

export function DaySetupPanel({
  active,
  variant = "drawer",
}: {
  active: boolean;
  variant?: "drawer" | "screen";
}) {
  const s = useAppState();
  const { controller, store } = useApp();
  const availableMintSessions = s.inputs?.daySemantics.mintSessions ?? [];
  const savedMintOverride = s.daySetup.schedulable?.minting;
  const savedAllotment =
    typeof s.daySetup.workAllotmentMinutes === "number"
      ? s.daySetup.workAllotmentMinutes
      : s.inputs?.daySemantics.effectiveAllotmentMinutes ?? 0;
  // Preserve the resolved backend value exactly, including zero and positive
  // sub-defaults. Only the old shape with no resolution evidence receives the
  // four-hour fallback; a selected preset (including a zero preset) is enough
  // to keep its resolved value authoritative.
  const hasResolvedMintContract =
    s.inputs?.daySemantics.effectiveAllotmentMinutes !== 0 ||
    s.inputs?.daySemantics.defaultAllotmentMinutes !== 0 ||
    s.inputs?.daySemantics.selectedPreset != null ||
    s.inputs?.daySemantics.mintBelowDefault !== undefined;
  // Only a saved numeric allotment, or a resolved zero, caps the Mint fill. A
  // configured or preset default gates Mint on and off; it does not clip the
  // default fill from the anchor through the end of the windows.
  const initialMintAllotment = explicitMintCapMinutes(
    s.daySetup,
    hasResolvedMintContract ? s.inputs?.daySemantics.effectiveAllotmentMinutes : null,
  );
  const initialMintAnchor = s.daySetup.anchor ?? s.inputs?.time.anchor;
  // FEEDBACK-28: Mint choices are filtered against the current effective
  // fixed/work calendar walls — the August 17 incident selected Mint
  // 15:00-15:30 over the OPPD fixed wall at 15:00. The wall set is the same
  // non-permeable calendar walls the overflow scan already respects.
  const effectiveMintWalls = calendarWalls(effectiveAnchoredBlocks(s));
  const initialMintSessionIds = resolveInitialMintSessionIds(
    availableMintSessions,
    savedMintOverride,
    initialMintAllotment,
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
  const [promptDirty, setPromptDirty] = useState(false);
  // U12 Undo restores the uncommitted day-frame edits to the last saved state.
  const initialRef = useRef({
    draft: initialDraft,
    preset: s.daySetup.dayPreset ?? automaticPreset,
    workAllotment: String(
      availableMintSessions.length > 0
        ? initialMintSessionIds.length * MINT_SESSION_MINUTES
        : savedAllotment,
    ),
    mode: Object.prototype.hasOwnProperty.call(s.daySetup, "workAllotmentMinutes")
      ? s.daySetup.workAllotmentMinutes === null
        ? ("reset" as const)
        : ("preserve" as const)
      : ("preserve" as const),
  });
  const close = () => store.dispatch({ type: "UI", patch: { settingsPanel: null } });

  const savePrompts = () => {
    setPromptDirty(false);
    void controller.savePromptDrafts({
      intention: draft.captures.intention,
      megan_nicety: draft.captures.forMeegy,
      stoic_intention: draft.captures.stoic,
    });
  };

  const autosavePrompts = () => {
    if (promptDirty) savePrompts();
  };

  // U12 command refs, declared before any early return so the subscription
  // hooks keep a stable order; the callbacks below fill them in.
  const continueRef = useRef<() => void>(() => {});
  const undoRef = useRef<() => void>(() => {});
  useEffect(() => {
    if (variant !== "screen") return;
    const offContinue = onSetupContinue(() => continueRef.current());
    const offUndo = onSetupUndo(() => undoRef.current());
    return () => {
      offContinue();
      offUndo();
    };
  }, [variant]);

  // Anchor time moves (strip drag / arrow keys) write today's override through
  // the same controller path as the anchored editor. Writes are chained so a
  // fast run of arrow presses reaches the server in order, and the draft is
  // kept in step with the store except while a write is still in flight.
  const [timeUi, setTimeUi] = useState<
    Record<string, { saving?: boolean; saved?: boolean; error?: string | null }>
  >({});
  // Planning 2a: the anchor whose block is selected (the popover will anchor to
  // it). Selecting keeps the existing editor path so nothing regresses yet.
  const [selectedAnchor, setSelectedAnchor] = useState<string | null>(null);
  const persistChain = useRef<Promise<void>>(Promise.resolve());
  const pendingPersists = useRef(0);
  useEffect(() => {
    if (variant !== "screen" || pendingPersists.current > 0) return;
    setDraft((d) =>
      d.anchored === s.daySetup.anchored ? d : { ...d, anchored: s.daySetup.anchored },
    );
  }, [variant, s.daySetup.anchored]);

  if (!active || !s.inputs) return null;

  const mintAnchor = draft.anchor ?? s.inputs.time.anchor;
  const mintOverride = draft.schedulable?.minting;
  const selectedMintSessionIds =
    mintOverride?.on === false
      ? []
      : Array.isArray(mintOverride?.sessions)
        ? wallFreeMintSessionIds(availableMintSessions, mintOverride.sessions, effectiveMintWalls)
        : mintSessionIdsForMinutes(availableMintSessions, Number(workAllotment), mintAnchor, effectiveMintWalls);
  const selectableMintSessionIds = mintSessionIdsForMinutes(
    availableMintSessions,
    availableMintSessions.length * MINT_SESSION_MINUTES,
    mintAnchor,
    effectiveMintWalls,
  );
  const mintSelectionMax = selectableMintSessionIds.length;

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

  // R20a: a single slot toggle (click or keyboard Space). Wall-blocked ids are
  // dropped again by setMintSelection, so a blocked slot never becomes a
  // commitment.
  const toggleMintSession = (id: string) => {
    const next = selectedMintSessionIds.includes(id)
      ? selectedMintSessionIds.filter((candidate) => candidate !== id)
      : [...selectedMintSessionIds, id];
    setMintSelection(next);
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
    // U12: the screen edits anchors through the shared AnchoredEditor, which
    // saves to the store. Prefer an unsaved local patch, then fall back to the
    // saved override so a just-applied skip/edit is reflected without a remount.
    return anchoredOverrideOf(block, draft.anchored[id] ?? s.daySetup.anchored[id]);
  };

  const patchAnchored = (id: string, patch: Partial<AnchoredOverride>) =>
    setDraft((d) => ({
      ...d,
      anchored: { ...d.anchored, [id]: { ...overrideOf(id), ...patch } },
    }));

  /** Chip Skip/Include today: writes the same today's override the anchored
      editor's On/Skip toggle applies through Apply. Keeps the draft in step so
      the chip flips immediately, then persists via the editor's own path. */
  const toggleAnchorSkip = (id: string) => {
    const o = overrideOf(id);
    const active = o.on && !o.skipToday;
    const next: AnchoredOverride = { ...o, on: !active, skipToday: active };
    setDraft((d) => ({ ...d, anchored: { ...d.anchored, [id]: next } }));
    patchTimeUi(id, { saved: false, error: null });
    persistAnchoredOverride(id, next);
  };

  const patchTimeUi = (id: string, patch: { saving?: boolean; saved?: boolean; error?: string | null }) =>
    setTimeUi((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  const errorText = (e: unknown) => String(e instanceof Error ? e.message : e);

  const persistAnchoredOverride = (id: string, override: AnchoredOverride) => {
    pendingPersists.current += 1;
    persistChain.current = persistChain.current
      .then(() => controller.saveAnchoredOverride(id, override))
      .catch((e) =>
        patchTimeUi(id, { error: `Could not save today's change: ${errorText(e)}` }),
      )
      .finally(() => {
        pendingPersists.current -= 1;
      });
  };

  /** Strip drag / arrow keys: today's start for one config anchor. */
  const moveAnchor = (id: string, time: string | null) => {
    // The strip only ever sends a valid snapped clock, but the popover's
    // free-text Starts field can clear or partially type; refuse anything that
    // is not a complete HH:MM so "" never reaches the server. Reset passes
    // null deliberately and is allowed through.
    if (time !== null && !isValidHHMM(time)) return;
    const next: AnchoredOverride = { ...overrideOf(id), time };
    setDraft((d) => ({ ...d, anchored: { ...d.anchored, [id]: next } }));
    patchTimeUi(id, { saved: false, error: null });
    persistAnchoredOverride(id, next);
  };

  const saveAnchorTime = async (id: string) => {
    const time = overrideOf(id).time;
    if (!time) return;
    patchTimeUi(id, { saving: true, error: null });
    try {
      await persistChain.current; // today's override first, so both agree
      const { reloaded, refreshError } = await controller.saveAnchoredStart(id, time);
      patchTimeUi(
        id,
        reloaded
          ? { saving: false, saved: true, error: null }
          : {
              saving: false,
              error:
                "Saved to config, but the page could not reload its inputs" +
                (refreshError ? ` (${refreshError})` : "") +
                ". Use Refresh sources to see it.",
            },
      );
    } catch (e) {
      patchTimeUi(id, { saving: false, error: errorText(e) });
    }
  };

  const setMintAllotment = (minutes: number) => {
    if (availableMintSessions.length === 0) {
      setWorkAllotment(String(minutes));
      setAllotmentMode("override");
      return;
    }
    setMintSelection(mintSessionIdsForMinutes(availableMintSessions, minutes, mintAnchor, effectiveMintWalls));
  };

  const setMintSessionCount = (count: number) => {
    setMintSelection(
      mintSessionIdsForMinutes(
        availableMintSessions,
        Math.max(0, count) * MINT_SESSION_MINUTES,
        mintAnchor,
        effectiveMintWalls,
      ),
    );
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
  // The automatic config default fills the day; a named preset or a zero
  // config is an explicit allotment and caps the selection.
  const applyConfiguredAllotment = (presetName: string) => {
    const configured = configAllotment(presetName);
    if (availableMintSessions.length > 0) {
      if (presetName === automaticPreset && configured > 0) {
        setMintSelection(fillMintSessionIds(availableMintSessions, mintAnchor, effectiveMintWalls));
      } else {
        setMintAllotment(configured);
      }
    } else {
      setWorkAllotment(String(configured));
      setAllotmentMode("reset");
    }
  };
  const resetAllotment = () => applyConfiguredAllotment(preset);
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
  // Derive this from the current normalized value, not the load-time signal:
  // changing 180 to 0 or 240 must clear the warning immediately. The same
  // check works for aggregate Mint when no concrete session rows exist.
  const mintBelowDefault =
    Number.isFinite(allotmentNumber) &&
    allotmentNumber > 0 &&
    allotmentNumber < MINT_DEFAULT_MINUTES;
  // A day with fewer eligible blocks than the default is the day's limit, not
  // a choice, so session mode warns only on a deliberate reduction below the
  // default or the eligible count.
  const belowDefaultCopy = "Mint is below the 8-block (4-hour) daily default. You can continue with less.";
  const mintWarning = availableMintSessions.length > 0
    ? selectedMintSessionIds.length > 0 &&
      selectedMintSessionIds.length < Math.min(mintSelectionMax, MINT_DEFAULT_BLOCKS)
      ? belowDefaultCopy
      : null
    : mintBelowDefault
      ? belowDefaultCopy
      : null;

  const anchorErrors = editableAnchors.flatMap((a) =>
    validateAnchoredOverride(sourceAnchor(a.id), overrideOf(a.id), s.inputs!.time).errors.map(
      (error) => `${a.name}: ${error}`,
    ),
  );

  const editAnchor = (id: string) =>
    store.dispatch({ type: "UI", patch: { editorAnchor: id } });



  // U12: the footer's Continue confirms the day frame (preserving the existing
  // day-setup confirm contract) and then advances the stepper to Plan tasks.
  continueRef.current = async () => {
    if (anchorErrors.length > 0 || allotmentError) return;
    try {
      await save();
    } catch {
      return;
    }
    store.dispatch({ type: "UI", patch: { screen: "plan", settingsPanel: null } });
  };
  undoRef.current = () => {
    setDraft(initialRef.current.draft);
    setPreset(initialRef.current.preset);
    setWorkAllotment(initialRef.current.workAllotment);
    setAllotmentMode(initialRef.current.mode);
  };

  if (variant === "screen") {
    const { start: frameStart, end: frameEnd } = dayFrame(s.inputs.time, {
      anchor: draft.anchor,
      eod: draft.eod,
    });
    const frameStartMin = toMinutes(frameStart);
    const stripAnchors = editableAnchors.map((a) => {
      const o = overrideOf(a.id);
      const on = o.on && !o.skipToday;
      const source = sourceAnchor(a.id);
      const blocks = anchoredBlocks(source, o);
      const placement = anchorFrameStart(a.kind, {
        start: o.time ?? a.start,
        end: source.end,
        blocks,
        frameStart,
        frameEnd,
      });
      const time = placement.start ?? frameStart;
      // Only a start after the frame end is "outside frame"; a config anchor
      // that began before the frame stays live (clipped) or sits at the edge.
      const outside = placement.afterFrame;
      const counted = on && !outside;
      const configTime = source.start;
      const ui = timeUi[a.id] ?? {};
      const timeState: AnchorTimeState = {
        configTime,
        dirty: o.time != null && o.time !== configTime,
        saved: ui.saved === true,
        saving: ui.saving === true,
        error: ui.error ?? null,
        windowOpen: a.kind === "window" || a.kind === "template",
      };
      return {
        id: a.id,
        name: a.name,
        start: time,
        // The popover's read-only Window field keeps consuming `end`; for
        // window/template kinds it is the source window close.
        end: source.end ?? time,
        window:
          (a.kind === "window" || a.kind === "template") && source.start !== null && source.end !== null
            ? { start: source.start, end: source.end }
            : undefined,
        counted,
        // The override intent, independent of frame clipping; the popover's
        // Count today switch shows this, while `counted` stays for the strip.
        counting: on,
        skipped: !on,
        reason: !on ? "skipped" : outside ? "outside frame" : null,
        blocks,
        // Calendar rows never reach editableAnchors; counted config anchors move.
        movable: counted,
        timeState,
      };
    });
    const stripCalendar = displayAnchored(effectiveAnchoredBlocks(s), { sessionCalendars: true })
      // Ignored calendars (the same ones Plan tasks tucks behind its "ignored"
      // count) have no place among the day's commitments either.
      .filter(
        (a) =>
          a.kind === "calendar" &&
          a.start !== null &&
          a.durationMin > 0 &&
          (a.capacityClass ?? "fixed") !== "ignored",
      )
      .map((a) => ({
        id: a.id,
        name: a.name,
        start: a.start as string,
        durationMin: a.durationMin,
        counted: a.on && !a.skipToday,
        // Work-class (Mint) rows draw yellow on the lane; fixed stays blue.
        capacityClass: a.capacityClass,
      }));
    const freeSlots = Math.max(0, selectableMintSessionIds.length - selectedMintSessionIds.length);
    // The popover's Count today switch shows the OVERRIDE intent, not the
    // strip's frame-clipped drawing state: an anchor clipped at the frame's
    // left edge draws as not counted but its override can still be on. The
    // hint explains why it is not counted yet.
    const selectedEntry = stripAnchors.find((anchor) => anchor.id === selectedAnchor) ?? null;
    const selectedBlock =
      selectedEntry && {
        ...selectedEntry,
        counted: selectedEntry.counting,
        outsideFrame: selectedEntry.reason === "outside frame",
        outsideHint:
          selectedEntry.reason !== "outside frame"
            ? null
            : toMinutes(selectedEntry.start) < frameStartMin
              ? `Starts before your day (${display12h(frameStart)}), so it isn't counted yet. Move it later or change Start.`
              : `Starts after your day ends (${display12h(frameEnd)}), so it isn't counted yet. Move it earlier or change Start.`,
      };

    return (
      <div class="settings-panel settings-panel--day day-setup-screen">
        <div class="day-setup-screen__stack">
            {micro.pendingConfirm && (
              <LooseEndBanner
                pending={micro.pendingConfirm}
                onAnswer={(entry, done) =>
                  void controller.resolveLooseEnd({ date: entry.date, id: entry.id }, done)
                }
              />
            )}
            <section
              class="setup-section setup-section--your-day"
              data-settings-section="frame"
              aria-labelledby="setup-sec-frame"
            >
              <div class="your-day__head">
                <div class="your-day__title">
                  <h3 id="setup-sec-frame">Your day</h3>
                  <p class="setup-section__hint">
                    Click an anchor to edit it. Drag to move it or pick Mint sessions.
                  </p>
                </div>
                <div class="frame-fields">
                  <div class="field">
                    <label for="setup-preset">Preset</label>
                    <select
                      id="setup-preset"
                      value={preset}
                      onChange={(e) => {
                        const value = (e.target as HTMLSelectElement).value;
                        setPreset(value);
                        applyConfiguredAllotment(value);
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
                    <label for="setup-anchor">Start</label>
                    <input
                      id="setup-anchor"
                      type="time"
                      step={900}
                      value={draft.anchor ?? s.inputs.time.anchor}
                      onInput={(e) => setMintAnchor((e.target as HTMLInputElement).value)}
                    />
                  </div>
                  <div class="field">
                    <label for="setup-eod">End</label>
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
                    <label for="setup-buffering">Buffer</label>
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
              </div>

              <DayStrip
                frameStart={frameStart}
                frameEnd={frameEnd}
                sessions={availableMintSessions}
                selectedIds={selectedMintSessionIds}
                selectableIds={selectableMintSessionIds}
                onSelectIds={(ids) => setMintSelection(ids)}
                onToggleSession={toggleMintSession}
                anchors={stripAnchors}
                calendar={stripCalendar}
                selectedAnchorId={selectedAnchor}
                onSelectAnchor={(id) => setSelectedAnchor(id)}
                onMoveAnchor={(id, start) => moveAnchor(id, start)}
              />

              {availableMintSessions.length > 0 && (
                <div class="frame-summary" role="status">
                  <span class="frame-summary__mint">
                    Mint {selectedMintSessionIds.length} of {availableMintSessions.length} sessions ·{" "}
                    {allotmentLabel}
                  </span>
                  <span class="frame-summary__free">
                    Only {freeSlots} free slot{freeSlots === 1 ? "" : "s"} inside the frame today.
                  </span>
                  <label class="frame-summary__blocks" for="setup-mint-blocks">
                    <span>Mint blocks</span>
                    <input
                      id="setup-mint-blocks"
                      type="range"
                      class="field__range"
                      min={0}
                      max={mintSelectionMax}
                      step={1}
                      value={selectedMintSessionIds.length}
                      aria-valuetext={`${selectedMintSessionIds.length} of ${mintSelectionMax} blocks`}
                      onInput={(e) =>
                        setMintSessionCount(Number((e.target as HTMLInputElement).value))
                      }
                    />
                  </label>
                  <button type="button" class="btn frame-summary__reset" onClick={resetAllotment}>
                    Reset to config
                  </button>
                </div>
              )}
              {allotmentError && (
                <span class="field-error" role="alert">Use nonnegative 15-minute increments.</span>
              )}
              {mintWarning && (
                <span class="field-warning" role="status">{mintWarning}</span>
              )}
            </section>

            {selectedBlock && (
              <AnchorPopover
                anchor={selectedBlock}
                onMoveTime={(id, time) => moveAnchor(id, time)}
                onToggleCount={toggleAnchorSkip}
                onEdit={(id) => {
                  setSelectedAnchor(null);
                  editAnchor(id);
                }}
                onResetDefault={(id) => moveAnchor(id, null)}
                onSaveDefault={(id) => void saveAnchorTime(id)}
                onClose={() => setSelectedAnchor(null)}
              />
            )}

            <PromptsCard
              micro={micro}
              liveCustom={liveCustom}
              onLiveCustom={setLiveCustom}
              onPickPool={(id) => {
                const idea = micro.pool.find((candidate) => candidate.id === id);
                if (idea) void controller.setMicroAdventure(idea);
              }}
              onSetCustom={() => {
                const text = liveCustom.trim();
                if (!text) return;
                void controller.setMicroAdventure({ id: "custom", idea: text, category: "custom" });
                setLiveCustom("");
              }}
              onShuffle={() => void shuffleLive()}
              onResetAuto={() => void controller.setMicroAdventure(null)}
              captures={draft.captures}
              onCapture={(key, value) => {
                setPromptDirty(true);
                setDraft((current) => ({
                  ...current,
                  captures: { ...current.captures, [key]: value },
                }));
              }}
              onAutosave={autosavePrompts}
              onPersistCustom={(patch) => void controller.savePromptDrafts(patch)}
              promptSave={s.promptSave}
            />
        </div>
      </div>
    );
  }

  return (
    <div class="settings-panel settings-panel--day">
      {/* FEEDBACK-16: each section is a distinct card (setup-section) in a
          fixed scan order; Frame groups every day-frame field together. */}
        <section class="setup-section" data-settings-section="frame" aria-labelledby="setup-sec-frame">
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
              applyConfiguredAllotment(value);
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
          {/* With concrete Mint sessions, the allotment is the selected-session
              total. The two sliders update the same exact selection. */}
          <input
            id="setup-work-allotment"
            type="range"
            class="field__range"
            min={0}
            max={availableMintSessions.length > 0 ? mintSelectionMax * MINT_SESSION_MINUTES : 720}
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
              ? `${selectedMintSessionIds.length} of ${mintSelectionMax} sessions selected · each session is 30min`
              : "0 disables Mint for today · 12hr max"}
          </span>
          {allotmentError && (
            <span class="field-error" role="alert">Use nonnegative 15-minute increments.</span>
          )}
          {mintWarning && (
            <span class="field-warning" role="status">{mintWarning}</span>
          )}
          <button type="button" class="btn" onClick={resetAllotment}>
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
          <section class="setup-section" data-settings-section="mint" aria-labelledby="setup-sec-mint">
            <div class="setup-section__head">
              <h3 id="setup-sec-mint">Mint sessions</h3>
            </div>
            <div class="setup-section__body">
            <div class="mint-selection">
              <label for="setup-mint-selection">Mint session count</label>
              <input
                id="setup-mint-selection"
                type="range"
                class="mint-selection__range"
                min={0}
                max={mintSelectionMax}
                step={1}
                value={selectedMintSessionIds.length}
                aria-valuetext={`${selectedMintSessionIds.length} of ${mintSelectionMax} sessions · ${allotmentLabel}`}
                aria-describedby="setup-mint-selection-hint"
                onInput={(e) => setMintSessionCount(Number((e.target as HTMLInputElement).value))}
              />
              <strong>{selectedMintSessionIds.length} / {mintSelectionMax}</strong>
              <p class="field__hint" id="setup-mint-selection-hint">
              Drag the session slider to reserve 30-minute blocks. The allotment above stays in sync.
            </p>
            </div>
            {availableMintSessions.map((session) => {
              const checked = selectedMintSessionIds.includes(session.id);
              const selectable = selectableMintSessionIds.includes(session.id);
              const sessionState = checked ? "Selected" : selectable ? "Available" : "Blocked by wall";
              return (
                <div
                  class={`mint-session-row${checked ? " mint-session-row--selected" : selectable ? "" : " mint-session-row--blocked"}`}
                  data-selected={checked ? "true" : "false"}
                  key={session.id}
                >
                  <span>{session.name}</span>
                  <span>{display12h(session.start)}–{display12h(session.end)}</span>
                  <span class="mint-session-row__state">{sessionState}</span>
                </div>
              );
            })}
            </div>
          </section>
        )}

        <section class="setup-section" data-settings-section="anchored" aria-labelledby="setup-sec-anchored">
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

        <section class="setup-section" data-settings-section="live" aria-labelledby="setup-sec-live">
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
          <LooseEndBanner
            pending={micro.pendingConfirm}
            onAnswer={(entry, done) =>
              void controller.resolveLooseEnd({ date: entry.date, id: entry.id }, done)
            }
          />
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

        <section class="setup-section" data-settings-section="captures" aria-labelledby="setup-sec-captures">
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
            onInput={(e) => {
              setPromptDirty(true);
              setDraft({
                ...draft,
                captures: { ...draft.captures, intention: (e.target as HTMLTextAreaElement).value },
              });
            }}
            onBlur={autosavePrompts}
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
            onInput={(e) => {
              setPromptDirty(true);
              setDraft({
                ...draft,
                captures: { ...draft.captures, forMeegy: (e.target as HTMLTextAreaElement).value },
              });
            }}
            onBlur={autosavePrompts}
          />
        </div>
        <div class="field">
          <label for="cap-stoic">Stoic</label>
          <textarea
            id="cap-stoic"
            value={draft.captures.stoic}
            onInput={(e) => {
              setPromptDirty(true);
              setDraft({
                ...draft,
                captures: { ...draft.captures, stoic: (e.target as HTMLTextAreaElement).value },
              });
            }}
            onBlur={autosavePrompts}
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
  );
}

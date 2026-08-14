/* persist.ts — today-only session persistence (locked decision 16).
   Client shaping state (overrides, placements, staged sequence, fingerprint)
   survives a same-date refresh/retry via localStorage, keyed by valid_date.
   A date change makes the stored blob unreadable by construction — restore
   only fires when the freshly loaded valid_date matches. Shadow/review state
   is deliberately NOT persisted: preview must rerun after any reload.

   Day Setup itself persists SERVER-side (runstate via POST /day-setup) and
   comes back through /plan-inputs — never through this blob. */

import type { Store } from "./createStore";
import type { Controller } from "./controller";
import type { SequenceRow, TodayOverride } from "../model/types";

const KEY = "tdtb-session-v2";
const V1_KEY = "tdtb-session-v1";

interface SessionBlob {
  version: 2;
  validDate: string;
  overrides: Record<string, TodayOverride>;
  placements: Record<string, string>;
  sequence: SequenceRow[] | null;
  fingerprint: string | null;
  anchoredSourceFingerprint: string | null;
  planningConfigFingerprint: string | null;
  overlapGrants: import("../model/types").OverlapGrant[];
  pinnedRows: SequenceRow[];
  pendingPinnedRows?: SequenceRow[];
}

function read(storage: Storage): SessionBlob | null {
  try {
    const raw = storage.getItem(KEY);
    if (!raw) return null;
    const blob = JSON.parse(raw);
    return blob?.version === 2 && typeof blob.validDate === "string"
      ? (blob as SessionBlob)
      : null;
  } catch {
    return null;
  }
}

/** Restore today-only state after the initial load, then keep persisting on
    every state change. Call once, right after controller.load() resolves. */
export function attachSessionPersistence(
  store: Store,
  controller: Controller,
  storage: Storage = localStorage,
): () => void {
  try {
    storage.removeItem(V1_KEY);
  } catch {
    /* storage unavailable */
  }
  const s = store.getState();
  const blob = read(storage);
  if (!blob) {
    try {
      storage.removeItem(KEY);
    } catch {
      /* storage unavailable */
    }
  }
  if (blob && s.validDate && blob.validDate === s.validDate) {
    const hasContent =
      Object.keys(blob.overrides ?? {}).length > 0 ||
      Object.keys(blob.placements ?? {}).length > 0 ||
      (blob.sequence?.length ?? 0) > 0 ||
      (blob.pinnedRows?.length ?? 0) > 0 ||
      (blob.overlapGrants?.length ?? 0) > 0;
    if (hasContent) {
      store.dispatch({
        type: "SESSION_RESTORED",
        overrides: blob.overrides ?? {},
        placements: blob.placements ?? {},
        sequence: blob.sequence ?? null,
        fingerprint: blob.fingerprint ?? null,
        anchoredSourceFingerprint: blob.anchoredSourceFingerprint ?? null,
        planningConfigFingerprint: blob.planningConfigFingerprint ?? null,
        overlapGrants: blob.overlapGrants ?? [],
        pinnedRows: blob.pinnedRows ?? [],
        pendingPinnedRows: blob.pendingPinnedRows ?? blob.pinnedRows ?? [],
      });
      // Deterministic + free: restored plans re-earn "valid" via the server
      // validator; capacity re-renders the server's numbers for the restored
      // include/exclude/duration set.
      void controller.revalidate();
      void controller.refreshCapacity();
    }
  } else if (blob && s.validDate && blob.validDate !== s.validDate) {
    // Stale date: today-only state from a prior day dies (locked decision 16).
    try {
      storage.removeItem(KEY);
    } catch {
      /* storage unavailable */
    }
  }

  return store.subscribe(() => {
    const st = store.getState();
    if (!st.validDate) return;
    const out: SessionBlob = {
      version: 2,
      validDate: st.validDate,
      overrides: st.overrides,
      placements: st.placements,
      sequence: st.sequence,
      fingerprint: st.fingerprint,
      anchoredSourceFingerprint: st.anchoredSourceFingerprint,
      planningConfigFingerprint: st.planningConfigFingerprint,
      overlapGrants: st.overlapGrants,
      pinnedRows: st.pinnedRows,
      pendingPinnedRows: st.pendingPinnedRows,
    };
    try {
      storage.setItem(KEY, JSON.stringify(out));
    } catch {
      /* private mode / quota — persistence degrades silently */
    }
  });
}

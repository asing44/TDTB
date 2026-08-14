/* ApprovalDrawer — shadow preview with surface totals, blockers, and
   expandable exact writes. A separate second click exposes and fires the
   live commit (locked decision 9); the server's single-flight and
   verification behavior remain authoritative. */

import { useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import { canLiveCommit, shadowBlockers } from "../store/store";
import { display12h } from "../model/time";
import type { ShadowEntry } from "../model/types";

/** Where a write lands, in words rather than in the system's own handle.
    `todoist:8899001122` identifies the row to Todoist and to nobody else — the
    approval panel is read by a human deciding whether to let the write happen,
    so it gets the destination; the raw handle stays as the hover title for when
    it's actually needed (chasing a bad write afterwards). */
function writeTarget(e: ShadowEntry): { label: string; title: string } {
  const raw = e.idOrPath;
  if (e.system === "todoist") return { label: "Todoist task", title: raw };
  if (e.system === "calendar") {
    const cal = raw.includes(":") ? raw.slice(raw.indexOf(":") + 1) : raw;
    return { label: `BusyCal · ${cal}`, title: raw };
  }
  const file = raw.split("/").pop() ?? raw;
  return { label: file.replace(/\.md$/, ""), title: raw };
}

function SurfaceTotals({ entries }: { entries: ShadowEntry[] }) {
  const systems = ["todoist", "calendar", "vault"] as const;
  return (
    <div class="surface-totals">
      {systems.map((sys) => {
        const rows = entries.filter((e) => e.system === sys);
        const active = rows.filter((e) => e.classification !== "no-op").length;
        return (
          <div key={sys} class="surface-card">
            <div class="surface-card__system">{sys}</div>
            <div class="surface-card__count">{active}</div>
            <div class="qrow__meta">
              {active === 1 ? "1 write" : `${active} writes`}
              {rows.length - active > 0 ? ` · ${rows.length - active} no-op` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ApprovalDrawer() {
  const s = useAppState();
  const { controller, store } = useApp();
  const [expanded, setExpanded] = useState(false);
  const close = () => store.dispatch({ type: "UI", patch: { approvalOpen: false } });
  const dialog = useDialog(close);

  if (!s.ui.approvalOpen) return null;

  const shadow = s.shadow;
  // Same shadow-blocker list the state-level ARM_LIVE/canLiveCommit gate uses.
  const blockers = [...(s.validation?.hardErrors ?? []), ...shadowBlockers(s)];
  const committed = s.commitPhase === "done" || s.commitPhase === "partial" || s.commitPhase === "failed";
  const report = s.commitReport;
  const activeWrites = shadow
    ? shadow.entries.filter((e) => e.classification !== "no-op").length
    : 0;
  const previewStatus: { label: string; detail: string } =
    s.shadowPhase === "current"
      ? {
          label: "Preview current",
          detail: `${activeWrites} exact write${activeWrites === 1 ? "" : "s"} staged — nothing written yet.`,
        }
      : s.shadowPhase === "stale"
        ? {
            label: "Preview stale",
            detail: "Rerun Preview commit from the dock before committing.",
          }
        : {
            label: "Preview loading",
            detail: "Building the exact-writes list…",
          };

  return (
    <>
      <div class="drawer-backdrop" onClick={close} />
      <div
        class="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Commit approval"
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}
      >
        <button class="iconbtn drawer__close" onClick={close} aria-label="Close approval">
          ✕
        </button>
        <h2>
          {committed
            ? "Results"
            : s.shadowPhase === "loading"
              ? "Building shadow preview…"
              : s.liveArmed
                ? "Approve writes"
                : "Preview writes"}
        </h2>

        {/* FEEDBACK-08: the Results entry is reachable from review before a
            preview exists — say what to do instead of rendering an empty
            panel. */}
        {!shadow && !committed && (
          <div class="alerts__item">
            <span aria-hidden="true">ℹ</span>
            <span>
              Run Preview commit from the dock to build the exact-writes preview.
            </span>
          </div>
        )}

        {s.commitError && !committed && (
          <div class="alerts__item alerts__item--error" role="alert">
            <span aria-hidden="true">⛔</span>
            <span>{s.commitError} — nothing was written; retry when ready.</span>
          </div>
        )}

        {s.shadowPhase === "stale" && !committed && (
          <div class="alerts__item alerts__item--warning">
            <span aria-hidden="true">⚠</span>
            <span>The plan changed since this preview — rerun it before committing.</span>
          </div>
        )}

        {shadow && !committed && (
          <>
            {/* FEEDBACK-18: the first scan line names the phase and what was
                not yet written; totals, blockers, and exact writes follow as
                distinct sections instead of one undifferentiated list. */}
            <div class={`drawer-status drawer-status--preview`} role="status">
              <span class="drawer-status__label">{previewStatus.label}</span>
              <span class="drawer-status__detail">{previewStatus.detail}</span>
            </div>
            <h3>Writes by surface</h3>
            <SurfaceTotals entries={shadow.entries} />
            {(s.acceptedDefects?.length ?? 0) > 0 && (
              <>
                <h3>Accepted defects</h3>
                <ul class="alerts__list" aria-label="Accepted defects">
                  {s.acceptedDefects!.map((d, i) => (
                    <li key={i} class="alerts__item alerts__item--warning">
                      <span aria-hidden="true">⚠</span>
                      <span>{d} — accepted as-is</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {blockers.length > 0 && (
              <>
                <h3>Blockers</h3>
                <ul class="alerts__list">
                  {blockers.map((b, i) => (
                    <li key={i} class="alerts__item alerts__item--error">
                      <span aria-hidden="true">⛔</span>
                      <span>{b}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <h3>
              <button
                class="capacity__disclose"
                style={{ marginLeft: 0, padding: 0 }}
                onClick={() => setExpanded(!expanded)}
                aria-expanded={expanded}
              >
                Exact writes ({shadow.entries.length}) {expanded ? "▴" : "▾"}
              </button>
            </h3>
            {expanded &&
              shadow.entries.map((e, i) => {
                const target = writeTarget(e);
                return (
                  <div key={i} class={`write-row write-row--${e.classification}`}>
                    <span class={`badge badge--w${e.classification}`}>{e.classification}</span>
                    <span class="write-row__name">
                      {e.action} · {e.name}
                      {e.time ? ` @ ${display12h(e.time)}` : ""}
                    </span>
                    <span class="write-row__path" title={target.title}>
                      {target.label}
                    </span>
                  </div>
                );
              })}

            <div class="commit-zone">
              {!s.liveArmed ? (
                <button
                  class="btn btn--primary"
                  onClick={() => controller.armLive()}
                  disabled={s.shadowPhase !== "current" || blockers.length > 0}
                  title={
                    s.shadowPhase !== "current"
                      ? "Preview must be current"
                      : blockers.length > 0
                        ? "Resolve blockers first"
                        : undefined
                  }
                >
                  Looks right — arm live commit
                  <span class="btn__sub">nothing written yet</span>
                </button>
              ) : (
                <>
                  <button
                    class="btn btn--danger"
                    onClick={() => void controller.requestLiveCommit()}
                    disabled={!canLiveCommit(s) || s.commitPhase === "committing"}
                  >
                    {s.commitPhase === "committing"
                      ? "Committing…"
                      : "Commit live — write to all surfaces"}
                  </button>
                  <button
                    class="btn commit-zone__back"
                    onClick={() => controller.disarmLive()}
                    disabled={s.commitPhase === "committing"}
                  >
                    Back
                  </button>
                </>
              )}
            </div>
          </>
        )}

        {committed && report && (
          <>
            {/* FEEDBACK-18: Results gets its own status strip (distinct from
                Preview writes), then the verification heading precedes the
                list it describes, then the single primary action: Done. */}
            <div
              class={`drawer-status ${report.status === "ok" ? "drawer-status--ok" : "drawer-status--partial"}`}
              role="status"
            >
              <span class="drawer-status__label">
                {report.status === "ok"
                  ? "Committed and verified"
                  : "Commit incomplete"}
              </span>
              <span class="drawer-status__detail">
                {report.status === "ok"
                  ? "Every surface wrote and read back cleanly."
                  : `${report.verifyFailures.length} verification failure${report.verifyFailures.length === 1 ? "" : "s"} to review — nothing here rewrites live state.`}
              </span>
            </div>
            <h3>Verification</h3>
            <ul class="verify-list">
              {report.surfaces.map((surf) => (
                <li key={surf.system}>
                  <span aria-hidden="true">
                    {surf.status === "ok" ? "✅" : surf.status === "skipped" ? "⏭" : "⛔"}
                  </span>
                  <span>
                    {surf.system} — {surf.status}
                    {surf.detail ? ` · ${surf.detail}` : ""}
                  </span>
                </li>
              ))}
            </ul>
            {report.verifyFailures.length === 0 ? (
              <div class="alerts__item" style={{ borderColor: "var(--c-free)" }}>
                <span aria-hidden="true">✅</span>
                <span>All surfaces verified — zero failures.</span>
              </div>
            ) : (
              <ul class="alerts__list">
                {report.verifyFailures.map((f, i) => {
                  // FEEDBACK-23: structured due failures render 12-hour from
                  // canonical machine fields; raw ISO/timezone stay available
                  // as the hover title for chasing a bad write. Non-due
                  // failures (calendar/vault/readback) render verbatim.
                  const d = report.verifyDetails?.[i];
                  return (
                    <li key={i} class="alerts__item alerts__item--error">
                      <span aria-hidden="true">⛔</span>
                      {d && d.kind === "due" ? (
                        <span
                          title={`live ${d.liveRaw ?? "—"} · ${d.liveTimezone ?? "floating local"}`}
                        >
                          {d.name} — due verification: intent {display12h(d.intent)}, live{" "}
                          {display12h(d.live)}
                          {d.reason !== "mismatch" ? ` (${d.reason})` : ""}
                        </span>
                      ) : (
                        <span>{f}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            <div class="drawer-done">
              <button
                class={`btn ${report.status === "ok" ? "btn--primary" : ""}`}
                onClick={close}
              >
                Done
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

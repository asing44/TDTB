/* CapacitiesSettingsDrawer — local TDTB assignment policy. This surface reads
   and saves the versioned local settings route only; it never discovers or
   mutates Capacities objects. */

import { useEffect, useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import { useDialog } from "./useDialog";
import type {
  AssignedItem,
  CapacitiesNativeTaskAutoPolicy,
  CapacitiesSettings,
  CapacitiesSettingsDraft,
} from "../model/types";
import { isCanonicalCapacitiesIdentity } from "../adapters/wire";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function draftOf(settings: CapacitiesSettings): CapacitiesSettingsDraft {
  return {
    expectedRevision: settings.revision,
    nativeTaskAuto: { ...settings.nativeTaskAuto },
    excluded: [...settings.excluded],
    // Round-tripped verbatim: this slice adds no editor, but a save must not
    // silently clear the server's active-structure inclusion set.
    activeStructures: [...settings.activeStructures],
  };
}

function identityOf(item: AssignedItem): string | null {
  const identity = item.capacitiesIdentity ?? item.identity ?? null;
  return isCanonicalCapacitiesIdentity(identity) ? identity : null;
}

function knownCapacitiesItems(items: AssignedItem[]): AssignedItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (item.source !== "capacities") return false;
    const identity = identityOf(item);
    if (!identity || seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function CapacitiesSettingsDrawer() {
  const s = useAppState();
  const { controller, store } = useApp();
  const [settings, setSettings] = useState<CapacitiesSettings | null>(null);
  const [draft, setDraft] = useState<CapacitiesSettingsDraft | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [horizonText, setHorizonText] = useState("");
  const close = () => store.dispatch({ type: "UI", patch: { capacitiesSettingsOpen: false } });
  const dialog = useDialog(close);

  const loadSettings = async () => {
    setPhase("loading");
    setError(null);
    try {
      const loaded = await controller.loadCapacitiesSettings();
      setSettings(loaded);
      setDraft(draftOf(loaded));
      setHorizonText(String(loaded.nativeTaskAuto.deadlineHorizonDays));
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    void loadSettings();
  }, [controller]);

  if (!s.ui.capacitiesSettingsOpen) return null;

  const known = knownCapacitiesItems(s.inputs?.assigned ?? []);
  const knownIds = new Set(known.map(identityOf).filter((id): id is string => id !== null));
  const hiddenExclusions = (draft?.excluded ?? []).filter((identity) => !knownIds.has(identity));
  // The vault-local source mapping is the only non-circular inventory of
  // structures to offer. Ids the server already honours but this vault no
  // longer lists are preserved on save, mirroring hiddenExclusions — and
  // surfaced rather than silently dropped.
  const availableStructures = settings?.availableStructures ?? [];
  const availableStructureIds = new Set(availableStructures);
  const staleActiveStructures = (draft?.activeStructures ?? []).filter(
    (structureId) => !availableStructureIds.has(structureId),
  );
  const horizonValue = Number(horizonText);
  const horizonTextValid = /^\d+$/.test(horizonText) && Number.isSafeInteger(horizonValue);
  const policy = draft?.nativeTaskAuto;
  const horizonInvalid = policy?.deadlineEnabled === true && !horizonTextValid;

  const updatePolicy = (patch: Partial<CapacitiesNativeTaskAutoPolicy>) => {
    setDraft((current) =>
      current
        ? { ...current, nativeTaskAuto: { ...current.nativeTaskAuto, ...patch } }
        : current,
    );
  };

  const toggleExclusion = (identity: string) => {
    setDraft((current) => {
      if (!current) return current;
      const excluded = current.excluded.includes(identity)
        ? current.excluded.filter((value) => value !== identity)
        : [...current.excluded, identity].sort();
      return { ...current, excluded };
    });
  };

  const toggleActiveStructure = (structureId: string) => {
    setDraft((current) => {
      if (!current) return current;
      const activeStructures = current.activeStructures.includes(structureId)
        ? current.activeStructures.filter((value) => value !== structureId)
        : [...current.activeStructures, structureId].sort();
      return { ...current, activeStructures };
    });
  };

  const save = async () => {
    if (!draft || horizonInvalid) return;
    // The backend requires a valid stored horizon even when the rule is off.
    // If the disabled control contains an unfinished edit, preserve the last
    // valid policy value rather than turning an empty field into zero.
    const horizon = horizonTextValid
      ? horizonValue
      : draft.nativeTaskAuto.deadlineHorizonDays;
    if (!Number.isSafeInteger(horizon) || horizon < 0) {
      setError("Deadline horizon must be a nonnegative whole number of days.");
      return;
    }
    const next: CapacitiesSettingsDraft = {
      ...draft,
      nativeTaskAuto: { ...draft.nativeTaskAuto, deadlineHorizonDays: horizon },
    };
    setPhase("saving");
    setError(null);
    try {
      const saved = await controller.saveCapacitiesSettings(next);
      setSettings(saved);
      setDraft(draftOf(saved));
      setHorizonText(String(saved.nativeTaskAuto.deadlineHorizonDays));
      close();
    } catch (e) {
      setPhase("ready");
      setError(messageOf(e));
    }
  };

  return (
    <>
      <div class="drawer-backdrop" onClick={close} />
      <div
        class="drawer capacities-settings-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Capacities settings"
        tabIndex={-1}
        ref={dialog.ref}
        onKeyDown={(e) => dialog.onKeyDown(e as unknown as KeyboardEvent)}
      >
        <button class="iconbtn drawer__close" onClick={close} aria-label="Close Capacities settings">
          ✕
        </button>
        <h2>Capacities settings</h2>
        <p class="capacities-settings__intro">
          TDTB assignment policy is stored locally and keyed by stable Capacities identity.
          Saving here does not write object properties, discover provider data, or activate a live route.
        </p>

        {phase === "loading" && (
          <p class="capacities-settings__state" role="status">Loading local policy…</p>
        )}
        {phase === "error" && (
          <section class="capacities-settings__error" role="alert">
            <strong>Settings could not be loaded</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void loadSettings()}>Reload settings</button>
          </section>
        )}

        {draft && phase !== "loading" && (
          <>
            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-auto">
              <div class="setup-section__head">
                <h3 id="capacities-sec-auto">Native Task Auto rules</h3>
                <span class="capacities-settings__revision">
                  {settings?.persisted ? `Saved revision ${settings.revision}` : "Using defaults"}
                </span>
              </div>
              <div class="setup-section__body">
                <p class="capacities-settings__hint">
                  These rules are OR conditions for native RootTask and Task objects. Completed and dropped objects remain excluded.
                </p>
                <label class="capacities-setting-row">
                  <input
                    type="checkbox"
                    checked={policy?.activeEnabled ?? false}
                    onChange={(e) => updatePolicy({ activeEnabled: (e.currentTarget as HTMLInputElement).checked })}
                  />
                  <span>
                    <strong>Active status</strong>
                    <small>Include a task when its source status is Active.</small>
                  </span>
                </label>
                <label class="capacities-setting-row">
                  <input
                    type="checkbox"
                    checked={policy?.dueEnabled ?? false}
                    onChange={(e) => updatePolicy({ dueEnabled: (e.currentTarget as HTMLInputElement).checked })}
                  />
                  <span>
                    <strong>Due today or overdue</strong>
                    <small>Include a task whose due date is today or earlier.</small>
                  </span>
                </label>
                <label class="capacities-setting-row">
                  <input
                    type="checkbox"
                    checked={policy?.deadlineEnabled ?? false}
                    onChange={(e) => updatePolicy({ deadlineEnabled: (e.currentTarget as HTMLInputElement).checked })}
                  />
                  <span>
                    <strong>Deadline window</strong>
                    <small>Include a task whose deadline is overdue or within the horizon below.</small>
                  </span>
                </label>
                <div class="field capacities-settings__horizon">
                  <label for="capacities-deadline-horizon">Deadline horizon</label>
                  <div class="capacities-settings__horizon-control">
                    <input
                      id="capacities-deadline-horizon"
                      type="number"
                      min="0"
                      step="1"
                      value={horizonText}
                      disabled={!policy?.deadlineEnabled}
                      onInput={(e) => {
                        const text = (e.currentTarget as HTMLInputElement).value;
                        setHorizonText(text);
                        const value = Number(text);
                        if (Number.isSafeInteger(value) && value >= 0) {
                          updatePolicy({ deadlineHorizonDays: value });
                        }
                      }}
                    />
                    <span>calendar days</span>
                  </div>
                  {horizonInvalid && <span class="field-error" role="alert">Use a nonnegative whole number.</span>}
                </div>
              </div>
            </section>

            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-objects">
              <div class="setup-section__head">
                <h3 id="capacities-sec-objects">Known Capacities objects</h3>
              </div>
              <div class="setup-section__body">
                <p class="capacities-settings__hint">
                  This list is limited to Capacities rows in the current TDTB snapshot. It is not a provider inventory browser.
                  A source-owned Assigned marker still wins over a TDTB exclusion.
                </p>
                {known.length === 0 ? (
                  <p class="capacities-settings__empty">
                    No Capacities objects are present in the current plan inputs.
                  </p>
                ) : (
                  <div class="capacities-object-list">
                    {known.map((item) => {
                      const identity = identityOf(item)!;
                      const excluded = draft.excluded.includes(identity);
                      return (
                        <div class="capacities-object-row" key={identity}>
                          <div class="capacities-object-row__identity">
                            <strong>{item.name}</strong>
                            <small>{item.types.length > 0 ? item.types.join(" · ") : "Structure not named"}</small>
                            <code>{identity}</code>
                          </div>
                          <label class="capacities-object-row__toggle">
                            <input
                              type="checkbox"
                              checked={excluded}
                              onChange={() => toggleExclusion(identity)}
                            />
                            <span>{excluded ? "Excluded in TDTB" : "Available to TDTB"}</span>
                          </label>
                        </div>
                      );
                    })}
                  </div>
                )}
                {hiddenExclusions.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Saved exclusions outside this snapshot</h4>
                    {hiddenExclusions.map((identity) => (
                      <div class="capacities-exclusion-row" key={identity}>
                        <code>{identity}</code>
                        <button class="btn" onClick={() => toggleExclusion(identity)}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section class="setup-section capacities-settings__section" aria-labelledby="capacities-sec-active-pull">
              <div class="setup-section__head">
                <h3 id="capacities-sec-active-pull">Active pull</h3>
                {staleActiveStructures.length > 0 && (
                  <span class="capacities-settings__revision">
                    {staleActiveStructures.length} saved {staleActiveStructures.length === 1 ? "structure" : "structures"} outside this vault
                  </span>
                )}
              </div>
              <div class="setup-section__body">
                <p class="capacities-settings__hint">
                  Choose which Capacities structures honour an Active status pull. The list is read from this vault's Capacities source mapping, not from the current plan inputs.
                </p>
                {availableStructures.length === 0 ? (
                  <p class="capacities-settings__empty">
                    No Capacities structures are configured for this vault yet. Add a Capacities source mapping before choosing which structures honour an Active status pull.
                  </p>
                ) : (
                  <div class="capacities-object-list" role="group" aria-label="Capacities structures honouring an Active status pull">
                    {availableStructures.map((structureId) => {
                      const active = draft.activeStructures.includes(structureId);
                      return (
                        <div class="capacities-object-row" key={structureId}>
                          <div class="capacities-object-row__identity">
                            <code>{structureId}</code>
                          </div>
                          <label class="capacities-object-row__toggle">
                            <input
                              type="checkbox"
                              checked={active}
                              aria-label={`Active pull for ${structureId}`}
                              onChange={() => toggleActiveStructure(structureId)}
                            />
                            <span>{active ? "Active pull on" : "Active pull off"}</span>
                          </label>
                        </div>
                      );
                    })}
                  </div>
                )}
                {staleActiveStructures.length > 0 && (
                  <div class="capacities-exclusion-list">
                    <h4>Active structures outside this vault's mapping</h4>
                    <p class="capacities-settings__hint">
                      These {staleActiveStructures.length} saved {staleActiveStructures.length === 1 ? "id is" : "ids are"} not in this vault's Capacities source mapping. They are retained unchanged on save.
                    </p>
                    {staleActiveStructures.map((structureId) => (
                      <div class="capacities-exclusion-row" key={structureId}>
                        <code>{structureId}</code>
                        <button class="btn" onClick={() => toggleActiveStructure(structureId)}>Remove</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <p class="capacities-settings__footer-note">
              Revision conflicts keep the drawer open so you can reload before replacing newer settings.
            </p>
            {error && (
              <div class="capacities-settings__save-error" role="alert">
                <span>{error}</span>
                <button class="btn" onClick={() => void loadSettings()}>Reload settings</button>
              </div>
            )}
            <div class="editor__actions setup__actions">
              <button class="btn" onClick={close}>Cancel</button>
              <button class="btn btn--primary" onClick={() => void save()} disabled={phase === "saving" || horizonInvalid}>
                {phase === "saving" ? "Saving…" : "Save Capacities settings"}
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

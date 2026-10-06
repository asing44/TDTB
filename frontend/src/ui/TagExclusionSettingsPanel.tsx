/* TagExclusionSettingsPanel — the app-managed "tasks NOT tagged X" policy.
   Stable Capacities tag identities only; titles are display metadata. This
   surface reads and saves the local settings route and never discovers or
   mutates Capacities objects. Saved ids that are absent from the catalog stay
   listed and removable — absence is never read as "deleted" unless the
   advisory inventory reports itself complete.

   The body is mounted by SettingsShell for the life of the shell (whether or
   not this tab is selected), so unsaved drafts survive panel switches; the
   shell discards it on close and reopening re-initializes from current state.
   The dialog chrome, focus management, and close affordance belong to the
   shell. */

import { useEffect, useMemo, useState } from "preact/hooks";
import { useApp } from "./context";
import type {
  TagCatalog,
  TagExclusionIdentity,
  TagExclusionSettings,
  TagExclusionSettingsDraft,
} from "../model/types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function identityKey(identity: TagExclusionIdentity): string {
  return `${identity.source}:${identity.spaceId}:${identity.tagId}`;
}

function draftOf(settings: TagExclusionSettings): TagExclusionSettingsDraft {
  return { expectedRevision: settings.revision, tags: [...settings.tags] };
}

function statusText(catalog: TagCatalog): string {
  switch (catalog.status) {
    case "complete":
      return "Tag catalog: complete";
    case "partial":
      return "Tag catalog: partial — some tags may be missing";
    case "unavailable":
      return "Tag catalog: unavailable — saved tags are still listed";
    default:
      return "Tag catalog: Capacities is not configured";
  }
}

interface Row {
  key: string;
  identity: TagExclusionIdentity;
  title: string | null;
  unresolved: boolean;
}

export function TagExclusionSettingsPanel({ active }: { active: boolean }) {
  const { controller } = useApp();
  const [settings, setSettings] = useState<TagExclusionSettings | null>(null);
  const [draft, setDraft] = useState<TagExclusionSettingsDraft | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "saving" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [reloadRequired, setReloadRequired] = useState(false);

  const loadSettings = async () => {
    setPhase("loading");
    setError(null);
    setNotice(null);
    setReloadRequired(false);
    try {
      const loaded = await controller.loadTagExclusionSettings();
      setSettings(loaded);
      setDraft(draftOf(loaded));
      setPhase("ready");
    } catch (e) {
      setSettings(null);
      setDraft(null);
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    void loadSettings();
  }, [controller]);

  const rows = useMemo<Row[]>(() => {
    if (!draft) return [];
    const catalog = settings?.catalog;
    const out: Row[] = [];
    if (catalog?.spaceId) {
      for (const tag of catalog.tags) {
        out.push({
          key: `${catalog.spaceId}:${tag.id}`,
          identity: { source: "capacities", spaceId: catalog.spaceId, tagId: tag.id },
          title: tag.title,
          unresolved: false,
        });
      }
    }
    const catalogIds = new Set((catalog?.tags ?? []).map((tag) => tag.id));
    for (const identity of draft.tags) {
      const inCatalog =
        identity.spaceId === catalog?.spaceId && catalogIds.has(identity.tagId);
      if (inCatalog) continue;
      if (out.some((row) => identityKey(row.identity) === identityKey(identity))) continue;
      out.push({
        key: identityKey(identity),
        identity,
        title: null,
        unresolved: true,
      });
    }
    return out;
  }, [draft, settings]);

  // Duplicate titles are ALWAYS disambiguated with a short id suffix; the
  // title is display-only and never identity.
  const titleCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of rows) {
      if (!row.title) continue;
      const key = row.title.toLocaleLowerCase();
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return counts;
  }, [rows]);

  if (!active) return null;

  const selectedKeys = new Set((draft?.tags ?? []).map(identityKey));
  const inventoryComplete = settings?.catalog.status === "complete";
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visible = rows.filter((row) => {
    if (!normalizedQuery) return true;
    const label = `${row.title ?? ""} ${row.identity.tagId}`.toLocaleLowerCase();
    return label.includes(normalizedQuery);
  });

  const toggle = (identity: TagExclusionIdentity) => {
    setDraft((current) => {
      if (!current) return current;
      const key = identityKey(identity);
      const has = current.tags.some((tag) => identityKey(tag) === key);
      const tags = has
        ? current.tags.filter((tag) => identityKey(tag) !== key)
        : [...current.tags, { ...identity }].sort((a, b) =>
            identityKey(a) < identityKey(b) ? -1 : 1,
          );
      return { ...current, tags };
    });
  };

  const save = async () => {
    if (!draft || phase === "saving" || reloadRequired) return;
    setPhase("saving");
    setError(null);
    setNotice(null);
    try {
      const { settings: saved, refreshError } =
        await controller.saveTagExclusionSettings(draft);
      setSettings(saved);
      setDraft(draftOf(saved));
      setPhase("ready");
      setNotice(
        refreshError
          ? "Saved; planning refresh failed — eligibility shown may be stale."
          : "Saved.",
      );
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
      setReloadRequired(true);
    }
  };

  return (
    <div class="settings-panel settings-panel--tags">
        <p class="tag-exclusion-settings__intro">
          Tasks carrying any excluded tag are removed from the brief before
          selection. Matching uses stable Capacities tag identity; titles are
          display-only.
        </p>

        {phase === "loading" && (
          <p class="tag-exclusion-settings__state" role="status">
            Loading tag exclusions…
          </p>
        )}

        {phase === "error" && settings === null && (
          <section class="tag-exclusion-settings__error" role="alert">
            <strong>Tag exclusions could not be loaded</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void loadSettings()}>
              Reload settings
            </button>
          </section>
        )}

        {settings !== null && draft && (
          <>
            <section
              class="setup-section tag-exclusion-settings__section"
              aria-labelledby="tag-exclusion-sec-list"
            >
              <div class="setup-section__head">
                <h3 id="tag-exclusion-sec-list">Excluded tags</h3>
                <span class="tag-exclusion-settings__revision">
                  {settings.persisted
                    ? `Saved revision ${settings.revision}`
                    : "Using defaults"}
                </span>
              </div>
              <div class="setup-section__body">
                <p class="tag-exclusion-settings__hint" role="status">
                  {statusText(settings.catalog)}
                </p>
                {settings.catalog.warnings.map((warning) => (
                  <p class="tag-exclusion-settings__warning" role="status" key={warning}>
                    {warning}
                  </p>
                ))}
                <div class="field tag-exclusion-settings__search">
                  <label for="tag-exclusion-search">Search tags</label>
                  <input
                    id="tag-exclusion-search"
                    type="search"
                    value={query}
                    onInput={(e) =>
                      setQuery((e.currentTarget as HTMLInputElement).value)
                    }
                    placeholder="#habituals or tag id"
                  />
                </div>
                {visible.length === 0 ? (
                  <p class="tag-exclusion-settings__empty">
                    {rows.length === 0
                      ? "No tags are available from the catalog."
                      : "No tags match this search."}
                  </p>
                ) : (
                  <div class="tag-exclusion-list">
                    {visible.map((row) => {
                      const checked = selectedKeys.has(identityKey(row.identity));
                      const duplicate =
                        row.title !== null &&
                        (titleCounts.get(row.title.toLocaleLowerCase()) ?? 0) > 1;
                      const label =
                        row.title !== null
                          ? `#${row.title}${
                              duplicate ? ` (${row.identity.tagId.slice(0, 8)})` : ""
                            }`
                          : "Unresolved saved tag";
                      const ariaName =
                        row.title !== null
                          ? `Exclude #${row.title} (${row.identity.tagId})`
                          : `Exclude saved tag (${row.identity.tagId})`;
                      return (
                        <div class="tag-exclusion-row" key={row.key}>
                          <label class="tag-exclusion-row__toggle">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggle(row.identity)}
                              aria-label={ariaName}
                            />
                            <span>
                              <strong>{label}</strong>
                              <small>{row.identity.tagId}</small>
                            </span>
                          </label>
                          {row.unresolved && (
                            <span class="tag-exclusion-row__state">
                              {inventoryComplete
                                ? "No longer exists in Capacities"
                                : "Unavailable/unresolved"}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </section>

            {error !== null && (
              <section class="tag-exclusion-settings__error" role="alert">
                <strong>Save failed</strong>
                <span>{error}</span>
                <span>The draft is retained; reload before saving again.</span>
              </section>
            )}
            {notice !== null && (
              <p class="tag-exclusion-settings__notice" role="status">
                {notice}
              </p>
            )}

            <div class="tag-exclusion-settings__actions">
              {reloadRequired && (
                <button class="btn" onClick={() => void loadSettings()}>
                  Reload settings
                </button>
              )}
              <button
                class="btn btn--primary"
                onClick={() => void save()}
                disabled={phase === "saving" || reloadRequired}
                aria-label="Save tag exclusions"
              >
                {phase === "saving" ? "Saving…" : "Save tag exclusions"}
              </button>
            </div>
          </>
        )}
    </div>
  );
}

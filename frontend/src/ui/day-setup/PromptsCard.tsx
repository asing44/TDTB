/* Morning prompts (Planning 2a). A header row with the "Edit prompts" link,
   a four-column grid of prompt cards (Live micro-adventure + Reminder + For
   Meegy + Stoic, then browser-defined custom prompts), and a foot row with the
   export line and "+ Add prompt".

   There are no mono routing tags, no per-prompt export checkboxes, and no
   "Prompt exports" block any more: the backend exports every non-empty prompt
   to the Todoist Inbox at Commit. Custom prompt answers persist through the
   captures patch (`onPersistCustom`) rather than the retired opt-in store. */

import { useState } from "preact/hooks";

import type { MicroAdventure } from "../../model/types";
import { MicroPoolNote, poolOptionLabel } from "../microPool";
import {
  CustomPromptAdd,
  CustomPromptList,
  useCustomPrompts,
} from "./CustomPromptList";

export interface PromptSaveState {
  phase: string;
  error: string | null;
  warnings: string[];
}

export interface PromptsCardProps {
  micro: MicroAdventure;
  liveCustom: string;
  onLiveCustom: (value: string) => void;
  onPickPool: (id: string) => void;
  onSetCustom: () => void;
  onShuffle: () => void;
  onResetAuto: () => void;
  captures: { intention: string; forMeegy: string; stoic: string };
  onCapture: (key: "intention" | "forMeegy" | "stoic", value: string) => void;
  onAutosave: () => void;
  /** Persist a custom prompt answer through POST /day-setup captures:
      `{ [id]: answer }`; "" clears on the server. */
  onPersistCustom: (patch: Record<string, string>) => void;
  promptSave: PromptSaveState;
  /** Injected for tests; defaults to the browser's localStorage. */
  customStorage?: Storage;
}

/** The mockup sentence-cases the category ("nature" → "Nature"). */
function categoryLabel(category: string): string {
  return category.charAt(0).toUpperCase() + category.slice(1);
}

export function PromptsCard(props: PromptsCardProps) {
  const micro = props.micro;
  // Edit prompts reveals rename and remove for the browser-defined prompts.
  const [editingPrompts, setEditingPrompts] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const custom = useCustomPrompts({
    storage: props.customStorage,
    onPersist: props.onPersistCustom,
  });

  return (
    <section
      class="setup-section setup-section--prompts"
      data-settings-section="prompts"
      aria-labelledby="setup-sec-prompts"
    >
      <div class="setup-section__head">
        <h3 id="setup-sec-prompts">Morning prompts</h3>
        <button
          type="button"
          class="setup-section__head-action"
          aria-pressed={editingPrompts}
          onClick={() => setEditingPrompts(!editingPrompts)}
        >
          Edit prompts
        </button>
      </div>
      <div class="setup-section__body">
        <div class="prompts-grid">
          <article class="prompt-card prompt-card--live">
            <div class="prompt-card__labelrow">
              <label for="live-pick">Live micro-adventure</label>
            </div>
            <div class="prompt-card__controls field__controls">
              <select
                id="live-pick"
                value={micro.pick?.id ?? ""}
                onChange={(e) => props.onPickPool((e.target as HTMLSelectElement).value)}
              >
                {micro.pick && !micro.pool.some((p) => p.id === micro.pick!.id) && (
                  <option value={micro.pick.id}>{micro.pick.idea}</option>
                )}
                {micro.pool.map((idea) => (
                  <option key={idea.id} value={idea.id}>
                    {poolOptionLabel(idea.id, idea.idea)}
                  </option>
                ))}
              </select>
              <button class="btn" onClick={props.onShuffle} disabled={micro.pool.length < 2}>
                Shuffle
              </button>
            </div>
            <MicroPoolNote warnings={micro.poolWarnings ?? []} />
            <p class="live-micro__meta" aria-live="polite">
              {micro.pick?.category ? (
                <span>{categoryLabel(micro.pick.category)} · </span>
              ) : null}
              {micro.streak > 0 ? <span>streak {micro.streak} · </span> : null}
              <button
                type="button"
                class="live-micro__custom-link"
                aria-expanded={customOpen}
                onClick={() => setCustomOpen(!customOpen)}
              >
                Use my own idea
              </button>
            </p>
            {customOpen && (
              <div class="prompt-card__controls field__controls">
                <input
                  id="live-custom"
                  type="text"
                  aria-label="Custom idea"
                  value={props.liveCustom}
                  onInput={(e) => props.onLiveCustom((e.target as HTMLInputElement).value)}
                />
                <button
                  class="btn"
                  disabled={!props.liveCustom.trim()}
                  onClick={props.onSetCustom}
                >
                  Set custom
                </button>
              </div>
            )}
            {micro.source === "override" && (
              <div class="live-micro__actions">
                <button class="btn" onClick={props.onResetAuto}>
                  Reset to auto
                </button>
              </div>
            )}
          </article>

          <article class="prompt-card">
            <div class="prompt-card__labelrow">
              <label for="cap-intention">Reminder</label>
            </div>
            <textarea
              id="cap-intention"
              class="cap-intention"
              placeholder="One thing to remind myself today"
              value={props.captures.intention}
              onInput={(e) =>
                props.onCapture("intention", (e.target as HTMLTextAreaElement).value)
              }
              onBlur={props.onAutosave}
            />
          </article>
          <article class="prompt-card">
            <div class="prompt-card__labelrow">
              <label for="cap-meegy">For Meegy</label>
            </div>
            <textarea
              id="cap-meegy"
              value={props.captures.forMeegy}
              onInput={(e) =>
                props.onCapture("forMeegy", (e.target as HTMLTextAreaElement).value)
              }
              onBlur={props.onAutosave}
            />
          </article>
          <article class="prompt-card">
            <div class="prompt-card__labelrow">
              <label for="cap-stoic">Stoic</label>
            </div>
            <textarea
              id="cap-stoic"
              value={props.captures.stoic}
              onInput={(e) =>
                props.onCapture("stoic", (e.target as HTMLTextAreaElement).value)
              }
              onBlur={props.onAutosave}
            />
          </article>

          {custom.list.length > 0 && (
            <CustomPromptList
              prompts={custom.list}
              editing={editingPrompts}
              onRename={custom.rename}
              onAnswer={custom.answer}
              onAnswerBlur={custom.answerBlur}
              onRemove={custom.remove}
            />
          )}
        </div>

        {editingPrompts && (
          <p class="field__hint custom-prompt__edit-note" role="status">
            {custom.list.length === 0
              ? "Reminder, For Meegy and Stoic are built in and can't be renamed or removed. Use + Add prompt to add your own."
              : "Rename or remove your own prompts. Reminder, For Meegy and Stoic are built in and stay as they are."}
          </p>
        )}

        <div class="prompts-foot">
          <p class="prompts-export-line">
            Filled prompts are added to your Todoist Inbox when you commit.
          </p>
          <CustomPromptAdd onAdd={custom.add} />
        </div>

        {props.promptSave.error && (
          <p class="field-error" role="alert">
            Prompts could not be saved: {props.promptSave.error}
          </p>
        )}
        {props.promptSave.warnings.map((warning) => (
          <p class="field-warning" role="status">
            {warning}
          </p>
        ))}
      </div>
    </section>
  );
}

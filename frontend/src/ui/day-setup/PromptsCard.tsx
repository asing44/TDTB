/* U12 Morning prompts card. Re-homes the existing Live micro-adventure picker
   and the three optional prompt drafts with their U5 opt-ins and save state.
   No copy-prompt control exists anywhere (R20b, retired in U5), and no prompt
   text renders outside this card. */

import type { MicroAdventure } from "../../model/types";

export interface PromptSaveState {
  phase: string;
  conflict: { currentRevision: number } | null;
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
  optinsAvailable: boolean;
  optins: Record<string, boolean>;
  onToggleOptin: (key: "megan_nicety" | "stoic_intention", value: boolean) => void;
  promptSave: PromptSaveState;
  onSavePrompts: () => void;
}

export function PromptsCard(props: PromptsCardProps) {
  const micro = props.micro;
  return (
    <section
      class="setup-section setup-section--prompts"
      data-settings-section="prompts"
      aria-labelledby="setup-sec-prompts"
    >
      <div class="setup-section__head">
        <h3 id="setup-sec-prompts">Morning prompts</h3>
      </div>
      <div class="setup-section__body">
        <div class="prompts-live">
          <div class="field field--stacked">
            <div class="field__labelrow"><label for="live-pick">Live micro-adventure</label><span class="field__tag">pool → Live block</span></div>
            <div class="field__controls">
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
                    {idea.idea}
                  </option>
                ))}
              </select>
              <button class="btn" onClick={props.onShuffle} disabled={micro.pool.length < 2}>
                Shuffle
              </button>
            </div>
          </div>
          <p class="live-micro__status" aria-live="polite">
            {micro.pick ? (
              <span>
                🌱 {micro.pick.idea}
                {micro.pick.category ? ` · ${micro.pick.category}` : ""}{" "}
                <em>({micro.source === "override" ? "your pick" : "auto"})</em>
              </span>
            ) : (
              <span>No micro-adventure today</span>
            )}
            {micro.streak > 0 && <span> · streak {micro.streak}</span>}
          </p>
          <div class="field field--stacked">
            <div class="field__labelrow"><label for="live-custom">Custom idea</label><span class="field__tag">overrides the pick</span></div>
            <div class="field__controls">
              <input
                id="live-custom"
                type="text"
                value={props.liveCustom}
                onInput={(e) => props.onLiveCustom((e.target as HTMLInputElement).value)}
              />
              <button class="btn" disabled={!props.liveCustom.trim()} onClick={props.onSetCustom}>
                Set custom
              </button>
            </div>
          </div>
          {micro.source === "override" && (
            <div class="live-micro__actions">
              <button class="btn" onClick={props.onResetAuto}>
                Reset to auto
              </button>
            </div>
          )}
        </div>

        <div class="field field--stacked">
          <div class="field__labelrow"><label for="cap-intention">Intention</label><span class="field__tag">local draft</span></div>
          <textarea
            id="cap-intention"
            class="cap-intention"
            placeholder="One thing to focus on today"
            value={props.captures.intention}
            onInput={(e) =>
              props.onCapture("intention", (e.target as HTMLTextAreaElement).value)
            }
            onBlur={props.onAutosave}
          />
        </div>
        <div class="field field--stacked">
          <div class="field__labelrow"><label for="cap-meegy">For Meegy</label><span class="field__tag">local draft</span></div>
          <textarea
            id="cap-meegy"
            value={props.captures.forMeegy}
            onInput={(e) => props.onCapture("forMeegy", (e.target as HTMLTextAreaElement).value)}
            onBlur={props.onAutosave}
          />
        </div>
        <div class="field field--stacked">
          <div class="field__labelrow"><label for="cap-stoic">Stoic</label><span class="field__tag">local draft</span></div>
          <textarea
            id="cap-stoic"
            value={props.captures.stoic}
            onInput={(e) => props.onCapture("stoic", (e.target as HTMLTextAreaElement).value)}
            onBlur={props.onAutosave}
          />
        </div>

        <div class="field field--stacked prompt-exports" data-settings-section="prompt-exports">
          <span class="field__label">Prompt exports</span>
          {!props.optinsAvailable ? (
            <p class="field__hint" role="status">
              Export preferences are unavailable — waiting on a server read; no preference is
              assumed and nothing is overwritten.
            </p>
          ) : (
            <>
              <p class="field__hint">
                Opted-in prompts become Todoist Inbox tasks on Commit only. Intention is never
                exported.
              </p>
              <label class="prompt-optin">
                <input
                  type="checkbox"
                  checked={props.optins.megan_nicety === true}
                  onChange={(e) => props.onToggleOptin("megan_nicety", e.currentTarget.checked)}
                />
                For Meegy — create a Todoist task on Commit
              </label>
              <label class="prompt-optin">
                <input
                  type="checkbox"
                  checked={props.optins.stoic_intention === true}
                  onChange={(e) => props.onToggleOptin("stoic_intention", e.currentTarget.checked)}
                />
                Stoic — create a Todoist task on Commit
              </label>
            </>
          )}
          {props.promptSave.conflict && (
            <p class="field-error" role="alert">
              Export preferences changed elsewhere (stored revision{" "}
              {props.promptSave.conflict.currentRevision}). Your unsaved choice is kept — reload to
              reconcile.
            </p>
          )}
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
          <div class="prompt-exports__actions">
            <button
              type="button"
              class="btn"
              onClick={props.onSavePrompts}
              disabled={props.promptSave.phase === "saving"}
            >
              {props.promptSave.phase === "saving" ? "Saving prompts…" : "Save prompts"}
            </button>
            <span class="field__hint">Drafts save separately — they never confirm the day.</span>
          </div>
        </div>
      </div>
    </section>
  );
}

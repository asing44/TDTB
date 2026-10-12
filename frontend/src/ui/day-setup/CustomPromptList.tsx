/* Local-only custom morning prompts. Rendered below the built-in
   prompt drafts. Questions and answers live in this browser's storage only:
   no backend write, no Todoist export, and they never confirm the day. The
   Edit prompts toggle (owned by PromptsCard) reveals rename and remove. */

import { useState } from "preact/hooks";

import {
  addCustomPrompt,
  newCustomPromptId,
  readCustomPrompts,
  removeCustomPrompt,
  renameCustomPrompt,
  setCustomAnswer,
  writeCustomPrompts,
  type CustomPrompt,
} from "./customPrompts";

export interface CustomPromptListProps {
  editing: boolean;
  /** Injected for tests; defaults to the browser's localStorage. */
  storage?: Storage;
}

export function CustomPromptList(props: CustomPromptListProps) {
  const [list, setList] = useState<CustomPrompt[]>(() => readCustomPrompts(props.storage));
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");

  const commit = (next: CustomPrompt[]) => {
    setList(next);
    writeCustomPrompts(next, props.storage);
  };

  const closeAdd = () => {
    setAdding(false);
    setDraft("");
  };

  const add = () => {
    const next = addCustomPrompt(list, draft, newCustomPromptId());
    if (next === list) return;
    commit(next);
    closeAdd();
  };

  return (
    <>
      {props.editing && (
        <p class="field__hint custom-prompt__edit-note" role="status">
          {list.length === 0
            ? "Intention, For Meegy and Stoic are built in and can't be renamed or removed. Use + Add prompt below to add your own."
            : "Rename or remove your own prompts. Intention, For Meegy and Stoic are built in and stay as they are."}
        </p>
      )}
      {list.map((p) => {
        const fieldId = `custom-prompt-${p.id}`;
        return (
          <div class="field field--stacked custom-prompt" key={p.id}>
            <div class="field__labelrow">
              {props.editing ? (
                <>
                  <input
                    type="text"
                    class="custom-prompt__rename"
                    aria-label={`Prompt question for ${p.question}`}
                    value={p.question}
                    onInput={(e) =>
                      commit(renameCustomPrompt(list, p.id, (e.target as HTMLInputElement).value))
                    }
                  />
                  <button
                    type="button"
                    class="btn"
                    aria-label={`Remove ${p.question}`}
                    onClick={() => commit(removeCustomPrompt(list, p.id))}
                  >
                    Remove
                  </button>
                </>
              ) : (
                <>
                  <label for={fieldId}>{p.question}</label>
                  <span class="field__tag">local only</span>
                </>
              )}
            </div>
            <textarea
              id={fieldId}
              aria-label={props.editing ? p.question : undefined}
              value={p.answer}
              onInput={(e) =>
                commit(setCustomAnswer(list, p.id, (e.target as HTMLTextAreaElement).value))
              }
            />
          </div>
        );
      })}
      {adding ? (
        <div class="custom-prompt__add">
          <input
            type="text"
            aria-label="New prompt question"
            placeholder="Question"
            value={draft}
            onInput={(e) => setDraft((e.target as HTMLInputElement).value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              } else if (e.key === "Escape") {
                closeAdd();
              }
            }}
          />
          <button type="button" class="btn" onClick={add} disabled={!draft.trim()}>
            Add
          </button>
          <button type="button" class="btn" onClick={closeAdd}>
            Cancel
          </button>
        </div>
      ) : (
        <button type="button" class="custom-prompt__open" onClick={() => setAdding(true)}>
          + Add prompt
        </button>
      )}
    </>
  );
}

/* Browser-defined custom morning prompts (Planning 2a). Questions live in
   this browser's storage; answers persist through POST /day-setup `captures`
   keyed by CustomPrompt.id so the backend exports every non-empty prompt to
   the Todoist Inbox at Commit. The Edit prompts toggle (owned by PromptsCard)
   reveals rename and remove.

   `useCustomPrompts` owns the list state; `CustomPromptList` renders the cards
   as grid items and `CustomPromptAdd` is the "+ Add prompt" control placed
   under the grid. */

import { useEffect, useRef, useState } from "preact/hooks";

import {
  addCustomPrompt,
  customPromptCapture,
  newCustomPromptId,
  readCustomPrompts,
  removeCustomPrompt,
  renameCustomPrompt,
  setCustomAnswer,
  writeCustomPrompts,
  type CustomPrompt,
} from "./customPrompts";

/** How long a typed answer waits before its coalesced PATCH is sent. Blur
    flushes immediately, so this only covers an answer typed and left in place. */
export const CUSTOM_PROMPT_PERSIST_DELAY_MS = 600;

export interface CustomPromptsStore {
  list: CustomPrompt[];
  add: (question: string) => void;
  rename: (id: string, question: string) => void;
  /** Update the local answer immediately; the PATCH is deferred and coalesced. */
  answer: (id: string, answer: string) => void;
  /** Send the pending answer for one prompt now (textarea blur). */
  answerBlur: (id: string) => void;
  remove: (id: string) => void;
}

/** List state for the browser-defined prompts. `onPersist` receives the
    captures patch (`{ [id]: answer }`) the controller sends to the backend;
    a removed prompt is cleared with "". Injected `storage` is for tests.

    Answers persist on blur (like the built-in captures) plus a trailing
    debounce that coalesces keystrokes, so the LAST text typed is the one that
    reaches the server. Persisting on every input fired one unawaited PATCH per
    keystroke with no ordering guarantee, so an earlier response could land
    after a later one and diverge the server draft from the local one. */
export function useCustomPrompts(options: {
  storage?: Storage;
  onPersist?: (patch: Record<string, string>) => void;
} = {}): CustomPromptsStore {
  const [list, setList] = useState<CustomPrompt[]>(() => readCustomPrompts(options.storage));
  const pendingRef = useRef<Map<string, string>>(new Map());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const persistRef = useRef(options.onPersist);
  persistRef.current = options.onPersist;

  const commit = (next: CustomPrompt[]) => {
    setList(next);
    writeCustomPrompts(next, options.storage);
  };

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  /** Persist one pending answer (id given) or every pending answer at once. */
  const flush = (id?: string) => {
    const pending = pendingRef.current;
    if (id !== undefined) {
      const answer = pending.get(id);
      if (answer === undefined) return;
      pending.delete(id);
      persistRef.current?.(customPromptCapture(id, answer));
      if (pending.size === 0) clearTimer();
      return;
    }
    clearTimer();
    if (pending.size === 0) return;
    const entries = Array.from(pending.entries());
    pending.clear();
    for (const [key, value] of entries) persistRef.current?.(customPromptCapture(key, value));
  };

  const schedule = () => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      flush();
    }, CUSTOM_PROMPT_PERSIST_DELAY_MS);
  };

  // A typed answer still pending when the card unmounts is flushed, so hiding
  // or navigating away from the card never drops the latest keystrokes.
  const flushRef = useRef(flush);
  flushRef.current = flush;
  useEffect(() => () => flushRef.current(), []);

  return {
    list,
    add: (question: string) => {
      const next = addCustomPrompt(list, question, newCustomPromptId());
      if (next !== list) commit(next);
    },
    rename: (id: string, question: string) => {
      commit(renameCustomPrompt(list, id, question));
    },
    answer: (id: string, answer: string) => {
      commit(setCustomAnswer(list, id, answer));
      pendingRef.current.set(id, answer);
      schedule();
    },
    answerBlur: (id: string) => {
      flush(id);
    },
    remove: (id: string) => {
      pendingRef.current.delete(id);
      if (pendingRef.current.size === 0) clearTimer();
      commit(removeCustomPrompt(list, id));
      persistRef.current?.(customPromptCapture(id, ""));
    },
  };
}

export interface CustomPromptListProps {
  prompts: CustomPrompt[];
  editing: boolean;
  onRename: (id: string, question: string) => void;
  onAnswer: (id: string, answer: string) => void;
  /** Flush the prompt's pending answer immediately (textarea blur). */
  onAnswerBlur: (id: string) => void;
  onRemove: (id: string) => void;
}

export function CustomPromptList(props: CustomPromptListProps) {
  return (
    <>
      {props.prompts.map((p) => {
        const fieldId = `custom-prompt-${p.id}`;
        return (
          <article class="prompt-card custom-prompt" key={p.id}>
            <div class="prompt-card__labelrow">
              {props.editing ? (
                <>
                  <input
                    type="text"
                    class="custom-prompt__rename"
                    aria-label={`Prompt question for ${p.question}`}
                    value={p.question}
                    onInput={(e) =>
                      props.onRename(p.id, (e.target as HTMLInputElement).value)
                    }
                  />
                  <button
                    type="button"
                    class="btn"
                    aria-label={`Remove ${p.question}`}
                    onClick={() => props.onRemove(p.id)}
                  >
                    Remove
                  </button>
                </>
              ) : (
                <label for={fieldId}>{p.question}</label>
              )}
            </div>
            <textarea
              id={fieldId}
              aria-label={props.editing ? p.question : undefined}
              value={p.answer}
              onInput={(e) =>
                props.onAnswer(p.id, (e.target as HTMLTextAreaElement).value)
              }
              onBlur={() => props.onAnswerBlur(p.id)}
            />
          </article>
        );
      })}
    </>
  );
}

export interface CustomPromptAddProps {
  onAdd: (question: string) => void;
}

export function CustomPromptAdd(props: CustomPromptAddProps) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");

  const closeAdd = () => {
    setAdding(false);
    setDraft("");
  };
  const add = () => {
    if (!draft.trim()) return;
    props.onAdd(draft);
    closeAdd();
  };

  return adding ? (
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
  );
}

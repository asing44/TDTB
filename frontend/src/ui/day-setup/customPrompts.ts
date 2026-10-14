/* Browser-defined custom morning prompts (Planning 2a). Each prompt is a
   question plus a draft answer. Question definitions stay in this browser's
   localStorage; the answer also persists through the backend captures patch
   (``customPromptCapture``) so every non-empty prompt exports to the Todoist
   Inbox at Commit. Reads and writes tolerate malformed or unavailable storage
   so the card still renders. */

export interface CustomPrompt {
  id: string;
  question: string;
  answer: string;
}

export const CUSTOM_PROMPTS_KEY = "tdtb.customPrompts.v1";

export function parseCustomPrompts(raw: string): CustomPrompt[] {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter(
    (p): p is CustomPrompt =>
      typeof p === "object" &&
      p !== null &&
      typeof (p as CustomPrompt).id === "string" &&
      typeof (p as CustomPrompt).question === "string" &&
      typeof (p as CustomPrompt).answer === "string",
  );
}

/** The injected store, else the browser's localStorage. Null when neither is
    available (disabled storage, or a test environment without it). */
function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage;
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readCustomPrompts(storage?: Storage): CustomPrompt[] {
  const store = resolveStorage(storage);
  if (!store) return [];
  try {
    const raw = store.getItem(CUSTOM_PROMPTS_KEY);
    return raw === null ? [] : parseCustomPrompts(raw);
  } catch {
    return [];
  }
}

export function writeCustomPrompts(list: CustomPrompt[], storage?: Storage): void {
  const store = resolveStorage(storage);
  if (!store) return;
  try {
    store.setItem(CUSTOM_PROMPTS_KEY, JSON.stringify(list));
  } catch {
    // Storage full or disabled: the prompt stays on screen for this visit.
  }
}

export function addCustomPrompt(list: CustomPrompt[], question: string, id: string): CustomPrompt[] {
  const text = question.trim();
  if (!text) return list;
  return [...list, { id, question: text, answer: "" }];
}

/** Rename keeps the raw text as typed, so a field can be cleared and retyped. */
export function renameCustomPrompt(list: CustomPrompt[], id: string, question: string): CustomPrompt[] {
  return list.map((p) => (p.id === id ? { ...p, question } : p));
}

export function setCustomAnswer(list: CustomPrompt[], id: string, answer: string): CustomPrompt[] {
  return list.map((p) => (p.id === id ? { ...p, answer } : p));
}

export function removeCustomPrompt(list: CustomPrompt[], id: string): CustomPrompt[] {
  return list.filter((p) => p.id !== id);
}

export function newCustomPromptId(now: number = Date.now(), rand: () => number = Math.random): string {
  return `custom-${now.toString(36)}-${Math.floor(rand() * 1e6).toString(36)}`;
}

/** The captures patch for one custom prompt: ``{ [id]: answer }``. An empty
    answer is sent verbatim so the server clears the draft (an omitted key
    preserves it), matching the PATCH semantics of POST /day-setup. */
export function customPromptCapture(id: string, answer: string): Record<string, string> {
  return { [id]: answer };
}

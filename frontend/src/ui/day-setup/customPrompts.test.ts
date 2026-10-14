/* customPrompts.test.ts. Local-only custom morning prompts. They
   live in this browser's localStorage and are never sent to the backend or
   Todoist. The store is a pure list plus a tolerant storage round trip. */

import { beforeEach, describe, expect, it } from "vitest";

import {
  CUSTOM_PROMPTS_KEY,
  addCustomPrompt,
  customPromptCapture,
  parseCustomPrompts,
  readCustomPrompts,
  removeCustomPrompt,
  renameCustomPrompt,
  setCustomAnswer,
  writeCustomPrompts,
  type CustomPrompt,
} from "./customPrompts";

const ONE: CustomPrompt = { id: "custom-a", question: "Gratitude", answer: "" };

function memoryStorage(seed?: string): Storage {
  const map = new Map<string, string>();
  if (seed !== undefined) map.set(CUSTOM_PROMPTS_KEY, seed);
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
  } as Storage;
}

describe("custom prompt list", () => {
  it("adds a trimmed question with an empty answer", () => {
    const out = addCustomPrompt([], "  Weather check  ", "custom-b");
    expect(out).toEqual([{ id: "custom-b", question: "Weather check", answer: "" }]);
  });

  it("ignores a blank question", () => {
    expect(addCustomPrompt([ONE], "   ", "custom-b")).toEqual([ONE]);
  });

  it("renames by id and keeps the answer", () => {
    const seeded = setCustomAnswer([ONE], "custom-a", "grateful for rain");
    expect(renameCustomPrompt(seeded, "custom-a", "Gratitude 2")).toEqual([
      { id: "custom-a", question: "Gratitude 2", answer: "grateful for rain" },
    ]);
  });

  it("removes by id", () => {
    expect(removeCustomPrompt([ONE, { ...ONE, id: "custom-c" }], "custom-a")).toEqual([
      { ...ONE, id: "custom-c" },
    ]);
  });

  it("sets an answer by id without touching the others", () => {
    const two = [ONE, { id: "custom-c", question: "Other", answer: "keep" }];
    expect(setCustomAnswer(two, "custom-a", "new")[1]).toEqual(two[1]);
    expect(setCustomAnswer(two, "custom-a", "new")[0].answer).toBe("new");
  });

  it("builds a single-key captures patch, sending'' verbatim to clear", () => {
    expect(customPromptCapture("custom-abc-1", "the rain")).toEqual({
      "custom-abc-1": "the rain",
    });
    expect(customPromptCapture("custom-abc-1", "")).toEqual({ "custom-abc-1": "" });
  });
});

describe("custom prompt storage", () => {
  beforeEach(() => {
    // Each case starts from an empty store.
  });

  it("round-trips through localStorage under one key", () => {
    const storage = memoryStorage();
    writeCustomPrompts([ONE], storage);
    expect(storage.getItem(CUSTOM_PROMPTS_KEY)).toContain("Gratitude");
    expect(readCustomPrompts(storage)).toEqual([ONE]);
  });

  it("reads nothing as an empty list", () => {
    expect(readCustomPrompts(memoryStorage())).toEqual([]);
  });

  it("drops malformed JSON and malformed entries instead of throwing", () => {
    expect(readCustomPrompts(memoryStorage("{not json"))).toEqual([]);
    const mixed = JSON.stringify([ONE, { id: 3 }, "x", { id: "c", question: "Q", answer: 1 }]);
    expect(parseCustomPrompts(mixed)).toEqual([ONE]);
  });

  it("swallows a write failure so the page keeps working", () => {
    const broken = {
      ...memoryStorage(),
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    } as unknown as Storage;
    expect(() => writeCustomPrompts([ONE], broken)).not.toThrow();
  });
});

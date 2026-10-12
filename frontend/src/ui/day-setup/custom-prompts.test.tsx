/* custom-prompts.test.tsx: "+ Add prompt" and the Edit prompts mode
   for local-only custom morning prompts. Everything stays in the injected
   store (the same in-memory Storage pattern as store/persist.test.ts). */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";

import { CustomPromptList } from "./CustomPromptList";
import { CUSTOM_PROMPTS_KEY, readCustomPrompts } from "./customPrompts";

afterEach(cleanup);

function memoryStorage(seed?: [string, string][]): Storage {
  const map = new Map<string, string>(seed);
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

describe("custom morning prompts", () => {
  it("adds a local prompt, answers it, and keeps both after a remount", () => {
    const store = memoryStorage();
    const r = render(<CustomPromptList editing={false} storage={store} />);
    fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
    fireEvent.input(r.getByLabelText("New prompt question"), { target: { value: "Gratitude" } });
    fireEvent.click(r.getByRole("button", { name: "Add" }));
    fireEvent.input(r.getByLabelText("Gratitude"), { target: { value: "the rain" } });
    expect(readCustomPrompts(store)).toEqual([
      expect.objectContaining({ question: "Gratitude", answer: "the rain" }),
    ]);
    r.unmount();

    const again = render(<CustomPromptList editing={false} storage={store} />);
    expect((again.getByLabelText("Gratitude") as HTMLTextAreaElement).value).toBe("the rain");
  });

  it("labels the prompts as local only", () => {
    const store = memoryStorage([
      [CUSTOM_PROMPTS_KEY, JSON.stringify([{ id: "c1", question: "Gratitude", answer: "" }])],
    ]);
    const r = render(<CustomPromptList editing={false} storage={store} />);
    expect(r.getByText("local only")).toBeTruthy();
  });

  it("does not add a blank question", () => {
    const store = memoryStorage();
    const r = render(<CustomPromptList editing={false} storage={store} />);
    fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
    fireEvent.input(r.getByLabelText("New prompt question"), { target: { value: "   " } });
    fireEvent.click(r.getByRole("button", { name: "Add" }));
    expect(readCustomPrompts(store)).toEqual([]);
  });

  it("renames and removes a prompt only in edit mode", () => {
    const store = memoryStorage([
      [
        CUSTOM_PROMPTS_KEY,
        JSON.stringify([
          { id: "c1", question: "Gratitude", answer: "keep" },
          { id: "c2", question: "Weather", answer: "" },
        ]),
      ],
    ]);
    const r = render(<CustomPromptList editing={true} storage={store} />);
    fireEvent.input(r.getByLabelText("Prompt question for Gratitude"), {
      target: { value: "Thanks" },
    });
    expect(readCustomPrompts(store)[0]).toEqual({ id: "c1", question: "Thanks", answer: "keep" });
    fireEvent.click(r.getByRole("button", { name: "Remove Weather" }));
    expect(readCustomPrompts(store).map((p) => p.id)).toEqual(["c1"]);
  });

  it("hides rename and remove outside edit mode", () => {
    const store = memoryStorage([
      [CUSTOM_PROMPTS_KEY, JSON.stringify([{ id: "c1", question: "Gratitude", answer: "" }])],
    ]);
    const r = render(<CustomPromptList editing={false} storage={store} />);
    expect(r.queryByLabelText("Prompt question for Gratitude")).toBeNull();
    expect(r.queryByRole("button", { name: "Remove Gratitude" })).toBeNull();
  });

  it("explains the built-ins when editing an empty list", () => {
    const r = render(<CustomPromptList editing={true} storage={memoryStorage()} />);
    expect(r.getByRole("button", { name: "+ Add prompt" })).toBeTruthy();
    expect(r.getByRole("status").textContent).toContain("built in");
  });
});

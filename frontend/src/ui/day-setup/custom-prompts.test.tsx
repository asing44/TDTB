/* custom-prompts.test.tsx: browser-defined morning prompts (Planning 2a).
   Question definitions stay in the injected store; answers persist through the
   captures patch (`{ [id]: answer }`, "" clears) that the card forwards to the
   backend. The Edit prompts toggle reveals rename and remove. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";

import {
  CUSTOM_PROMPT_PERSIST_DELAY_MS,
  CustomPromptAdd,
  CustomPromptList,
  useCustomPrompts,
} from "./CustomPromptList";
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

function Host({
  editing,
  storage,
  onPersist,
}: {
  editing: boolean;
  storage: Storage;
  onPersist?: (patch: Record<string, string>) => void;
}) {
  const custom = useCustomPrompts({ storage, onPersist });
  return (
    <>
      <CustomPromptList
        prompts={custom.list}
        editing={editing}
        onRename={custom.rename}
        onAnswer={custom.answer}
        onAnswerBlur={custom.answerBlur}
        onRemove={custom.remove}
      />
      <CustomPromptAdd onAdd={custom.add} />
    </>
  );
}

describe("custom morning prompts", () => {
  it("adds a local prompt, answers it, and keeps both after a remount", () => {
    const store = memoryStorage();
    const r = render(<Host editing={false} storage={store} />);
    fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
    fireEvent.input(r.getByLabelText("New prompt question"), { target: { value: "Gratitude" } });
    fireEvent.click(r.getByRole("button", { name: "Add" }));
    fireEvent.input(r.getByLabelText("Gratitude"), { target: { value: "the rain" } });
    expect(readCustomPrompts(store)).toEqual([
      expect.objectContaining({ question: "Gratitude", answer: "the rain" }),
    ]);
    r.unmount();

    const again = render(<Host editing={false} storage={store} />);
    expect((again.getByLabelText("Gratitude") as HTMLTextAreaElement).value).toBe("the rain");
  });

  it("does not add a blank question", () => {
    const store = memoryStorage();
    const r = render(<Host editing={false} storage={store} />);
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
    const r = render(<Host editing={true} storage={store} />);
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
    const r = render(<Host editing={false} storage={store} />);
    expect(r.queryByLabelText("Prompt question for Gratitude")).toBeNull();
    expect(r.queryByRole("button", { name: "Remove Gratitude" })).toBeNull();
  });

  it("coalesces keystrokes into one persist of the final text", () => {
    vi.useFakeTimers();
    try {
      const store = memoryStorage();
      const persist = vi.fn();
      const r = render(<Host editing={false} storage={store} onPersist={persist} />);
      fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
      fireEvent.input(r.getByLabelText("New prompt question"), { target: { value: "Gratitude" } });
      fireEvent.click(r.getByRole("button", { name: "Add" }));
      const prompt = readCustomPrompts(store)[0];

      const field = r.getByLabelText("Gratitude") as HTMLTextAreaElement;
      fireEvent.input(field, { target: { value: "t" } });
      fireEvent.input(field, { target: { value: "th" } });
      fireEvent.input(field, { target: { value: "the rain" } });
      expect(persist).not.toHaveBeenCalled();

      vi.advanceTimersByTime(CUSTOM_PROMPT_PERSIST_DELAY_MS);
      expect(persist).toHaveBeenCalledTimes(1);
      expect(persist).toHaveBeenCalledWith({ [prompt.id]: "the rain" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("persists immediately on blur and does not double-send", () => {
    vi.useFakeTimers();
    try {
      const store = memoryStorage();
      const persist = vi.fn();
      const r = render(<Host editing={false} storage={store} onPersist={persist} />);
      fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
      fireEvent.input(r.getByLabelText("New prompt question"), { target: { value: "Gratitude" } });
      fireEvent.click(r.getByRole("button", { name: "Add" }));
      const prompt = readCustomPrompts(store)[0];

      const field = r.getByLabelText("Gratitude") as HTMLTextAreaElement;
      fireEvent.input(field, { target: { value: "the rain" } });
      fireEvent.blur(field);
      expect(persist).toHaveBeenCalledTimes(1);
      expect(persist).toHaveBeenCalledWith({ [prompt.id]: "the rain" });

      vi.advanceTimersByTime(CUSTOM_PROMPT_PERSIST_DELAY_MS);
      expect(persist).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("flushes a still-pending answer when the card unmounts", () => {
    vi.useFakeTimers();
    try {
      const store = memoryStorage();
      const persist = vi.fn();
      const r = render(<Host editing={false} storage={store} onPersist={persist} />);
      fireEvent.click(r.getByRole("button", { name: "+ Add prompt" }));
      fireEvent.input(r.getByLabelText("New prompt question"), { target: { value: "Gratitude" } });
      fireEvent.click(r.getByRole("button", { name: "Add" }));
      const prompt = readCustomPrompts(store)[0];

      fireEvent.input(r.getByLabelText("Gratitude"), { target: { value: "the rain" } });
      r.unmount();
      expect(persist).toHaveBeenCalledWith({ [prompt.id]: "the rain" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("clears a removed prompt on the server with an empty answer", () => {
    const store = memoryStorage([
      [CUSTOM_PROMPTS_KEY, JSON.stringify([{ id: "c1", question: "Gratitude", answer: "keep" }])],
    ]);
    const persist = vi.fn();
    const r = render(<Host editing={true} storage={store} onPersist={persist} />);
    fireEvent.click(r.getByRole("button", { name: "Remove Gratitude" }));
    expect(persist).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenLastCalledWith({ c1: "" });
    expect(readCustomPrompts(store)).toEqual([]);
  });
});

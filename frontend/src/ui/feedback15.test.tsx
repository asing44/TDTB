/* FEEDBACK-15 — Correct Intention copy and textarea readability. Validates
   the one-focus copy, placeholder, and every readable state (text, surface,
   border, focus, disabled) plus the narrow-width no-clip rule, while pinning
   persistence behavior and 12-hour user-facing times. FEEDBACK-13/14
   contracts are preserved: the helper sentence, aria wiring, and label are
   unchanged. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, act } from "@testing-library/preact";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { SetupDrawer } from "./SetupDrawer";
import { makeHarness } from "./test-harness";

afterEach(cleanup);

// Resolve app.css from the frontend root regardless of vitest cwd.
const CSS_CANDIDATES = [
  resolve(process.cwd(), "app.css"),
  resolve(process.cwd(), "frontend", "app.css"),
  resolve(process.cwd(), "src", "app.css"),
];
const CSS_PATH = CSS_CANDIDATES.find((p) => existsSync(p)) ?? CSS_CANDIDATES[0];
const APP_CSS = readFileSync(CSS_PATH, "utf8");

function openDrawer() {
  const h = makeHarness("ready");
  h.store.dispatch({ type: "UI", patch: { setupOpen: true } });
  const r = h.ui(<SetupDrawer />);
  return { h, r };
}

describe("FEEDBACK-15 — Intention copy and textarea readability", () => {
  it("shows the exact one-focus helper and a one-focus placeholder", () => {
    const { r } = openDrawer();
    // One-focus copy: the helper names exactly one thing for today.
    expect(r.getByText("One thing to focus on today.")).toBeTruthy();
    // The empty state carries the same one-focus framing.
    const ta = r.getByLabelText("Intention") as HTMLTextAreaElement;
    expect(ta.getAttribute("placeholder")?.toLowerCase()).toContain("one thing");
    // FEEDBACK-13 aria wiring remains intact.
    expect(ta.getAttribute("aria-describedby")).toBe("cap-intention-hint");
  });

  it("defines readable text, placeholder, surface, border, focus, and disabled states in CSS", () => {
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*color:\s*var\(--t-text\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*background:\s*var\(--t-surface\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*border:\s*1px solid var\(--t-border\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention\s*\{[\s\S]*min-height:\s*64px/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention::placeholder\s*\{[\s\S]*color:\s*var\(--t-muted\)/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention:focus-visible\s*\{[\s\S]*outline/);
    // Disabled state is readable and intentional, not a silent gray wash.
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention:disabled\s*\{[\s\S]*color:/);
    expect(APP_CSS).toMatch(/\.field textarea\.cap-intention:disabled\s*\{[\s\S]*cursor:\s*not-allowed/);
  });

  it("keeps the intention textarea unclipped at narrow widths", () => {
    // At phone width the textarea takes a full field line (flex-basis: 100%)
    // so the 96px label never squeezes or clips the capture.
    expect(APP_CSS).toMatch(
      /@media\s*\(max-width:\s*520px\)[\s\S]*\.field textarea\.cap-intention\s*\{[^}]*flex-basis:\s*100%/,
    );
  });

  it("persists the typed intention unchanged on save", async () => {
    const { h, r } = openDrawer();
    const save = vi.spyOn(h.controller, "saveDaySetup").mockResolvedValue();
    const ta = r.getByLabelText("Intention") as HTMLTextAreaElement;
    fireEvent.input(ta, { target: { value: "Close the FEEDBACK-15 loop" } });
    await act(async () => {
      fireEvent.click(r.getByText("Save day setup"));
    });
    expect(save).toHaveBeenCalledWith(expect.objectContaining({
      captures: expect.objectContaining({
        intention: "Close the FEEDBACK-15 loop",
      }),
    }));
  });

  it("keeps user-facing times 12-hour in the drawer", () => {
    const { r } = openDrawer();
    const text = r.container.textContent ?? "";
    expect(text).toMatch(/AM|PM/);
    const bare = text.match(/\d{1,2}:\d{2}(?!\s*(AM|PM))/g);
    expect(bare).toBeNull();
  });
});

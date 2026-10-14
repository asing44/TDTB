import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/preact";
import { AnchorsCard, type AnchorTimeState } from "./AnchorsCard";

afterEach(cleanup);

const clean: AnchorTimeState = { configTime: "17:45", dirty: false, saved: false, saving: false, error: null };

function setup(timeState: AnchorTimeState | undefined, extra: Partial<Parameters<typeof AnchorsCard>[0]> = {}) {
  const onSaveTime = vi.fn();
  const onResetTime = vi.fn();
  const onEdit = vi.fn();
  const r = render(
    <AnchorsCard
      counted={[{ id: "Sudsing", name: "Sudsing", time: "18:15", blocks: 1, timeState }]}
      notCounted={[]}
      onEdit={onEdit}
      onSaveTime={onSaveTime}
      onResetTime={onResetTime}
      {...extra}
    />,
  );
  return { r, onSaveTime, onResetTime, onEdit };
}

describe("AnchorsCard time controls", () => {
  it("shows nothing extra for an anchor at its configured time", () => {
    const { r } = setup(clean);
    expect(r.queryByRole("button", { name: "Save time" })).toBeNull();
    expect(r.queryByText("Today only")).toBeNull();
    expect(r.queryByText("Saved")).toBeNull();
  });

  it("offers Save time and Reset, labelled Today only, once the time differs from config", () => {
    const { r, onSaveTime, onResetTime } = setup({ ...clean, dirty: true });
    expect(r.getByText("Today only")).toBeTruthy();
    expect(r.getByText(/Configured 5:45 PM/)).toBeTruthy();
    expect(r.getByText("Saves for every day this anchor runs")).toBeTruthy();
    fireEvent.click(r.getByRole("button", { name: "Save time" }));
    expect(onSaveTime).toHaveBeenCalledWith("Sudsing");
    fireEvent.click(r.getByRole("button", { name: "Reset" }));
    expect(onResetTime).toHaveBeenCalledWith("Sudsing");
  });

  it("does not open the editor when a control is used", () => {
    const { r, onEdit } = setup({ ...clean, dirty: true });
    fireEvent.click(r.getByRole("button", { name: "Save time" }));
    fireEvent.click(r.getByRole("button", { name: "Reset" }));
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("disables both controls while saving", () => {
    const { r } = setup({ ...clean, dirty: true, saving: true });
    expect((r.getByRole("button", { name: /Saving/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((r.getByRole("button", { name: "Reset" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says Saved once persisted, without the save controls", () => {
    const { r } = setup({ ...clean, configTime: "18:15", saved: true });
    expect(r.getByText("Saved")).toBeTruthy();
    expect(r.queryByRole("button", { name: "Save time" })).toBeNull();
  });

  it("shows a save failure inline as an alert and keeps the controls", () => {
    const { r } = setup({ ...clean, dirty: true, error: "The machine-local config.json overrides anchored_blocks" });
    expect(r.getByRole("alert").textContent).toContain("overrides anchored_blocks");
    expect(r.getByRole("button", { name: "Save time" })).toBeTruthy();
  });

  it("explains that a window anchor saves its open time", () => {
    const { r } = setup({ ...clean, dirty: true, windowOpen: true });
    expect(r.getByText(/window's open time/)).toBeTruthy();
  });

  it("puts the controls on a not-counted tile that was moved too", () => {
    const onSaveTime = vi.fn();
    const r = render(
      <AnchorsCard
        counted={[]}
        notCounted={[{
          id: "Sudsing", name: "Sudsing", time: "06:00", reason: "outside frame",
          timeState: { ...clean, dirty: true },
        }]}
        onEdit={() => {}}
        onSaveTime={onSaveTime}
        onResetTime={() => {}}
      />,
    );
    fireEvent.click(r.getByRole("button", { name: "Save time" }));
    expect(onSaveTime).toHaveBeenCalledWith("Sudsing");
  });
});

describe("AnchorsCard per-anchor icon actions", () => {
  it("shows Skip / Adjust time / Adjust duration icon buttons on a counted chip", () => {
    const { r } = setup(clean);
    const skip = r.getByRole("button", { name: "Skip Sudsing today" });
    expect(skip.querySelector("svg")).toBeTruthy();
    expect(
      r.getByRole("button", { name: "Adjust time for Sudsing" }).querySelector("svg"),
    ).toBeTruthy();
    expect(
      r.getByRole("button", { name: "Adjust duration for Sudsing" }).querySelector("svg"),
    ).toBeTruthy();
  });

  it("shows the label + detail tooltip when an action gains focus", () => {
    const { r } = setup(clean);
    const skip = r.getByRole("button", { name: "Skip Sudsing today" });
    fireEvent.focus(skip);
    const bubble = skip.parentElement?.querySelector('[role="tooltip"]');
    expect(bubble?.textContent).toContain("Skip today");
    expect(bubble?.textContent).toContain("reversible");
  });

  it("toggles today through the supplied callback without opening the editor", () => {
    const onToggleSkip = vi.fn();
    const { r, onEdit } = setup(clean, { onToggleSkip });
    fireEvent.click(r.getByRole("button", { name: "Skip Sudsing today" }));
    expect(onToggleSkip).toHaveBeenCalledWith("Sudsing");
    expect(onEdit).not.toHaveBeenCalled();
  });

  it("offers Include today on a skipped not-counted chip", () => {
    const onToggleSkip = vi.fn();
    const r = render(
      <AnchorsCard
        counted={[]}
        notCounted={[{
          id: "Sudsing", name: "Sudsing", time: "06:00", reason: "skipped",
          skipped: true,
        }]}
        onEdit={() => {}}
        onToggleSkip={onToggleSkip}
      />,
    );
    fireEvent.click(r.getByRole("button", { name: "Include Sudsing today" }));
    expect(onToggleSkip).toHaveBeenCalledWith("Sudsing");
  });

  it("routes Adjust time and Adjust duration to their own callbacks", () => {
    const onAdjustTime = vi.fn();
    const onAdjustDuration = vi.fn();
    const { r, onEdit } = setup(clean, { onAdjustTime, onAdjustDuration });
    fireEvent.click(r.getByRole("button", { name: "Adjust time for Sudsing" }));
    expect(onAdjustTime).toHaveBeenCalledWith("Sudsing");
    fireEvent.click(r.getByRole("button", { name: "Adjust duration for Sudsing" }));
    expect(onAdjustDuration).toHaveBeenCalledWith("Sudsing");
    expect(onEdit).not.toHaveBeenCalled();
  });
});

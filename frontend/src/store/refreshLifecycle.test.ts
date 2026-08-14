import { describe, expect, it, vi } from "vitest";
import {
  installSourceRefreshLifecycle,
  msUntilLogicalDayBoundary,
} from "./refreshLifecycle";

function localDate(hour: number, minute = 0, second = 0): Date {
  return new Date(2026, 7, 4, hour, minute, second, 0);
}

describe("logical-day rollover timing", () => {
  it("fires at the two-o'clock boundary from the pre-rollover window", () => {
    expect(msUntilLogicalDayBoundary(localDate(1, 59, 30))).toBe(30_000);
  });

  it("waits for the next day when already past the boundary", () => {
    expect(msUntilLogicalDayBoundary(localDate(2))).toBe(24 * 60 * 60 * 1_000);
  });
});

describe("source refresh lifecycle", () => {
  it("refreshes at rollover and arms the following rollover", () => {
    let current = localDate(1, 59, 30);
    const refresh = vi.fn();
    const timers: Array<{ callback: () => void; delay: number; cancelled: boolean }> = [];
    const stop = installSourceRefreshLifecycle(refresh, () => true, {
      now: () => current,
      setTimer: (callback, delay) => {
        const timer = { callback, delay, cancelled: false };
        timers.push(timer);
        return timer;
      },
      clearTimer: (handle) => {
        (handle as (typeof timers)[number]).cancelled = true;
      },
    });

    expect(timers[0].delay).toBe(30_000);
    current = localDate(2);
    timers[0].callback();

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(timers[1].delay).toBe(24 * 60 * 60 * 1_000);
    stop();
    expect(timers[1].cancelled).toBe(true);
  });

  it("refreshes when returning to the visible cockpit and debounces paired focus events", () => {
    let current = localDate(8);
    let visible = false;
    let visibilityListener: (() => void) | undefined;
    let focusListener: (() => void) | undefined;
    const refresh = vi.fn();
    const stop = installSourceRefreshLifecycle(refresh, () => true, {
      now: () => current,
      setTimer: () => ({}),
      clearTimer: vi.fn(),
      isVisible: () => visible,
      onVisibility: (listener) => {
        visibilityListener = listener;
        return () => {
          visibilityListener = undefined;
        };
      },
      onFocus: (listener) => {
        focusListener = listener;
        return () => {
          focusListener = undefined;
        };
      },
    });

    visibilityListener!();
    expect(refresh).not.toHaveBeenCalled();
    visible = true;
    visibilityListener!();
    focusListener!();
    expect(refresh).toHaveBeenCalledTimes(1);

    current = new Date(current.getTime() + 1_001);
    focusListener!();
    expect(refresh).toHaveBeenCalledTimes(2);
    stop();
    expect(visibilityListener).toBeUndefined();
    expect(focusListener).toBeUndefined();
  });
});

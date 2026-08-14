/* refreshLifecycle — keep a long-lived production cockpit aligned with the
   server's logical day and with calendar changes made while the tab was away.

   The source refresh itself remains the Controller/API safety boundary. This
   module only decides when to request that already-existing read path. */

export const LOGICAL_DAY_ROLLOVER_HOUR = 2;
const RESUME_DEBOUNCE_MS = 1_000;

export function msUntilLogicalDayBoundary(
  now: Date,
  rolloverHour = LOGICAL_DAY_ROLLOVER_HOUR,
): number {
  const boundary = new Date(now);
  boundary.setHours(rolloverHour, 0, 0, 0);
  if (boundary.getTime() <= now.getTime()) {
    boundary.setDate(boundary.getDate() + 1);
  }
  return Math.max(1, boundary.getTime() - now.getTime());
}

export interface SourceRefreshLifecycleOptions {
  now?: () => Date;
  setTimer?: (callback: () => void, delayMs: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  onVisibility?: (listener: () => void) => () => void;
  isVisible?: () => boolean;
  onFocus?: (listener: () => void) => () => void;
}

/**
 * Attach the free source-refresh triggers for a browser-owned cockpit.
 *
 * The rollover timer is the authoritative day-change trigger. Visibility and
 * focus are resume triggers: they catch calendar edits made in another app or
 * a timer delayed while the browser tab was backgrounded. The Controller
 * still owns its busy-state guard, source-degrade handling, and reconciliation.
 */
export function installSourceRefreshLifecycle(
  refresh: () => Promise<void> | void,
  ready: () => boolean,
  options: SourceRefreshLifecycleOptions = {},
): () => void {
  const now = options.now ?? (() => new Date());
  const setTimer = options.setTimer ?? ((callback, delayMs) => setTimeout(callback, delayMs));
  const clearTimer =
    options.clearTimer ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));

  let stopped = false;
  let timer: unknown = null;
  // The first resume event after installation is meaningful when the page was
  // loaded in the background. Paired visibility/focus events are deduped after
  // that first refresh.
  let lastResumeAt = Number.NEGATIVE_INFINITY;

  const refreshIfReady = () => {
    if (stopped || !ready()) return;
    const current = now().getTime();
    if (current - lastResumeAt < RESUME_DEBOUNCE_MS) return;
    lastResumeAt = current;
    void Promise.resolve(refresh()).catch(() => undefined);
  };

  const armRollover = () => {
    if (stopped) return;
    timer = setTimer(() => {
      timer = null;
      if (stopped) return;
      refreshIfReady();
      armRollover();
    }, msUntilLogicalDayBoundary(now()));
  };

  const onResume = () => {
    if (options.isVisible && !options.isVisible()) return;
    refreshIfReady();
  };

  const removeVisibility = options.onVisibility?.(onResume);
  const removeFocus = options.onFocus?.(onResume);
  armRollover();

  return () => {
    if (stopped) return;
    stopped = true;
    if (timer !== null) clearTimer(timer);
    removeVisibility?.();
    removeFocus?.();
  };
}

/* U12 setup-screen commands — a tiny in-module channel so the shared Footer
   can drive the day-frame confirm/advance and the local Undo without the
   footer owning the panel's draft. At most one DaySetupPanel is mounted at a
   time (the promoted screen or the settings drawer), so a single listener is
   sufficient. This is deliberately not global state: it carries a request,
   not a value. */

export type SetupCommand = () => void;

const continueHandlers = new Set<SetupCommand>();
const undoHandlers = new Set<SetupCommand>();

export function onSetupContinue(fn: SetupCommand): () => void {
  continueHandlers.add(fn);
  return () => {
    continueHandlers.delete(fn);
  };
}

export function requestSetupContinue(): void {
  for (const fn of Array.from(continueHandlers)) fn();
}

export function onSetupUndo(fn: SetupCommand): () => void {
  undoHandlers.add(fn);
  return () => {
    undoHandlers.delete(fn);
  };
}

export function requestSetupUndo(): void {
  for (const fn of Array.from(undoHandlers)) fn();
}

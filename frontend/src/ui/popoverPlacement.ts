/* popoverPlacement.ts — pure viewport-fitting math for the Sources details
   popover. The rail clips with overflow:hidden, so the panel is position:fixed
   and this module decides where it goes: prefer opening upward when the panel
   fits above the trigger, otherwise downward, always clamped inside the
   viewport with an 8px margin and a max-height that scrolls the overflow. */

export interface PopoverRect {
  top: number;
  bottom: number;
  left: number;
  right: number;
  width: number;
  height: number;
}

export interface ViewportSize {
  width: number;
  height: number;
}

export interface PopoverPlacement {
  top: number;
  left: number;
  maxHeight: number;
  placement: "up" | "down";
}

/** Keep at least this much gap from the viewport edges. */
export const POPOVER_EDGE = 8;
/** Space between the trigger and the panel. */
export const POPOVER_GAP = 6;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

export function computePopoverPlacement(
  trigger: PopoverRect,
  viewport: ViewportSize,
  panel: { width: number; height: number },
): PopoverPlacement {
  const above = Math.max(0, trigger.top - POPOVER_EDGE - POPOVER_GAP);
  const below = Math.max(
    0,
    viewport.height - trigger.bottom - POPOVER_EDGE - POPOVER_GAP,
  );
  const placement: "up" | "down" =
    above >= panel.height
      ? "up"
      : below >= panel.height
        ? "down"
        : above >= below
          ? "up"
          : "down";
  const available = placement === "up" ? above : below;
  const maxHeight = Math.max(0, Math.min(panel.height, available));
  const desiredTop =
    placement === "up"
      ? trigger.top - POPOVER_GAP - maxHeight
      : trigger.bottom + POPOVER_GAP;
  const top = clamp(
    desiredTop,
    POPOVER_EDGE,
    viewport.height - POPOVER_EDGE - maxHeight,
  );
  const left = clamp(
    trigger.left,
    POPOVER_EDGE,
    viewport.width - POPOVER_EDGE - panel.width,
  );
  return { top, left, maxHeight, placement };
}

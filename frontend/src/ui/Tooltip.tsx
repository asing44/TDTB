/* Tooltip — FEEDBACK-10 (A12): hover + focus label popover for icon-only
   actions. Native `title` is pointer-only (and delayed); this gives keyboard
   and touch users the same explanation the instant the control is focused,
   and hover keeps it for pointer users.

   The label is rendered as a role="tooltip" span positioned above the
   trigger. The trigger's own aria-label stays the AT name; the tooltip is a
   visual affordance, and the wrapped child's existing handlers (click/blur
   arming) are preserved via cloneElement.

   Reduced motion: the show/hide transition is a plain opacity transition in
   app.css, and tokens.css already zeroes transitions under
   prefers-reduced-motion, so nothing extra is needed here. */

import { cloneElement, isValidElement, type ComponentChildren } from "preact";
import { useState } from "preact/hooks";

let tooltipSeq = 0;

export function Tooltip({
  label,
  align = "center",
  children,
}: {
  label: string;
  /** "center" keeps the bubble centred on the trigger; "end" right-aligns it
      so the right-edge row triggers (More) stay in view. */
  align?: "center" | "end";
  children: ComponentChildren;
}) {
  const [show, setShow] = useState(false);
  const [tipId] = useState(() => `tip-${++tooltipSeq}`);

  const child = isValidElement(children) ? children : <span>{children}</span>;
  const trigger = cloneElement(child as never, {
    onMouseEnter: (e: MouseEvent) => {
      setShow(true);
      (child.props as { onMouseEnter?: (ev: MouseEvent) => void }).onMouseEnter?.(e);
    },
    onMouseLeave: (e: MouseEvent) => {
      setShow(false);
      (child.props as { onMouseLeave?: (ev: MouseEvent) => void }).onMouseLeave?.(e);
    },
    onFocus: (e: FocusEvent) => {
      setShow(true);
      (child.props as { onFocus?: (ev: FocusEvent) => void }).onFocus?.(e);
    },
    onBlur: (e: FocusEvent) => {
      setShow(false);
      (child.props as { onBlur?: (ev: FocusEvent) => void }).onBlur?.(e);
    },
    "aria-describedby": show ? tipId : undefined,
  });

  return (
    <span class={`tooltip-anchor tooltip-anchor--${align}`}>
      {trigger}
      {show && (
        <span id={tipId} class="tooltip" role="tooltip">
          {label}
        </span>
      )}
    </span>
  );
}

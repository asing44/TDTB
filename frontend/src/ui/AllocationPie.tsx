/* AllocationPie — allocator-rewrite T8 → IMP-07. In-app SVG pie of the
   engine's capacity segments plus Unallocated, replacing the allotment
   line's role as the primary "where did my day go" read. The thin segmented
   bar in the rail's budget card stays as a compact linear echo — it scans
   faster for "how much is left", which is a different question from "what is
   it going in".

   IMP-07 (contract items 8-9): clicking a slice or legend item sets an
   inspection state that highlights that segment and dims the others; the
   legend items are focusable buttons (keyboard path), and hover remains a
   preview only. Clearing restores the full list (click the same segment
   again, or the readout's Clear control). The center readout uses the
   compact hierarchy — planned blocks / capacity context / over or remaining
   state — never the old "planned / day" treatment, and is exposed to
   assistive tech as a live status region.

   Theme-aware for free: every fill is a CSS custom property, so light/dark
   follow tokens.css rather than a second palette defined here.

   Selected tracks the in-flight slider values (same substitution as T7's
   remaining readout) so dragging a duration moves the wedge on the same
   frame. */

import { useState } from "preact/hooks";
import { useAppState } from "./context";
import { effectiveBlocks, includedItems } from "../store/store";
import { pieSlices, pieSummary } from "../model/pie";

/* Sized to the rail: a 280px column less its 16px padding leaves 248, so 240
   fills the width without forcing the legend to wrap. */
const SIZE = 240;
const R = 108;
/* Donut, not pie: the readout needs a hole to sit in. Overlaying it on solid
   wedges meant a text-shadow plate fighting four different fills. */
const INNER_R = 62;
/** Slices drawn as hatch rather than solid — the day's uncommitted time. */
const HATCHED = [
  { key: "buffer", token: "--c-buffer" },
  { key: "unallocated", token: "--c-free" },
] as const;
const C = SIZE / 2;

export function AllocationPie() {
  const s = useAppState();
  // Hover is a read affordance, not state anyone else needs: which wedge is
  // under the pointer, mirrored between the chart and its legend so either
  // one can be the thing you point at.
  const [hover, setHover] = useState<string | null>(null);
  // Inspection (contract item 8): the segment the user selected for a closer
  // look. Clicking the same segment again clears it; the legend buttons and
  // the readout's Clear control reach the same state from the keyboard.
  const [inspection, setInspection] = useState<string | null>(null);
  const cap = s.capacity;
  const localSelected = includedItems(s).reduce(
    (sum, i) => sum + effectiveBlocks(s, i.id),
    0,
  );
  const slices = pieSlices(cap, C, C, R, localSelected, INNER_R);
  if (slices.length === 0) return null;

  const summary = pieSummary(slices);
  const activeKey = inspection ?? hover;
  const active = slices.find((x) => x.key === activeKey) ?? null;
  // Unallocated is a slice but not an allocation — counting it would make the
  // idle readout say the day is fuller than it is.
  const allocated = slices
    .filter((x) => x.key !== "unallocated")
    .reduce((sum, x) => sum + x.blocks, 0);
  const total = cap?.total ?? 0;
  const over = Math.max(0, allocated - total);
  const remaining = Math.max(0, total - allocated);

  const toggleInspection = (key: string) =>
    setInspection((cur) => (cur === key ? null : key));

  return (
    <div class="pie" onMouseLeave={() => setHover(null)}>
      <div class="pie__chart">
        <svg
          class="pie__svg"
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          width={SIZE}
          height={SIZE}
          role="img"
          aria-label={`Allocation: ${summary}`}
        >
          {/* Buffer and Unallocated are the day's uncommitted time. They carry
              the same diagonal hatch the capacity bar uses for its free tail,
              so "not spoken for" reads the same way in both charts. SVG fills
              can't take a CSS gradient, hence real patterns. */}
          <defs>
            {HATCHED.map(({ key, token }) => (
              <pattern
                key={key}
                id={`pie-hatch-${key}`}
                width="7"
                height="7"
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <rect width="7" height="7" fill={`var(${token})`} opacity="0.3" />
                <rect width="3.5" height="7" fill={`var(${token})`} />
              </pattern>
            ))}
          </defs>
          {slices.map((slice) => (
            <path
              key={slice.key}
              class={`pie__slice pie__slice--${slice.key} ${
                activeKey && activeKey !== slice.key ? "pie__slice--dim" : ""
              }`}
              d={slice.d}
              fill={
                HATCHED.some((h) => h.key === slice.key)
                  ? `url(#pie-hatch-${slice.key})`
                  : slice.color
              }
              stroke="var(--t-surface)"
              stroke-width="1"
              onMouseEnter={() => setHover(slice.key)}
              onClick={() => toggleInspection(slice.key)}
            >
              <title>{`${slice.label} — ${slice.blocks} blk`}</title>
            </path>
          ))}
        </svg>
        {/* Readout sits over the chart so the eye doesn't leave the wedge it
            is pointing at. Idle state carries the day's planned/capacity/
            remaining hierarchy (contract item 9); inspection swaps to the
            selected segment. Exposed to AT: a live status region, never
            aria-hidden. */}
        <div
          class={`pie__readout ${active ? "pie__readout--active" : ""}`}
          role="status"
          aria-live="polite"
        >
          {active ? (
            <>
              <span class="pie__readout-value">{active.blocks} blk</span>
              <span class="pie__readout-label">{active.label}</span>
              <span class="pie__readout-state">
                {Math.round(active.fraction * 100)}% of day
              </span>
            </>
          ) : (
            <>
              <span class="pie__readout-value">{allocated} blk</span>
              <span class="pie__readout-label">of {total} blk capacity</span>
              <span class="pie__readout-state">
                {over > 0 ? `${over} over` : `${remaining} remaining`}
              </span>
            </>
          )}
        </div>
      </div>
      {/* FEEDBACK-10 (A09): overflow is explicit and actionable, not only a
          red number in the readout. Own status region so the state is
          announced without waiting on the readout's polite live region. */}
      {over > 0 && (
        <p class="pie__over-caption" role="status">
          Over by {over} blk - trim or drop
        </p>
      )}
      {inspection && (
        <button
          type="button"
          class="pie__clear"
          onClick={() => setInspection(null)}
        >
          Clear inspection
        </button>
      )}
      <ul class="pie__legend">
        {slices.map((slice) => (
          <li key={slice.key}>
            <button
              type="button"
              class={`pie__legend-item ${hover === slice.key ? "pie__legend-item--on" : ""}`}
              aria-pressed={inspection === slice.key}
              aria-label={`${slice.label}: ${slice.blocks} blk${
                inspection === slice.key ? ", selected" : ""
              }`}
              onMouseEnter={() => setHover(slice.key)}
              onClick={() => toggleInspection(slice.key)}
            >
              <span
                class={`pie__swatch ${
                  HATCHED.some((h) => h.key === slice.key) ? "pie__swatch--hatched" : ""
                }`}
                style={{ background: slice.color, "--swatch": slice.color }}
              />
              <span class="pie__legend-label">{slice.label}</span>
              <span class="pie__legend-value">{slice.blocks} blk</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

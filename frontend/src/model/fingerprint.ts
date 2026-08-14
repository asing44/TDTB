/* fingerprint.ts — normalized fixed-input fingerprint (locked decision 17).
   Covers calendar commitments + effective anchored blocks. Deterministic:
   sorted keys, sorted rows, djb2 hash of the canonical JSON. A source-read
   FAILURE must never be treated as "unchanged" — callers branch on health
   before comparing fingerprints. */

import type { FixedInputs } from "./types";

function djb2(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  }
  return (h >>> 0).toString(16);
}

export function fingerprintFixedInputs(inputs: FixedInputs): string {
  const cal = [...inputs.calendar]
    .map((c) => ({ n: c.name, s: c.start ?? "", d: c.durationMin, at: c.attending !== false }))
    .sort((a, b) => (a.n + a.s).localeCompare(b.n + b.s));
  const anc = [...inputs.anchored]
    .map((a) => ({
      n: a.name,
      s: a.start ?? "",
      d: a.durationMin,
      on: a.on && !a.skipToday,
    }))
    .sort((a, b) => (a.n + a.s).localeCompare(b.n + b.s));
  return djb2(JSON.stringify({ cal, anc }));
}

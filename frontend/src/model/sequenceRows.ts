/* Shape checks for sequence rows that arrive from outside the typed wire
   projection: a restored localStorage blob and a 422 rejected proposal. Both
   render directly, so a row missing its id or times is dropped here rather
   than drawn. Pure; no domain imports. */
import type { SequenceRow } from "./types";

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A server-shaped row (before projectSequenceRow): id, start and end. */
export function isWireSequenceRow(value: unknown): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    value.id != null &&
    String(value.id) !== "" &&
    value.start != null &&
    value.end != null
  );
}

/** A projected row as persisted: id, canonical HH:MM times, and a known kind. */
export function isSequenceRow(value: unknown): value is SequenceRow {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    value.id !== "" &&
    typeof value.start === "string" &&
    HHMM.test(value.start) &&
    typeof value.end === "string" &&
    HHMM.test(value.end) &&
    (value.kind === "work" || value.kind === "zone")
  );
}

/** Keep only well-formed rows; a non-array becomes []. */
export function sanitizeSequenceRows(value: unknown): SequenceRow[] {
  return Array.isArray(value) ? value.filter(isSequenceRow) : [];
}

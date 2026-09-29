/** Duration/classification tags are metadata, not shared-work relationships. */
const DURATION_TAG_RE = /^dur(\d+)$/i;
const QUICK_TASK_TAG_RE = /^🚀\s*(\d+)\s*min$/i;

export function isDurationTag(value: unknown): boolean {
  const text = String(value ?? "").trim();
  return DURATION_TAG_RE.test(text) || QUICK_TASK_TAG_RE.test(text);
}

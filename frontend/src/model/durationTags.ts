/** Duration/classification tags are metadata, not shared-work relationships. */
const DURATION_TAG_RE = /^dur(\d+)$/i;
const QUICK_TASK_TAG_RE = /^🚀\s*(\d+)\s*min$/i;

/* Word-based operator labels, mirroring the backend recognizer
   (app/duration_tags.py) so both halves agree about what a duration label is.
   An optional non-word prefix plus spacing absorbs emoji-form variance (a
   dropped ZWJ or variation selector on 🏃‍♂️ must not silently break
   recognition), and the patterns stay anchored and are checked
   most-specific-first so `Multi-hour` can never match as `Hour`.

   Recognition is deliberately boolean here: 🐢 Multi-hour is recognized
   metadata that encodes NO minutes, so it stays out of shared-work grouping
   (which is all this predicate feeds) while contributing no duration. */
const LABEL_PREFIX = "[^\\w\\s]*\\s*";
const WORD_LABEL_RES = [
  new RegExp(`^${LABEL_PREFIX}multi[-\\s]?hour$`, "i"),
  new RegExp(`^${LABEL_PREFIX}half[-\\s]?hour$`, "i"),
  new RegExp(`^${LABEL_PREFIX}hour$`, "i"),
];

export function isDurationTag(value: unknown): boolean {
  const text = String(value ?? "").trim();
  if (DURATION_TAG_RE.test(text) || QUICK_TASK_TAG_RE.test(text)) return true;
  return WORD_LABEL_RES.some((re) => re.test(text));
}

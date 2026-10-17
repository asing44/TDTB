/* Shared Live micro-adventure pool presentation: the Capacities option suffix
   and the single provenance note (first warning visible, full list in the
   title and aria-label). Used by the settings drawer and the Day Setup card. */

/** Option label; Capacities-sourced ideas (`cap-` ids) get a suffix. */
export function poolOptionLabel(id: string, idea: string): string {
  return id.startsWith("cap-") ? `${idea} · Capacities` : idea;
}

export function MicroPoolNote({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <p
      class="field__hint live-micro__pool-note"
      role="note"
      title={warnings.join("\n")}
      aria-label={warnings.join(" ")}
    >
      {warnings[0]}
    </p>
  );
}

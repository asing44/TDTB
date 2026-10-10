/* CapacitiesRulesEditor — the schema-backed per-type inclusion-rule editor for
   the Connections surface.

   Contract this surface keeps:
   - The grammar is exactly the store's: a recursive predicate of
     all/any/not or a leaf {prop, op, values}. The operator choices come from
     the server's own capability sets, filtered by the discovered property
     kind; `matches` is never offered.
   - Active and draft are distinct: `active` is the eligibility authority and
     is rendered read-only; the editor edits `draft`. Saving an invalid rule
     (bad syntax, a removed property, or a type with no published schema)
     stores a draft and PRESERVES the prior active rule — the surface says so
     rather than showing a fake activation.
   - A real 409 conflict keeps the local draft and reports both revisions;
     reload is explicit and never re-bases or retries the save automatically.
   - fallback_minutes is editable and nullable (empty clears it).
   - A type with no mapped status or no published schema is not offered a
     fake activation: schema availability is stated per structure. */

import { useEffect, useRef, useState } from "preact/hooks";
import { useApp } from "./context";
import { capacitiesRulesConflictOf } from "../adapters/api";
import type {
  CapacitiesRuleCapabilities,
  CapacitiesRuleNode,
  CapacitiesRuleStructure,
  CapacitiesRules,
} from "../model/types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isAll(node: CapacitiesRuleNode): node is { all: CapacitiesRuleNode[] } {
  return typeof node === "object" && node !== null && "all" in node;
}
function isAny(node: CapacitiesRuleNode): node is { any: CapacitiesRuleNode[] } {
  return typeof node === "object" && node !== null && "any" in node;
}
function isNot(node: CapacitiesRuleNode): node is { not: CapacitiesRuleNode } {
  return typeof node === "object" && node !== null && "not" in node;
}
function isLeaf(
  node: CapacitiesRuleNode,
): node is { prop: string; op: string; values?: unknown[] } {
  return typeof node === "object" && node !== null && "prop" in node;
}

function opsForKind(kind: string | undefined, caps: CapacitiesRuleCapabilities): string[] {
  if (!kind) return [...caps.ops].sort();
  const allowed = new Set<string>(caps.presenceOps);
  if (caps.numberKinds.includes(kind)) for (const op of caps.numberOps) allowed.add(op);
  if (caps.dateKinds.includes(kind)) for (const op of caps.dateOps) allowed.add(op);
  if (caps.valueKinds.includes(kind)) for (const op of caps.equalityOps) allowed.add(op);
  return [...allowed].sort();
}

function defaultLeaf(
  schema: Record<string, string>,
  caps: CapacitiesRuleCapabilities,
): CapacitiesRuleNode {
  const prop = Object.keys(schema)[0] ?? "";
  const ops = opsForKind(schema[prop], caps);
  const op = ops.includes("eq") ? "eq" : ops[0] ?? "exists";
  const leaf: { prop: string; op: string; values?: unknown[] } = { prop, op };
  if (caps.valueOps.includes(op)) leaf.values = [""];
  return leaf;
}

function defaultRoot(
  schema: Record<string, string>,
  caps: CapacitiesRuleCapabilities,
): CapacitiesRuleNode {
  return { all: Object.keys(schema).length > 0 ? [defaultLeaf(schema, caps)] : [] };
}

function parseValues(text: string, op: string, kind: string | undefined): unknown[] {
  const parts = text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
  if (op === "lt" || op === "gt" || (kind && ["number"].includes(kind))) {
    return parts.map((part) => {
      const value = Number(part);
      return Number.isFinite(value) ? value : part;
    });
  }
  return parts;
}

function valuesText(values: unknown[] | undefined): string {
  if (!values) return "";
  return values.map((value) => String(value)).join(", ");
}

/** One node editor. `onRemove` is absent for the root node. */
function RuleNodeEditor({
  node,
  schema,
  caps,
  onChange,
  onRemove,
  depth,
}: {
  node: CapacitiesRuleNode;
  schema: Record<string, string>;
  caps: CapacitiesRuleCapabilities;
  onChange: (next: CapacitiesRuleNode) => void;
  onRemove?: () => void;
  depth: number;
}) {
  if (isNot(node)) {
    return (
      <div class="capacities-rule__node capacities-rule__node--not" data-depth={depth}>
        <div class="capacities-rule__row">
          <span class="capacities-rule__label">NOT</span>
          {onRemove && (
            <button class="btn" onClick={onRemove} aria-label="Remove negation">Remove</button>
          )}
        </div>
        <RuleNodeEditor
          node={node.not}
          schema={schema}
          caps={caps}
          onChange={(next) => onChange({ not: next })}
          depth={depth + 1}
        />
      </div>
    );
  }

  if (isAll(node) || isAny(node)) {
    const combinator = isAll(node) ? "all" : "any";
    const children = isAll(node) ? node.all : node.any;
    const setChildren = (next: CapacitiesRuleNode[]) =>
      onChange(combinator === "all" ? { all: next } : { any: next });
    return (
      <div class={`capacities-rule__node capacities-rule__node--group`} data-depth={depth}>
        <div class="capacities-rule__row">
          <label class="capacities-rule__label">
            <span class="sr-only">Combinator</span>
            <select
              value={combinator}
              aria-label="Combinator"
              onChange={(e) => {
                const next = (e.currentTarget as HTMLSelectElement).value;
                onChange(next === "any" ? { any: children } : { all: children });
              }}
            >
              <option value="all">ALL of</option>
              <option value="any">ANY of</option>
            </select>
          </label>
          <div class="capacities-rule__actions">
            <button class="btn" onClick={() => setChildren([...children, defaultLeaf(schema, caps)])}>
              Add condition
            </button>
            <button class="btn" onClick={() => setChildren([...children, { all: [] }])}>
              Add group
            </button>
            <button
              class="btn"
              onClick={() => setChildren([...children, { not: defaultLeaf(schema, caps) }])}
            >
              Add negation
            </button>
            {onRemove && (
              <button class="btn" onClick={onRemove} aria-label="Remove group">Remove</button>
            )}
          </div>
        </div>
        {children.length === 0 ? (
          <p class="capacities-settings__empty">
            {combinator === "all" ? "An empty ALL matches everything." : "An empty ANY matches nothing."}
          </p>
        ) : (
          <div class="capacities-rule__children">
            {children.map((child, index) => (
              <RuleNodeEditor
                key={index}
                node={child}
                schema={schema}
                caps={caps}
                depth={depth + 1}
                onChange={(next) =>
                  setChildren(children.map((current, i) => (i === index ? next : current)))
                }
                onRemove={() => setChildren(children.filter((_, i) => i !== index))}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  const leaf: { prop: string; op: string; values?: unknown[] } = isLeaf(node)
    ? node
    : (defaultLeaf(schema, caps) as { prop: string; op: string; values?: unknown[] });
  const kind = schema[leaf.prop];
  const ops = opsForKind(kind, caps);
  const opOptions = ops.includes(leaf.op) ? ops : [leaf.op, ...ops];
  const needsValues = caps.valueOps.includes(leaf.op);
  const propOptions = Object.keys(schema);
  const propKnown = leaf.prop !== "" && propOptions.includes(leaf.prop);
  const setLeaf = (patch: Partial<{ prop: string; op: string; values: unknown[] }>) =>
    onChange({ ...leaf, ...patch });

  return (
    <div class="capacities-rule__node capacities-rule__node--leaf" data-depth={depth}>
      <div class="capacities-rule__row">
        {propOptions.length > 0 ? (
          <label class="capacities-rule__field">
            <span class="sr-only">Property</span>
            <select
              value={propKnown ? leaf.prop : ""}
              aria-label="Property"
              onChange={(e) => {
                const prop = (e.currentTarget as HTMLSelectElement).value;
                const nextOps = opsForKind(schema[prop], caps);
                const op = nextOps.includes(leaf.op) ? leaf.op : nextOps[0] ?? "exists";
                setLeaf({ prop, op });
              }}
            >
              {!propKnown && <option value="">{leaf.prop || "Select property"}</option>}
              {propOptions.map((prop) => (
                <option value={prop} key={prop}>
                  {prop}{schema[prop] ? ` (${schema[prop]})` : ""}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label class="capacities-rule__field">
            <span class="sr-only">Property id</span>
            <input
              type="text"
              value={leaf.prop}
              placeholder="property id"
              aria-label="Property id"
              onInput={(e) => setLeaf({ prop: (e.currentTarget as HTMLInputElement).value })}
            />
          </label>
        )}
        <label class="capacities-rule__field">
          <span class="sr-only">Operator</span>
          <select
            value={leaf.op}
            aria-label="Operator"
            onChange={(e) => {
              const op = (e.currentTarget as HTMLSelectElement).value;
              if (caps.valueOps.includes(op)) {
                setLeaf({ op, values: leaf.values && leaf.values.length > 0 ? leaf.values : [""] });
              } else {
                // A presence op (exists/truthy) carries no values: drop them so
                // the leaf is exactly {prop, op}.
                const { values: _dropped, ...rest } = leaf;
                onChange({ ...rest, op });
              }
            }}
          >
            {opOptions.map((op) => (
              <option value={op} key={op}>{op}</option>
            ))}
          </select>
        </label>
        {needsValues && (
          <label class="capacities-rule__field capacities-rule__field--grow">
            <span class="sr-only">Values</span>
            <input
              type="text"
              value={valuesText(leaf.values)}
              placeholder="value, value"
              aria-label="Values"
              onInput={(e) =>
                setLeaf({ values: parseValues((e.currentTarget as HTMLInputElement).value, leaf.op, kind) })
              }
            />
          </label>
        )}
        {onRemove && (
          <button class="btn" onClick={onRemove} aria-label="Remove condition">Remove</button>
        )}
      </div>
    </div>
  );
}

function StructureRuleRow({
  structure,
  caps,
  revision,
  saving,
  onSave,
}: {
  structure: CapacitiesRuleStructure;
  caps: CapacitiesRuleCapabilities;
  revision: number;
  saving: boolean;
  onSave: (args: {
    structureId: string;
    rule: CapacitiesRuleNode;
    fallbackMinutes: number | null;
    expectedRevision: number;
  }) => Promise<void>;
}) {
  const [draft, setDraft] = useState<CapacitiesRuleNode>(
    structure.draft ?? defaultRoot(structure.schema, caps),
  );
  const [fallbackText, setFallbackText] = useState(
    structure.fallbackMinutes === null ? "" : String(structure.fallbackMinutes),
  );

  // Re-base on the server's authoritative draft only when the RULES revision
  // changes (a successful save or an explicit reload) — never on an ordinary
  // parent re-render, which would wipe an in-progress edit. The row keeps its
  // DOM node across saves, so a local edit is preserved until it is saved.
  const lastRevision = useRef(revision);
  if (lastRevision.current !== revision) {
    lastRevision.current = revision;
    setDraft(structure.draft ?? defaultRoot(structure.schema, caps));
    setFallbackText(structure.fallbackMinutes === null ? "" : String(structure.fallbackMinutes));
  }

  const fallbackValid = fallbackText.trim() === "" || /^\d+$/.test(fallbackText.trim());
  const fallbackMinutes = fallbackText.trim() === "" ? null : Number(fallbackText.trim());
  const draftDiffers =
    JSON.stringify(structure.draft ?? null) !== JSON.stringify(draft) ||
    (structure.fallbackMinutes ?? null) !== fallbackMinutes;

  return (
    <div class="capacities-rule" data-structure={structure.structureId}>
      <div class="capacities-rule__head">
        <code>{structure.structureId}</code>
        <span class="capacities-rule__badges">
          <span class={`capacities-rule__badge${structure.schemaAvailable ? "" : " capacities-rule__badge--warn"}`}>
            {structure.schemaAvailable
              ? `${Object.keys(structure.schema).length} known properties`
              : "no published schema"}
          </span>
          {!structure.mapped && <span class="capacities-rule__badge capacities-rule__badge--warn">not mapped</span>}
        </span>
      </div>

      <p class="capacities-settings__hint">
        Active rule: {structure.active ? "configured (eligibility authority)" : "none — this type admits nothing"}
      </p>

      {!structure.schemaAvailable && (
        <p class="capacities-settings__hint">
          This type has no published structure contract, so a save can only be stored as a draft.
        </p>
      )}

      <div class="capacities-rule__editor">
        <RuleNodeEditor
          node={draft}
          schema={structure.schema}
          caps={caps}
          onChange={setDraft}
          depth={0}
        />
      </div>

      <div class="capacities-rule__row capacities-rule__footer">
        <label class="capacities-rule__field">
          <span>Fallback minutes</span>
          <input
            type="number"
            min="0"
            step="1"
            value={fallbackText}
            aria-label={`Fallback minutes for ${structure.structureId}`}
            onInput={(e) => setFallbackText((e.currentTarget as HTMLInputElement).value)}
          />
        </label>
        <button
          class="btn btn--primary"
          disabled={saving || !fallbackValid}
          onClick={() => void onSave({
            structureId: structure.structureId,
            rule: draft,
            fallbackMinutes,
            expectedRevision: revision,
          })}
        >
          {saving ? "Saving…" : "Save rule"}
        </button>
        {draftDiffers && <span class="capacities-rule__dirty">Unsaved draft</span>}
        {!fallbackValid && <span class="field-error" role="alert">Use a nonnegative whole number.</span>}
      </div>
    </div>
  );
}

export function CapacitiesRulesEditor({ active }: { active: boolean }) {
  const { controller } = useApp();
  const [rules, setRules] = useState<CapacitiesRules | null>(null);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ structureId: string; valid: boolean; reason: string | null } | null>(null);
  const [conflict, setConflict] = useState<{ expectedRevision: number; currentRevision: number } | null>(null);

  const load = async () => {
    setPhase("loading");
    setError(null);
    try {
      const loaded = await controller.loadCapacitiesRules();
      setRules(loaded);
      setConflict(null);
      setPhase("ready");
    } catch (e) {
      setPhase("error");
      setError(messageOf(e));
    }
  };

  useEffect(() => {
    if (!active) return;
    void load();
  }, [active, controller]);

  if (!active) return null;

  const save = async (args: {
    structureId: string;
    rule: CapacitiesRuleNode;
    fallbackMinutes: number | null;
    expectedRevision: number;
  }) => {
    setSavingId(args.structureId);
    setNotice(null);
    setConflict(null);
    setError(null);
    try {
      const response = await controller.saveCapacitiesRule(args);
      // The response is the full GET shape plus the save outcome, so the
      // editor re-bases every row on the server's authoritative state.
      setRules(response);
      setNotice({
        structureId: response.save.structureId,
        valid: response.save.valid,
        reason: response.save.reason,
      });
      setPhase("ready");
    } catch (e) {
      const asConflict = capacitiesRulesConflictOf(e);
      if (asConflict) {
        setConflict(asConflict);
      } else {
        setError(messageOf(e));
      }
    } finally {
      setSavingId(null);
    }
  };

  return (
    <section
      class="setup-section capacities-settings__section"
      aria-labelledby="capacities-sec-rules"
      data-settings-section="rules"
    >
      <div class="setup-section__head">
        <h3 id="capacities-sec-rules">Per-type inclusion rules</h3>
        {rules && <span class="capacities-settings__revision">Rules revision {rules.revision}</span>}
      </div>
      <div class="setup-section__body">
        <p class="capacities-settings__hint">
          Each Capacities type admits objects through its own nested predicate
          (ALL / ANY / NOT of property conditions). A rule that is valid for the
          published type shape becomes active; an invalid rule is stored as a
          draft and the previous active rule is preserved.
        </p>

        {phase === "loading" && (
          <p class="capacities-settings__state" role="status">Loading inclusion rules…</p>
        )}
        {phase === "error" && (
          <section class="capacities-settings__error" role="alert">
            <strong>Inclusion rules could not be loaded</strong>
            <span>{error}</span>
            <button class="btn" onClick={() => void load()}>Reload rules</button>
          </section>
        )}

        {rules && phase !== "loading" && !rules.configured && (
          <p class="capacities-settings__empty" role="status">
            No Capacities source is configured; there are no types to write rules for.
          </p>
        )}

        {rules && rules.configured && (
          <>
            {conflict && (
              <div class="capacities-settings__save-error" role="alert">
                <span>
                  Rules changed since they were read (stored revision {conflict.currentRevision},
                  expected {conflict.expectedRevision}). Your draft is preserved; reload to review
                  the newer rules before saving.
                </span>
                <button class="btn" onClick={() => void load()}>Reload rules</button>
              </div>
            )}
            {error && !conflict && (
              <div class="capacities-settings__save-error" role="alert">
                <span>{error}</span>
                <button class="btn" onClick={() => void load()}>Reload rules</button>
              </div>
            )}
            {notice && (
              <div
                class={`capacities-rule__notice${notice.valid ? "" : " capacities-rule__notice--draft"}`}
                role="status"
              >
                {notice.valid
                  ? `Rule for ${notice.structureId} is active.`
                  : `Saved as a draft for ${notice.structureId}; the previous active rule is unchanged. ${notice.reason ?? ""}`}
              </div>
            )}

            {rules.structures.length === 0 ? (
              <p class="capacities-settings__empty">
                The configured source lists no structures, so there is no type to write a rule for.
              </p>
            ) : (
              rules.structures.map((structure) => (
                <StructureRuleRow
                  key={structure.structureId}
                  structure={structure}
                  caps={rules.capabilities}
                  revision={rules.revision}
                  saving={savingId === structure.structureId}
                  onSave={save}
                />
              ))
            )}
          </>
        )}
      </div>
    </section>
  );
}

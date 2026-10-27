/* UnassignedToday — the "Not in plan" drawer under the urgency bands: what
   today's plan does NOT hold right now. It reads
   `assignmentRows(s).unassigned`, whose `origin` says where each row's state
   actually lives. Three groups follow from that, and a row never carries the
   wrong affordance:

   - local (U3b): a row taken out of today's plan in this browser by the E key
     or the row switch. Nothing upstream knows, so the row keeps the switch in
     its off state, worded "Add to plan". This is the only group whose action
     is local, so it is the only one that gets a switch.
   - journal-unassign (U5): a Todoist row really unassigned at the source
     (today's runtime journal). The switch would lie here — flipping it is not
     a browser edit — so the row gets an explicit "Restore in Todoist", which
     reverses that row's own journal entry through the controller's reassign
     path. It is an undo, so one click is the whole confirmation.
   - dropped (U5): a date-scoped `drop_from_plan` entry (Capacities and
     calendar). It keeps "Restore today", the same button and the same styling
     the standalone Skipped today section uses.

   A row that first appeared AFTER the day's first load and then defaulted OUT
   carries a small "new" badge, so a mid-day arrival is not mistaken for
   something the operator set aside this morning.

   U6: the LOCAL group is grouped by commitment first (Overdue → Due today →
   Fixed time → Due in 7 days → Priority p1–p2 → No date) and by source /
   object type second, because a row's date is the fact that decides whether
   today still owes it. The rule lives in `model/drawerGroups.ts`; each tier is
   one disclosure with its own count, and a tier with no rows is not rendered.
   The two server-held groups keep their flat layout — they follow the plan's
   own sections, not the local membership map.

   U3a (revised): selecting what belongs to today is Adam's job, so a cold day
   starts with EVERY sourced row in here — nothing is pre-selected from Todoist
   or Capacities. The drawer is therefore the day's front door, not a repair
   surface, and it earns a per-tier "Add all": one click settles a whole
   commitment tier through the same local membership write the row switch
   uses (no adapter call, nothing written upstream). Tiers start open, so the
   ladder is one scan away from an empty plan.

   The empty state is still the whole point of the section: when every sourced
   row is in today's plan, there is nothing to render. */

import { useState } from "preact/hooks";
import { useApp, useAppState } from "./context";
import {
  drawerTierGroups,
  type DrawerFacts,
  type DrawerTierGroup,
} from "../model/drawerGroups";
import { dueLabel } from "../model/urgency";
import { kindLabel } from "./TaskRow";
import {
  assignmentRows,
  taskActionDisabledReason,
  type AssignmentRow,
  type AppState,
  type SkippedTodayRow,
} from "../store/store";
import { ADD_TO_PLAN_LABEL, AssignSwitch } from "./TaskRow";
import { DurationCell, SkippedRow } from "./SkippedToday";
import "./drawer-groups.css";
import "./drawer-rows.css";

const SOURCE_LABEL: Record<string, string> = {
  todoist: "Todoist",
  capacities: "Capacities",
  calendar: "Calendar",
};

function sourceKey(source: string): string {
  return SOURCE_LABEL[source] ? source : "other";
}

function rowKey(identity: string): string {
  return identity.replace(/[^A-Za-z0-9_-]+/g, "-");
}

/** The badge a mid-day arrival wears. Its title says what "new" means; the
    drawer never has to explain the day's baseline in prose. */
function NewBadge() {
  return (
    <span class="unassigned-today__new" title="Appeared after today's first load">
      new
    </span>
  );
}

function SourceChip({ source }: { source: string }) {
  const key = sourceKey(source);
  return (
    <>
      <span class={`qrow__swatch qrow__swatch--${key}`} aria-hidden="true" />
      <span class={`qrow__src qrow__src--${key}`}>
        {SOURCE_LABEL[source] ?? "Other"}
      </span>
    </>
  );
}

/** Group 1: local plan membership, so the Add to plan switch is exact. The
    row also carries its own due text: a tier groups by date, so the date that
    put the row there stays visible. */
function LocalRow({ row, facts }: { row: AssignmentRow; facts: DrawerFacts }) {
  const s = useAppState();
  const due = dueLabel(facts.deadline, s.inputs?.validDate ?? "");
  return (
    <li class="qrow trow trow--skipped trow--unassigned" data-identity={row.identity}>
      <div class="qrow__body">
        <span class="qrow__nameline">
          <span class="qrow__name">{row.name}</span>
          {row.isNewArrival && <NewBadge />}
        </span>
        <span class="qrow__source">
          <SourceChip source={String(row.source)} />
          {due && (
            <span class={`unassigned-due unassigned-due--${due.tone ?? "none"}`}>
              {due.text}
            </span>
          )}
        </span>
      </div>
      <DurationCell minutes={row.minutes} />
      <div class="trow__acts">
        <AssignSwitch
          id={row.id}
          name={row.name}
          assigned={false}
          s={s}
          offLabel={ADD_TO_PLAN_LABEL}
          caption={ADD_TO_PLAN_LABEL}
        />
      </div>
    </li>
  );
}

/** Group 2: really unassigned at the source. `setAssigned(id, true)` is the
    public entry point that runs the controller's reassign (undo of that row's
    own journal unassign); it is never the local plan switch. */
function RemovedFromTodoistRow({ row }: { row: AssignmentRow }) {
  const { controller } = useApp();
  const s = useAppState();
  const why = taskActionDisabledReason(s, "complete");
  const whyId = `why-reassign-${rowKey(row.identity)}`;
  return (
    <li class="qrow trow trow--skipped" data-identity={row.identity}>
      <div class="qrow__body">
        <span class="qrow__nameline">
          <span class="qrow__name">{row.name}</span>
        </span>
        <span class="qrow__source">
          <SourceChip source="todoist" />
          <span class="trow__state">Unassigned in Todoist</span>
        </span>
      </div>
      <DurationCell minutes={row.minutes} />
      <div class="trow__acts">
        <button
          type="button"
          class="trow__btn trow__btn--restore"
          disabled={s.runtimeBusy || why !== null}
          title={why ?? "Restore in Todoist"}
          aria-describedby={why ? whyId : undefined}
          onClick={() => void controller.setAssigned(row.identity, true)}
        >
          Restore in Todoist<span class="sr-only"> {row.name}</span>
        </button>
        {why && (
          <span class="sr-only" id={whyId}>
            {why}
          </span>
        )}
      </div>
    </li>
  );
}

/** Group 3: a date-scoped drop. Reuses the Skipped today row so Restore today
    looks and behaves identically in both places. */
function DroppedRow({ row, busy }: { row: AssignmentRow; busy: boolean }) {
  const skipped: SkippedTodayRow = {
    identity: row.identity,
    name: row.name,
    droppedAt: null,
    source: row.source,
    kind: row.kind,
    minutes: row.minutes,
  };
  return <SkippedRow row={skipped} busy={busy} />;
}

/** One tier disclosure, following the lane-header pattern: a full-width
    button, the tier name, a rule, the count, and a chevron
    (down = open, right = collapsed). Tiers with rows open by default; an empty tier is never rendered.

    The tier's "Add all" sits beside the disclosure, never inside it: the
    disclosure is already one button, and nesting a second one would make the
    same click mean two things to a screen reader. */
function TierHead({
  tier,
  open,
  onToggle,
  onAddAll,
  controlsId,
}: {
  tier: DrawerTierGroup;
  open: boolean;
  onToggle: () => void;
  onAddAll: () => void;
  controlsId: string;
}) {
  const rows = `${tier.count} row${tier.count === 1 ? "" : "s"}`;
  return (
    <div class="unassigned-tier__headrow">
      <button
        type="button"
        class="unassigned-tier__head"
        data-tier-head={tier.key}
        aria-expanded={open}
        aria-controls={controlsId}
        aria-label={`${tier.label}, ${open ? "expanded" : "collapsed"}, ${rows}${open ? "" : " hidden"}, activate to ${open ? "collapse" : "expand"}`}
        title={open ? "Collapse" : "Expand"}
        onClick={onToggle}
      >
        <span class="unassigned-tier__tick" aria-hidden="true" />
        <span class="unassigned-tier__label">{tier.label}</span>
        <span class="unassigned-tier__rule" />
        <span class="unassigned-tier__count">
          {rows}
          {open ? "" : " hidden"}
        </span>
        <svg
          class={`unassigned-tier__chevron${open ? "" : " unassigned-tier__chevron--closed"}`}
          viewBox="0 0 12 12"
          width="12"
          height="12"
          aria-hidden="true"
          focusable="false"
        >
          <path
            d={open ? "M2.5 4.25 6 7.75l3.5-3.5" : "M4.25 2.5 7.75 6l-3.5 3.5"}
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <button
        type="button"
        class="unassigned-tier__add"
        data-tier-add-all={tier.key}
        aria-label={`Add all ${tier.count} ${tier.label} to plan`}
        title={`Add all ${rows} to today's plan — this browser only`}
        onClick={onAddAll}
      >
        <span class="unassigned-tier__add-icon" aria-hidden="true">
          +
        </span>
        <span>Add all</span>
      </button>
    </div>
  );
}

/** Group 1's body: the commitment ladder, then source / object type inside
    each tier. Collapse state is per tier and defaults to open, so the drawer
    opens on today's hardest commitments.

    "Add all" is the tier's one-click settle: it loops this tier's own rows
    through `controller.setAssigned`, which for a row still in `inputs.assigned`
    resolves to a bare PLAN_MEMBERSHIP_SET dispatch. Local only — zero adapter
    calls, zero source writes — which is exactly the guarantee the per-row
    switch already carries. */
function LocalGroups({
  rows,
  s,
  structureTitles,
}: {
  rows: AssignmentRow[];
  s: AppState;
  structureTitles?: Record<string, string>;
}) {
  const { controller } = useApp();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const itemsById = new Map((s.inputs?.assigned ?? []).map((item) => [item.id, item]));
  const factsOf = (row: AssignmentRow): DrawerFacts => {
    const item = itemsById.get(row.id);
    if (!item) {
      return {
        source: row.source,
        deadline: null,
        urgency: null,
        scheduledStart: null,
        kind: row.kind,
      };
    }
    return {
      source: item.source,
      deadline: item.deadline,
      urgency: item.urgency,
      scheduledStart: item.scheduledStart ?? null,
      kind: kindLabel(item, structureTitles),
    };
  };
  const tiers = drawerTierGroups(rows, factsOf, s.inputs?.validDate ?? "");
  const addTier = (tier: DrawerTierGroup) => {
    for (const source of tier.sources) {
      for (const row of source.rows) void controller.setAssigned(row.id, true);
    }
  };
  return (
    <div class="unassigned-today__group unassigned-tiers" data-group="local">
      {tiers.map((tier) => {
        const open = !(collapsed[tier.key] ?? false);
        const regionId = `unassigned-tier-${tier.key}`;
        return (
          <div
            key={tier.key}
            class={`unassigned-tier unassigned-tier--${tier.key}`}
            data-tier={tier.key}
          >
            <TierHead
              tier={tier}
              open={open}
              controlsId={regionId}
              onAddAll={() => addTier(tier)}
              onToggle={() =>
                setCollapsed((c) => ({ ...c, [tier.key]: !(c[tier.key] ?? false) }))
              }
            />
            {open && (
              <div id={regionId} class="unassigned-tier__body">
                {tier.sources.map((source) => (
                  <div
                    key={source.key}
                    class="unassigned-source"
                    data-source-group={source.key}
                  >
                    <h4 class="unassigned-source__head">
                      {source.label}
                      <span class="unassigned-source__count">
                        {source.rows.length}
                      </span>
                    </h4>
                    <ul class="skipped-today__list">
                      {source.rows.map((row) => (
                        <LocalRow
                          key={row.identity}
                          row={row}
                          facts={factsOf(row)}
                        />
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function GroupHead({ label, count }: { label: string; count: number }) {
  return (
    <h3 class="unassigned-today__group-head">
      {label}
      <span>{count}</span>
    </h3>
  );
}

export function UnassignedToday({
  structureTitles,
}: {
  /** The app's machine-local Capacities titles, so a local Capacities row
      sub-groups under its structure's NAME, not a raw id. */
  structureTitles?: Record<string, string>;
}) {
  const s = useAppState();
  const rows = assignmentRows(s).unassigned;
  if (rows.length === 0) return null;
  const local = rows.filter((row) => row.origin === "local");
  const removed = rows.filter((row) => row.origin === "journal-unassign");
  const dropped = rows.filter((row) => row.origin === "dropped");
  const count = `${rows.length} not in plan`;
  return (
    <section
      class="skipped-today unassigned-today"
      id="unassigned-today"
      aria-label="Not in plan"
    >
      <div class="skipped-today__head">
        <h2>Not in plan</h2>
        <span>{count}</span>
        <span class="unassigned-today__note">
          Local to this browser — adding a row here changes nothing in Todoist or
          Capacities.
        </span>
      </div>
      {local.length > 0 && (
        <LocalGroups rows={local} s={s} structureTitles={structureTitles} />
      )}
      {removed.length > 0 && (
        <div class="unassigned-today__group" data-group="removed-from-todoist">
          <GroupHead label="Removed from Todoist" count={removed.length} />
          <ul class="skipped-today__list">
            {removed.map((row) => (
              <RemovedFromTodoistRow key={row.identity} row={row} />
            ))}
          </ul>
        </div>
      )}
      {dropped.length > 0 && (
        <div class="unassigned-today__group" data-group="skipped-today">
          <GroupHead label="Skipped today" count={dropped.length} />
          <ul class="skipped-today__list">
            {dropped.map((row) => (
              <DroppedRow key={row.identity} row={row} busy={s.runtimeBusy} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

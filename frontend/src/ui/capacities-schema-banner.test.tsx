/* capacities-schema-banner.test.tsx — the one schema-change banner in the
   Settings → Source refresh panel.

   Covers: a warn notice (role=alert) and an info notice (role=status), the
   verbatim server summary with no invented names or counts, no banner when the
   notice is acknowledged or absent, the Acknowledge action posting the exact
   to_hash and then clearing the banner from a re-read status, a 409 stale
   acknowledgement re-reading the status without showing an error, a failure
   keeping the banner and button usable with a short inline error, the button
   disabled while the request is in flight, and the banner sitting above the
   Warnings block. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, waitFor } from "@testing-library/preact";
import { CapacitiesRefreshPanel } from "./CapacitiesRefreshPanel";
import { makeHarness } from "./test-harness";
import { ApiError, ApiTransportError } from "../adapters/api";
import type {
  CapacitiesRefreshStatus,
  CapacitiesSchemaChange,
} from "../model/types";

const SUMMARY =
  "Capacities changed since the last refresh: 1 status option removed. Review the mapping in Connections.";
const INFO_SUMMARY =
  "Capacities changed since the last refresh: 2 properties added. Review the mapping in Connections.";
const ACK_NAME = "Acknowledge Capacities change";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function notice(patch: Partial<CapacitiesSchemaChange> = {}): CapacitiesSchemaChange {
  return {
    severity: "warn",
    summary: SUMMARY,
    detectedAt: "2026-10-29T10:00:00+00:00",
    generation: 4,
    fromHash: "from-hash",
    toHash: "to-hash",
    acknowledged: false,
    acknowledgedAt: null,
    counts: { options_removed: 1 },
    ...patch,
  };
}

function statusWith(
  schemaChange: CapacitiesSchemaChange | null,
  warnings: string[] = [],
): CapacitiesRefreshStatus {
  return {
    configured: true,
    phase: "complete",
    outcome: "published",
    mode: "refresh",
    scope: "all",
    job: null,
    progress: { listed: 227, read: 227, toRead: 227, waiting: null, types: {} },
    warnings,
    coverage: {},
    schemaChange,
    snapshot: {
      present: true,
      generation: 4,
      revision: "rev",
      installedAt: 1_792_500_000,
      memberCount: 227,
      typeCheckTimes: {},
    },
  };
}

async function renderPanel(status: CapacitiesRefreshStatus) {
  const h = makeHarness("ready");
  const load = vi
    .spyOn(h.controller, "loadCapacitiesRefreshStatus")
    .mockResolvedValue(status);
  const rendered = h.ui(<CapacitiesRefreshPanel active={true} />);
  await waitFor(() => expect(load).toHaveBeenCalled());
  return { h, rendered, load };
}

/** A promise the test resolves by hand, so an in-flight state is observable. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("CapacitiesRefreshPanel schema-change banner", () => {
  it("shows a warn notice as an alert with the server's sentence", async () => {
    const { rendered } = await renderPanel(statusWith(notice()));

    const banner = rendered.getByRole("alert");
    expect(banner.textContent).toContain(SUMMARY);
    // The sentence is the server's, verbatim: the panel adds no names, no
    // counts, and no wrapper prose of its own.
    expect(rendered.getByText(SUMMARY).textContent).toBe(SUMMARY);
    expect(rendered.getByRole("button", { name: ACK_NAME })).toBeTruthy();
  });

  it("shows an info notice as a quieter status notice", async () => {
    const { rendered } = await renderPanel(
      statusWith(notice({ severity: "info", summary: INFO_SUMMARY })),
    );

    const banner = rendered.getByRole("status");
    expect(banner.textContent).toContain(INFO_SUMMARY);
    expect(rendered.queryByRole("alert")).toBeNull();
    expect(rendered.getByRole("button", { name: ACK_NAME })).toBeTruthy();
  });

  it("shows no banner when the notice is acknowledged or absent", async () => {
    const acked = await renderPanel(
      statusWith(notice({ acknowledged: true, acknowledgedAt: "2026-10-29T11:00:00+00:00" })),
    );
    expect(acked.rendered.queryByRole("button", { name: ACK_NAME })).toBeNull();
    expect(acked.rendered.queryByText(SUMMARY)).toBeNull();
    expect(acked.rendered.queryByRole("alert")).toBeNull();
    cleanup();

    const absent = await renderPanel(statusWith(null));
    expect(absent.rendered.queryByRole("button", { name: ACK_NAME })).toBeNull();
    expect(absent.rendered.queryByText(SUMMARY)).toBeNull();
    expect(absent.rendered.queryByRole("status")).toBeNull();
  });

  it("posts the exact to_hash and clears the banner from the re-read status", async () => {
    const { h, rendered, load } = await renderPanel(statusWith(notice()));
    const ack = vi
      .spyOn(h.controller, "acknowledgeCapacitiesSchemaChange")
      .mockResolvedValue(undefined);
    // From here the server answers the acknowledged notice; the panel only
    // learns that from the re-read, never from an optimistic local guess.
    load.mockResolvedValue(statusWith(notice({ acknowledged: true })));

    fireEvent.click(rendered.getByRole("button", { name: ACK_NAME }));
    await waitFor(() => expect(ack).toHaveBeenCalledWith("to-hash"));

    // The panel re-reads status after acknowledging, so the banner reflects
    // the server's own record instead of an optimistic local guess.
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(rendered.queryByText(SUMMARY)).toBeNull());
    expect(rendered.queryByRole("button", { name: ACK_NAME })).toBeNull();
  });

  it("re-reads the status on a stale 409 without showing an error", async () => {
    const { h, rendered, load } = await renderPanel(statusWith(notice()));
    const ack = vi.spyOn(h.controller, "acknowledgeCapacitiesSchemaChange").mockRejectedValue(
      new ApiError(
        409,
        { ok: false, error: "stale" },
        "request failed (409)",
        "/capacities/refresh/schema-change/ack",
      ),
    );

    fireEvent.click(rendered.getByRole("button", { name: ACK_NAME }));
    await waitFor(() => expect(ack).toHaveBeenCalledTimes(1));

    // A stale hash means a newer notice already replaced this one: read the
    // current truth and say nothing about a failure.
    await waitFor(() => expect(load.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(rendered.queryByRole("alert")).toBeTruthy();
    expect(rendered.queryByText(/request failed/i)).toBeNull();
    expect(rendered.queryByText(/stale/i)).toBeNull();
    expect((rendered.getByRole("button", { name: ACK_NAME }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps the banner usable and shows a short error when the ack fails", async () => {
    const { h, rendered } = await renderPanel(statusWith(notice()));
    vi.spyOn(h.controller, "acknowledgeCapacitiesSchemaChange").mockRejectedValue(
      new ApiTransportError(
        "/capacities/refresh/schema-change/ack",
        "TDTB request failed before a response was available",
      ),
    );

    const button = rendered.getByRole("button", { name: ACK_NAME });
    fireEvent.click(button);

    await waitFor(() => expect(rendered.getAllByRole("alert").length).toBe(2));
    // The notice sentence survives; the failure is its own short line.
    expect(rendered.getByText(SUMMARY)).toBeTruthy();
    expect(rendered.getByText(/request failed before a response/i)).toBeTruthy();
    expect((rendered.getByRole("button", { name: ACK_NAME }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables the Acknowledge button while the request is in flight", async () => {
    const { h, rendered } = await renderPanel(statusWith(notice()));
    const pending = deferred<void>();
    const ack = vi
      .spyOn(h.controller, "acknowledgeCapacitiesSchemaChange")
      .mockReturnValue(pending.promise);

    fireEvent.click(rendered.getByRole("button", { name: ACK_NAME }));
    await waitFor(() => expect(ack).toHaveBeenCalledTimes(1));
    expect((rendered.getByRole("button", { name: ACK_NAME }) as HTMLButtonElement).disabled).toBe(true);

    pending.resolve();
    await waitFor(() =>
      expect((rendered.getByRole("button", { name: ACK_NAME }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
  });

  it("places the banner above the Warnings block", async () => {
    const { rendered } = await renderPanel(
      statusWith(notice(), ["Capacities partial - 2 evaluated, 51 deferred."]),
    );

    const banner = rendered.getByRole("alert");
    const warnings = rendered.getByText("Warnings");

    // DOCUMENT_POSITION_FOLLOWING means the Warnings heading comes after the
    // banner in document order.
    expect(banner.compareDocumentPosition(warnings) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("does not add the notice to the plan warnings list", async () => {
    const { rendered } = await renderPanel(statusWith(notice()));

    // "Warnings" only appears for the job's own warnings, never for the
    // advisory schema change.
    expect(rendered.queryByText("Warnings")).toBeNull();
    expect(rendered.getByRole("alert").textContent).toContain(SUMMARY);
  });
});

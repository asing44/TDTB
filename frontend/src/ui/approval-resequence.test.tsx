/* Deterministic refusals need a resequence, not an inert Retry (issue #14).

   2026-10-16: the drawer answered a 422 "commit refused: forged fixed-lane
   timing/backdrop" with `Retry preview`, which resends the identical sequence
   and can never succeed. A typed 4xx refusal now offers the same billed
   Re/Sequence action the dock uses; a transient transport failure keeps Retry.

   Fixtures and direct store dispatches only — no network, no billed calls:
   every controller action here is a spy. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent } from "@testing-library/preact";
import { ApprovalDrawer } from "./ApprovalDrawer";
import { makeHarness, type Harness } from "./test-harness";
import { fixedInputsOf } from "../fixtures/scenarios";
import { ApiError, ApiTransportError } from "../adapters/api";
import type { ShadowErrorKind } from "../store/store";

afterEach(cleanup);

const REFUSAL =
  "commit refused: forged fixed-lane timing/backdrop: Live: client backdrop on non-zone row — regenerate before committing";

/** Open the drawer on a failed preview of the given recovery class. Omitting
    `kind` is the legacy/untyped dispatch — it must fail safe to Retry. */
function openFailedPreview(h: Harness, kind?: ShadowErrorKind): void {
  h.store.dispatch({ type: "SHADOW_FAIL", error: REFUSAL, ...(kind ? { kind } : {}) });
  h.store.dispatch({ type: "UI", patch: { approvalOpen: true } });
}

describe("a deterministic preview refusal offers Resequence, never Retry", () => {
  it("renders the refusal text with the billed Resequence action", () => {
    const h = makeHarness("sequenced");
    openFailedPreview(h, "deterministic");
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByText(REFUSAL)).toBeTruthy();
    const resequence = r.getByRole("button", { name: /Resequence/ }) as HTMLButtonElement;
    expect(resequence.disabled).toBe(false);
    // The billed cost and the remaining ledger are on the button itself.
    expect(resequence.textContent).toContain("1 billed");
    expect(resequence.textContent).toContain("3 left");
    // Retrying the same sequence can never succeed — it must not be offered.
    expect(r.queryByRole("button", { name: /Retry preview/ })).toBeNull();
  });

  it("keeps Retry and offers no Resequence for a transient transport failure", () => {
    const h = makeHarness("sequenced");
    openFailedPreview(h, "transport");
    const r = h.ui(<ApprovalDrawer />);

    expect((r.getByRole("button", { name: /Retry preview/ }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(r.queryByRole("button", { name: /Resequence/ })).toBeNull();
  });

  it("defaults an unclassified failure to Retry (fail safe)", () => {
    const h = makeHarness("sequenced");
    openFailedPreview(h, "unknown");
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByRole("button", { name: /Retry preview/ })).toBeTruthy();
    expect(r.queryByRole("button", { name: /Resequence/ })).toBeNull();
  });

  it("also defaults a kindless SHADOW_FAIL to Retry", () => {
    const h = makeHarness("sequenced");
    openFailedPreview(h);
    const r = h.ui(<ApprovalDrawer />);

    expect(r.getByRole("button", { name: /Retry preview/ })).toBeTruthy();
    expect(r.queryByRole("button", { name: /Resequence/ })).toBeNull();
  });
});

describe("the Resequence action is explicit and billed", () => {
  it("fires nothing on render — no billed sequence without a click", () => {
    const h = makeHarness("sequenced");
    const sequence = vi.spyOn(h.controller, "autoSequence").mockResolvedValue(undefined);
    openFailedPreview(h, "deterministic");

    h.ui(<ApprovalDrawer />);

    expect(sequence).not.toHaveBeenCalled();
  });

  it("calls the controller sequence action exactly once, on click", () => {
    const h = makeHarness("sequenced");
    const sequence = vi.spyOn(h.controller, "autoSequence").mockResolvedValue(undefined);
    openFailedPreview(h, "deterministic");
    const r = h.ui(<ApprovalDrawer />);

    fireEvent.click(r.getByRole("button", { name: /Resequence/ }));

    expect(sequence).toHaveBeenCalledTimes(1);
  });

  it("disables Resequence with the spent-ledger reason, same selector as the dock", () => {
    const h = makeHarness("sequenced", (sc) => {
      sc.ledger = { ...sc.ledger, spent: sc.ledger.cap, remaining: 0 };
    });
    openFailedPreview(h, "deterministic");
    const r = h.ui(<ApprovalDrawer />);

    const resequence = r.getByRole("button", { name: /Resequence/ }) as HTMLButtonElement;
    expect(resequence.disabled).toBe(true);
    expect(resequence.getAttribute("title")).toMatch(/budget spent/i);
    expect(resequence.textContent).toContain("0 left");
  });
});

describe("the controller classifies the failure with the typed errors", () => {
  /** The real shadowPreview path with the fixed-input read stubbed to the
      staged fingerprint, so only the failing /commit call is under test. */
  async function failPreview(h: Harness, error: unknown): Promise<void> {
    vi.spyOn(h.adapter, "readFixedInputs").mockResolvedValue(fixedInputsOf(h.scenario.inputs));
    vi.spyOn(h.adapter, "shadowCommit").mockRejectedValue(error);
    await h.controller.shadowPreview();
  }

  it("a 422 protocol refusal is deterministic and keeps its message", async () => {
    const h = makeHarness("sequenced");
    await failPreview(h, new ApiError(422, { message: REFUSAL }, REFUSAL));

    const s = h.store.getState();
    expect(s.shadowError).toBe(REFUSAL);
    expect(s.shadowErrorKind).toBe("deterministic");
  });

  it("a transport rejection is retryable", async () => {
    const h = makeHarness("sequenced");
    await failPreview(
      h,
      new ApiTransportError("/commit", "TDTB request failed before a response was available (/commit)"),
    );

    expect(h.store.getState().shadowErrorKind).toBe("transport");
  });

  it("an untyped failure is unknown — Retry, not a resequence", async () => {
    const h = makeHarness("sequenced");
    await failPreview(h, new Error("preview endpoint down"));

    expect(h.store.getState().shadowErrorKind).toBe("unknown");
  });

  it("a new sequence clears the stale refusal kind with the message", async () => {
    const h = makeHarness("sequenced");
    await failPreview(h, new ApiError(422, { message: REFUSAL }, REFUSAL));
    expect(h.store.getState().shadowErrorKind).toBe("deterministic");

    h.store.dispatch({
      type: "SEQUENCE_OK",
      sequence: h.scenario.staged.sequence!.map((row) => ({ ...row })),
      warnings: [],
      fingerprint: h.store.getState().fingerprint ?? "",
      anchoredSourceFingerprint: h.store.getState().anchoredSourceFingerprint ?? "",
      ledger: { ...h.scenario.ledger },
    });

    expect(h.store.getState().shadowError).toBeNull();
    expect(h.store.getState().shadowErrorKind).toBeNull();
  });
});

import { describe, expect, it } from "vitest";

import type { ProjectionReceipt } from "./projection-schema";
import {
  initialProjectionSetup,
  reduceProjectionSetup,
  type ProjectionIdentity,
  type ProjectionPendingCommand,
} from "./projection";

const identity: ProjectionIdentity = {
  courseVersionId: "course-version-1",
  slideDeckId: "deck-version-1",
  runtimeManifestId: "runtime-version-1",
  runtimeManifestDigest: "a".repeat(64),
};

const commandId = (suffix: number): string =>
  `00000000-0000-4000-8000-${suffix.toString().padStart(12, "0")}`;

const pending = (
  command: ProjectionPendingCommand["command"],
  generation: number,
  sessionId: string | null = "11111111-1111-4111-8111-111111111111",
): ProjectionPendingCommand => ({
  commandId: commandId(generation + 1),
  command,
  sessionId,
  expectedGeneration: generation,
});

const receipt = (
  request: ProjectionPendingCommand,
  patch: Partial<ProjectionReceipt> = {},
): ProjectionReceipt => ({
  schemaVersion: 1,
  commandId: request.commandId,
  sessionId: request.sessionId,
  command: request.command,
  accepted: true,
  status: "candidate",
  generation: request.expectedGeneration,
  message: "projection_command_accepted",
  assignments: [],
  ...patch,
});

const apply = (
  state: ReturnType<typeof initialProjectionSetup>,
  request: ProjectionPendingCommand,
  response: ProjectionReceipt,
) =>
  reduceProjectionSetup(
    reduceProjectionSetup(state, { type: "COMMAND_STARTED", pending: request }),
    { type: "RECEIPT_RECEIVED", identity, receipt: response },
  );

describe("native projection setup reducer", () => {
  it("advances only through correlated detect open assign fullscreen and witness receipts", () => {
    let state = initialProjectionSetup(identity);
    const detect = pending("detect_displays", 0, null);
    state = apply(state, detect, receipt(detect));
    expect(state.steps.detect).toBe("complete");

    const open = pending("open_projection_session", 0);
    state = apply(state, open, receipt(open));
    const assign = pending("assign_projection_window", 0);
    state = apply(
      state,
      assign,
      receipt(assign, {
        status: "assigned",
        generation: 1,
        assignments: [
          { role: "stage", displayId: "b".repeat(64), windowGeneration: 1 },
          { role: "presenter", displayId: "c".repeat(64), windowGeneration: 1 },
        ],
      }),
    );
    expect(state.steps.assign).toBe("complete");
    expect(state.assignment).toBe("external-stage");

    const fullscreen = pending("enter_projection_fullscreen", 1);
    state = apply(
      state,
      fullscreen,
      receipt(fullscreen, { status: "fullscreen", generation: 1 }),
    );
    expect(state.steps.fullscreen).toBe("complete");
    expect(state.status).toBe("witness-ready");

    const verify = pending("verify_projection_assignment", 1);
    state = apply(
      state,
      verify,
      receipt(verify, { status: "certified", generation: 1 }),
    );
    expect(state.steps.witness).toBe("complete");
    expect(state.physicalDualScreenCertified).toBe(true);
    expect(state.releaseSignatureCertified).toBe(false);
  });

  it("invalidates stale generation and mismatched projection identity without certifying", () => {
    const request = pending("enter_projection_fullscreen", 3);
    const waiting = reduceProjectionSetup(initialProjectionSetup(identity), {
      type: "COMMAND_STARTED",
      pending: request,
    });
    const stale = reduceProjectionSetup(waiting, {
      type: "RECEIPT_RECEIVED",
      identity,
      receipt: receipt(request, { status: "fullscreen", generation: 2 }),
    });
    expect(stale.status).toBe("invalidated");
    expect(stale.physicalDualScreenCertified).toBe(false);

    const wrongIdentity = reduceProjectionSetup(waiting, {
      type: "RECEIPT_RECEIVED",
      identity: { ...identity, runtimeManifestDigest: "f".repeat(64) },
      receipt: receipt(request, { status: "fullscreen", generation: 3 }),
    });
    expect(wrongIdentity.status).toBe("invalidated");
    expect(wrongIdentity.steps.fullscreen).not.toBe("complete");
  });

  it("invalidates certification immediately when Swap starts", () => {
    const certified = {
      ...initialProjectionSetup(identity),
      status: "certified" as const,
      physicalDualScreenCertified: true,
      generation: 4,
      assignment: "external-stage" as const,
      steps: {
        detect: "complete" as const,
        assign: "complete" as const,
        fullscreen: "complete" as const,
        witness: "complete" as const,
      },
    };
    const swap = pending("assign_projection_window", 4);
    const swapping = reduceProjectionSetup(certified, {
      type: "COMMAND_STARTED",
      pending: swap,
      swap: true,
    });
    expect(swapping.physicalDualScreenCertified).toBe(false);
    expect(swapping.steps.fullscreen).toBe("waiting");
    expect(swapping.steps.witness).toBe("waiting");

    const swapped = reduceProjectionSetup(swapping, {
      type: "RECEIPT_RECEIVED",
      identity,
      receipt: receipt(swap, {
        status: "assigned",
        generation: 5,
        assignments: [
          { role: "stage", displayId: "d".repeat(64), windowGeneration: 5 },
          { role: "presenter", displayId: "e".repeat(64), windowGeneration: 5 },
        ],
      }),
    });
    expect(swapped.assignment).toBe("internal-stage");
    expect(swapped.status).toBe("running");
  });
});

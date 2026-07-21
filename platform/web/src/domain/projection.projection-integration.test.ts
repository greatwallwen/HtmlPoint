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

const apply = (
  state: ReturnType<typeof initialProjectionSetup>,
  pending: ProjectionPendingCommand,
  status: ProjectionReceipt["status"],
  assignments: ProjectionReceipt["assignments"] = [],
) => {
  const started = reduceProjectionSetup(state, {
    type: "COMMAND_STARTED",
    pending,
  });
  return reduceProjectionSetup(started, {
    type: "RECEIPT_RECEIVED",
    identity,
    receipt: {
      schemaVersion: 1,
      commandId: pending.commandId,
      sessionId: pending.sessionId,
      command: pending.command,
      accepted: true,
      status,
      generation:
        pending.command === "assign_projection_window"
          ? pending.expectedGeneration + 1
          : pending.expectedGeneration,
      message: "projection_command_accepted",
      assignments,
    },
  });
};

describe("published native integration lifecycle", () => {
  it("keeps fullscreen explicitly non-certified until attended verify succeeds", () => {
    const processEnvironment = (
      globalThis as typeof globalThis & {
        process?: { env?: Record<string, string | undefined> };
      }
    ).process?.env;
    expect(processEnvironment?.COURSE_PROJECTION_INTEGRATION_TEST).toBe("1");
    const sessionId = "11111111-1111-4111-8111-111111111111";
    let state = initialProjectionSetup(identity);
    state = apply(state, {
      commandId: "00000000-0000-4000-8000-000000000001",
      command: "detect_displays",
      sessionId: null,
      expectedGeneration: 0,
    }, "candidate");
    state = apply(state, {
      commandId: "00000000-0000-4000-8000-000000000002",
      command: "open_projection_session",
      sessionId,
      expectedGeneration: 0,
    }, "candidate");
    state = apply(state, {
      commandId: "00000000-0000-4000-8000-000000000003",
      command: "assign_projection_window",
      sessionId,
      expectedGeneration: 0,
    }, "assigned", [
      { role: "stage", displayId: "b".repeat(64), windowGeneration: 1 },
      { role: "presenter", displayId: "c".repeat(64), windowGeneration: 1 },
    ]);
    state = apply(state, {
      commandId: "00000000-0000-4000-8000-000000000004",
      command: "enter_projection_fullscreen",
      sessionId,
      expectedGeneration: 1,
    }, "fullscreen");

    expect(state.status).toBe("witness-ready");
    expect(state.physicalDualScreenCertified).toBe(false);
    expect(state.releaseSignatureCertified).toBe(false);
  });
});

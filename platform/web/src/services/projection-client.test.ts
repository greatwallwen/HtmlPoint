import { describe, expect, it, vi } from "vitest";

import type { ProjectionReceipt } from "../domain/projection-schema";
import type { VerifiedHelperSession } from "./helper-session";
import {
  HelperProjectionClient,
  ProjectionClientError,
} from "./projection-client";

const helperOrigin = "http://127.0.0.1:8765";
const sessionToken = "t".repeat(43);
const session = { helperOrigin, sessionToken } as VerifiedHelperSession;
const commandId = "00000000-0000-4000-8000-000000000001";
const sessionId = "11111111-1111-4111-8111-111111111111";
const evidence = {
  evidenceId: "projection-evidence-1",
  kind: "runtime",
  subjectVersionId: null,
  status: "verified",
  inputSummary: {},
  outputSummary: {},
  producer: "course-helper-projection-gateway",
  producerVersion: "0.1.0",
  startedAt: "2026-07-20T05:00:00Z",
  finishedAt: "2026-07-20T05:00:01Z",
  durationMs: 1000,
  checks: [],
  errors: [],
  artifacts: [],
};

const receipt = (patch: Partial<ProjectionReceipt> = {}): ProjectionReceipt => ({
  schemaVersion: 1,
  commandId,
  sessionId: null,
  command: "detect_displays",
  accepted: true,
  status: "candidate",
  generation: 0,
  message: "projection_command_accepted",
  assignments: [],
  ...patch,
});

const response = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("HelperProjectionClient", () => {
  it("posts the exact detect job with session authentication outside the URL", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response({ result: { receipt: receipt() }, evidence }),
    );
    const client = new HelperProjectionClient(session, fetcher);

    await expect(
      client.detect({ commandId, sessionId: null, expectedGeneration: 0 }),
    ).resolves.toEqual(receipt());

    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${helperOrigin}/v1/jobs`);
    expect(url).not.toContain(sessionToken);
    expect(new Headers(init.headers).get("X-Course-Session")).toBe(sessionToken);
    expect(init.credentials).toBe("omit");
    expect(JSON.parse(String(init.body))).toEqual({
      type: "projection_detect_displays",
      commandId,
      sessionId: null,
      expectedGeneration: 0,
      payload: {},
    });
  });

  it("builds all five session-bound jobs without accepting browser-authored native fields", async () => {
    const calls: unknown[] = [];
    const fetcher = vi.fn((_url: string, init: RequestInit) => {
      const body = JSON.parse(String(init.body));
      calls.push(body);
      const commands = {
        projection_open_session: "open_projection_session",
        projection_assign_window: "assign_projection_window",
        projection_enter_fullscreen: "enter_projection_fullscreen",
        projection_verify_assignment: "verify_projection_assignment",
        projection_close_session: "close_projection_session",
      } as const;
      return Promise.resolve(
        response({
          result: {
            receipt: receipt({
              sessionId,
              command: commands[body.type as keyof typeof commands],
              generation: body.type === "projection_assign_window" ? 1 : body.expectedGeneration,
            }),
          },
          evidence,
        }),
      );
    });
    const client = new HelperProjectionClient(session, fetcher);
    const base = { commandId, sessionId, expectedGeneration: 0 };
    await client.open({
      ...base,
      courseVersionId: "course-v1",
      slideDeckId: "deck-v1",
      runtimeManifestId: "runtime-v1",
    });
    await client.assign({ ...base, swap: false });
    await client.fullscreen({ ...base, expectedGeneration: 1 });
    await client.verify({ ...base, expectedGeneration: 1 });
    await client.close({ ...base, expectedGeneration: 1 });

    expect(calls).toEqual([
      { type: "projection_open_session", ...base, payload: { courseVersionId: "course-v1", slideDeckId: "deck-v1", runtimeManifestId: "runtime-v1" } },
      { type: "projection_assign_window", ...base, payload: { swap: false } },
      { type: "projection_enter_fullscreen", ...base, expectedGeneration: 1, payload: {} },
      { type: "projection_verify_assignment", ...base, expectedGeneration: 1, payload: {} },
      { type: "projection_close_session", ...base, expectedGeneration: 1, payload: {} },
    ]);
  });

  it("rejects extra response fields, mismatched identities, and impossible generations", async () => {
    for (const unsafe of [
      { ...receipt(), executablePath: "C:/private/host.exe" },
      receipt({ commandId: "00000000-0000-4000-8000-000000000002" }),
      receipt({ generation: 9 }),
    ]) {
      const client = new HelperProjectionClient(
        session,
        vi.fn().mockResolvedValue(
          response({ result: { receipt: unsafe }, evidence }),
        ),
      );
      await expect(
        client.detect({ commandId, sessionId: null, expectedGeneration: 0 }),
      ).rejects.toMatchObject({ code: "invalid_response" });
    }
  });

  it("maps unavailable Helper and Host failures to one bounded retryable error", async () => {
    const client = new HelperProjectionClient(
      session,
      vi.fn().mockResolvedValue(
        response(
          {
            result: { reasonCode: "projection_unavailable", status: "failed" },
            evidence: { ...evidence, status: "failed" },
          },
          503,
        ),
      ),
    );
    const failure = await client
      .detect({ commandId, sessionId: null, expectedGeneration: 0 })
      .catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ProjectionClientError);
    expect(failure).toMatchObject({ code: "projection_unavailable", retryable: true });
    expect(String(failure)).not.toContain("127.0.0.1");
  });
});

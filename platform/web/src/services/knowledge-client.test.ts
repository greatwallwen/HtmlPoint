import { afterEach, describe, expect, it, vi } from "vitest";

import type { VerifiedHelperSession } from "./helper-session";
import {
  KnowledgeClient,
  SAFE_HELPER_FAILURE_MESSAGE,
  trustedExternalLinkProps,
} from "./knowledge-client";

const helperOrigin = "http://127.0.0.1:8765";
const token = "t".repeat(43);
const digest = "a".repeat(64);
const timestamp = "2026-07-19T02:00:00Z";
const session = { helperOrigin, sessionToken: token } as VerifiedHelperSession;
const evidence = {
  evidenceId: "evidence-1",
  kind: "execution",
  subjectVersionId: null,
  status: "verified",
  inputSummary: {},
  outputSummary: {},
  producer: "test",
  producerVersion: "1",
  startedAt: timestamp,
  finishedAt: timestamp,
  durationMs: 0,
  checks: [],
  errors: [],
  artifacts: [],
};

function response(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("KnowledgeClient governed surface", () => {
  it("exposes every Task 13/14 method without an arbitrary runner", () => {
    const client = new KnowledgeClient(session) as unknown as Record<string, unknown>;
    const methods = [
      "getSummary",
      "uploadSource",
      "listSources",
      "startImport",
      "getImportStatus",
      "cancelImport",
      "recoverOperation",
      "listReviews",
      "getReviewDetail",
      "listUpgrades",
      "resolveReview",
      "publishCard",
      "resolveUpgrade",
      "indexKnowledge",
      "composeCourse",
      "confirmOutline",
      "buildCharts",
      "searchVisuals",
      "acquireVisuals",
      "revalidateVisual",
      "attachVisual",
      "detachVisual",
      "validateCourse",
      "publishCourse",
    ];
    for (const method of methods) {
      expect(typeof client[method]).toBe("function");
    }
    expect(client.runArbitraryJob).toBeUndefined();
  });

  it("parses bounded review pages strictly and sends only the typed job", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ result: { items: [], nextCursor: null }, evidence }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const client = new KnowledgeClient(session);

    await expect(
      client.listReviews({
        type: "knowledge_review_list",
        status: "open",
        category: "candidate-card",
        limit: 25,
        cursor: null,
      }),
    ).resolves.toMatchObject({ result: { items: [], nextCursor: null } });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${helperOrigin}/v1/jobs`);
    expect(url).not.toContain(token);
    expect(init.credentials).toBe("omit");
    expect(new Headers(init.headers).get("X-Course-Session")).toBe(token);
    expect(JSON.parse(String(init.body))).toEqual({
      type: "knowledge_review_list",
      status: "open",
      category: "candidate-card",
      limit: 25,
      cursor: null,
    });

    fetchMock.mockResolvedValueOnce(
      response({ result: { items: [], nextCursor: null, privatePath: "D:/private" }, evidence }),
    );
    await expect(
      client.listReviews({ type: "knowledge_review_list", limit: 25 }),
    ).rejects.toThrow(SAFE_HELPER_FAILURE_MESSAGE);
  });

  it("rejects mismatched operation identities and mismatched job result types", async () => {
    const client = new KnowledgeClient(session);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockResolvedValueOnce(
      response({
        result: {
          operationId: "different-operation",
          status: "committed",
          requestDigest: digest,
          resultRefs: {},
        },
        evidence,
      }),
    );
    await expect(
      client.recoverOperation({
        type: "operation_status",
        operationId: "operation-1",
        actor: { actorType: "human", actorId: "user-1" },
      }),
    ).rejects.toThrow(SAFE_HELPER_FAILURE_MESSAGE);

    fetchMock.mockResolvedValueOnce(
      response({ result: { items: [], nextCursor: null }, evidence }),
    );
    await expect(
      client.getImportStatus({
        type: "knowledge_import_status",
        importId: `import-${"b".repeat(32)}`,
        actor: { actorType: "human", actorId: "user-1" },
      }),
    ).rejects.toThrow(SAFE_HELPER_FAILURE_MESSAGE);
  });

  it("closes the upload and source inventory boundaries without leaking the token", async () => {
    const upload = {
      schemaVersion: 1,
      uploadId: `upload-${"b".repeat(32)}`,
      safeName: "course.md",
      sourceKind: "markdown",
      mediaType: "text/markdown",
      byteSize: 4,
      contentDigest: digest,
      state: "available",
      expiresAt: timestamp,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(upload, 201))
      .mockResolvedValueOnce(
        response({ schemaVersion: 1, items: [], nextCursor: null }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new KnowledgeClient(session);

    await expect(
      client.uploadSource(new Blob(["demo"], { type: "text/markdown" }), "course.md"),
    ).resolves.toEqual(upload);
    await expect(client.listSources({ limit: 10 })).resolves.toEqual({
      schemaVersion: 1,
      items: [],
      nextCursor: null,
    });

    const [uploadUrl, uploadInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(uploadUrl).toBe(`${helperOrigin}/v1/uploads`);
    expect(new Headers(uploadInit.headers).get("X-Upload-Name")).toBe("course.md");
    expect(String(uploadInit.body)).not.toContain(token);
    expect(fetchMock.mock.calls[1][0]).toBe(`${helperOrigin}/v1/knowledge/sources?limit=10`);
  });

  it("rejects duplicate visual placement requests before making a request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const client = new KnowledgeClient(session);
    await expect(
      client.detachVisual({
        type: "course_visual_detach",
        operationId: "op-detach",
        requestDigest: digest,
        actor: { actorType: "human", actorId: "user-1" },
        courseVersionId: "course-v1",
        expectedCourseDigest: digest,
        placementId: "placement-1",
        activePlacementIds: ["placement-1", "placement-1"],
      }),
    ).rejects.toThrow(SAFE_HELPER_FAILURE_MESSAGE);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("projects trusted external links with safe browser attributes", () => {
    expect(
      trustedExternalLinkProps({
        schemaVersion: 1,
        linkId: "link-1",
        linkType: "license",
        href: "https://creativecommons.org/licenses/by/4.0/",
        provenanceKind: "licensed-secondary",
        label: "CC BY 4.0",
      }),
    ).toEqual({
      href: "https://creativecommons.org/licenses/by/4.0/",
      target: "_blank",
      rel: "noopener noreferrer external",
    });
  });
});

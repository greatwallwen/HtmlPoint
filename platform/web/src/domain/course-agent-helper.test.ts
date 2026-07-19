import { describe, expect, it, vi } from "vitest";

import type { KnowledgeClient } from "../services/knowledge-client";
import {
  HelperCourseAgent,
  LocalCourseAgent,
  type CourseRequirementDraft,
} from "./course-agent";
import { stableDigest } from "./validation";

const digest = "a".repeat(64);
const confirmationDigest = "b".repeat(64);
const timestamp = "2026-07-19T02:00:00Z";
const evidence = {
  evidenceId: "evidence-compose",
  kind: "execution" as const,
  subjectVersionId: null,
  status: "verified" as const,
  inputSummary: {},
  outputSummary: {},
  producer: "test",
  producerVersion: "1",
  startedAt: timestamp,
  finishedAt: timestamp,
  durationMs: 0,
  checks: [
    { code: "bound", status: "passed" as const, message: "证据已绑定", details: {} },
  ],
  errors: [],
  artifacts: [],
};
const outline = {
  schemaVersion: 1 as const,
  logicalId: "outline",
  versionId: "outline-v1",
  revision: 1,
  contentDigest: digest,
  supersedesVersionId: null,
  createdAt: timestamp,
  createdBy: { actorType: "human" as const, actorId: "local-user", displayName: null },
  requirementId: "requirement-server",
  chapters: [
    {
      schemaVersion: 1 as const,
      chapterId: "chapter-1",
      title: "证据优先",
      objective: "建立验证习惯",
      placements: [
        {
          schemaVersion: 1 as const,
          placementId: "placement-1",
          cardVersionId: "card-v1",
          chapterId: "chapter-1",
          lessonId: "lesson-1",
          purpose: "core" as const,
          allocatedMinutes: 45,
        },
      ],
    },
  ],
  uncoveredGoals: [],
  retrievalEvidenceId: "evidence-retrieval",
  indexSnapshotId: "snapshot-1",
};
const composeResult = {
  operationId: "compose-operation-server",
  operationStatus: "committed" as const,
  requirementId: "requirement-server",
  outlineVersionId: outline.versionId,
  outlineDigest: outline.contentDigest,
  indexSnapshotId: outline.indexSnapshotId,
  blockingGaps: [],
  compositionEvidenceId: "evidence-compose",
  retrievalEvidenceIds: ["evidence-retrieval"],
  confirmationSummary: {
    usageScope: "internal" as const,
    outlineVersionId: outline.versionId,
    outlineDigest: outline.contentDigest,
    requirementId: "requirement-server",
    confirmationDigest,
    text: "确认一章一节课程",
  },
  outline,
};
const slideNode = {
  schemaVersion: 1 as const,
  nodeId: "slide-1",
  nodeType: "heading" as const,
  text: "证据验证方法",
  items: [],
  placementIds: ["placement-1"],
  cardVersionIds: ["card-v1"],
  chunkIds: ["chunk-1"],
  sourceVersionIds: ["source-v1"],
  evidenceIds: ["evidence-1"],
  presenterNotes: null,
  assetBindings: [],
  children: [],
};
const confirmResult = {
  operationId: "confirm-operation-server",
  operationStatus: "committed" as const,
  confirmationId: "confirmation-1",
  confirmationDigest,
  courseVersionId: "course-v1",
  courseDigest: digest,
  courseStatus: "confirmed" as const,
  outlineVersionId: outline.versionId,
  outlineDigest: outline.contentDigest,
  placementIds: ["placement-1"],
  usageScope: "internal" as const,
  slideDeckId: "deck-v1",
  runtimeManifestId: "runtime-v1",
  slideDeck: {
    schemaVersion: 1 as const,
    logicalId: "deck",
    versionId: "deck-v1",
    revision: 1,
    contentDigest: digest,
    supersedesVersionId: null,
    createdAt: timestamp,
    createdBy: { actorType: "human" as const, actorId: "local-user", displayName: null },
    courseVersionId: "course-v1",
    nodes: [slideNode],
  },
};
const draft: CourseRequirementDraft = {
  title: "  企业 AI 课程  ",
  audience: "  产品团队  ",
  learningGoals: ["  建立证据习惯  "],
  durationMinutes: 45,
  requiredTagIds: ["topic:evidence"],
  excludedTagIds: [],
  usageScope: "internal",
  includeCardVersionIds: [],
  excludeCardVersionIds: [],
  requireVisualRefs: true,
  requireDatasetRefs: false,
};

function helperClient() {
  const client = {
    getSummary: vi.fn().mockResolvedValue({
      schemaVersion: 1,
      sourceCount: 1,
      publishedCardCount: 1,
      reviewTaskCount: 0,
      retrievalMode: "fts-degraded",
      indexSnapshotId: "snapshot-1",
      indexSnapshotDigest: "c".repeat(64),
      indexState: "degraded",
      tagLabels: ["证据"],
      tagOptions: [{ id: "topic:evidence", label: "证据", dimension: "topic" }],
      updatedAt: timestamp,
    }),
    composeCourse: vi.fn().mockImplementation(async (job) => ({
      result: { ...composeResult, operationId: job.operationId, requirementId: job.requirement.requirementId, outline: { ...outline, requirementId: job.requirement.requirementId, versionId: job.outlineVersionId, logicalId: job.outlineLogicalId }, outlineVersionId: job.outlineVersionId, confirmationSummary: { ...composeResult.confirmationSummary, requirementId: job.requirement.requirementId, outlineVersionId: job.outlineVersionId } },
      evidence,
    })),
    confirmOutline: vi.fn().mockImplementation(async (job) => ({
      result: { ...confirmResult, operationId: job.operationId, confirmationId: job.confirmationId, courseVersionId: job.courseVersionId, outlineVersionId: job.outlineVersionId, slideDeck: { ...confirmResult.slideDeck, courseVersionId: job.courseVersionId } },
      evidence: { ...evidence, evidenceId: "evidence-confirm" },
    })),
  };
  return client;
}

describe("HelperCourseAgent", () => {
  it("creates a normalized digest-bound compose request and reuses identical input", async () => {
    const client = helperClient();
    const agent = new HelperCourseAgent(client as unknown as KnowledgeClient);
    const first = await agent.compose(draft);
    const second = await agent.compose(draft);

    expect(client.composeCourse).toHaveBeenCalledTimes(1);
    expect(second).toBe(first);
    const job = client.composeCourse.mock.calls[0][0];
    expect(job).toMatchObject({
      type: "course_compose",
      requirement: {
        title: "企业 AI 课程",
        audience: "产品团队",
        learningGoals: ["建立证据习惯"],
        durationMinutes: 45,
        requiredTagIds: ["topic:evidence"],
        usageScope: "internal",
      },
      options: {
        indexSnapshotId: "snapshot-1",
        requireVisualRefs: true,
      },
    });
    expect(job.requestDigest).toBe(
      await stableDigest({
        kind: "course_compose",
        options: job.options,
        outlineLogicalId: job.outlineLogicalId,
        outlineRevision: job.outlineRevision,
        outlineVersionId: job.outlineVersionId,
        requirement: job.requirement,
      }),
    );
    expect(first.retrievalMode).toBe("fts-degraded");
    expect(first.indexSnapshotId).toBe("snapshot-1");
  });

  it("requires a ready snapshot and does not silently compose without one", async () => {
    const client = helperClient();
    client.getSummary.mockResolvedValueOnce({
      ...(await client.getSummary()),
      indexSnapshotId: null,
      indexSnapshotDigest: null,
      indexState: "unavailable",
    });
    const agent = new HelperCourseAgent(client as unknown as KnowledgeClient);
    await expect(agent.compose(draft)).rejects.toThrow("索引");
    expect(client.composeCourse).not.toHaveBeenCalled();
  });

  it("creates a course projection only after explicit digest-bound confirmation", async () => {
    const client = helperClient();
    const agent = new HelperCourseAgent(client as unknown as KnowledgeClient);
    const preview = await agent.compose(draft);
    expect(client.confirmOutline).not.toHaveBeenCalled();

    const confirmed = await agent.confirm(preview);
    expect(client.confirmOutline).toHaveBeenCalledTimes(1);
    const job = client.confirmOutline.mock.calls[0][0];
    expect(job.requestDigest).toBe(
      await stableDigest({
        kind: "course_outline_confirm",
        confirmationDigest: job.confirmationDigest,
        confirmationId: job.confirmationId,
        courseLogicalId: job.courseLogicalId,
        courseRevision: job.courseRevision,
        courseVersionId: job.courseVersionId,
        expectedOutlineDigest: job.expectedOutlineDigest,
        outlineVersionId: job.outlineVersionId,
        requirementId: job.requirementId,
      }),
    );
    expect(confirmed.governed).toMatchObject({
      requirementId: preview.requirementId,
      outlineVersionId: preview.result.outlineVersionId,
      courseVersionId: job.courseVersionId,
      slideDeckId: "deck-v1",
      runtimeManifestId: "runtime-v1",
      cardVersionIds: ["card-v1"],
      visualPlacementIds: [],
    });
    expect(confirmed.course).toMatchObject({
      id: job.courseVersionId,
      title: "企业 AI 课程",
      chapters: [{ title: "证据优先", lessons: [{ title: "证据验证方法" }] }],
      sources: [{ id: "source-v1" }],
    });
    expect(confirmed.receipt.id).toBe("evidence-confirm");
  });

  it("blocks confirmation while the outline has coverage gaps", async () => {
    const client = helperClient();
    const agent = new HelperCourseAgent(client as unknown as KnowledgeClient);
    const preview = await agent.compose(draft);
    await expect(
      agent.confirm({
        ...preview,
        result: { ...preview.result, blockingGaps: ["缺少真实案例"] },
      }),
    ).rejects.toThrow("未覆盖");
    expect(client.confirmOutline).not.toHaveBeenCalled();
  });
});

describe("LocalCourseAgent fallback", () => {
  it("marks offline generation as legacy-unlinked", async () => {
    const result = await new LocalCourseAgent().generate(
      { title: "演练", audience: "个人", goal: "练习", durationMinutes: 90 },
      [{ id: "source-1", name: "demo.md", kind: "markdown", size: 4, status: "ready", addedAt: timestamp }],
    );
    expect(result.mode).toBe("legacy-unlinked");
  });
});

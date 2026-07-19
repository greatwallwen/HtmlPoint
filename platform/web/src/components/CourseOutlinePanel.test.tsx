import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { CourseCompositionPreview } from "../domain/course-agent";
import { CourseOutlinePanel } from "./CourseOutlinePanel";

const timestamp = "2026-07-19T02:00:00Z";
const digest = "a".repeat(64);
const preview = {
  draft: {
    title: "AI 课程",
    audience: "产品团队",
    learningGoals: ["建立证据习惯", "设计工作流"],
    durationMinutes: 90,
    requiredTagIds: ["topic:evidence"],
    excludedTagIds: [],
    usageScope: "internal",
    includeCardVersionIds: [],
    excludeCardVersionIds: [],
    requireVisualRefs: true,
    requireDatasetRefs: false,
  },
  requirementId: "requirement-1",
  inputDigest: digest,
  indexSnapshotId: "snapshot-1",
  indexSnapshotDigest: digest,
  retrievalMode: "fts-degraded",
  result: {
    operationId: "operation-1",
    operationStatus: "committed",
    requirementId: "requirement-1",
    outlineVersionId: "outline-v1",
    outlineDigest: digest,
    indexSnapshotId: "snapshot-1",
    blockingGaps: [],
    compositionEvidenceId: "evidence-compose",
    retrievalEvidenceIds: ["evidence-retrieval"],
    confirmationSummary: {
      usageScope: "internal",
      outlineVersionId: "outline-v1",
      outlineDigest: digest,
      requirementId: "requirement-1",
      confirmationDigest: "b".repeat(64),
      text: "确认课程大纲",
    },
    outline: {
      schemaVersion: 1,
      logicalId: "outline",
      versionId: "outline-v1",
      revision: 1,
      contentDigest: digest,
      supersedesVersionId: null,
      createdAt: timestamp,
      createdBy: { actorType: "human", actorId: "local-user", displayName: null },
      requirementId: "requirement-1",
      chapters: [
        {
          schemaVersion: 1,
          chapterId: "chapter-1",
          title: "证据优先",
          objective: "验证结果",
          placements: [
            {
              schemaVersion: 1,
              placementId: "placement-1",
              cardVersionId: "card-v1",
              chapterId: "chapter-1",
              lessonId: "lesson-1",
              purpose: "core",
              allocatedMinutes: 90,
            },
          ],
        },
      ],
      uncoveredGoals: [],
      retrievalEvidenceId: "evidence-retrieval",
      indexSnapshotId: "snapshot-1",
    },
  },
  evidence: {
    evidenceId: "evidence-compose",
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
  },
} as CourseCompositionPreview;

describe("CourseOutlinePanel", () => {
  it("shows bounded retrieval evidence, coverage, cards, minutes, and adjustment controls", async () => {
    const user = userEvent.setup();
    const onCardDisposition = vi.fn();
    const onConfirm = vi.fn();
    render(
      <CourseOutlinePanel
        preview={preview}
        stale={false}
        onCardDisposition={onCardDisposition}
        onConfirm={onConfirm}
      />,
    );

    expect(screen.getByText("仅全文检索（降级模式）")).toBeVisible();
    expect(screen.getByText("2/2")).toBeVisible();
    expect(screen.getByText("90 分钟")).toBeVisible();
    expect(screen.getAllByText("card-v1").length).toBeGreaterThan(0);
    expect(screen.getByText("topic:evidence")).toBeVisible();
    expect(screen.getByText("evidence-compose")).toBeVisible();
    expect(screen.getByText("使用已发布知识卡与当前索引快照")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "固定选用" }));
    expect(onCardDisposition).toHaveBeenCalledWith("card-v1", "include");
    await user.click(screen.getByRole("button", { name: "确认大纲并创建课程" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("invalidates confirmation when the draft changed or gaps remain", () => {
    const { rerender } = render(
      <CourseOutlinePanel
        preview={preview}
        stale
        onCardDisposition={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByText("需求或卡片选择已更改，请重新组合后再确认。")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认大纲并创建课程" })).toBeDisabled();

    rerender(
      <CourseOutlinePanel
        preview={{ ...preview, result: { ...preview.result, blockingGaps: ["缺少案例"] } }}
        stale={false}
        onCardDisposition={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );
    expect(screen.getByText("缺少案例")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认大纲并创建课程" })).toBeDisabled();
  });
});

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  HelperCourseAgent,
  type CourseCompositionPreview,
} from "../domain/course-agent";
import type { KnowledgeClient } from "../services/knowledge-client";
import {
  WorkspaceProvider,
  createFreshWorkspaceState,
} from "../state/workspace";
import { GenerateStep } from "./GenerateStep";

const timestamp = "2026-07-19T02:00:00Z";
const digest = "a".repeat(64);
const preview = {
  draft: {
    title: "个人 AI 课程",
    audience: "产品团队",
    learningGoals: ["建立证据习惯"],
    durationMinutes: 90,
    requiredTagIds: [],
    excludedTagIds: [],
    usageScope: "internal",
    includeCardVersionIds: [],
    excludeCardVersionIds: [],
    requireVisualRefs: false,
    requireDatasetRefs: false,
  },
  requirementId: "requirement-1",
  inputDigest: digest,
  indexSnapshotId: "snapshot-1",
  indexSnapshotDigest: digest,
  retrievalMode: "hybrid",
  result: {
    operationId: "compose-1",
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
          objective: "建立验证习惯",
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

function state() {
  const value = createFreshWorkspaceState();
  return {
    ...value,
    step: "generate" as const,
    brief: {
      title: "个人 AI 课程",
      audience: "",
      goal: "",
      durationMinutes: 90,
    },
    course: {
      ...value.course,
      title: "个人 AI 课程",
      sources: [
        {
          id: "source-1",
          name: "demo.md",
          kind: "markdown" as const,
          size: 4,
          status: "ready" as const,
          addedAt: timestamp,
        },
      ],
    },
  };
}

afterEach(() => vi.restoreAllMocks());

describe("GenerateStep", () => {
  it("submits the complete requirement, invalidates changed previews, and confirms explicitly", async () => {
    const user = userEvent.setup();
    const compose = vi
      .spyOn(HelperCourseAgent.prototype, "compose")
      .mockResolvedValue(preview);
    const confirm = vi
      .spyOn(HelperCourseAgent.prototype, "confirm")
      .mockResolvedValue({
        course: {
          schemaVersion: 1,
          id: "course-v1",
          title: "个人 AI 课程",
          audience: "产品团队",
          goal: "建立证据习惯",
          durationMinutes: 90,
          chapters: [
            {
              id: "chapter-1",
              title: "证据优先",
              objective: "建立验证习惯",
              lessons: [
                {
                  id: "placement-1",
                  title: "验证方法",
                  summary: "知识卡 card-v1",
                  durationMinutes: 90,
                  sourceIds: [],
                  status: "grounded",
                },
              ],
            },
          ],
          sources: [],
          updatedAt: timestamp,
        },
        receipt: {
          id: "evidence-confirm",
          courseId: "course-v1",
          kind: "generation",
          createdAt: timestamp,
          inputDigest: digest,
          summary: "已确认",
          checks: [],
        },
        governed: {
          requirementId: "requirement-1",
          outlineVersionId: "outline-v1",
          courseVersionId: "course-v1",
          slideDeckId: "deck-v1",
          runtimeManifestId: "runtime-v1",
          cardVersionIds: ["card-v1"],
          visualPlacementIds: [],
        },
        result: {} as never,
        evidence: preview.evidence,
      });
    const storage = new Map<string, string>();
    const localStorage = {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    };
    const knowledgeClient = {
      getSummary: vi.fn().mockResolvedValue({
        schemaVersion: 1,
        sourceCount: 1,
        publishedCardCount: 1,
        reviewTaskCount: 0,
        retrievalMode: "hybrid",
        indexSnapshotId: "snapshot-1",
        indexSnapshotDigest: digest,
        indexState: "ready",
        tagLabels: [],
        tagOptions: [],
        updatedAt: timestamp,
      }),
    } as unknown as KnowledgeClient;
    render(
      <WorkspaceProvider initialState={state()} storage={localStorage}>
        <GenerateStep knowledgeClient={knowledgeClient} />
      </WorkspaceProvider>,
    );

    await user.type(screen.getByLabelText("课程受众"), "产品团队");
    await user.type(screen.getByLabelText("课程目标"), "建立证据习惯");
    await user.selectOptions(screen.getByLabelText("使用范围"), "internal");
    await user.click(screen.getByRole("button", { name: "组合课程大纲" }));

    expect(await screen.findByRole("heading", { name: "可调整课程大纲" })).toBeVisible();
    expect(compose).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "个人 AI 课程",
        audience: "产品团队",
        learningGoals: ["建立证据习惯"],
        usageScope: "internal",
      }),
    );
    await user.type(screen.getByLabelText("课程名称"), "·新版");
    expect(screen.getByText("需求或卡片选择已更改，请重新组合后再确认。")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认大纲并创建课程" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新组合大纲" }));
    await waitFor(() => expect(compose).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "确认大纲并创建课程" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    await waitFor(() => {
      const serialized = [...storage.values()].at(-1) ?? "";
      expect(serialized).toContain('"courseVersionId":"course-v1"');
      expect(serialized).not.toContain("知识卡 card-v1");
    });
  });

  it("labels the no-Helper path as a non-publishable offline rehearsal", () => {
    render(
      <WorkspaceProvider initialState={state()} storage={{ getItem: () => null, setItem: vi.fn() }}>
        <GenerateStep />
      </WorkspaceProvider>,
    );
    expect(
      screen.getByText("离线演练模式：生成结果不会关联知识索引，也不可发布。"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "生成课程结构" })).toBeVisible();
  });
});

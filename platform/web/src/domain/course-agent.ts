import {
  createId,
  type CourseBrief,
  type CourseDocument,
  type EvidenceReceipt,
  type SourceAsset,
} from "./course";
import type { GovernedWorkspaceBindings } from "./course";
import type { KnowledgeSummary } from "./knowledge";
import type { CourseProjectionResponse, Evidence } from "./helper-contracts-schema";
import type { KnowledgeClient } from "../services/knowledge-client";
import type { z } from "zod";
import {
  courseComposeResultSchema,
  outlineConfirmResultSchema,
} from "./helper-contracts-schema";
import { stableDigest, validateCourse } from "./validation";

export interface GeneratedCourse {
  course: CourseDocument;
  receipt: EvidenceReceipt;
  mode?: "legacy-unlinked";
}

export interface CourseAgent {
  generate(brief: CourseBrief, sources: SourceAsset[]): Promise<GeneratedCourse>;
  applyIntent(
    course: CourseDocument,
    intent: string,
    chapterId?: string,
  ): Promise<{
    course: CourseDocument;
    receipt: EvidenceReceipt;
    message: string;
  }>;
}

const courseStructure = [
  {
    title: "为什么现在需要 AI",
    objective: "建立共同认知",
    lessons: ["AI 的发展与现状", "企业面临的变化", "AI 能为企业带来什么"],
  },
  {
    title: "从任务到工作流",
    objective: "把 AI 放进真实工作",
    lessons: ["识别高价值任务", "任务拆解与流程化", "工具选择与组合"],
  },
  {
    title: "建立验证习惯",
    objective: "用证据判断结果",
    lessons: ["输出不等于结果", "验证方法与改进"],
  },
] as const;

const removeFinalExtension = (name: string): string => {
  const separatorIndex = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  const extensionIndex = name.lastIndexOf(".");
  return extensionIndex > separatorIndex + 1 ? name.slice(0, extensionIndex) : name;
};

const distributeDurations = (lessonCount: number, target: number): number[] => {
  const validLessonCount = Number.isInteger(lessonCount) && lessonCount > 0;
  const globallyValid =
    Number.isInteger(target) && target > 0 && target <= 480 && target % 5 === 0;
  const feasible =
    validLessonCount && target >= lessonCount * 5 && target <= lessonCount * 90;

  if (!globallyValid || !feasible) {
    throw new Error("课程时长必须为 5 的倍数、不超过 480 分钟，并适配课节数量。");
  }

  const base = Math.floor(target / lessonCount / 5) * 5;
  const remainder = target - base * lessonCount;
  const finalDuration = base + remainder;

  if (base < 5 || base > 90 || finalDuration > 90) {
    throw new Error("课程时长分配后每节必须介于 5 到 90 分钟。");
  }

  const durations = Array.from({ length: lessonCount }, () => base);
  durations[lessonCount - 1] = finalDuration;
  return durations;
};

const generationReceipt = async (
  course: CourseDocument,
  input: unknown,
  summary: string,
  check: EvidenceReceipt["checks"][number],
): Promise<EvidenceReceipt> => ({
  id: createId("evidence"),
  courseId: course.id,
  kind: "generation",
  createdAt: new Date().toISOString(),
  inputDigest: await stableDigest(input),
  summary,
  checks: [check],
});

export type CourseUsageScope = "private-training" | "internal" | "public";

export interface CourseRequirementDraft {
  title: string;
  audience: string;
  learningGoals: string[];
  durationMinutes: number;
  requiredTagIds: string[];
  excludedTagIds: string[];
  usageScope: CourseUsageScope;
  includeCardVersionIds: string[];
  excludeCardVersionIds: string[];
  requireVisualRefs: boolean;
  requireDatasetRefs: boolean;
}

type ComposeResult = z.infer<typeof courseComposeResultSchema>;
type ConfirmResult = z.infer<typeof outlineConfirmResultSchema>;

export interface CourseCompositionPreview {
  readonly draft: CourseRequirementDraft;
  readonly requirementId: string;
  readonly inputDigest: string;
  readonly indexSnapshotId: string;
  readonly indexSnapshotDigest: string;
  readonly retrievalMode: "hybrid" | "fts-degraded";
  readonly result: ComposeResult;
  readonly evidence: Evidence;
}

export interface ConfirmedGovernedCourse extends GeneratedCourse {
  mode?: never;
  governed: GovernedWorkspaceBindings;
  result: ConfirmResult;
  evidence: Evidence;
}

function normalizedRequirementDraft(
  draft: CourseRequirementDraft,
): CourseRequirementDraft {
  const normalized = {
    ...draft,
    title: draft.title.trim(),
    audience: draft.audience.trim(),
    learningGoals: draft.learningGoals.map((goal) => goal.trim()).filter(Boolean),
    requiredTagIds: [...new Set(draft.requiredTagIds)].sort(),
    excludedTagIds: [...new Set(draft.excludedTagIds)].sort(),
    includeCardVersionIds: [...new Set(draft.includeCardVersionIds)].sort(),
    excludeCardVersionIds: [...new Set(draft.excludeCardVersionIds)].sort(),
  };
  if (!normalized.title || !normalized.audience || normalized.learningGoals.length === 0) {
    throw new Error("课程名称、受众和至少一个学习目标不能为空。");
  }
  if (
    normalized.learningGoals.length > 20 ||
    normalized.durationMinutes < 40 ||
    normalized.durationMinutes > 480 ||
    normalized.durationMinutes % 5 !== 0
  ) {
    throw new Error("学习目标或课程时长超出允许范围。");
  }
  if (
    normalized.requiredTagIds.some((item) => normalized.excludedTagIds.includes(item)) ||
    normalized.includeCardVersionIds.some((item) => normalized.excludeCardVersionIds.includes(item))
  ) {
    throw new Error("必选与排除项不能重叠。");
  }
  return normalized;
}

function evidenceReceipt(
  courseId: string,
  confirmationDigest: string,
  evidence: Evidence,
): EvidenceReceipt {
  return {
    id: evidence.evidenceId,
    courseId,
    kind: "generation",
    createdAt: evidence.finishedAt,
    inputDigest: confirmationDigest,
    summary: "已通过本地 Helper 确认证据绑定的课程大纲。",
    checks: evidence.checks.map((check) => ({
      id: check.code,
      level:
        check.status === "passed"
          ? "pass"
          : check.status === "failed"
            ? "error"
            : "warning",
      message: check.message,
    })),
  };
}

function flattenSlideNodes(nodes: ConfirmResult["slideDeck"]["nodes"]) {
  const flattened: ConfirmResult["slideDeck"]["nodes"] = [];
  const stack = [...nodes].reverse();
  while (stack.length > 0) {
    const node = stack.pop()!;
    flattened.push(node);
    stack.push(...[...node.children].reverse());
  }
  return flattened;
}

function projectConfirmedCourse(
  preview: CourseCompositionPreview,
  confirmed: ConfirmResult,
  evidence: Evidence,
): CourseDocument {
  const nodes = flattenSlideNodes(confirmed.slideDeck.nodes);
  const sourceIds = [
    ...new Set(nodes.flatMap((node) => node.sourceVersionIds)),
  ];
  const chapters = preview.result.outline.chapters.map((chapter) => ({
    id: chapter.chapterId,
    title: chapter.title,
    objective: chapter.objective,
    lessons: chapter.placements.map((placement, index) => {
      const relatedNodes = nodes.filter((node) =>
        node.placementIds.includes(placement.placementId),
      );
      const title =
        relatedNodes.find(
          (node) =>
            (node.nodeType === "heading" || node.nodeType === "title") &&
            node.text?.trim(),
        )?.text?.trim() ?? `知识单元 ${index + 1}`;
      const lessonSources = [
        ...new Set(relatedNodes.flatMap((node) => node.sourceVersionIds)),
      ];
      return {
        id: placement.placementId,
        title,
        summary: `知识卡 ${placement.cardVersionId} · ${placement.purpose}`,
        durationMinutes: Math.min(90, placement.allocatedMinutes),
        sourceIds: lessonSources,
        status: "grounded" as const,
      };
    }),
  }));
  return {
    schemaVersion: 1,
    id: confirmed.courseVersionId,
    title: preview.draft.title,
    audience: preview.draft.audience,
    goal: preview.draft.learningGoals.join("；"),
    durationMinutes: preview.draft.durationMinutes,
    chapters,
    sources: sourceIds.map((sourceId) => ({
      id: sourceId,
      name: `已治理来源 ${sourceId}`,
      kind: "note" as const,
      size: 0,
      status: "ready" as const,
      addedAt: evidence.finishedAt,
    })),
    updatedAt: evidence.finishedAt,
  };
}

export function projectReopenedCourse(
  reopened: CourseProjectionResponse,
): CourseDocument {
  const nodes = flattenSlideNodes(reopened.slideDeck.nodes);
  const sourceIds = [...new Set(nodes.flatMap((node) => node.sourceVersionIds))];
  const chapters = reopened.outline.chapters.map((chapter) => ({
    id: chapter.chapterId,
    title: chapter.title,
    objective: chapter.objective,
    lessons: chapter.placements.map((placement, index) => {
      const relatedNodes = nodes.filter((node) =>
        node.placementIds.includes(placement.placementId),
      );
      const title = relatedNodes.find(
        (node) =>
          (node.nodeType === "heading" || node.nodeType === "title") &&
          node.text?.trim(),
      )?.text?.trim() ?? `知识单元 ${index + 1}`;
      return {
        id: placement.placementId,
        title,
        summary: `知识卡 ${placement.cardVersionId} · ${placement.purpose}`,
        durationMinutes: Math.min(90, placement.allocatedMinutes),
        sourceIds: [...new Set(relatedNodes.flatMap((node) => node.sourceVersionIds))],
        status: "grounded" as const,
      };
    }),
  }));
  return {
    schemaVersion: 1,
    id: reopened.courseVersionId,
    title: reopened.requirement.title,
    audience: reopened.requirement.audience,
    goal: reopened.requirement.learningGoals.join("；"),
    durationMinutes: reopened.requirement.durationMinutes,
    chapters,
    sources: sourceIds.map((sourceId) => ({
      id: sourceId,
      name: `已治理来源 ${sourceId}`,
      kind: "note" as const,
      size: 0,
      status: "ready" as const,
      addedAt: reopened.slideDeck.createdAt,
    })),
    updatedAt: reopened.slideDeck.createdAt,
  };
}

export class HelperCourseAgent {
  readonly #client: Pick<
    KnowledgeClient,
    "getSummary" | "composeCourse" | "confirmOutline"
  >;
  #lastComposition?: {
    inputDigest: string;
    promise: Promise<CourseCompositionPreview>;
  };

  constructor(
    client: Pick<KnowledgeClient, "getSummary" | "composeCourse" | "confirmOutline">,
  ) {
    this.#client = client;
  }

  async compose(draft: CourseRequirementDraft): Promise<CourseCompositionPreview> {
    const normalized = normalizedRequirementDraft(draft);
    const summary: KnowledgeSummary = await this.#client.getSummary();
    if (
      summary.indexSnapshotId == null ||
      summary.indexSnapshotDigest == null ||
      summary.indexState === "unavailable"
    ) {
      throw new Error("知识索引尚未就绪，请先完成知识卡发布与索引。");
    }
    const inputDigest = await stableDigest({
      draft: normalized,
      indexSnapshotId: summary.indexSnapshotId,
      indexSnapshotDigest: summary.indexSnapshotDigest,
    });
    if (this.#lastComposition?.inputDigest === inputDigest) {
      return this.#lastComposition.promise;
    }

    const promise = this.#composeFresh(normalized, summary, inputDigest);
    this.#lastComposition = { inputDigest, promise };
    void promise.catch(() => {
      if (this.#lastComposition?.promise === promise) {
        this.#lastComposition = undefined;
      }
    });
    return promise;
  }

  async #composeFresh(
    draft: CourseRequirementDraft,
    summary: KnowledgeSummary & {
      indexSnapshotId?: string | null;
      indexSnapshotDigest?: string | null;
    },
    inputDigest: string,
  ): Promise<CourseCompositionPreview> {
    const requirementId = createId("requirement");
    const outlineLogicalId = createId("outline");
    const outlineVersionId = createId("outline-version");
    const operationId = createId("compose-operation");
    const requirement = {
      requirementId,
      title: draft.title,
      audience: draft.audience,
      learningGoals: draft.learningGoals,
      durationMinutes: draft.durationMinutes,
      requiredTagIds: draft.requiredTagIds,
      excludedTagIds: draft.excludedTagIds,
      usageScope: draft.usageScope,
    };
    const options = {
      audienceTagId:
        draft.requiredTagIds.find((item) => item.startsWith("audience:")) ?? null,
      difficultyTagId:
        draft.requiredTagIds.find((item) => item.startsWith("difficulty:")) ?? null,
      indexSnapshotId: summary.indexSnapshotId!,
      includeCardVersionIds: draft.includeCardVersionIds,
      excludeCardVersionIds: draft.excludeCardVersionIds,
      requireVisualRefs: draft.requireVisualRefs,
      requireDatasetRefs: draft.requireDatasetRefs,
    };
    const requestDigest = await stableDigest({
      kind: "course_compose",
      options,
      outlineLogicalId,
      outlineRevision: 1,
      outlineVersionId,
      requirement,
    });
    const response = await this.#client.composeCourse({
      type: "course_compose",
      operationId,
      requestDigest,
      actor: { actorType: "human", actorId: "local-user" },
      requirement,
      options,
      outlineLogicalId,
      outlineVersionId,
      outlineRevision: 1,
    });
    return {
      draft,
      requirementId,
      inputDigest,
      indexSnapshotId: summary.indexSnapshotId!,
      indexSnapshotDigest: summary.indexSnapshotDigest!,
      retrievalMode: summary.retrievalMode,
      result: response.result,
      evidence: response.evidence,
    };
  }

  async confirm(preview: CourseCompositionPreview): Promise<ConfirmedGovernedCourse> {
    if (preview.result.blockingGaps.length > 0) {
      throw new Error("大纲仍有未覆盖目标，暂不能确认。");
    }
    const confirmationId = createId("confirmation");
    const courseLogicalId = createId("course");
    const courseVersionId = createId("course-version");
    const operationId = createId("confirm-operation");
    const summary = preview.result.confirmationSummary;
    const requestDigest = await stableDigest({
      kind: "course_outline_confirm",
      confirmationDigest: summary.confirmationDigest,
      confirmationId,
      courseLogicalId,
      courseRevision: 1,
      courseVersionId,
      expectedOutlineDigest: preview.result.outlineDigest,
      outlineVersionId: preview.result.outlineVersionId,
      requirementId: preview.requirementId,
    });
    const response = await this.#client.confirmOutline({
      type: "course_outline_confirm",
      operationId,
      requestDigest,
      actor: { actorType: "human", actorId: "local-user" },
      confirmationId,
      requirementId: preview.requirementId,
      outlineVersionId: preview.result.outlineVersionId,
      expectedOutlineDigest: preview.result.outlineDigest,
      confirmationDigest: summary.confirmationDigest,
      courseLogicalId,
      courseVersionId,
      courseRevision: 1,
    });
    const course = projectConfirmedCourse(preview, response.result, response.evidence);
    const cardVersionIds = [
      ...new Set(
        preview.result.outline.chapters.flatMap((chapter) =>
          chapter.placements.map((placement) => placement.cardVersionId),
        ),
      ),
    ];
    const visualPlacementIds = [
      ...new Set(
        flattenSlideNodes(response.result.slideDeck.nodes).flatMap((node) =>
          node.assetBindings.map((binding) => binding.visualPlacementId),
        ),
      ),
    ];
    return {
      course,
      receipt: evidenceReceipt(
        course.id,
        response.result.confirmationDigest,
        response.evidence,
      ),
      governed: {
        requirementId: preview.requirementId,
        outlineVersionId: response.result.outlineVersionId,
        courseVersionId: response.result.courseVersionId,
        slideDeckId: response.result.slideDeckId,
        runtimeManifestId: response.result.runtimeManifestId,
        cardVersionIds,
        visualPlacementIds,
      },
      result: response.result,
      evidence: response.evidence,
    };
  }
}

export class LocalCourseAgent implements CourseAgent {
  async generate(brief: CourseBrief, sources: SourceAsset[]): Promise<GeneratedCourse> {
    const trimmedBrief: CourseBrief = {
      title: brief.title.trim(),
      audience: brief.audience.trim(),
      goal: brief.goal.trim(),
      durationMinutes: brief.durationMinutes,
    };

    if (!trimmedBrief.title) {
      throw new Error("课程标题不能为空。");
    }
    if (!trimmedBrief.audience) {
      throw new Error("课程受众不能为空。");
    }
    if (!trimmedBrief.goal) {
      throw new Error("课程目标不能为空。");
    }

    const readySources = sources.filter((source) => source.status === "ready");
    if (readySources.length === 0) {
      throw new Error("课程生成至少需要一份已就绪的来源。");
    }

    const durations = distributeDurations(8, trimmedBrief.durationMinutes);
    let lessonIndex = 0;
    const course: CourseDocument = {
      schemaVersion: 1,
      id: createId("course"),
      title: trimmedBrief.title,
      audience: trimmedBrief.audience,
      goal: trimmedBrief.goal,
      durationMinutes: trimmedBrief.durationMinutes,
      chapters: courseStructure.map((chapter) => ({
        id: createId("chapter"),
        title: chapter.title,
        objective: chapter.objective,
        lessons: chapter.lessons.map((title) => {
          const currentIndex = lessonIndex;
          const source = readySources[currentIndex % readySources.length];
          lessonIndex += 1;
          return {
            id: createId("lesson"),
            title,
            summary: `围绕“${trimmedBrief.goal}”，结合来源“${removeFinalExtension(source.name)}”设计本节。`,
            durationMinutes: durations[currentIndex],
            sourceIds: [source.id],
            status: "grounded",
          };
        }),
      })),
      sources: [...sources],
      updatedAt: new Date().toISOString(),
    };

    return {
      course,
      mode: "legacy-unlinked",
      receipt: await generationReceipt(
        course,
        {
          brief: trimmedBrief,
          sources: sources.map(({ id, name, status }) => ({ id, name, status })),
        },
        `已基于 ${readySources.length} 份可用资料生成 3 章 8 节课程。`,
        {
          id: "generation-structure",
          level: "pass",
          message: "课程结构已生成。",
        },
      ),
    };
  }

  async applyIntent(
    course: CourseDocument,
    intent: string,
    chapterId?: string,
  ): Promise<{
    course: CourseDocument;
    receipt: EvidenceReceipt;
    message: string;
  }> {
    const inputCourse = course;
    const trimmedIntent = intent.trim();
    const durationMatch = trimmedIntent.match(/缩短课程到\s*(\d+)\s*分钟/);

    if (durationMatch) {
      const durationMinutes = Number(durationMatch[1]);
      if (durationMinutes < 40 || durationMinutes > 480) {
        throw new Error("课程时长必须介于 40 到 480 分钟。");
      }
      const lessonCount = course.chapters.reduce(
        (count, chapter) => count + chapter.lessons.length,
        0,
      );
      const durations = distributeDurations(lessonCount, durationMinutes);
      let lessonIndex = 0;
      const modifiedCourse: CourseDocument = {
        ...course,
        durationMinutes,
        chapters: course.chapters.map((chapter) => ({
          ...chapter,
          lessons: chapter.lessons.map((lesson) => ({
            ...lesson,
            durationMinutes: durations[lessonIndex++],
          })),
        })),
        updatedAt: new Date().toISOString(),
      };
      const message = `已将课程时长调整为 ${durationMinutes} 分钟。`;

      return {
        course: modifiedCourse,
        receipt: await generationReceipt(
          modifiedCourse,
          { course: inputCourse, intent: trimmedIntent },
          message,
          {
            id: "duration-adjusted",
            level: "pass",
            message: "课程时长已调整。",
          },
        ),
        message,
      };
    }

    if (trimmedIntent.includes("补充案例")) {
      const chapterIndex = course.chapters.findIndex(
        (chapter) => chapter.id === chapterId,
      );
      if (chapterIndex < 0) {
        throw new Error("补充案例需要指定一个已存在的章节。");
      }

      const source = course.sources.find((candidate) => candidate.status === "ready");
      if (!source) {
        throw new Error("补充案例至少需要一份已就绪的课程来源。");
      }

      const chapters = course.chapters.map((chapter, index) =>
        index === chapterIndex
          ? {
              ...chapter,
              lessons: [
                ...chapter.lessons,
                {
                  id: createId("lesson"),
                  title: "业务案例：从资料到行动",
                  summary: `围绕“${course.goal.trim()}”，结合来源“${removeFinalExtension(source.name)}”设计业务案例。`,
                  durationMinutes: 15,
                  sourceIds: [source.id],
                  status: "grounded" as const,
                },
              ],
            }
          : chapter,
      );
      const modifiedCourse: CourseDocument = {
        ...course,
        durationMinutes: Math.min(480, course.durationMinutes + 15),
        chapters,
        updatedAt: new Date().toISOString(),
      };
      const message = "已为当前章节补充一个业务案例。";

      return {
        course: modifiedCourse,
        receipt: await generationReceipt(
          modifiedCourse,
          { course: inputCourse, intent: trimmedIntent },
          message,
          {
            id: "case-added",
            level: "pass",
            message: "业务案例已补充。",
          },
        ),
        message,
      };
    }

    if (trimmedIntent.includes("检查来源覆盖")) {
      return {
        course,
        receipt: await validateCourse(course),
        message: "已完成来源覆盖检查",
      };
    }

    const message =
      "我没有修改课程。可以尝试“缩短课程到 90 分钟”“为本章补充案例”或“检查来源覆盖”。";
    return {
      course,
      receipt: await generationReceipt(
        course,
        { course, intent: trimmedIntent },
        "未识别可执行意图，课程未修改。",
        {
          id: "intent-unrecognized",
          level: "warning",
          message: "未识别可执行意图。",
        },
      ),
      message,
    };
  }
}

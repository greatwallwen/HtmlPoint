import { act, render, waitFor } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { describe, expect, it, vi } from "vitest";

import type {
  CourseBrief,
  CourseDocument,
  EvidenceReceipt,
  SourceAsset,
} from "../domain/course";
import type { CourseAgent, GeneratedCourse } from "../domain/course-agent";
import { stableDigest } from "../domain/validation";
import * as workspaceModule from "./workspace";
import {
  WORKSPACE_STORAGE_KEY,
  type WorkspaceSnapshot,
} from "./storage";
import {
  WorkspaceProvider,
  createFreshWorkspaceState,
  hydrateWorkspaceState,
  useWorkspace,
  workspaceReducer,
  type WorkspaceAction,
  type WorkspaceContextValue,
  type WorkspaceState,
} from "./workspace";

const EARLIER = "2026-07-15T08:00:00.000Z";
const NOW = "2026-07-15T09:00:00.000Z";

const brief: CourseBrief = {
  title: "企业 AI 实战课",
  audience: "产品团队",
  goal: "建立可验证的 AI 工作流",
  durationMinutes: 120,
};

const source = (patch: Partial<SourceAsset> = {}): SourceAsset => ({
  id: "source-a",
  name: "evidence.md",
  kind: "markdown",
  size: 42,
  status: "ready",
  extractedText: "Evidence first.",
  addedAt: EARLIER,
  ...patch,
});

const course = (patch: Partial<CourseDocument> = {}): CourseDocument => ({
  schemaVersion: 1,
  id: "course-1",
  title: brief.title,
  audience: brief.audience,
  goal: brief.goal,
  durationMinutes: brief.durationMinutes,
  chapters: [
    {
      id: "chapter-1",
      title: "第一章",
      objective: "建立共识",
      lessons: [
        {
          id: "lesson-1",
          title: "证据优先",
          summary: "用来源支撑结论。",
          durationMinutes: 60,
          sourceIds: ["source-a", "source-b"],
          status: "grounded",
        },
        {
          id: "lesson-2",
          title: "验证输出",
          summary: "保留可复查证据。",
          durationMinutes: 30,
          sourceIds: ["source-a"],
          status: "grounded",
        },
        {
          id: "lesson-3",
          title: "待完善课节",
          summary: "继续完善内容。",
          durationMinutes: 30,
          sourceIds: ["source-a"],
          status: "draft",
        },
      ],
    },
  ],
  sources: [source(), source({ id: "source-b", name: "workflow.txt", kind: "text" })],
  updatedAt: EARLIER,
  ...patch,
});

const receipt = (patch: Partial<EvidenceReceipt> = {}): EvidenceReceipt => ({
  id: "receipt-1",
  courseId: "course-1",
  kind: "generation",
  createdAt: EARLIER,
  inputDigest: "sha256:course-1",
  summary: "课程生成完成。",
  checks: [{ id: "generated", level: "pass", message: "结构已生成。" }],
  ...patch,
});

const validationReceipt = (
  level: "pass" | "warning" | "error" = "pass",
  patch: Partial<EvidenceReceipt> = {},
): EvidenceReceipt =>
  receipt({
    id: `receipt-validation-${level}`,
    kind: "validation",
    summary: `课程校验完成：${level === "error" ? 1 : 0} 个错误，${level === "warning" ? 1 : 0} 个警告。`,
    checks: [
      {
        id: `check-${level}`,
        level,
        message: `${level} finding`,
      },
    ],
    ...patch,
  });

const canTeach = (workspaceState: WorkspaceState): boolean =>
  (
    workspaceModule as unknown as {
      canTeach(state: WorkspaceState): boolean;
    }
  ).canTeach(workspaceState);

const state = (patch: Partial<WorkspaceState> = {}): WorkspaceState => ({
  step: "generate",
  course: course(),
  brief: { ...brief },
  receipts: [],
  governed: {
    cardVersionIds: [],
    visualPlacementIds: [],
  },
  selectedChapterId: undefined,
  selectedLessonId: undefined,
  courseRevision: 0,
  generation: "idle",
  validation: "idle",
  validationWarningsAcknowledged: false,
  assistant: "idle",
  validationMessage: undefined,
  assistantMessage: undefined,
  operationError: undefined,
  persistenceWarning: undefined,
  ...patch,
});

const snapshot = (patch: Partial<WorkspaceSnapshot> = {}): WorkspaceSnapshot => ({
  version: 2,
  governed: {
    requirementId: "requirement-1",
    outlineVersionId: "outline-v1",
    courseVersionId: "course-v1",
    slideDeckId: "deck-v1",
    runtimeManifestId: "runtime-v1",
    cardVersionIds: ["card-v1"],
    visualPlacementIds: ["visual-placement-1"],
  },
  view: {
    step: "edit",
    selectedChapterId: "chapter-1",
    selectedLessonId: "lesson-1",
  },
  savedAt: NOW,
  ...patch,
});

class MapStorage implements Pick<Storage, "getItem" | "setItem" | "removeItem"> {
  readonly values = new Map<string, string>();
  readonly getItemCalls: string[] = [];
  readonly setItemValues: Array<{ key: string; value: string }> = [];

  getItem(key: string): string | null {
    this.getItemCalls.push(key);
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.setItemValues.push({ key, value });
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

class ControlledAgent implements CourseAgent {
  readonly inputs: Array<{ brief: CourseBrief; sources: SourceAsset[] }> = [];
  readonly intentInputs: Array<{
    course: CourseDocument;
    intent: string;
    chapterId?: string;
  }> = [];

  constructor(
    private readonly run: () => Promise<GeneratedCourse>,
    private readonly runIntent: (
      course: CourseDocument,
      intent: string,
      chapterId?: string,
    ) => ReturnType<CourseAgent["applyIntent"]> = async () => {
      throw new Error("not used by workspace generation tests");
    },
  ) {}

  generate(inputBrief: CourseBrief, sources: SourceAsset[]): Promise<GeneratedCourse> {
    this.inputs.push({
      brief: structuredClone(inputBrief),
      sources: structuredClone(sources),
    });
    return this.run();
  }

  applyIntent(
    inputCourse: CourseDocument,
    intent: string,
    chapterId?: string,
  ): ReturnType<CourseAgent["applyIntent"]> {
    this.intentInputs.push({
      course: structuredClone(inputCourse),
      intent,
      chapterId,
    });
    return this.runIntent(inputCourse, intent, chapterId);
  }
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function CaptureWorkspace({
  onValue,
}: {
  onValue(value: WorkspaceContextValue): void;
}) {
  const value = useWorkspace();
  useLayoutEffect(() => onValue(value), [onValue, value]);
  return null;
}

function textFile(contents: string, name: string): File {
  const file = new File([contents], name);
  Object.defineProperty(file, "text", {
    value: () => Promise.resolve(contents),
  });
  return file;
}

describe("workspace state", () => {
  it("creates the exact fresh defaults", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const fresh = createFreshWorkspaceState();

      expect(fresh).toEqual({
        step: "import",
        course: {
          schemaVersion: 1,
          id: expect.stringMatching(/^course-[0-9a-f-]{36}$/),
          title: "未命名课程",
          audience: "",
          goal: "",
          durationMinutes: 90,
          chapters: [],
          sources: [],
          updatedAt: NOW,
        },
        brief: {
          title: "企业 AI 入门课",
          audience: "",
          goal: "",
          durationMinutes: 120,
        },
        receipts: [],
        governed: {
          cardVersionIds: [],
          visualPlacementIds: [],
        },
        courseRevision: 0,
        generation: "idle",
        validation: "idle",
        validationWarningsAcknowledged: false,
        assistant: "idle",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("starts a new workspace without retaining prior state", () => {
    const previous = state({
      generation: "error",
      validation: "error",
      validationMessage: "旧验证消息",
      assistant: "success",
      assistantMessage: "旧助手消息",
      operationError: "生成失败",
      persistenceWarning: "保存失败",
      receipts: [receipt()],
    });
    const before = structuredClone(previous);

    const next = workspaceReducer(previous, { type: "START_NEW" });

    expect(previous).toEqual(before);
    expect(next).not.toBe(previous);
    expect(next.step).toBe("import");
    expect(next.receipts).toEqual([]);
    expect(next.generation).toBe("idle");
    expect(next.validation).toBe("idle");
    expect(next.validationWarningsAcknowledged).toBe(false);
    expect(next.validationMessage).toBeUndefined();
    expect(next.assistant).toBe("idle");
    expect(next.assistantMessage).toBeUndefined();
    expect(next.operationError).toBeUndefined();
    expect(next.persistenceWarning).toBeUndefined();
  });

  it("adds new sources, replaces same-ID sources, and keeps the prior state immutable", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const previous = state();
      const before = structuredClone(previous);
      const replacement = source({
        id: "source-a",
        name: "updated.md",
        extractedText: "Updated evidence.",
      });
      const added = source({ id: "source-c", name: "new.pdf", kind: "pdf" });

      const next = workspaceReducer(previous, {
        type: "ADD_SOURCES",
        sources: [replacement, added, { ...added, name: "newer.pdf" }],
      });

      expect(previous).toEqual(before);
      expect(next.course).not.toBe(previous.course);
      expect(next.course.sources.map(({ id, name }) => ({ id, name }))).toEqual([
        { id: "source-a", name: "updated.md" },
        { id: "source-b", name: "workflow.txt" },
        { id: "source-c", name: "newer.pdf" },
      ]);
      expect(next.course.updatedAt).toBe(NOW);
    } finally {
      vi.useRealTimers();
    }
  });

  it("removes a linked source and marks only newly ungrounded lessons as needing a source", () => {
    const previous = state();
    const before = structuredClone(previous);

    const next = workspaceReducer(previous, {
      type: "REMOVE_SOURCE",
      sourceId: "source-a",
    });

    expect(previous).toEqual(before);
    expect(next.course.sources.map(({ id }) => id)).toEqual(["source-b"]);
    expect(next.course.chapters[0].lessons).toMatchObject([
      { id: "lesson-1", sourceIds: ["source-b"], status: "grounded" },
      { id: "lesson-2", sourceIds: [], status: "needs-source" },
      { id: "lesson-3", sourceIds: [], status: "draft" },
    ]);
  });

  it("merges a brief patch onto both the brief and draft course", () => {
    const previous = state();
    const before = structuredClone(previous);
    const patch = {
      title: "新课程",
      goal: "新目标",
      durationMinutes: 90,
    };

    const next = workspaceReducer(previous, { type: "SET_BRIEF", patch });

    expect(previous).toEqual(before);
    expect(next.brief).toEqual({ ...brief, ...patch });
    expect(next.course).toMatchObject({
      title: "新课程",
      audience: brief.audience,
      goal: "新目标",
      durationMinutes: 90,
    });
    expect(next.course.updatedAt).not.toBe(EARLIER);
  });

  it("moves to a requested non-teaching step and clears operation errors", () => {
    const previous = state({ operationError: "之前的错误" });

    const next = workspaceReducer(previous, { type: "GO_TO_STEP", step: "edit" });

    expect(next).toEqual({ ...previous, step: "edit", operationError: undefined });
    expect(previous.operationError).toBe("之前的错误");
  });

  it("rejects direct teaching navigation until the current validation gate is open", () => {
    const locked = state({ step: "edit" });
    expect(
      workspaceReducer(locked, { type: "GO_TO_STEP", step: "teach" }).step,
    ).toBe("edit");

    const ready = state({
      step: "edit",
      validation: "success",
      receipts: [validationReceipt("pass")],
    });
    expect(
      workspaceReducer(ready, { type: "GO_TO_STEP", step: "teach" }).step,
    ).toBe("teach");
  });

  it("handles the generation started and completed transitions", () => {
    const previous = state({ operationError: "之前的错误", receipts: [receipt()] });
    const started = workspaceReducer(previous, { type: "GENERATION_STARTED" });
    const generatedCourse = course({ id: "course-generated" });
    const generatedReceipt = receipt({ id: "receipt-generated", courseId: "course-generated" });

    const completed = workspaceReducer(started, {
      type: "GENERATION_COMPLETED",
      course: generatedCourse,
      receipt: generatedReceipt,
    });

    expect(started).toEqual({
      ...previous,
      generation: "running",
      operationError: undefined,
    });
    expect(completed).toMatchObject({
      step: "edit",
      course: generatedCourse,
      generation: "success",
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
    });
    expect(completed.receipts).toEqual([receipt(), generatedReceipt]);
    expect(completed.receipts).not.toBe(started.receipts);
    expect(previous.operationError).toBe("之前的错误");
  });

  it("records a non-empty generation failure without changing course inputs", () => {
    const previous = state({ generation: "running" });
    const before = structuredClone(previous);

    const next = workspaceReducer(previous, {
      type: "GENERATION_FAILED",
      message: "   ",
    });

    expect(previous).toEqual(before);
    expect(next.generation).toBe("error");
    expect(next.operationError?.trim().length).toBeGreaterThan(0);
    expect(next.course).toBe(previous.course);
    expect(next.brief).toBe(previous.brief);
    expect(next.course.sources).toBe(previous.course.sources);
  });

  it("records persistence warnings alone and clears an operation error", () => {
    const previous = state({ operationError: "需要清除" });

    const warned = workspaceReducer(previous, {
      type: "PERSISTENCE_FAILED",
      message: "工作区保存失败。",
    });
    const cleared = workspaceReducer(warned, { type: "CLEAR_OPERATION_ERROR" });

    expect(warned).toEqual({
      ...previous,
      persistenceWarning: "工作区保存失败。",
    });
    expect(cleared).toEqual({ ...warned, operationError: undefined });
  });

  it("selects known chapters with their first lesson and treats unknown chapters as a no-op", () => {
    const inputCourse = course();
    const previous = state({
      course: {
        ...inputCourse,
        chapters: [
          ...inputCourse.chapters,
          {
            id: "chapter-2",
            title: "第二章",
            objective: "继续学习",
            lessons: [],
          },
        ],
      },
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-2",
    });

    const first = workspaceReducer(previous, {
      type: "SELECT_CHAPTER",
      chapterId: "chapter-1",
    });
    const empty = workspaceReducer(first, {
      type: "SELECT_CHAPTER",
      chapterId: "chapter-2",
    });
    const unknown = workspaceReducer(empty, {
      type: "SELECT_CHAPTER",
      chapterId: "missing-chapter",
    });

    expect(first).toMatchObject({
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
    });
    expect(empty).toMatchObject({
      selectedChapterId: "chapter-2",
      selectedLessonId: undefined,
    });
    expect(unknown).toBe(empty);
  });

  it("selects a lesson only when it belongs to the selected chapter", () => {
    const inputCourse = course();
    const previous = state({
      course: {
        ...inputCourse,
        chapters: [
          ...inputCourse.chapters,
          {
            id: "chapter-2",
            title: "第二章",
            objective: "继续学习",
            lessons: [
              {
                id: "lesson-4",
                title: "跨章课节",
                summary: "不能从当前章节直接选中。",
                durationMinutes: 15,
                sourceIds: [],
                status: "needs-source",
              },
            ],
          },
        ],
      },
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
    });

    const selected = workspaceReducer(previous, {
      type: "SELECT_LESSON",
      lessonId: "lesson-2",
    });
    const crossChapter = workspaceReducer(selected, {
      type: "SELECT_LESSON",
      lessonId: "lesson-4",
    });
    const missing = workspaceReducer(selected, {
      type: "SELECT_LESSON",
      lessonId: "missing-lesson",
    });

    expect(selected.selectedLessonId).toBe("lesson-2");
    expect(crossChapter).toBe(selected);
    expect(missing).toBe(selected);
  });

  it("updates lesson fields without trimming or rejecting transient values and stays immutable", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const previous = state();
      const before = structuredClone(previous);

      const next = workspaceReducer(previous, {
        type: "UPDATE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-2",
        patch: {
          title: "  临时标题  ",
          summary: "",
          durationMinutes: -5,
        },
      });

      expect(previous).toEqual(before);
      expect(next.course).not.toBe(previous.course);
      expect(next.course.chapters).not.toBe(previous.course.chapters);
      expect(next.course.chapters[0].lessons[0]).toBe(
        previous.course.chapters[0].lessons[0],
      );
      expect(next.course.chapters[0].lessons[1]).toMatchObject({
        title: "  临时标题  ",
        summary: "",
        durationMinutes: -5,
      });
      expect(next.course.updatedAt).toBe(NOW);
      expect(
        workspaceReducer(previous, {
          type: "UPDATE_LESSON",
          chapterId: "chapter-1",
          lessonId: "missing-lesson",
          patch: { title: "不会应用" },
        }),
      ).toBe(previous);
    } finally {
      vi.useRealTimers();
    }
  });

  it("moves one lesson position while preserving IDs and selection without mutating input", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const previous = state({
        selectedChapterId: "chapter-1",
        selectedLessonId: "lesson-2",
      });
      const before = structuredClone(previous);

      const next = workspaceReducer(previous, {
        type: "MOVE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-2",
        direction: -1,
      });

      expect(previous).toEqual(before);
      expect(next.course.chapters[0].lessons.map(({ id }) => id)).toEqual([
        "lesson-2",
        "lesson-1",
        "lesson-3",
      ]);
      expect(next.selectedChapterId).toBe("chapter-1");
      expect(next.selectedLessonId).toBe("lesson-2");
      expect(next.course.updatedAt).toBe(NOW);
    } finally {
      vi.useRealTimers();
    }
  });

  it("returns the unchanged state when moving beyond a boundary or targeting a missing lesson", () => {
    const previous = state();

    expect(
      workspaceReducer(previous, {
        type: "MOVE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        direction: -1,
      }),
    ).toBe(previous);
    expect(
      workspaceReducer(previous, {
        type: "MOVE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-3",
        direction: 1,
      }),
    ).toBe(previous);
    expect(
      workspaceReducer(previous, {
        type: "MOVE_LESSON",
        chapterId: "chapter-1",
        lessonId: "missing-lesson",
        direction: 1,
      }),
    ).toBe(previous);
  });

  it("toggles lesson sources and derives grounding only from linked ready sources", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const inputCourse = course();
      const previous = state({
        course: {
          ...inputCourse,
          sources: [
            source(),
            source({ id: "source-b", status: "reading" }),
          ],
          chapters: [
            {
              ...inputCourse.chapters[0],
              lessons: inputCourse.chapters[0].lessons.map((lesson, index) =>
                index === 0
                  ? { ...lesson, sourceIds: [], status: "draft" }
                  : lesson,
              ),
            },
          ],
        },
      });
      const before = structuredClone(previous);

      const pendingOnly = workspaceReducer(previous, {
        type: "TOGGLE_LESSON_SOURCE",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        sourceId: "source-b",
      });
      const withReady = workspaceReducer(pendingOnly, {
        type: "TOGGLE_LESSON_SOURCE",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        sourceId: "source-a",
      });
      const readyRemoved = workspaceReducer(withReady, {
        type: "TOGGLE_LESSON_SOURCE",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        sourceId: "source-a",
      });

      expect(previous).toEqual(before);
      expect(pendingOnly.course.chapters[0].lessons[0]).toMatchObject({
        sourceIds: ["source-b"],
        status: "needs-source",
      });
      expect(withReady.course.chapters[0].lessons[0]).toMatchObject({
        sourceIds: ["source-b", "source-a"],
        status: "grounded",
      });
      expect(readyRemoved.course.chapters[0].lessons[0]).toMatchObject({
        sourceIds: ["source-b"],
        status: "needs-source",
      });
      expect(readyRemoved.course.updatedAt).toBe(NOW);
      expect(
        workspaceReducer(previous, {
          type: "TOGGLE_LESSON_SOURCE",
          chapterId: "chapter-1",
          lessonId: "lesson-1",
          sourceId: "missing-source",
        }),
      ).toBe(previous);
      expect(
        workspaceReducer(previous, {
          type: "TOGGLE_LESSON_SOURCE",
          chapterId: "chapter-1",
          lessonId: "missing-lesson",
          sourceId: "source-a",
        }),
      ).toBe(previous);
    } finally {
      vi.useRealTimers();
    }
  });

  it("adds a chapter with localized defaults, a fresh ID, and selected empty state", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const previous = state({
        selectedChapterId: "chapter-1",
        selectedLessonId: "lesson-2",
      });
      const before = structuredClone(previous);

      const next = workspaceReducer(previous, { type: "ADD_CHAPTER" });
      const added = next.course.chapters[1];

      expect(previous).toEqual(before);
      expect(added).toEqual({
        id: expect.stringMatching(/^chapter-[0-9a-f-]{36}$/),
        title: "新章节 2",
        objective: "填写本章学习目标",
        lessons: [],
      });
      expect(added.id).not.toBe("chapter-1");
      expect(next.selectedChapterId).toBe(added.id);
      expect(next.selectedLessonId).toBeUndefined();
      expect(next.course.updatedAt).toBe(NOW);
    } finally {
      vi.useRealTimers();
    }
  });

  it("appends and selects a lesson with localized defaults and ignores an unknown chapter", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    try {
      const previous = state();

      const next = workspaceReducer(previous, {
        type: "ADD_LESSON",
        chapterId: "chapter-1",
      });
      const added = next.course.chapters[0].lessons[3];

      expect(added).toEqual({
        id: expect.stringMatching(/^lesson-[0-9a-f-]{36}$/),
        title: "新小节 4",
        summary: "填写本节内容摘要",
        durationMinutes: 15,
        sourceIds: [],
        status: "needs-source",
      });
      expect(next.selectedChapterId).toBe("chapter-1");
      expect(next.selectedLessonId).toBe(added.id);
      expect(next.course.updatedAt).toBe(NOW);
      expect(
        workspaceReducer(previous, {
          type: "ADD_LESSON",
          chapterId: "missing-chapter",
        }),
      ).toBe(previous);
    } finally {
      vi.useRealTimers();
    }
  });

  it("records validation start, completion receipt, and success message", () => {
    const previous = state({
      validation: "error",
      validationMessage: "旧验证消息",
      operationError: "旧错误",
      validationWarningsAcknowledged: true,
      receipts: [receipt()],
    });
    const validationReceipt = receipt({
      id: "receipt-validation",
      kind: "validation",
    });

    const started = workspaceReducer(previous, { type: "VALIDATION_STARTED" });
    const completed = workspaceReducer(started, {
      type: "VALIDATION_COMPLETED",
      receipt: validationReceipt,
      courseRevision: started.courseRevision,
    });

    expect(started).toEqual({
      ...previous,
      validation: "running",
      validationMessage: undefined,
      validationWarningsAcknowledged: false,
      operationError: undefined,
    });
    expect(completed.validation).toBe("success");
    expect(completed.validationMessage).toBe("课程验证完成。");
    expect(completed.receipts).toEqual([receipt(), validationReceipt]);
    expect(completed.receipts).not.toBe(started.receipts);
    expect(completed.validationWarningsAcknowledged).toBe(false);
  });

  it("invalidates a green validation gate for every structural course edit while retaining receipts", () => {
    const historical = validationReceipt("warning");
    const previous = state({
      step: "edit",
      validation: "success",
      validationMessage: "课程验证完成。",
      validationWarningsAcknowledged: true,
      receipts: [receipt(), historical],
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
    });
    const edits: WorkspaceAction[] = [
      {
        type: "UPDATE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        patch: { title: "编辑后的课节" },
      },
      {
        type: "MOVE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        direction: 1,
      },
      {
        type: "TOGGLE_LESSON_SOURCE",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        sourceId: "source-a",
      },
      { type: "ADD_CHAPTER" },
      { type: "ADD_LESSON", chapterId: "chapter-1" },
    ];

    for (const edit of edits) {
      const next = workspaceReducer(previous, edit);
      expect(next.validation, edit.type).toBe("idle");
      expect(next.validationMessage, edit.type).toBeUndefined();
      expect(next.validationWarningsAcknowledged, edit.type).toBe(false);
      expect(next.receipts, edit.type).toEqual([receipt(), historical]);
    }
  });

  it("gates teaching on the latest successful validation result and explicit warning acknowledgement", () => {
    const errorState = state({
      step: "edit",
      validation: "success",
      receipts: [validationReceipt("error")],
    });
    expect(canTeach(errorState)).toBe(false);

    const warningState = state({
      step: "edit",
      validation: "success",
      receipts: [validationReceipt("warning")],
    });
    expect(canTeach(warningState)).toBe(false);
    const acknowledged = workspaceReducer(warningState, {
      type: "ACKNOWLEDGE_VALIDATION_WARNINGS",
      acknowledged: true,
    });
    expect(acknowledged.validationWarningsAcknowledged).toBe(true);
    expect(canTeach(acknowledged)).toBe(true);

    const cleanState = state({
      step: "edit",
      validation: "success",
      receipts: [validationReceipt("pass")],
    });
    expect(cleanState.validationWarningsAcknowledged).toBe(false);
    expect(canTeach(cleanState)).toBe(true);
  });

  it("rejects warning acknowledgement without an applicable warning-only validation", () => {
    for (const latest of [
      validationReceipt("pass"),
      validationReceipt("error"),
      validationReceipt("warning", { courseId: "another-course" }),
    ]) {
      const previous = state({ validation: "success", receipts: [latest] });
      const next = workspaceReducer(previous, {
        type: "ACKNOWLEDGE_VALIDATION_WARNINGS",
        acknowledged: true,
      });
      expect(next.validationWarningsAcknowledged).toBe(false);
      expect(canTeach(next)).toBe(latest.checks[0]?.level === "pass");
    }
  });

  it("records a Chinese validation failure while preserving course and receipts", () => {
    const previous = state({
      validation: "running",
      receipts: [receipt()],
    });

    const next = workspaceReducer(previous, {
      type: "VALIDATION_FAILED",
      message: "   ",
      courseRevision: previous.courseRevision,
    });

    expect(next.validation).toBe("error");
    expect(next.operationError).toMatch(/[\u4e00-\u9fff]/);
    expect(next.operationError?.trim().length).toBeGreaterThan(0);
    expect(next.course).toBe(previous.course);
    expect(next.receipts).toBe(previous.receipts);
  });

  it("keeps validation and assistant failures owned by their operations", () => {
    const validationFailed = workspaceReducer(state(), {
      type: "VALIDATION_FAILED",
      message: "校验专属错误",
      courseRevision: 0,
    });
    const assistantFailedAfterValidation = workspaceReducer(validationFailed, {
      type: "ASSISTANT_FAILED",
      message: "助手专属错误",
      courseRevision: validationFailed.courseRevision,
    });

    expect(assistantFailedAfterValidation.validationError).toBe("校验专属错误");
    expect(assistantFailedAfterValidation.assistantError).toBe("助手专属错误");

    const assistantFailed = workspaceReducer(state(), {
      type: "ASSISTANT_FAILED",
      message: "助手先失败",
      courseRevision: 0,
    });
    const validationFailedAfterAssistant = workspaceReducer(assistantFailed, {
      type: "VALIDATION_FAILED",
      message: "校验后失败",
      courseRevision: assistantFailed.courseRevision,
    });

    expect(validationFailedAfterAssistant.assistantError).toBe("助手先失败");
    expect(validationFailedAfterAssistant.validationError).toBe("校验后失败");
  });

  it("records assistant start and completion while preserving valid selections", () => {
    const previous = state({
      assistant: "error",
      assistantMessage: "旧助手消息",
      operationError: "旧错误",
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-2",
      receipts: [receipt()],
      validation: "success",
      validationMessage: "课程验证完成。",
      validationWarningsAcknowledged: true,
    });
    const assistedCourse = course({ title: "助手调整后的课程" });
    const assistantReceipt = receipt({ id: "receipt-assistant" });

    const started = workspaceReducer(previous, { type: "ASSISTANT_STARTED" });
    const completed = workspaceReducer(started, {
      type: "ASSISTANT_COMPLETED",
      course: assistedCourse,
      receipt: assistantReceipt,
      message: "已完成调整。",
      courseRevision: started.courseRevision,
    });

    expect(started).toEqual({
      ...previous,
      assistant: "running",
      assistantMessage: undefined,
      operationError: undefined,
    });
    expect(completed).toMatchObject({
      assistant: "success",
      assistantMessage: "已完成调整。",
      course: assistedCourse,
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-2",
    });
    expect(completed.receipts).toEqual([receipt(), assistantReceipt]);
    expect(completed.assistantReceiptId).toBe("receipt-assistant");
    expect(completed.validation).toBe("idle");
    expect(completed.validationMessage).toBeUndefined();
    expect(completed.validationWarningsAcknowledged).toBe(false);
  });

  it("treats an assistant validation receipt as the latest validation result", () => {
    const previous = state({
      validation: "success",
      validationWarningsAcknowledged: true,
      receipts: [validationReceipt("warning")],
    });
    const latest = validationReceipt("pass", { id: "assistant-validation" });

    const next = workspaceReducer(previous, {
      type: "ASSISTANT_COMPLETED",
      course: previous.course,
      receipt: latest,
      message: "已完成来源覆盖检查",
      courseRevision: previous.courseRevision,
    });

    expect(next.validation).toBe("success");
    expect(next.validationMessage).toBe("课程验证完成。");
    expect(next.validationWarningsAcknowledged).toBe(false);
    expect(next.receipts).toEqual([validationReceipt("warning"), latest]);
    expect(canTeach(next)).toBe(true);
  });

  it("falls back to the first valid chapter and lesson after assistant replacement", () => {
    const previous = state({
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-2",
    });
    const assistantReceipt = receipt({ id: "receipt-assistant" });
    const retainedChapterCourse = course({
      chapters: [
        {
          ...course().chapters[0],
          lessons: [course().chapters[0].lessons[0]],
        },
      ],
    });
    const replacedCourse = course({
      chapters: [
        {
          id: "chapter-new",
          title: "新第一章",
          objective: "重新开始",
          lessons: [
            {
              id: "lesson-new",
              title: "新第一节",
              summary: "新的课程内容。",
              durationMinutes: 30,
              sourceIds: [],
              status: "needs-source",
            },
          ],
        },
      ],
    });

    const missingLesson = workspaceReducer(previous, {
      type: "ASSISTANT_COMPLETED",
      course: retainedChapterCourse,
      receipt: assistantReceipt,
      message: "课节已替换。",
      courseRevision: previous.courseRevision,
    });
    const missingChapter = workspaceReducer(previous, {
      type: "ASSISTANT_COMPLETED",
      course: replacedCourse,
      receipt: assistantReceipt,
      message: "课程已替换。",
      courseRevision: previous.courseRevision,
    });

    expect(missingLesson.selectedChapterId).toBe("chapter-1");
    expect(missingLesson.selectedLessonId).toBe("lesson-1");
    expect(missingChapter.selectedChapterId).toBe("chapter-new");
    expect(missingChapter.selectedLessonId).toBe("lesson-new");
  });

  it("records a Chinese assistant failure without changing course or receipts", () => {
    const previous = state({
      assistant: "running",
      receipts: [receipt()],
    });

    const next = workspaceReducer(previous, {
      type: "ASSISTANT_FAILED",
      message: "",
      courseRevision: previous.courseRevision,
    });

    expect(next.assistant).toBe("error");
    expect(next.operationError).toMatch(/[\u4e00-\u9fff]/);
    expect(next.operationError?.trim().length).toBeGreaterThan(0);
    expect(next.course).toBe(previous.course);
    expect(next.receipts).toBe(previous.receipts);
  });
});


describe("workspace v2 hydration", () => {
  it("reopens with identical governed IDs but no persisted course body or receipts", () => {
    const saved = snapshot();
    const restored = hydrateWorkspaceState({ status: "ready", snapshot: saved });

    expect(restored.governed).toEqual(saved.governed);
    expect(restored.step).toBe("edit");
    expect(restored.selectedChapterId).toBe("chapter-1");
    expect(restored.selectedLessonId).toBe("lesson-1");
    expect(restored.course.chapters).toEqual([]);
    expect(restored.course.sources).toEqual([]);
    expect(restored.receipts).toEqual([]);
    expect(restored.validation).toBe("idle");
    expect(canTeach(restored)).toBe(false);
  });

  it("keeps only a bounded legacy-unlinked summary and contains corrupt input", () => {
    const summary = {
      status: "legacy-unlinked" as const,
      sourceCount: 2,
      chapterCount: 1,
      lessonCount: 3,
      receiptCount: 1,
    };
    const restored = hydrateWorkspaceState({
      status: "ready",
      snapshot: snapshot({ legacyUnlinked: summary }),
    });
    expect(restored.legacyUnlinked).toEqual(summary);
    expect(restored.course.chapters).toEqual([]);

    const empty = hydrateWorkspaceState({ status: "empty" });
    expect(empty.step).toBe("import");
    const corrupt = hydrateWorkspaceState({
      status: "corrupt",
      message: "已保存数据无法读取。",
    });
    expect(corrupt.step).toBe("import");
    expect(corrupt.persistenceWarning).toBe("已保存数据无法读取。");
  });
});

describe("WorkspaceProvider", () => {
  it("imports files through the real source reader and exposes the updated state", async () => {
    const storage = new MapStorage();
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={state()} storage={storage}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let imported: SourceAsset[] = [];
    await act(async () => {
      imported = await current.importFiles([
        textFile("# 可验证课程", "course.md"),
        new File([new Uint8Array([1, 2])], "slides.pptx"),
      ]);
    });

    expect(imported).toMatchObject([
      {
        name: "course.md",
        kind: "markdown",
        status: "ready",
        extractedText: "# 可验证课程",
      },
      { name: "slides.pptx", kind: "pptx", status: "ready" },
    ]);
    expect(current.state.course.sources.slice(-2)).toEqual(imported);
  });

  it("does not add a stale import result after starting a new workspace", async () => {
    const gate = deferred<string>();
    const pendingFile = new File(["stale"], "stale.md");
    Object.defineProperty(pendingFile, "text", { value: () => gate.promise });
    const storage = new MapStorage();
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider
        initialState={state({ receipts: [receipt()] })}
        storage={storage}
      >
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let operation!: Promise<SourceAsset[]>;
    await act(async () => {
      operation = current.importFiles([pendingFile]);
      await Promise.resolve();
    });
    act(() => current.dispatch({ type: "START_NEW" }));

    let imported: SourceAsset[] = [];
    await act(async () => {
      gate.resolve("# stale source");
      imported = await operation;
    });

    expect(imported).toMatchObject([
      { name: "stale.md", status: "ready", extractedText: "# stale source" },
    ]);
    expect(current.state).toMatchObject({
      step: "import",
      generation: "idle",
      course: { sources: [], chapters: [] },
      receipts: [],
    });
  });

  it("shows generation running, passes current inputs, and publishes a successful result", async () => {
    const gate = deferred<GeneratedCourse>();
    const agent = new ControlledAgent(() => gate.promise);
    const storage = new MapStorage();
    const initialState = state();
    const generatedCourse = course({ id: "course-generated" });
    const generatedReceipt = receipt({ id: "receipt-generated", courseId: "course-generated" });
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage} agent={agent}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let operation!: Promise<void>;
    await act(async () => {
      operation = current.generateCourse();
      await Promise.resolve();
    });

    expect(current.state.generation).toBe("running");
    expect(agent.inputs).toEqual([
      { brief: initialState.brief, sources: initialState.course.sources },
    ]);

    await act(async () => {
      gate.resolve({ course: generatedCourse, receipt: generatedReceipt });
      await operation;
    });

    expect(current.state).toMatchObject({
      generation: "success",
      step: "edit",
      course: generatedCourse,
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
    });
    expect(current.state.receipts).toEqual([generatedReceipt]);
  });

  it("does not publish a stale generation result after starting a new workspace", async () => {
    const gate = deferred<GeneratedCourse>();
    const agent = new ControlledAgent(() => gate.promise);
    const storage = new MapStorage();
    const generatedCourse = course({ id: "course-stale" });
    const generatedReceipt = receipt({
      id: "receipt-stale",
      courseId: "course-stale",
    });
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider
        initialState={state({ receipts: [receipt()] })}
        storage={storage}
        agent={agent}
      >
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let operation!: Promise<void>;
    await act(async () => {
      operation = current.generateCourse();
      await Promise.resolve();
    });
    expect(current.state.generation).toBe("running");
    act(() => current.dispatch({ type: "START_NEW" }));

    await act(async () => {
      gate.resolve({ course: generatedCourse, receipt: generatedReceipt });
      await operation;
    });

    expect(current.state).toMatchObject({
      step: "import",
      generation: "idle",
      course: { sources: [], chapters: [] },
      receipts: [],
    });
  });

  it("contains unknown generation failures and exposes a non-empty Chinese error", async () => {
    const gate = deferred<GeneratedCourse>();
    const agent = new ControlledAgent(() => gate.promise);
    const storage = new MapStorage();
    const initialState = state();
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage} agent={agent}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let operation!: Promise<void>;
    await act(async () => {
      operation = current.generateCourse();
      await Promise.resolve();
    });
    expect(current.state.generation).toBe("running");

    await act(async () => {
      gate.reject({ unexpected: true });
      await expect(operation).resolves.toBeUndefined();
    });

    expect(current.state.generation).toBe("error");
    expect(current.state.operationError).toMatch(/[\u4e00-\u9fff]/);
    expect(current.state.operationError?.trim().length).toBeGreaterThan(0);
    expect(current.state.course).toEqual(initialState.course);
    expect(current.state.brief).toEqual(initialState.brief);
    expect(current.state.course.sources).toEqual(initialState.course.sources);
  });

  it("validates the current course with the real validator and stores its receipt", async () => {
    const storage = new MapStorage();
    const initialState = state({ receipts: [receipt()] });
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let returned: EvidenceReceipt | undefined;
    await act(async () => {
      returned = await current.validateCurrentCourse();
    });

    expect(returned).toMatchObject({
      courseId: initialState.course.id,
      kind: "validation",
      inputDigest: await stableDigest(initialState.course),
    });
    expect(returned?.checks.map(({ id }) => id)).toContain("source-coverage");
    expect(current.state.validation).toBe("success");
    expect(current.state.validationWarningsAcknowledged).toBe(false);
    expect(current.state.validationMessage).toBe("课程验证完成。");
    expect(current.state.receipts).toEqual([receipt(), returned]);
  });

  it("contains validation failures and returns undefined without changing course or receipts", async () => {
    const digest = vi
      .spyOn(crypto.subtle, "digest")
      .mockRejectedValueOnce(new Error("digest unavailable"));
    try {
      const storage = new MapStorage();
      const initialState = state({ receipts: [receipt()] });
      let current!: WorkspaceContextValue;
      const capture = (value: WorkspaceContextValue) => {
        current = value;
      };
      render(
        <WorkspaceProvider initialState={initialState} storage={storage}>
          <CaptureWorkspace onValue={capture} />
        </WorkspaceProvider>,
      );

      let returned: EvidenceReceipt | undefined = receipt();
      await act(async () => {
        returned = await current.validateCurrentCourse();
      });

      expect(returned).toBeUndefined();
      expect(current.state.validation).toBe("error");
      expect(current.state.operationError).toMatch(/[\u4e00-\u9fff]/);
      expect(current.state.course).toBe(initialState.course);
      expect(current.state.receipts).toBe(initialState.receipts);
    } finally {
      digest.mockRestore();
    }
  });

  it("does not publish an in-flight validation result after starting a new workspace", async () => {
    const gate = deferred<ArrayBuffer>();
    const digest = vi.spyOn(crypto.subtle, "digest").mockReturnValueOnce(gate.promise);
    try {
      const storage = new MapStorage();
      let current!: WorkspaceContextValue;
      const capture = (value: WorkspaceContextValue) => {
        current = value;
      };
      render(
        <WorkspaceProvider
          initialState={state({ receipts: [receipt()] })}
          storage={storage}
        >
          <CaptureWorkspace onValue={capture} />
        </WorkspaceProvider>,
      );

      let operation!: Promise<EvidenceReceipt | undefined>;
      await act(async () => {
        operation = current.validateCurrentCourse();
        await Promise.resolve();
      });
      expect(current.state.validation).toBe("running");
      act(() => current.dispatch({ type: "START_NEW" }));

      let returned: EvidenceReceipt | undefined = receipt();
      await act(async () => {
        gate.resolve(new ArrayBuffer(32));
        returned = await operation;
      });

      expect(returned).toBeUndefined();
      expect(current.state).toMatchObject({
        step: "import",
        generation: "idle",
        validation: "idle",
        assistant: "idle",
        course: { sources: [], chapters: [] },
        receipts: [],
      });
    } finally {
      digest.mockRestore();
    }
  });

  it("lets an in-flight validation finish after an invalid structural no-op", async () => {
    const gate = deferred<ArrayBuffer>();
    const digest = vi.spyOn(crypto.subtle, "digest").mockReturnValueOnce(gate.promise);
    try {
      const storage = new MapStorage();
      let current!: WorkspaceContextValue;
      const capture = (value: WorkspaceContextValue) => {
        current = value;
      };
      render(
        <WorkspaceProvider initialState={state()} storage={storage}>
          <CaptureWorkspace onValue={capture} />
        </WorkspaceProvider>,
      );

      let operation!: Promise<EvidenceReceipt | undefined>;
      await act(async () => {
        operation = current.validateCurrentCourse();
        await Promise.resolve();
      });
      expect(current.state.validation).toBe("running");
      act(() =>
        current.dispatch({
          type: "MOVE_LESSON",
          chapterId: "missing-chapter",
          lessonId: "missing-lesson",
          direction: 1,
        }),
      );
      expect(current.state.validation).toBe("running");

      let returned: EvidenceReceipt | undefined;
      await act(async () => {
        gate.resolve(new ArrayBuffer(32));
        returned = await operation;
      });

      expect(returned?.kind).toBe("validation");
      expect(current.state.validation).toBe("success");
    } finally {
      digest.mockRestore();
    }
  });

  it("rejects a validation completion batched with an accepted structural edit", async () => {
    const gate = deferred<ArrayBuffer>();
    const digest = vi.spyOn(crypto.subtle, "digest").mockReturnValueOnce(gate.promise);
    try {
      let current!: WorkspaceContextValue;
      render(
        <WorkspaceProvider initialState={state()} storage={new MapStorage()}>
          <CaptureWorkspace
            onValue={(value) => {
              current = value;
            }}
          />
        </WorkspaceProvider>,
      );

      let operation!: Promise<EvidenceReceipt | undefined>;
      await act(async () => {
        operation = current.validateCurrentCourse();
        await Promise.resolve();
      });
      expect(current.state.validation).toBe("running");

      let returned: EvidenceReceipt | undefined = receipt();
      await act(async () => {
        current.dispatch({
          type: "UPDATE_LESSON",
          chapterId: "chapter-1",
          lessonId: "lesson-1",
          patch: { title: "同批次人工编辑" },
        });
        gate.resolve(new ArrayBuffer(32));
        returned = await operation;
      });

      expect(returned).toBeUndefined();
      expect(current.state.courseRevision).toBe(1);
      expect(current.state.course.chapters[0].lessons[0].title).toBe(
        "同批次人工编辑",
      );
      expect(current.state.validation).toBe("idle");
      expect(current.state.receipts).toEqual([]);
    } finally {
      digest.mockRestore();
    }
  });

  it("applies the LocalCourseAgent duration intent through the provider", async () => {
    const storage = new MapStorage();
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider
        initialState={state({
          selectedChapterId: "chapter-1",
          selectedLessonId: "lesson-2",
        })}
        storage={storage}
      >
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    await act(async () => {
      await current.applyAssistantIntent("  缩短课程到 90 分钟  ");
    });

    expect(current.state.course.durationMinutes).toBe(90);
    expect(
      current.state.course.chapters[0].lessons.map(
        ({ durationMinutes }) => durationMinutes,
      ),
    ).toEqual([30, 30, 30]);
    expect(current.state.assistant).toBe("success");
    expect(current.state.assistantMessage).toBe("已将课程时长调整为 90 分钟。");
    expect(current.state.selectedChapterId).toBe("chapter-1");
    expect(current.state.selectedLessonId).toBe("lesson-2");
    expect(current.state.receipts.at(-1)?.kind).toBe("generation");
  });

  it("applies the LocalCourseAgent case intent to the selected chapter", async () => {
    const storage = new MapStorage();
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider
        initialState={state({
          selectedChapterId: "chapter-1",
          selectedLessonId: "lesson-1",
        })}
        storage={storage}
      >
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    await act(async () => {
      await current.applyAssistantIntent("为本章补充案例");
    });

    expect(current.state.course.chapters[0].lessons).toHaveLength(4);
    expect(current.state.course.chapters[0].lessons[3]).toMatchObject({
      title: "业务案例：从资料到行动",
      durationMinutes: 15,
      sourceIds: ["source-a"],
      status: "grounded",
    });
    expect(current.state.assistantMessage).toBe("已为当前章节补充一个业务案例。");
    expect(current.state.selectedLessonId).toBe("lesson-1");
    expect(current.state.receipts.at(-1)?.kind).toBe("generation");
  });

  it("applies the LocalCourseAgent source-coverage intent and keeps its validation evidence", async () => {
    const storage = new MapStorage();
    const initialState = state({
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
    });
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    await act(async () => {
      await current.applyAssistantIntent("检查来源覆盖");
    });

    expect(current.state.course).toBe(initialState.course);
    expect(current.state.assistant).toBe("success");
    expect(current.state.assistantMessage).toBe("已完成来源覆盖检查");
    expect(current.state.receipts).toHaveLength(1);
    expect(current.state.receipts[0]).toMatchObject({
      courseId: initialState.course.id,
      kind: "validation",
      inputDigest: await stableDigest(initialState.course),
    });
    expect(current.state.validation).toBe("success");
    expect(current.state.validationWarningsAcknowledged).toBe(false);
  });

  it("rejects blank assistant input locally with the exact Chinese message", async () => {
    const agent = new ControlledAgent(
      async () => ({ course: course(), receipt: receipt() }),
      async () => {
        throw new Error("blank input must not reach the agent");
      },
    );
    const storage = new MapStorage();
    const initialState = state();
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage} agent={agent}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    await act(async () => {
      await current.applyAssistantIntent("  \n\t  ");
    });

    expect(agent.intentInputs).toEqual([]);
    expect(current.state.assistant).toBe("error");
    expect(current.state.operationError).toBe("请输入希望课程助手执行的内容。");
    expect(current.state.course).toBe(initialState.course);
  });

  it("contains assistant failures without changing the course or receipts", async () => {
    const agent = new ControlledAgent(
      async () => ({ course: course(), receipt: receipt() }),
      async () => {
        throw new Error("agent unavailable");
      },
    );
    const storage = new MapStorage();
    const initialState = state({
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-2",
      receipts: [receipt()],
    });
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage} agent={agent}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    await act(async () => {
      await expect(current.applyAssistantIntent("  未知操作  ")).resolves.toBe(false);
    });

    expect(agent.intentInputs).toEqual([
      {
        course: initialState.course,
        intent: "未知操作",
        chapterId: "chapter-1",
      },
    ]);
    expect(current.state.assistant).toBe("error");
    expect(current.state.operationError).toMatch(/[\u4e00-\u9fff]/);
    expect(current.state.course).toBe(initialState.course);
    expect(current.state.receipts).toBe(initialState.receipts);
  });

  it("does not publish an in-flight assistant result after starting a new workspace", async () => {
    const gate = deferred<Awaited<ReturnType<CourseAgent["applyIntent"]>>>();
    const agent = new ControlledAgent(
      async () => ({ course: course(), receipt: receipt() }),
      () => gate.promise,
    );
    const storage = new MapStorage();
    const initialState = state({
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-2",
      receipts: [receipt()],
    });
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={initialState} storage={storage} agent={agent}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    let operation!: Promise<boolean>;
    await act(async () => {
      operation = current.applyAssistantIntent("  缩短课程到 90 分钟  ");
      await Promise.resolve();
    });
    expect(current.state.assistant).toBe("running");
    expect(agent.intentInputs).toEqual([
      {
        course: initialState.course,
        intent: "缩短课程到 90 分钟",
        chapterId: "chapter-1",
      },
    ]);
    act(() => current.dispatch({ type: "START_NEW" }));

    await act(async () => {
      gate.resolve({
        course: course({ id: "course-stale" }),
        receipt: receipt({ id: "receipt-stale", courseId: "course-stale" }),
        message: "不应发布。",
      });
      await operation;
    });

    expect(current.state).toMatchObject({
      step: "import",
      generation: "idle",
      validation: "idle",
      assistant: "idle",
      course: { sources: [], chapters: [] },
      receipts: [],
    });
  });

  it("cancels and settles an in-flight assistant result after a structural edit", async () => {
    const gate = deferred<Awaited<ReturnType<CourseAgent["applyIntent"]>>>();
    const agent = new ControlledAgent(
      async () => ({ course: course(), receipt: receipt() }),
      () => gate.promise,
    );
    const initialState = state({
      step: "edit",
      selectedChapterId: "chapter-1",
      selectedLessonId: "lesson-1",
      receipts: [receipt()],
    });
    let current!: WorkspaceContextValue;
    render(
      <WorkspaceProvider
        initialState={initialState}
        storage={new MapStorage()}
        agent={agent}
      >
        <CaptureWorkspace
          onValue={(value) => {
            current = value;
          }}
        />
      </WorkspaceProvider>,
    );

    let operation!: Promise<boolean>;
    await act(async () => {
      operation = current.applyAssistantIntent("缩短课程到 90 分钟");
      await Promise.resolve();
    });
    expect(current.state.assistant).toBe("running");

    act(() =>
      current.dispatch({
        type: "UPDATE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        patch: { title: "人工编辑后的标题" },
      }),
    );
    expect(current.state.assistant).toBe("idle");

    let recognized = true;
    await act(async () => {
      gate.resolve({
        course: course({ title: "不应发布的助手课程" }),
        receipt: receipt({ id: "receipt-stale-assistant" }),
        message: "不应发布。",
      });
      recognized = await operation;
    });

    expect(recognized).toBe(false);
    expect(current.state.course.chapters[0].lessons[0].title).toBe(
      "人工编辑后的标题",
    );
    expect(current.state.receipts).toEqual([receipt()]);
    expect(current.state.assistant).toBe("idle");
  });

  it("rejects an assistant completion batched with an accepted structural edit", async () => {
    const gate = deferred<Awaited<ReturnType<CourseAgent["applyIntent"]>>>();
    const agent = new ControlledAgent(
      async () => ({ course: course(), receipt: receipt() }),
      () => gate.promise,
    );
    let current!: WorkspaceContextValue;
    render(
      <WorkspaceProvider
        initialState={state({
          step: "edit",
          selectedChapterId: "chapter-1",
          selectedLessonId: "lesson-1",
          receipts: [receipt()],
        })}
        storage={new MapStorage()}
        agent={agent}
      >
        <CaptureWorkspace
          onValue={(value) => {
            current = value;
          }}
        />
      </WorkspaceProvider>,
    );

    let operation!: Promise<boolean>;
    await act(async () => {
      operation = current.applyAssistantIntent("缩短课程到 90 分钟");
      await Promise.resolve();
    });
    expect(current.state.assistant).toBe("running");

    let recognized = true;
    await act(async () => {
      current.dispatch({
        type: "UPDATE_LESSON",
        chapterId: "chapter-1",
        lessonId: "lesson-1",
        patch: { title: "同批次人工编辑" },
      });
      gate.resolve({
        course: course({ title: "不应发布的助手课程" }),
        receipt: receipt({ id: "receipt-batched-stale-assistant" }),
        message: "不应发布。",
      });
      recognized = await operation;
    });

    expect(recognized).toBe(false);
    expect(current.state.courseRevision).toBe(1);
    expect(current.state.course.chapters[0].lessons[0].title).toBe(
      "同批次人工编辑",
    );
    expect(current.state.receipts).toEqual([receipt()]);
    expect(current.state.assistant).toBe("idle");
  });

  it("publishes only the newest overlapping generation result", async () => {
    const first = deferred<GeneratedCourse>();
    const second = deferred<GeneratedCourse>();
    let callCount = 0;
    const agent = new ControlledAgent(() => {
      callCount += 1;
      return callCount === 1 ? first.promise : second.promise;
    });
    let current!: WorkspaceContextValue;
    render(
      <WorkspaceProvider initialState={state()} storage={new MapStorage()} agent={agent}>
        <CaptureWorkspace onValue={(value) => { current = value; }} />
      </WorkspaceProvider>,
    );

    let firstRun!: Promise<void>;
    let secondRun!: Promise<void>;
    await act(async () => {
      firstRun = current.generateCourse();
      secondRun = current.generateCourse();
    });
    await act(async () => {
      second.resolve({
        course: course({ id: "course-newest" }),
        receipt: receipt({ id: "receipt-newest", courseId: "course-newest" }),
      });
      await secondRun;
    });
    await act(async () => {
      first.resolve({
        course: course({ id: "course-stale" }),
        receipt: receipt({ id: "receipt-stale", courseId: "course-stale" }),
      });
      await firstRun;
    });

    expect(current.state.course.id).toBe("course-newest");
    expect(current.state.receipts.at(-1)?.id).toBe("receipt-newest");
    expect(current.state.generation).toBe("success");
  });


  it("restores governed IDs and persists only the workspace v2 whitelist", async () => {
    const storage = new MapStorage();
    const saved = snapshot();
    storage.values.set(WORKSPACE_STORAGE_KEY, JSON.stringify(saved));
    let current!: WorkspaceContextValue;
    render(
      <WorkspaceProvider storage={storage}>
        <CaptureWorkspace onValue={(value) => { current = value; }} />
      </WorkspaceProvider>,
    );

    expect(current.state.governed).toEqual(saved.governed);
    expect(current.state.course.chapters).toEqual([]);
    act(() => current.dispatch({ type: "GO_TO_STEP", step: "import" }));

    await waitFor(() => {
      const persisted = JSON.parse(
        storage.values.get(WORKSPACE_STORAGE_KEY) ?? "null",
      ) as WorkspaceSnapshot | null;
      expect(persisted).toMatchObject({
        version: 2,
        governed: saved.governed,
        view: { step: "import" },
      });
      expect(persisted).not.toHaveProperty("course");
      expect(persisted).not.toHaveProperty("brief");
      expect(persisted).not.toHaveProperty("receipts");
      expect(persisted).not.toHaveProperty("activeValidationReceiptId");
    });
  });

  it("summarizes local legacy content without persisting its bodies", async () => {
    const storage = new MapStorage();
    const activeReceipt = validationReceipt("pass");
    let current!: WorkspaceContextValue;
    render(
      <WorkspaceProvider
        initialState={state({
          step: "edit",
          validation: "success",
          receipts: [activeReceipt],
          selectedChapterId: "chapter-1",
          selectedLessonId: "lesson-1",
        })}
        storage={storage}
      >
        <CaptureWorkspace onValue={(value) => { current = value; }} />
      </WorkspaceProvider>,
    );

    await waitFor(() => {
      const serialized = storage.values.get(WORKSPACE_STORAGE_KEY) ?? "";
      const persisted = JSON.parse(serialized) as WorkspaceSnapshot;
      expect(persisted.legacyUnlinked).toEqual({
        status: "legacy-unlinked",
        sourceCount: 2,
        chapterCount: 1,
        lessonCount: 3,
        receiptCount: 1,
      });
      expect(serialized).not.toContain(current.state.course.chapters[0].lessons[0].summary);
      expect(serialized).not.toContain(activeReceipt.summary);
      expect(persisted).not.toHaveProperty("receipts");
    });
  });

  it("settles a persistence failure warning without an effect loop", async () => {
    let setItemCalls = 0;
    const failingStorage: Pick<Storage, "getItem" | "setItem"> = {
      getItem: () => null,
      setItem: () => {
        setItemCalls += 1;
        throw new Error("quota exceeded");
      },
    };
    let current!: WorkspaceContextValue;
    const capture = (value: WorkspaceContextValue) => {
      current = value;
    };
    render(
      <WorkspaceProvider initialState={state()} storage={failingStorage}>
        <CaptureWorkspace onValue={capture} />
      </WorkspaceProvider>,
    );

    await waitFor(() => {
      expect(current.state.persistenceWarning).toBe("工作区保存失败。");
      expect(setItemCalls).toBe(2);
    });
    await act(async () => Promise.resolve());
    expect(setItemCalls).toBe(2);
  });

  it("throws the exact error when the hook is used outside its provider", () => {
    function OutsideProvider() {
      useWorkspace();
      return null;
    }

    expect(() => render(<OutsideProvider />)).toThrow(
      "useWorkspace 必须在 WorkspaceProvider 内使用。",
    );
  });
});

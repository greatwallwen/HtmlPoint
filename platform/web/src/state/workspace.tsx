import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type Dispatch,
  type JSX,
  type ReactNode,
} from "react";

import {
  createId,
  createEmptyCourse,
  createEmptyGovernedBindings,
  type CourseBrief,
  type CourseDocument,
  type EvidenceReceipt,
  type GovernedWorkspaceBindings,
  type GovernedCourseProjection,
  type GovernedAvailableAssets,
  type LegacyUnlinkedSummary,
  type LessonNode,
  type SourceAsset,
  type WorkflowStep,
} from "../domain/course";
import type { CourseAgent } from "../domain/course-agent";
import type { PersonalCourseView } from "../domain/personal-course-schema";
import { readSourceFiles } from "../domain/source-import";
import { validateCourse } from "../domain/validation";
import {
  loadWorkspace,
  saveWorkspace,
  type LoadWorkspaceResult,
  type WorkspaceSnapshot,
} from "./storage";

type OperationStatus = "idle" | "running" | "success" | "error";

export interface WorkspaceState {
  step: WorkflowStep;
  course: CourseDocument;
  brief: CourseBrief;
  receipts: EvidenceReceipt[];
  governed: GovernedWorkspaceBindings;
  governedProjection?: GovernedCourseProjection;
  governedAssets?: GovernedAvailableAssets;
  legacyUnlinked?: LegacyUnlinkedSummary;
  selectedChapterId?: string;
  selectedLessonId?: string;
  courseRevision: number;
  generation: OperationStatus;
  validation: OperationStatus;
  validationWarningsAcknowledged: boolean;
  assistant: OperationStatus;
  validationMessage?: string;
  validationError?: string;
  assistantMessage?: string;
  assistantError?: string;
  assistantReceiptId?: string;
  operationError?: string;
  persistenceWarning?: string;
  personalRunId?: string;
  personalView?: PersonalCourseView;
}

export type WorkspaceAction =
  | { type: "START_NEW" }
  | { type: "PERSONAL_COURSE_TRACKED"; runId: string; view: PersonalCourseView }
  | { type: "PERSONAL_COURSE_OPENED"; course: CourseDocument; receipt: EvidenceReceipt; target: "edit" | "teach" }
  | { type: "ADD_SOURCES"; sources: SourceAsset[] }
  | { type: "REMOVE_SOURCE"; sourceId: string }
  | { type: "SET_BRIEF"; patch: Partial<CourseBrief> }
  | { type: "GO_TO_STEP"; step: WorkflowStep }
  | { type: "GENERATION_STARTED" }
  | {
      type: "GENERATION_COMPLETED";
      course: CourseDocument;
      receipt: EvidenceReceipt;
    }
  | {
      type: "GOVERNED_COURSE_CONFIRMED";
      course: CourseDocument;
      receipt: EvidenceReceipt;
      governed: GovernedWorkspaceBindings;
      projection: GovernedCourseProjection;
    }
  | {
      type: "GOVERNED_PROJECTION_UPDATED";
      governed: GovernedWorkspaceBindings;
      projection: GovernedCourseProjection;
    }
  | {
      type: "GOVERNED_COURSE_RESTORED";
      course: CourseDocument;
      governed: GovernedWorkspaceBindings;
      projection: GovernedCourseProjection;
      receipt: EvidenceReceipt;
    }
  | { type: "REGISTER_GOVERNED_ASSETS"; assets: GovernedAvailableAssets }
  | { type: "GENERATION_FAILED"; message: string }
  | { type: "SELECT_CHAPTER"; chapterId: string }
  | { type: "SELECT_LESSON"; lessonId: string }
  | {
      type: "UPDATE_LESSON";
      chapterId: string;
      lessonId: string;
      patch: Partial<
        Pick<LessonNode, "title" | "summary" | "durationMinutes">
      >;
    }
  | {
      type: "MOVE_LESSON";
      chapterId: string;
      lessonId: string;
      direction: -1 | 1;
    }
  | {
      type: "TOGGLE_LESSON_SOURCE";
      chapterId: string;
      lessonId: string;
      sourceId: string;
    }
  | { type: "ADD_CHAPTER" }
  | { type: "ADD_LESSON"; chapterId: string }
  | { type: "VALIDATION_STARTED" }
  | {
      type: "VALIDATION_COMPLETED";
      receipt: EvidenceReceipt;
      courseRevision: number;
    }
  | { type: "VALIDATION_FAILED"; message: string; courseRevision: number }
  | { type: "ACKNOWLEDGE_VALIDATION_WARNINGS"; acknowledged: boolean }
  | { type: "ASSISTANT_STARTED" }
  | {
      type: "ASSISTANT_COMPLETED";
      course: CourseDocument;
      receipt: EvidenceReceipt;
      message: string;
      courseRevision: number;
    }
  | { type: "ASSISTANT_FAILED"; message: string; courseRevision: number }
  | { type: "PERSISTENCE_FAILED"; message: string }
  | { type: "CLEAR_OPERATION_ERROR" };

export function createFreshWorkspaceState(): WorkspaceState {
  return {
    step: "import",
    course: createEmptyCourse(),
    brief: {
      title: "企业 AI 入门课",
      audience: "",
      goal: "",
      durationMinutes: 120,
    },
    receipts: [],
    governed: createEmptyGovernedBindings(),
    courseRevision: 0,
    generation: "idle",
    validation: "idle",
    validationWarningsAcknowledged: false,
    assistant: "idle",
  };
}

export function hydrateWorkspaceState(result: LoadWorkspaceResult): WorkspaceState {
  if (result.status === "ready") {
    const fresh = createFreshWorkspaceState();
    return {
      ...fresh,
      step:
        result.snapshot.view.step === "teach"
          ? "edit"
          : result.snapshot.view.step,
      governed: result.snapshot.governed,
      personalRunId: result.snapshot.personalRunId,
      legacyUnlinked: result.snapshot.legacyUnlinked,
      selectedChapterId: result.snapshot.view.selectedChapterId,
      selectedLessonId: result.snapshot.view.selectedLessonId,
    };
  }

  const fresh = createFreshWorkspaceState();
  return result.status === "corrupt"
    ? { ...fresh, persistenceWarning: result.message }
    : fresh;
}

function mergeSources(
  currentSources: SourceAsset[],
  incomingSources: SourceAsset[],
): SourceAsset[] {
  const merged = [...currentSources];
  for (const source of incomingSources) {
    const existingIndex = merged.findIndex((candidate) => candidate.id === source.id);
    if (existingIndex >= 0) {
      merged[existingIndex] = source;
    } else {
      merged.push(source);
    }
  }
  return merged;
}

const invalidValidationGate = {
  validation: "idle" as const,
  validationMessage: undefined,
  validationError: undefined,
  validationWarningsAcknowledged: false,
};

const invalidAssistantState = {
  assistant: "idle" as const,
  assistantMessage: undefined,
  assistantError: undefined,
  assistantReceiptId: undefined,
};

const nextCourseRevision = (state: WorkspaceState): number =>
  state.courseRevision + 1;

function courseMutationWillApply(
  state: WorkspaceState,
  action: WorkspaceAction,
): boolean {
  switch (action.type) {
    case "PERSONAL_COURSE_OPENED":
      return true;
    case "ADD_SOURCES": {
      if (action.sources.length === 0) {
        return false;
      }
      const sources = mergeSources(state.course.sources, action.sources);
      return (
        sources.length !== state.course.sources.length ||
        sources.some((source, index) => source !== state.course.sources[index])
      );
    }
    case "REMOVE_SOURCE":
      return (
        state.course.sources.some((source) => source.id === action.sourceId) ||
        state.course.chapters.some((chapter) =>
          chapter.lessons.some((lesson) =>
            lesson.sourceIds.includes(action.sourceId),
          ),
        )
      );
    case "SET_BRIEF": {
      const brief = { ...state.brief, ...action.patch };
      return (
        brief.title !== state.brief.title ||
        brief.audience !== state.brief.audience ||
        brief.goal !== state.brief.goal ||
        brief.durationMinutes !== state.brief.durationMinutes
      );
    }
    case "GENERATION_COMPLETED":
    case "GOVERNED_COURSE_CONFIRMED":
    case "ADD_CHAPTER":
      return true;
    case "UPDATE_LESSON": {
      const lesson = state.course.chapters
        .find((chapter) => chapter.id === action.chapterId)
        ?.lessons.find((candidate) => candidate.id === action.lessonId);
      return (
        lesson !== undefined &&
        Object.entries(action.patch).some(
          ([key, value]) =>
            (lesson as unknown as Record<string, unknown>)[key] !== value,
        )
      );
    }
    case "MOVE_LESSON": {
      const lessons = state.course.chapters.find(
        (chapter) => chapter.id === action.chapterId,
      )?.lessons;
      const lessonIndex = lessons?.findIndex(
        (lesson) => lesson.id === action.lessonId,
      );
      const targetIndex =
        lessonIndex === undefined ? -1 : lessonIndex + action.direction;
      return (
        lessons !== undefined &&
        lessonIndex !== undefined &&
        lessonIndex >= 0 &&
        targetIndex >= 0 &&
        targetIndex < lessons.length
      );
    }
    case "TOGGLE_LESSON_SOURCE":
      return (
        state.course.sources.some((source) => source.id === action.sourceId) &&
        (state.course.chapters
          .find((chapter) => chapter.id === action.chapterId)
          ?.lessons.some((lesson) => lesson.id === action.lessonId) ?? false)
      );
    case "ADD_LESSON":
      return state.course.chapters.some(
        (chapter) => chapter.id === action.chapterId,
      );
    case "ASSISTANT_COMPLETED":
      return (
        action.courseRevision === state.courseRevision &&
        action.receipt.kind !== "validation" &&
        action.course !== state.course
      );
    default:
      return false;
  }
}

export function latestApplicableValidationReceipt(
  state: WorkspaceState,
): EvidenceReceipt | undefined {
  if (state.validation !== "success") {
    return undefined;
  }

  for (let index = state.receipts.length - 1; index >= 0; index -= 1) {
    const candidate = state.receipts[index];
    if (candidate.kind === "validation" && candidate.courseId === state.course.id) {
      return candidate;
    }
  }
  return undefined;
}

export function canTeach(state: WorkspaceState): boolean {
  if (state.course.chapters.length === 0) {
    return false;
  }
  const receipt = latestApplicableValidationReceipt(state);
  if (receipt === undefined) {
    return false;
  }
  const hasError = receipt.checks.some((check) => check.level === "error");
  const hasWarning = receipt.checks.some((check) => check.level === "warning");
  return (
    !hasError &&
    (!hasWarning || state.validationWarningsAcknowledged)
  );
}

export function workspaceReducer(
  state: WorkspaceState,
  action: WorkspaceAction,
): WorkspaceState {
  switch (action.type) {
    case "START_NEW":
      return createFreshWorkspaceState();

    case "PERSONAL_COURSE_TRACKED":
      return {
        ...state,
        personalRunId: action.runId,
        personalView: action.view,
        operationError: undefined,
      };

    case "PERSONAL_COURSE_OPENED": {
      const firstChapter = action.course.chapters[0];
      return {
        ...state,
        course: action.course,
        brief: {
          title: action.course.title,
          audience: action.course.audience,
          goal: action.course.goal,
          durationMinutes: action.course.durationMinutes,
        },
        receipts: [...state.receipts, action.receipt],
        validation: "success",
        validationWarningsAcknowledged: true,
        courseRevision: nextCourseRevision(state),
        selectedChapterId: firstChapter?.id,
        selectedLessonId: firstChapter?.lessons[0]?.id,
        step: action.target,
        operationError: undefined,
      };
    }

    case "ADD_SOURCES": {
      if (action.sources.length === 0) {
        return state;
      }
      const sources = mergeSources(state.course.sources, action.sources);
      if (
        sources.length === state.course.sources.length &&
        sources.every((source, index) => source === state.course.sources[index])
      ) {
        return state;
      }
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          sources,
          updatedAt: new Date().toISOString(),
        },
      };
    }

    case "REMOVE_SOURCE": {
      const sourceExists = state.course.sources.some(
        (source) => source.id === action.sourceId,
      );
      const sourceIsLinked = state.course.chapters.some((chapter) =>
        chapter.lessons.some((lesson) =>
          lesson.sourceIds.includes(action.sourceId),
        ),
      );
      if (!sourceExists && !sourceIsLinked) {
        return state;
      }
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          sources: state.course.sources.filter(
            (source) => source.id !== action.sourceId,
          ),
          chapters: state.course.chapters.map((chapter) => ({
            ...chapter,
            lessons: chapter.lessons.map((lesson) => {
              const sourceIds = lesson.sourceIds.filter(
                (sourceId) => sourceId !== action.sourceId,
              );
              return {
                ...lesson,
                sourceIds,
                status:
                  lesson.status === "grounded" && sourceIds.length === 0
                    ? "needs-source"
                    : lesson.status,
              };
            }),
          })),
          updatedAt: new Date().toISOString(),
        },
      };
    }

    case "SET_BRIEF": {
      const brief = { ...state.brief, ...action.patch };
      if (
        brief.title === state.brief.title &&
        brief.audience === state.brief.audience &&
        brief.goal === state.brief.goal &&
        brief.durationMinutes === state.brief.durationMinutes
      ) {
        return state;
      }
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        brief,
        course: {
          ...state.course,
          title: brief.title,
          audience: brief.audience,
          goal: brief.goal,
          durationMinutes: brief.durationMinutes,
          updatedAt: new Date().toISOString(),
        },
      };
    }

    case "GO_TO_STEP":
      return action.step === "teach" && !canTeach(state)
        ? state
        : { ...state, step: action.step, operationError: undefined };

    case "GENERATION_STARTED":
      return { ...state, generation: "running", operationError: undefined };

    case "GENERATION_COMPLETED": {
      const firstChapter = action.course.chapters[0];
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        step: "edit",
        course: action.course,
        governed: createEmptyGovernedBindings(),
        governedProjection: undefined,
        legacyUnlinked: {
          status: "legacy-unlinked",
          sourceCount: action.course.sources.length,
          chapterCount: action.course.chapters.length,
          lessonCount: action.course.chapters.reduce(
            (total, chapter) => total + chapter.lessons.length,
            0,
          ),
          receiptCount: state.receipts.length + 1,
        },
        receipts: [...state.receipts, action.receipt],
        selectedChapterId: firstChapter?.id,
        selectedLessonId: firstChapter?.lessons[0]?.id,
        generation: "success",
        operationError: undefined,
      };
    }

    case "GOVERNED_COURSE_CONFIRMED": {
      const firstChapter = action.course.chapters[0];
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        step: "edit",
        course: action.course,
        governed: action.governed,
        governedProjection: action.projection,
        legacyUnlinked: undefined,
        receipts: [...state.receipts, action.receipt],
        selectedChapterId: firstChapter?.id,
        selectedLessonId: firstChapter?.lessons[0]?.id,
        generation: "success",
        operationError: undefined,
      };
    }

    case "GOVERNED_PROJECTION_UPDATED":
      return {
        ...state,
        governed: action.governed,
        governedProjection: action.projection,
        operationError: undefined,
      };

    case "GOVERNED_COURSE_RESTORED": {
      const selectedChapter = action.course.chapters.find(
        (chapter) => chapter.id === state.selectedChapterId,
      ) ?? action.course.chapters[0];
      const selectedLesson = selectedChapter?.lessons.find(
        (lesson) => lesson.id === state.selectedLessonId,
      ) ?? selectedChapter?.lessons[0];
      return {
        ...state,
        course: action.course,
        governed: action.governed,
        governedProjection: action.projection,
        legacyUnlinked: undefined,
        receipts: [action.receipt],
        selectedChapterId: selectedChapter?.id,
        selectedLessonId: selectedLesson?.id,
        generation: "success",
        validation: "success",
        validationMessage: "已恢复并核验发布课程。",
        validationError: undefined,
        validationWarningsAcknowledged: false,
        operationError: undefined,
      };
    }

    case "REGISTER_GOVERNED_ASSETS": {
      const visualById = new Map(
        [...(state.governedAssets?.sourceVisuals ?? []), ...action.assets.sourceVisuals].map(
          (item) => [item.visualVersionId, item],
        ),
      );
      const datasetProfileById = new Map(
        [...(state.governedAssets?.datasetProfiles ?? []), ...action.assets.datasetProfiles].map(
          (item) => [item.datasetVersionId, item],
        ),
      );
      return {
        ...state,
        governedAssets: {
          sourceVisuals: [...visualById.values()],
          datasetVersionIds: [
            ...new Set([
              ...(state.governedAssets?.datasetVersionIds ?? []),
              ...action.assets.datasetVersionIds,
            ]),
          ],
          datasetProfiles: [...datasetProfileById.values()],
        },
      };
    }

    case "GENERATION_FAILED":
      return {
        ...state,
        generation: "error",
        operationError: action.message.trim() || "课程生成失败，请重试。",
      };

    case "SELECT_CHAPTER": {
      const chapter = state.course.chapters.find(
        (candidate) => candidate.id === action.chapterId,
      );
      return chapter
        ? {
            ...state,
            selectedChapterId: chapter.id,
            selectedLessonId: chapter.lessons[0]?.id,
          }
        : state;
    }

    case "SELECT_LESSON": {
      const selectedChapter = state.course.chapters.find(
        (chapter) => chapter.id === state.selectedChapterId,
      );
      return selectedChapter?.lessons.some(
        (lesson) => lesson.id === action.lessonId,
      )
        ? { ...state, selectedLessonId: action.lessonId }
        : state;
    }

    case "UPDATE_LESSON": {
      const chapterIndex = state.course.chapters.findIndex(
        (chapter) => chapter.id === action.chapterId,
      );
      const chapter = state.course.chapters[chapterIndex];
      const lessonIndex = chapter?.lessons.findIndex(
        (lesson) => lesson.id === action.lessonId,
      );
      if (chapter === undefined || lessonIndex === undefined || lessonIndex < 0) {
        return state;
      }

      const currentLesson = chapter.lessons[lessonIndex];
      const unchanged = Object.entries(action.patch).every(
        ([key, value]) =>
          (currentLesson as unknown as Record<string, unknown>)[key] === value,
      );
      if (unchanged) {
        return state;
      }

      const lessons = [...chapter.lessons];
      lessons[lessonIndex] = { ...lessons[lessonIndex], ...action.patch };
      const chapters = [...state.course.chapters];
      chapters[chapterIndex] = { ...chapter, lessons };
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          chapters,
          updatedAt: new Date().toISOString(),
        },
      };
    }

    case "MOVE_LESSON": {
      const chapterIndex = state.course.chapters.findIndex(
        (chapter) => chapter.id === action.chapterId,
      );
      const chapter = state.course.chapters[chapterIndex];
      const lessonIndex = chapter?.lessons.findIndex(
        (lesson) => lesson.id === action.lessonId,
      );
      const targetIndex =
        lessonIndex === undefined ? -1 : lessonIndex + action.direction;
      if (
        chapter === undefined ||
        lessonIndex === undefined ||
        lessonIndex < 0 ||
        targetIndex < 0 ||
        targetIndex >= chapter.lessons.length
      ) {
        return state;
      }

      const lessons = [...chapter.lessons];
      [lessons[lessonIndex], lessons[targetIndex]] = [
        lessons[targetIndex],
        lessons[lessonIndex],
      ];
      const chapters = [...state.course.chapters];
      chapters[chapterIndex] = { ...chapter, lessons };
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          chapters,
          updatedAt: new Date().toISOString(),
        },
      };
    }

    case "TOGGLE_LESSON_SOURCE": {
      const sourceExists = state.course.sources.some(
        (source) => source.id === action.sourceId,
      );
      const chapterIndex = state.course.chapters.findIndex(
        (chapter) => chapter.id === action.chapterId,
      );
      const chapter = state.course.chapters[chapterIndex];
      const lessonIndex = chapter?.lessons.findIndex(
        (lesson) => lesson.id === action.lessonId,
      );
      if (
        !sourceExists ||
        chapter === undefined ||
        lessonIndex === undefined ||
        lessonIndex < 0
      ) {
        return state;
      }

      const lesson = chapter.lessons[lessonIndex];
      const sourceIds = lesson.sourceIds.includes(action.sourceId)
        ? lesson.sourceIds.filter((sourceId) => sourceId !== action.sourceId)
        : [...lesson.sourceIds, action.sourceId];
      const hasReadySource = sourceIds.some((sourceId) =>
        state.course.sources.some(
          (source) => source.id === sourceId && source.status === "ready",
        ),
      );
      const lessons = [...chapter.lessons];
      lessons[lessonIndex] = {
        ...lesson,
        sourceIds,
        status: hasReadySource ? "grounded" : "needs-source",
      };
      const chapters = [...state.course.chapters];
      chapters[chapterIndex] = { ...chapter, lessons };
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          chapters,
          updatedAt: new Date().toISOString(),
        },
      };
    }

    case "ADD_CHAPTER": {
      const chapter = {
        id: createId("chapter"),
        title: `新章节 ${state.course.chapters.length + 1}`,
        objective: "填写本章学习目标",
        lessons: [],
      };
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          chapters: [...state.course.chapters, chapter],
          updatedAt: new Date().toISOString(),
        },
        selectedChapterId: chapter.id,
        selectedLessonId: undefined,
      };
    }

    case "ADD_LESSON": {
      const chapterIndex = state.course.chapters.findIndex(
        (chapter) => chapter.id === action.chapterId,
      );
      const chapter = state.course.chapters[chapterIndex];
      if (chapter === undefined) {
        return state;
      }

      const lesson: LessonNode = {
        id: createId("lesson"),
        title: `新小节 ${chapter.lessons.length + 1}`,
        summary: "填写本节内容摘要",
        durationMinutes: 15,
        sourceIds: [],
        status: "needs-source",
      };
      const chapters = [...state.course.chapters];
      chapters[chapterIndex] = {
        ...chapter,
        lessons: [...chapter.lessons, lesson],
      };
      return {
        ...state,
        ...invalidValidationGate,
        ...invalidAssistantState,
        courseRevision: nextCourseRevision(state),
        course: {
          ...state.course,
          chapters,
          updatedAt: new Date().toISOString(),
        },
        selectedChapterId: chapter.id,
        selectedLessonId: lesson.id,
      };
    }

    case "VALIDATION_STARTED":
      return {
        ...state,
        validation: "running",
        validationMessage: undefined,
        validationError: undefined,
        validationWarningsAcknowledged: false,
        operationError: undefined,
      };

    case "VALIDATION_COMPLETED":
      if (action.courseRevision !== state.courseRevision) {
        return state;
      }
      return {
        ...state,
        receipts: [...state.receipts, action.receipt],
        validation: "success",
        validationMessage: "课程验证完成。",
        validationError: undefined,
        validationWarningsAcknowledged: false,
        operationError: undefined,
      };

    case "VALIDATION_FAILED": {
      if (action.courseRevision !== state.courseRevision) {
        return state;
      }
      const message = action.message.trim() || "课程验证失败，请重试。";
      return {
        ...state,
        validation: "error",
        validationMessage: undefined,
        validationError: message,
        validationWarningsAcknowledged: false,
        operationError: message,
      };
    }

    case "ACKNOWLEDGE_VALIDATION_WARNINGS": {
      const receipt = latestApplicableValidationReceipt(state);
      const hasError =
        receipt?.checks.some((check) => check.level === "error") ?? false;
      const hasWarning =
        receipt?.checks.some((check) => check.level === "warning") ?? false;
      const acknowledged =
        action.acknowledged && hasWarning && !hasError;
      return acknowledged === state.validationWarningsAcknowledged
        ? state
        : { ...state, validationWarningsAcknowledged: acknowledged };
    }

    case "ASSISTANT_STARTED":
      return {
        ...state,
        assistant: "running",
        assistantMessage: undefined,
        assistantError: undefined,
        assistantReceiptId: undefined,
        operationError: undefined,
      };

    case "ASSISTANT_COMPLETED": {
      if (action.courseRevision !== state.courseRevision) {
        return state;
      }
      const selectedChapter =
        action.course.chapters.find(
          (chapter) => chapter.id === state.selectedChapterId,
        ) ?? action.course.chapters[0];
      const selectedLesson =
        selectedChapter?.lessons.find(
          (lesson) => lesson.id === state.selectedLessonId,
        ) ?? selectedChapter?.lessons[0];
      const validationResult = action.receipt.kind === "validation";
      const courseChanged = !validationResult && action.course !== state.course;
      return {
        ...state,
        ...(validationResult
          ? {
              validation: "success" as const,
              validationMessage: "课程验证完成。",
              validationError: undefined,
              validationWarningsAcknowledged: false,
            }
          : courseChanged
            ? invalidValidationGate
            : null),
        ...(courseChanged
          ? { courseRevision: nextCourseRevision(state) }
          : null),
        course: action.course,
        receipts: [...state.receipts, action.receipt],
        selectedChapterId: selectedChapter?.id,
        selectedLessonId: selectedLesson?.id,
        assistant: "success",
        assistantMessage: action.message,
        assistantError: undefined,
        assistantReceiptId: action.receipt.id,
        operationError: undefined,
      };
    }

    case "ASSISTANT_FAILED": {
      if (action.courseRevision !== state.courseRevision) {
        return state;
      }
      const message = action.message.trim() || "课程助手执行失败，请重试。";
      return {
        ...state,
        assistant: "error",
        assistantMessage: undefined,
        assistantError: message,
        assistantReceiptId: undefined,
        operationError: message,
      };
    }

    case "PERSISTENCE_FAILED":
      return state.persistenceWarning === action.message
        ? state
        : { ...state, persistenceWarning: action.message };

    case "CLEAR_OPERATION_ERROR":
      return state.operationError === undefined
        ? state
        : { ...state, operationError: undefined };
  }
}

export interface WorkspaceProviderProps {
  children: ReactNode;
  initialState?: WorkspaceState;
  storage?: Pick<Storage, "getItem" | "setItem"> &
    Partial<Pick<Storage, "removeItem">>;
  agent?: CourseAgent;
}

export interface WorkspaceContextValue {
  state: WorkspaceState;
  dispatch: Dispatch<WorkspaceAction>;
  importFiles(files: Iterable<File>): Promise<SourceAsset[]>;
  generateCourse(): Promise<void>;
  validateCurrentCourse(): Promise<EvidenceReceipt | undefined>;
  applyAssistantIntent(intent: string): Promise<boolean>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | undefined>(undefined);

function generationFailureMessage(error: unknown): string {
  const detail =
    error instanceof Error
      ? error.message.trim()
      : typeof error === "string"
        ? error.trim()
        : "";
  return detail
    ? `课程生成失败：${detail}`
    : "课程生成失败，请重试。";
}

function validationFailureMessage(error: unknown): string {
  const detail =
    error instanceof Error
      ? error.message.trim()
      : typeof error === "string"
        ? error.trim()
        : "";
  return detail
    ? `课程验证失败：${detail}`
    : "课程验证失败，请重试。";
}

function assistantFailureMessage(error: unknown): string {
  const detail =
    error instanceof Error
      ? error.message.trim()
      : typeof error === "string"
        ? error.trim()
        : "";
  return detail
    ? `课程助手执行失败：${detail}`
    : "课程助手执行失败，请重试。";
}

function workspaceSnapshot(state: WorkspaceState): WorkspaceSnapshot {
  const derivedLegacySummary: LegacyUnlinkedSummary | undefined =
    state.legacyUnlinked ??
    (state.governed.courseVersionId === undefined &&
    (state.course.sources.length > 0 ||
    state.course.chapters.length > 0 ||
    state.receipts.length > 0)
      ? {
          status: "legacy-unlinked",
          sourceCount: state.course.sources.length,
          chapterCount: state.course.chapters.length,
          lessonCount: state.course.chapters.reduce(
            (total, chapter) => total + chapter.lessons.length,
            0,
          ),
          receiptCount: state.receipts.length,
        }
      : undefined);
  return {
    version: 2,
    governed: state.governed,
    ...(state.personalRunId === undefined
      ? null
      : { personalRunId: state.personalRunId }),
    view: {
      step: state.step,
      ...(state.selectedChapterId === undefined
        ? null
        : { selectedChapterId: state.selectedChapterId }),
      ...(state.selectedLessonId === undefined
        ? null
        : { selectedLessonId: state.selectedLessonId }),
    },
    ...(derivedLegacySummary === undefined
      ? null
      : { legacyUnlinked: derivedLegacySummary }),
    savedAt: new Date().toISOString(),
  };
}

export function WorkspaceProvider({
  children,
  initialState,
  storage,
  agent,
}: WorkspaceProviderProps): JSX.Element {
  const resolvedStorage = storage ?? window.localStorage;
  const resolvedAgent = agent;

  const [state, rawDispatch] = useReducer(
    workspaceReducer,
    { initialState, storage: resolvedStorage },
    ({ initialState: providedState, storage: initialStorage }) =>
      providedState ?? hydrateWorkspaceState(loadWorkspace(initialStorage)),
  );
  const stateRef = useRef(state);
  stateRef.current = state;
  const dispatchedCourseRevision = useRef(state.courseRevision);
  dispatchedCourseRevision.current = state.courseRevision;
  const operationEpoch = useRef(0);
  const generationRunEpoch = useRef(0);
  const validationRunEpoch = useRef(0);
  const assistantRunEpoch = useRef(0);
  const dispatch = useCallback<Dispatch<WorkspaceAction>>(
    (action) => {
      if (action.type === "START_NEW") {
        operationEpoch.current += 1;
        generationRunEpoch.current += 1;
        validationRunEpoch.current += 1;
        assistantRunEpoch.current += 1;
        dispatchedCourseRevision.current = 0;
      } else if (courseMutationWillApply(stateRef.current, action)) {
        dispatchedCourseRevision.current += 1;
      }
      rawDispatch(action);
    },
    [rawDispatch],
  );

  const importFiles = useCallback(
    async (files: Iterable<File>): Promise<SourceAsset[]> => {
      const epoch = operationEpoch.current;
      const sources = await readSourceFiles(files);
      if (epoch === operationEpoch.current) {
        dispatch({ type: "ADD_SOURCES", sources });
      }
      return sources;
    },
    [dispatch],
  );

  const generateCourse = useCallback(async (): Promise<void> => {
    const epoch = operationEpoch.current;
    const generationEpoch = ++generationRunEpoch.current;
    dispatch({ type: "GENERATION_STARTED" });
    try {
      if (resolvedAgent === undefined) {
        throw new Error("课程服务未连接，请从课程工作台启动。");
      }
      const result = await resolvedAgent.generate(state.brief, state.course.sources);
      if (
        epoch === operationEpoch.current &&
        generationEpoch === generationRunEpoch.current
      ) {
        dispatch({
          type: "GENERATION_COMPLETED",
          course: result.course,
          receipt: result.receipt,
        });
      }
    } catch (error: unknown) {
      if (
        epoch === operationEpoch.current &&
        generationEpoch === generationRunEpoch.current
      ) {
        dispatch({
          type: "GENERATION_FAILED",
          message: generationFailureMessage(error),
        });
      }
    }
  }, [dispatch, resolvedAgent, state.brief, state.course.sources]);

  const validateCurrentCourse = useCallback(async (): Promise<
    EvidenceReceipt | undefined
  > => {
    const epoch = operationEpoch.current;
    const courseRevision = dispatchedCourseRevision.current;
    const validationEpoch = ++validationRunEpoch.current;
    dispatch({ type: "VALIDATION_STARTED" });
    try {
      const receipt = await validateCourse(state.course);
      if (
        epoch !== operationEpoch.current ||
        courseRevision !== dispatchedCourseRevision.current ||
        validationEpoch !== validationRunEpoch.current
      ) {
        return undefined;
      }
      dispatch({ type: "VALIDATION_COMPLETED", receipt, courseRevision });
      return receipt;
    } catch (error: unknown) {
      if (
        epoch === operationEpoch.current &&
        courseRevision === dispatchedCourseRevision.current &&
        validationEpoch === validationRunEpoch.current
      ) {
        dispatch({
          type: "VALIDATION_FAILED",
          message: validationFailureMessage(error),
          courseRevision,
        });
      }
      return undefined;
    }
  }, [dispatch, state.course]);

  const applyAssistantIntent = useCallback(
    async (intent: string): Promise<boolean> => {
      const trimmedIntent = intent.trim();
      if (!trimmedIntent) {
        dispatch({
          type: "ASSISTANT_FAILED",
          message: "请输入希望课程助手执行的内容。",
          courseRevision: dispatchedCourseRevision.current,
        });
        return false;
      }

      const epoch = operationEpoch.current;
      const courseRevision = dispatchedCourseRevision.current;
      const assistantEpoch = ++assistantRunEpoch.current;
      const validationIntent = trimmedIntent.includes("检查来源覆盖");
      const validationEpoch = validationIntent
        ? ++validationRunEpoch.current
        : undefined;
      if (validationEpoch !== undefined) {
        dispatch({ type: "VALIDATION_STARTED" });
      }
      dispatch({ type: "ASSISTANT_STARTED" });
      try {
        if (resolvedAgent === undefined) {
          throw new Error("课程服务未连接，请从课程工作台启动。");
        }
        const result = await resolvedAgent.applyIntent(
          state.course,
          trimmedIntent,
          state.selectedChapterId,
        );
        if (
          epoch === operationEpoch.current &&
          courseRevision === dispatchedCourseRevision.current &&
          assistantEpoch === assistantRunEpoch.current &&
          (validationEpoch === undefined ||
            validationEpoch === validationRunEpoch.current)
        ) {
          if (
            result.receipt.checks.some(
              (check) => check.id === "intent-unrecognized",
            )
          ) {
            dispatch({
              type: "ASSISTANT_FAILED",
              message: result.message,
              courseRevision,
            });
            return false;
          }
          dispatch({
            type: "ASSISTANT_COMPLETED",
            course: result.course,
            receipt: result.receipt,
            message: result.message,
            courseRevision,
          });
          return true;
        }
        return false;
      } catch (error: unknown) {
        if (
          epoch === operationEpoch.current &&
          courseRevision === dispatchedCourseRevision.current &&
          assistantEpoch === assistantRunEpoch.current &&
          (validationEpoch === undefined ||
            validationEpoch === validationRunEpoch.current)
        ) {
          if (validationIntent) {
            dispatch({
              type: "VALIDATION_FAILED",
              message: validationFailureMessage(error),
              courseRevision,
            });
          }
          dispatch({
            type: "ASSISTANT_FAILED",
            message: assistantFailureMessage(error),
            courseRevision,
          });
        }
        return false;
      }
    },
    [dispatch, resolvedAgent, state.course, state.selectedChapterId],
  );

  useEffect(() => {
    const result = saveWorkspace(resolvedStorage, workspaceSnapshot(state));
    if (
      result.status === "failed" &&
      result.message !== state.persistenceWarning
    ) {
      rawDispatch({ type: "PERSISTENCE_FAILED", message: result.message });
    }
  }, [rawDispatch, resolvedStorage, state]);

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      state,
      dispatch,
      importFiles,
      generateCourse,
      validateCurrentCourse,
      applyAssistantIntent,
    }),
    [
      applyAssistantIntent,
      dispatch,
      generateCourse,
      importFiles,
      state,
      validateCurrentCourse,
    ],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const value = useContext(WorkspaceContext);
  if (value === undefined) {
    throw new Error("useWorkspace 必须在 WorkspaceProvider 内使用。");
  }
  return value;
}

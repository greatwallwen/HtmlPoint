import { z } from "zod";

import {
  courseBriefSchema,
  courseDocumentSchema,
  evidenceReceiptSchema,
  governedWorkspaceBindingsSchema,
  legacyUnlinkedSummarySchema,
} from "../domain/course-schema";
import type {
  GovernedWorkspaceBindings,
  LegacyUnlinkedSummary,
  WorkflowStep,
} from "../domain/course";

export const WORKSPACE_STORAGE_KEY = "personal-ai-course-studio:v2";
export const LEGACY_WORKSPACE_STORAGE_KEY = "personal-ai-course-studio:v1";

export interface WorkspaceViewPreferences {
  step: WorkflowStep;
  selectedChapterId?: string;
  selectedLessonId?: string;
}

export interface PersistedWorkspaceV2 {
  version: 2;
  governed: GovernedWorkspaceBindings;
  personalRunId?: string;
  view: WorkspaceViewPreferences;
  legacyUnlinked?: LegacyUnlinkedSummary;
  savedAt: string;
}

export type WorkspaceSnapshot = PersistedWorkspaceV2;

export type LoadWorkspaceResult =
  | { status: "empty" }
  | { status: "ready"; snapshot: PersistedWorkspaceV2 }
  | { status: "corrupt"; message: string };

export type SaveWorkspaceResult =
  | { status: "saved" }
  | { status: "failed"; message: string };

type MigrationStorage = Pick<Storage, "getItem"> &
  Partial<Pick<Storage, "setItem" | "removeItem">>;
type SaveStorage = Pick<Storage, "setItem"> & Partial<Pick<Storage, "getItem">>;

const opaqueIdSchema = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
const workflowStepSchema = z.enum(["import", "generate", "edit", "teach"]);

const workspaceViewPreferencesSchema = z
  .object({
    step: workflowStepSchema,
    selectedChapterId: opaqueIdSchema.optional(),
    selectedLessonId: opaqueIdSchema.optional(),
  })
  .strict() satisfies z.ZodType<WorkspaceViewPreferences>;

export const persistedWorkspaceV2Schema = z
  .object({
    version: z.literal(2),
    governed: governedWorkspaceBindingsSchema,
    personalRunId: z.string().regex(/^personal-run-[0-9a-f]{32}$/).optional(),
    view: workspaceViewPreferencesSchema,
    legacyUnlinked: legacyUnlinkedSummarySchema.optional(),
    savedAt: z.string().datetime({ offset: true }),
  })
  .strict() satisfies z.ZodType<PersistedWorkspaceV2>;

const draftCourseDocumentSchema = courseDocumentSchema.extend({
  audience: z.string(),
  goal: z.string(),
});
const draftCourseBriefSchema = courseBriefSchema.extend({
  audience: z.string(),
  goal: z.string(),
});
const legacyWorkspaceV1Schema = z
  .object({
    version: z.literal(1),
    step: workflowStepSchema,
    course: draftCourseDocumentSchema,
    brief: draftCourseBriefSchema,
    receipts: z.array(evidenceReceiptSchema).max(10_000),
    activeValidationReceiptId: z.string().min(1).optional(),
    selectedChapterId: z.string().min(1).max(128).optional(),
    selectedLessonId: z.string().min(1).max(128).optional(),
    savedAt: z.string().min(1),
  })
  .strict()
  .superRefine((value, context) => {
    const lessonCount = value.course.chapters.reduce(
      (total, chapter) => total + chapter.lessons.length,
      0,
    );
    if (
      value.course.sources.length > 10_000 ||
      value.course.chapters.length > 1_000 ||
      lessonCount > 10_000
    ) {
      context.addIssue({ code: "custom", message: "legacy workspace is unbounded" });
    }
  });

function legacySummary(
  legacy: z.infer<typeof legacyWorkspaceV1Schema>,
): LegacyUnlinkedSummary {
  return {
    status: "legacy-unlinked",
    sourceCount: legacy.course.sources.length,
    chapterCount: legacy.course.chapters.length,
    lessonCount: legacy.course.chapters.reduce(
      (total, chapter) => total + chapter.lessons.length,
      0,
    ),
    receiptCount: legacy.receipts.length,
  };
}

function migrateLegacy(
  legacy: z.infer<typeof legacyWorkspaceV1Schema>,
): PersistedWorkspaceV2 {
  const selectedChapterId = opaqueIdSchema.safeParse(legacy.selectedChapterId);
  const selectedLessonId = opaqueIdSchema.safeParse(legacy.selectedLessonId);
  return persistedWorkspaceV2Schema.parse({
    version: 2,
    governed: {
      cardVersionIds: [],
      visualPlacementIds: [],
    },
    view: {
      step: legacy.step === "teach" ? "edit" : legacy.step,
      ...(selectedChapterId.success
        ? { selectedChapterId: selectedChapterId.data }
        : null),
      ...(selectedLessonId.success
        ? { selectedLessonId: selectedLessonId.data }
        : null),
    },
    legacyUnlinked: legacySummary(legacy),
    savedAt: new Date().toISOString(),
  });
}

export function serializeWorkspaceV2(snapshot: PersistedWorkspaceV2): string {
  return JSON.stringify(persistedWorkspaceV2Schema.parse(snapshot));
}

function cleanupLegacyAfterValidatedV2(storage: MigrationStorage): void {
  if (storage.removeItem === undefined) {
    return;
  }
  try {
    if (storage.getItem(LEGACY_WORKSPACE_STORAGE_KEY) !== null) {
      storage.removeItem(LEGACY_WORKSPACE_STORAGE_KEY);
    }
  } catch {
    // A validated v2 remains authoritative; cleanup can safely retry later.
  }
}

export function loadWorkspace(storage: MigrationStorage): LoadWorkspaceResult {
  try {
    const storedV2 = storage.getItem(WORKSPACE_STORAGE_KEY);
    if (storedV2 !== null) {
      const snapshot = persistedWorkspaceV2Schema.parse(JSON.parse(storedV2));
      cleanupLegacyAfterValidatedV2(storage);
      return { status: "ready", snapshot };
    }

    const storedV1 = storage.getItem(LEGACY_WORKSPACE_STORAGE_KEY);
    if (storedV1 === null) {
      return { status: "empty" };
    }
    if (storage.setItem === undefined || storage.removeItem === undefined) {
      return { status: "corrupt", message: "旧工作区需要安全迁移后才能读取。" };
    }

    const legacy = legacyWorkspaceV1Schema.parse(JSON.parse(storedV1));
    const migrated = migrateLegacy(legacy);
    const serialized = serializeWorkspaceV2(migrated);
    let wroteV2 = false;
    try {
      storage.setItem(WORKSPACE_STORAGE_KEY, serialized);
      wroteV2 = true;
      const written = storage.getItem(WORKSPACE_STORAGE_KEY);
      if (
        written !== serialized ||
        serializeWorkspaceV2(
          persistedWorkspaceV2Schema.parse(JSON.parse(written ?? "null")),
        ) !== serialized
      ) {
        throw new Error("workspace migration verification failed");
      }
    } catch {
      if (wroteV2) {
        try {
          storage.removeItem(WORKSPACE_STORAGE_KEY);
        } catch {
          // The legacy key remains authoritative and is never deleted here.
        }
      }
      return { status: "corrupt", message: "旧工作区迁移失败，原数据已保留。" };
    }

    try {
      storage.removeItem(LEGACY_WORKSPACE_STORAGE_KEY);
    } catch {
      // The fully validated v2 is already authoritative.
    }
    return { status: "ready", snapshot: migrated };
  } catch {
    return { status: "corrupt", message: "工作区数据损坏，无法读取。" };
  }
}

export function saveWorkspace(
  storage: SaveStorage,
  snapshot: PersistedWorkspaceV2,
): SaveWorkspaceResult {
  try {
    const serialized = serializeWorkspaceV2(snapshot);
    if (storage.getItem?.(WORKSPACE_STORAGE_KEY) === serialized) {
      return { status: "saved" };
    }
    storage.setItem(WORKSPACE_STORAGE_KEY, serialized);
    return { status: "saved" };
  } catch {
    return { status: "failed", message: "工作区保存失败。" };
  }
}

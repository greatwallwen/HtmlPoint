import { z } from "zod";

import type {
  ChapterNode,
  CourseBrief,
  CourseDocument,
  EvidenceReceipt,
  GovernedWorkspaceBindings,
  LegacyUnlinkedSummary,
  LessonNode,
  SourceAsset,
} from "./course";

const nonEmptyString = z.string().min(1);
const opaqueIdSchema = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
const nonBlankString = z.string().refine((value) => value.trim().length > 0);
const courseDurationSchema = z.number().int().min(5).max(480);
const lessonDurationSchema = z.number().int().min(5).max(90);

const sourceKindSchema = z.enum([
  "markdown",
  "text",
  "pdf",
  "pptx",
  "docx",
  "web",
  "note",
]);

const parseStatusSchema = z.enum([
  "queued",
  "reading",
  "ready",
  "unsupported",
  "failed",
]);

const lessonStatusSchema = z.enum(["draft", "grounded", "needs-source"]);
const evidenceKindSchema = z.enum(["generation", "validation", "rehearsal"]);
const validationLevelSchema = z.enum(["pass", "warning", "error"]);

export const sourceAssetSchema = z.object({
  id: nonEmptyString,
  name: nonEmptyString,
  kind: sourceKindSchema,
  size: z.number().int().nonnegative(),
  status: parseStatusSchema,
  extractedText: nonBlankString.optional(),
  failureReason: nonBlankString.optional(),
  addedAt: nonEmptyString,
}).superRefine((source, context) => {
  if (source.status !== "failed" && source.failureReason !== undefined) {
    context.addIssue({
      code: "custom",
      path: ["failureReason"],
      message: "Only failed sources can include a failure reason.",
    });
  }
}) satisfies z.ZodType<SourceAsset>;

export const lessonNodeSchema = z.object({
  id: nonEmptyString,
  title: nonEmptyString,
  summary: nonEmptyString,
  durationMinutes: lessonDurationSchema,
  sourceIds: z.array(nonEmptyString),
  status: lessonStatusSchema,
}) satisfies z.ZodType<LessonNode>;

export const chapterNodeSchema = z.object({
  id: nonEmptyString,
  title: nonEmptyString,
  objective: nonEmptyString,
  lessons: z.array(lessonNodeSchema),
}) satisfies z.ZodType<ChapterNode>;

export const courseBriefSchema = z.object({
  title: nonEmptyString,
  audience: nonEmptyString,
  goal: nonEmptyString,
  durationMinutes: courseDurationSchema,
}) satisfies z.ZodType<CourseBrief>;

export const evidenceReceiptSchema = z.object({
  id: nonEmptyString,
  courseId: nonEmptyString,
  kind: evidenceKindSchema,
  createdAt: nonEmptyString,
  inputDigest: nonEmptyString,
  summary: nonEmptyString,
  checks: z.array(z.object({
    id: nonEmptyString,
    level: validationLevelSchema,
    message: nonEmptyString,
  })),
}) satisfies z.ZodType<EvidenceReceipt>;

export const courseDocumentSchema = z.object({
  schemaVersion: z.literal(1),
  id: nonEmptyString,
  title: nonEmptyString,
  audience: nonEmptyString,
  goal: nonEmptyString,
  durationMinutes: courseDurationSchema,
  chapters: z.array(chapterNodeSchema),
  sources: z.array(sourceAssetSchema),
  updatedAt: nonEmptyString,
}) satisfies z.ZodType<CourseDocument>;

const uniqueOpaqueIds = (maximum: number) =>
  z
    .array(opaqueIdSchema)
    .max(maximum)
    .refine((values) => new Set(values).size === values.length);

export const governedWorkspaceBindingsSchema = z
  .object({
    requirementId: opaqueIdSchema.optional(),
    outlineVersionId: opaqueIdSchema.optional(),
    courseVersionId: opaqueIdSchema.optional(),
    slideDeckId: opaqueIdSchema.optional(),
    runtimeManifestId: opaqueIdSchema.optional(),
    cardVersionIds: uniqueOpaqueIds(500),
    visualPlacementIds: uniqueOpaqueIds(500),
  })
  .strict() satisfies z.ZodType<GovernedWorkspaceBindings>;

export const legacyUnlinkedSummarySchema = z
  .object({
    status: z.literal("legacy-unlinked"),
    sourceCount: z.number().int().min(0).max(10_000),
    chapterCount: z.number().int().min(0).max(1_000),
    lessonCount: z.number().int().min(0).max(10_000),
    receiptCount: z.number().int().min(0).max(10_000),
  })
  .strict() satisfies z.ZodType<LegacyUnlinkedSummary>;

const teachingChapterNodeSchema = chapterNodeSchema.extend({
  lessons: z.array(lessonNodeSchema).min(1),
});

export const teachingCourseSchema = courseDocumentSchema.extend({
  chapters: z.array(teachingChapterNodeSchema).min(1),
});

export const validateForTeaching = (value: unknown): CourseDocument =>
  teachingCourseSchema.parse(value);

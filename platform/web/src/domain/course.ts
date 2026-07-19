export type WorkflowStep = "import" | "generate" | "edit" | "teach";
export type ParseStatus = "queued" | "reading" | "ready" | "unsupported" | "failed";
export type ValidationLevel = "pass" | "warning" | "error";
export type SourceKind = "markdown" | "text" | "pdf" | "pptx" | "docx" | "web" | "note";
export type LessonStatus = "draft" | "grounded" | "needs-source";
export type EvidenceKind = "generation" | "validation" | "rehearsal";

export interface SourceAsset {
  id: string;
  name: string;
  kind: SourceKind;
  size: number;
  status: ParseStatus;
  extractedText?: string;
  failureReason?: string;
  addedAt: string;
}

export interface LessonNode {
  id: string;
  title: string;
  summary: string;
  durationMinutes: number;
  sourceIds: string[];
  status: LessonStatus;
}

export interface ChapterNode {
  id: string;
  title: string;
  objective: string;
  lessons: LessonNode[];
}

export interface CourseBrief {
  title: string;
  audience: string;
  goal: string;
  durationMinutes: number;
}

export interface CourseDocument {
  schemaVersion: 1;
  id: string;
  title: string;
  audience: string;
  goal: string;
  durationMinutes: number;
  chapters: ChapterNode[];
  sources: SourceAsset[];
  updatedAt: string;
}

export interface EvidenceReceipt {
  id: string;
  courseId: string;
  kind: EvidenceKind;
  createdAt: string;
  inputDigest: string;
  summary: string;
  checks: Array<{ id: string; level: ValidationLevel; message: string }>;
}

export interface GovernedWorkspaceBindings {
  requirementId?: string;
  outlineVersionId?: string;
  courseVersionId?: string;
  slideDeckId?: string;
  runtimeManifestId?: string;
  cardVersionIds: string[];
  visualPlacementIds: string[];
}

export interface GovernedCourseProjection {
  courseDigest: string;
  usageScope: "private-training" | "internal" | "public";
  courseUpdatedAt: string;
  slideDeck: SlideDeck;
  runtimeManifest?: RuntimeManifest;
  courseProjectionId?: string;
  warnings: string[];
  publicationStatus: "confirmed" | "validated" | "published";
}

export interface GovernedAvailableAssets {
  sourceVisuals: Array<{
    visualVersionId: string;
    sourceVersionId: string;
    label: string;
  }>;
  datasetVersionIds: string[];
  datasetProfiles: Array<{
    datasetVersionId: string;
    contentDigest: string;
    schemaDigest: string;
    rowCount: number;
    columns: Array<{ name: string; dataType: string; digest: string }>;
  }>;
}

export interface LegacyUnlinkedSummary {
  status: "legacy-unlinked";
  sourceCount: number;
  chapterCount: number;
  lessonCount: number;
  receiptCount: number;
}

export const createEmptyGovernedBindings = (): GovernedWorkspaceBindings => ({
  cardVersionIds: [],
  visualPlacementIds: [],
});

export const createId = (prefix: string): string =>
  `${prefix}-${crypto.randomUUID()}`;

export const createEmptyCourse = (now = new Date().toISOString()): CourseDocument => ({
  schemaVersion: 1,
  id: createId("course"),
  title: "未命名课程",
  audience: "",
  goal: "",
  durationMinutes: 90,
  chapters: [],
  sources: [],
  updatedAt: now,
});
import type { RuntimeManifest, SlideDeck } from "./helper-contracts-schema";

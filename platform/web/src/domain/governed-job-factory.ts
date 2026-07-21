import {
  type CardPublishJob,
  type ChartBuildJob,
  type CoursePublishJob,
  type CourseValidateJob,
  type ImportStartJob,
  type KnowledgeIndexJob,
  type PersonalCourseCreateJob,
  type PersonalCourseResolveJob,
  type PersonalCourseStatusJob,
  type ReviewResolveJob,
  type UploadResponse,
  type VisualAcquireJob,
  type VisualAttachJob,
  type VisualRevalidateJob,
  type VisualSearchJob,
} from "./helper-contracts-schema";
import { createId } from "./course";
import { stableDigest } from "./validation";

const ACTOR = { actorType: "human" as const, actorId: "local-user" };

export async function createImportStartJob(
  upload: UploadResponse,
): Promise<ImportStartJob> {
  return {
    type: "knowledge_import_start",
    uploadId: upload.uploadId,
    expectedContentDigest: upload.contentDigest,
    operationId: createId("import-operation"),
    requestDigest: await stableDigest({
      operation: "governed-import-start-v1",
      upload_id: upload.uploadId,
      expected_content_digest: upload.contentDigest,
    }),
    actor: ACTOR,
  };
}

export async function createReviewResolveJob(input: {
  taskId: string;
  decision: "accept" | "reject" | "dismiss";
  expectedReviewDigest: string;
  evidenceIds: string[];
}): Promise<ReviewResolveJob> {
  const evidenceIds = [...input.evidenceIds].sort();
  return {
    type: "knowledge_review_resolve",
    operationId: createId("review-operation"),
    requestDigest: await stableDigest({
      kind: "knowledge_review_resolve",
      decision: input.decision,
      evidenceIds,
      expectedReviewDigest: input.expectedReviewDigest,
      taskId: input.taskId,
    }),
    actor: ACTOR,
    taskId: input.taskId,
    decision: input.decision,
    expectedReviewDigest: input.expectedReviewDigest,
    evidenceIds,
  };
}

export async function createCardPublishJob(input: {
  cardVersionId: string;
  expectedCardDigest: string;
}): Promise<CardPublishJob> {
  return {
    type: "knowledge_card_publish",
    operationId: createId("card-publish-operation"),
    requestDigest: await stableDigest({
      kind: "knowledge_card_publish",
      cardVersionId: input.cardVersionId,
      expectedCardDigest: input.expectedCardDigest,
    }),
    actor: ACTOR,
    ...input,
  };
}

export async function createKnowledgeIndexJob(
  expectedOutboxId: string,
): Promise<KnowledgeIndexJob> {
  return {
    type: "knowledge_index",
    operationId: createId("index-operation"),
    requestDigest: await stableDigest({
      kind: "knowledge_index",
      expectedOutboxId,
    }),
    actor: ACTOR,
    expectedOutboxId,
  };
}

export async function createPersonalCourseJob(input: {
  requestId: string;
  prompt: string;
  sourceVersionIds: string[];
  titleHint?: string | null;
  createdAt?: string;
}): Promise<PersonalCourseCreateJob> {
  const request = {
    requestId: input.requestId,
    prompt: input.prompt,
    sourceVersionIds: input.sourceVersionIds,
    titleHint: input.titleHint ?? null,
    createdAt: input.createdAt ?? new Date().toISOString(),
  };
  return {
    type: "personal_course_create",
    operationId: createId("personal-create-operation"),
    requestDigest: await stableDigest({
      kind: "personal_course_create",
      request,
    }),
    actor: ACTOR,
    request,
  };
}

export function createPersonalCourseStatusJob(runId: string): PersonalCourseStatusJob {
  return { type: "personal_course_status", runId, actor: ACTOR };
}

export async function createPersonalCourseResolveJob(input: {
  runId: string;
  expectedAttentionDigest: string;
  action: PersonalCourseResolveJob["action"];
}): Promise<PersonalCourseResolveJob> {
  return {
    type: "personal_course_resolve",
    operationId: createId("personal-resolve-operation"),
    requestDigest: await stableDigest({
      kind: "personal_course_resolve",
      action: input.action,
      expectedAttentionDigest: input.expectedAttentionDigest,
      runId: input.runId,
    }),
    actor: ACTOR,
    ...input,
  };
}

export async function createChartBuildJob(
  specs: ChartBuildJob["specs"],
): Promise<ChartBuildJob> {
  return {
    type: "chart_build",
    operationId: createId("chart-build-operation"),
    requestDigest: await stableDigest({ kind: "chart_build", specs }),
    actor: ACTOR,
    specs,
  };
}

export async function createVisualSearchJob(
  query: string,
  limit = 6,
): Promise<VisualSearchJob> {
  return {
    type: "visual_search",
    operationId: createId("visual-search-operation"),
    requestDigest: await stableDigest({ kind: "visual_search", limit, query }),
    actor: ACTOR,
    query,
    limit,
  };
}

export async function createVisualAcquireJob(
  candidateIds: string[],
): Promise<VisualAcquireJob> {
  return {
    type: "visual_acquire",
    operationId: createId("visual-acquire-operation"),
    requestDigest: await stableDigest({
      kind: "visual_acquire",
      candidateIds,
    }),
    actor: ACTOR,
    candidateIds,
  };
}

export async function createVisualRevalidateJob(
  visualVersionId: string,
): Promise<VisualRevalidateJob> {
  return {
    type: "visual_revalidate",
    operationId: createId("visual-revalidate-operation"),
    requestDigest: await stableDigest({
      kind: "visual_revalidate",
      visualVersionId,
    }),
    actor: ACTOR,
    visualVersionId,
  };
}

export async function createVisualAttachJob(input: Omit<
  VisualAttachJob,
  "type" | "operationId" | "requestDigest" | "actor"
>): Promise<VisualAttachJob> {
  const operationId = createId("visual-attach-operation");
  return {
    type: "course_visual_attach",
    operationId,
    requestDigest: await stableDigest({
      kind: "course_visual_attach",
      ...input,
    }),
    actor: ACTOR,
    ...input,
  };
}

async function createCourseProjectionJob(
  type: "course_validate" | "course_publish",
  input: {
    courseVersionId: string;
    expectedCourseDigest: string;
    visualPlacementIds: string[];
  },
): Promise<CourseValidateJob | CoursePublishJob> {
  const requestDigest = await stableDigest({
    confirmed_course_version_id: input.courseVersionId,
    expected_course_digest: input.expectedCourseDigest,
    visual_placement_ids: input.visualPlacementIds,
    job_bindings: [],
  });
  return {
    type,
    operationId: createId(
      type === "course_validate" ? "course-validate-operation" : "course-publish-operation",
    ),
    requestDigest,
    actor: ACTOR,
    ...input,
  };
}

export async function createCourseValidateJob(input: {
  courseVersionId: string;
  expectedCourseDigest: string;
  visualPlacementIds: string[];
}): Promise<CourseValidateJob> {
  return (await createCourseProjectionJob("course_validate", input)) as CourseValidateJob;
}

export async function createCoursePublishJob(input: {
  courseVersionId: string;
  expectedCourseDigest: string;
  visualPlacementIds: string[];
}): Promise<CoursePublishJob> {
  return (await createCourseProjectionJob("course_publish", input)) as CoursePublishJob;
}

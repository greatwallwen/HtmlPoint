import type { z } from "zod";

import type {
  CardPublishJob,
  ChartBuildJob,
  CourseComposeJob,
  CoursePublishJob,
  CourseProjectionResponse,
  CourseValidateJob,
  Evidence,
  ImportCancelJob,
  ImportStartJob,
  ImportStatusJob,
  KnowledgeIndexJob,
  OperationStatusJob,
  OutlineConfirmJob,
  PersonalCourseCreateJob,
  PersonalCourseResolveJob,
  PersonalCourseStatusJob,
  ReviewDetailJob,
  ReviewListJob,
  ReviewResolveJob,
  SourceInventoryResponse,
  TrustedExternalLink,
  UpgradeListJob,
  UpgradeResolveJob,
  UploadResponse,
  VisualAcquireJob,
  VisualAttachJob,
  VisualDetachJob,
  VisualRevalidateJob,
  VisualSearchJob,
} from "../domain/helper-contracts-schema";
import {
  cardPublishJobSchema,
  cardPublishResultSchema,
  chartBuildJobSchema,
  chartBuildResultSchema,
  courseComposeJobSchema,
  courseComposeResultSchema,
  coursePublishJobSchema,
  coursePublishResultSchema,
  courseProjectionResponseSchema,
  courseValidateJobSchema,
  courseValidateResultSchema,
  importCancelJobSchema,
  importStartJobSchema,
  importStartResultSchema,
  importStatusJobSchema,
  importStatusResultSchema,
  jobResponseSchema,
  knowledgeIndexJobSchema,
  knowledgeIndexResultSchema,
  operationStatusJobSchema,
  operationStatusResultSchema,
  outlineConfirmJobSchema,
  outlineConfirmResultSchema,
  personalCourseCreateJobSchema,
  personalCourseResolveJobSchema,
  personalCourseResultSchema,
  personalCourseStatusJobSchema,
  reviewDetailJobSchema,
  reviewDetailResultSchema,
  reviewListJobSchema,
  reviewListResultSchema,
  reviewResolveJobSchema,
  reviewResolveResultSchema,
  sourceInventoryResponseSchema,
  trustedExternalLinkSchema,
  upgradeListJobSchema,
  upgradeListResultSchema,
  upgradeResolveJobSchema,
  upgradeResolveResultSchema,
  uploadResponseSchema,
  visualAcquireJobSchema,
  visualAcquireResultSchema,
  visualAttachJobSchema,
  visualAttachResultSchema,
  visualDetachJobSchema,
  visualDetachResultSchema,
  visualRevalidateJobSchema,
  visualRevalidateResultSchema,
  visualSearchJobSchema,
  visualSearchResultSchema,
  opaqueIdSchema,
} from "../domain/helper-contracts-schema";
import type {
  KnowledgeSummary,
  KnowledgeSummaryClient,
} from "../domain/knowledge";
import { knowledgeSummarySchema } from "../domain/knowledge-schema";
import type { VerifiedHelperSession } from "./helper-session";

const SUMMARY_PATH = "/v1/knowledge/summary";
const SOURCES_PATH = "/v1/knowledge/sources";
const UPLOADS_PATH = "/v1/uploads";
const JOBS_PATH = "/v1/jobs";
const READ_TIMEOUT_MS = 5_000;
const JOB_TIMEOUT_MS = 60_000;
const UPLOAD_TIMEOUT_MS = 120_000;
export const SAFE_HELPER_FAILURE_MESSAGE = "本地知识服务暂不可用";

type JobResult<Schema extends z.ZodTypeAny> = {
  result: z.infer<Schema>;
  evidence: Evidence;
};

function operationIdOf(value: unknown): string | undefined {
  if (typeof value !== "object" || value === null || !("operationId" in value)) {
    return undefined;
  }
  return typeof value.operationId === "string" ? value.operationId : undefined;
}

export function trustedExternalLinkProps(link: TrustedExternalLink): {
  href: string;
  target: "_blank";
  rel: "noopener noreferrer external";
} {
  const verified = trustedExternalLinkSchema.parse(link);
  return {
    href: verified.href,
    target: "_blank",
    rel: "noopener noreferrer external",
  };
}

export type SessionRefresher = () => Promise<VerifiedHelperSession | undefined>;

export class KnowledgeClient implements KnowledgeSummaryClient {
  readonly #helperOrigin: string;
  #sessionToken: string;
  readonly #refreshSession?: SessionRefresher;

  constructor(session: VerifiedHelperSession, refreshSession?: SessionRefresher) {
    this.#helperOrigin = session.helperOrigin;
    this.#sessionToken = session.sessionToken;
    this.#refreshSession = refreshSession;
  }

  async #fetchJson<Schema extends z.ZodTypeAny>(
    path: string,
    init: RequestInit,
    schema: Schema,
    timeoutMs: number,
  ): Promise<z.infer<Schema>> {
    const result = await this.#doFetch(path, init, schema, timeoutMs, this.#sessionToken);
    if (!result.ok && result.status === 401 && this.#refreshSession) {
      console.warn("[KnowledgeClient] 401 received, attempting session refresh");
      const refreshed = await this.#refreshSession();
      if (refreshed) {
        console.info("[KnowledgeClient] session refreshed, retrying request");
        this.#sessionToken = refreshed.sessionToken;
        const retry = await this.#doFetch(path, init, schema, timeoutMs, this.#sessionToken);
        if (retry.ok) return retry.value;
        console.error("[KnowledgeClient] retry also failed", retry.status);
        throw retry.error;
      }
      console.error("[KnowledgeClient] session refresh returned undefined");
    }
    if (result.ok) return result.value;
    throw result.error;
  }

  async #doFetch<Schema extends z.ZodTypeAny>(
    path: string,
    init: RequestInit,
    schema: Schema,
    timeoutMs: number,
    token: string,
  ): Promise<{ ok: true; value: z.infer<Schema> } | { ok: false; status: number; error: Error }> {
    const controller = new AbortController();
    const timeout = globalThis.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${this.#helperOrigin}${path}`, {
        ...init,
        credentials: "omit",
        headers: {
          Accept: "application/json",
          "X-Course-Session": token,
          ...init.headers,
        },
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = "";
        try {
          detail = await response.text();
        } catch {
          // ignore
        }
        return {
          ok: false,
          status: response.status,
          error: new Error(
            `${SAFE_HELPER_FAILURE_MESSAGE}（HTTP ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ""}）`,
          ),
        };
      }
      return { ok: true, value: schema.parse(await response.json()) };
    } catch (error) {
      if (error instanceof Error && error.message.startsWith(SAFE_HELPER_FAILURE_MESSAGE)) {
        return { ok: false, status: 0, error: error };
      }
      return {
        ok: false,
        status: 0,
        error: new Error(`${SAFE_HELPER_FAILURE_MESSAGE}（${error instanceof Error ? error.message : String(error)}）`),
      };
    } finally {
      globalThis.clearTimeout(timeout);
    }
  }

  async #runJob<Job, Schema extends z.ZodTypeAny>(
    rawJob: Job,
    requestSchema: z.ZodType<Job>,
    resultSchema: Schema,
  ): Promise<JobResult<Schema>> {
    let job: Job;
    try {
      job = requestSchema.parse(rawJob);
    } catch {
      throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
    }
    const response = (await this.#fetchJson(
      JOBS_PATH,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(job),
      },
      jobResponseSchema(resultSchema),
      JOB_TIMEOUT_MS,
    )) as JobResult<Schema>;
    const requestOperationId = operationIdOf(job);
    const resultOperationId = operationIdOf(response.result);
    if (
      requestOperationId !== undefined &&
      resultOperationId !== undefined &&
      requestOperationId !== resultOperationId
    ) {
      throw new Error(SAFE_HELPER_FAILURE_MESSAGE);
    }
    return response;
  }

  getSummary(): Promise<KnowledgeSummary> {
    return this.#fetchJson(
      SUMMARY_PATH,
      { method: "GET" },
      knowledgeSummarySchema,
      READ_TIMEOUT_MS,
    );
  }

  uploadSource(file: Blob, fileName: string): Promise<UploadResponse> {
    if (
      file.size < 1 ||
      file.size > 20 * 1024 * 1024 ||
      fileName.trim() === "" ||
      fileName.length > 255 ||
      file.type.trim() === ""
    ) {
      return Promise.reject(new Error(SAFE_HELPER_FAILURE_MESSAGE));
    }
    return this.#fetchJson(
      UPLOADS_PATH,
      {
        method: "POST",
        headers: {
          "Content-Type": file.type,
          "X-Upload-Name": encodeURIComponent(fileName),
        },
        body: file,
      },
      uploadResponseSchema,
      UPLOAD_TIMEOUT_MS,
    );
  }

  listSources(options: { cursor?: string; limit?: number } = {}): Promise<SourceInventoryResponse> {
    const limit = options.limit ?? 50;
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return Promise.reject(new Error(SAFE_HELPER_FAILURE_MESSAGE));
    }
    const query = new URLSearchParams({ limit: String(limit) });
    if (options.cursor !== undefined) {
      if (options.cursor.length < 1 || options.cursor.length > 256) {
        return Promise.reject(new Error(SAFE_HELPER_FAILURE_MESSAGE));
      }
      query.set("cursor", options.cursor);
    }
    return this.#fetchJson(
      `${SOURCES_PATH}?${query.toString()}`,
      { method: "GET" },
      sourceInventoryResponseSchema,
      READ_TIMEOUT_MS,
    );
  }

  getCourseProjection(input: {
    courseVersionId: string;
    slideDeckId: string;
    runtimeManifestId: string;
  }): Promise<CourseProjectionResponse> {
    let courseVersionId: string;
    let slideDeckId: string;
    let runtimeManifestId: string;
    try {
      courseVersionId = opaqueIdSchema.parse(input.courseVersionId);
      slideDeckId = opaqueIdSchema.parse(input.slideDeckId);
      runtimeManifestId = opaqueIdSchema.parse(input.runtimeManifestId);
    } catch {
      return Promise.reject(new Error(SAFE_HELPER_FAILURE_MESSAGE));
    }
    const query = new URLSearchParams({ slideDeckId, runtimeManifestId });
    return this.#fetchJson(
      `/v1/courses/${encodeURIComponent(courseVersionId)}/projection?${query.toString()}`,
      { method: "GET" },
      courseProjectionResponseSchema,
      READ_TIMEOUT_MS,
    );
  }

  startImport(job: ImportStartJob) {
    return this.#runJob(job, importStartJobSchema, importStartResultSchema);
  }

  getImportStatus(job: ImportStatusJob) {
    return this.#runJob(job, importStatusJobSchema, importStatusResultSchema);
  }

  cancelImport(job: ImportCancelJob) {
    return this.#runJob(job, importCancelJobSchema, operationStatusResultSchema);
  }

  recoverOperation(job: OperationStatusJob) {
    return this.#runJob(job, operationStatusJobSchema, operationStatusResultSchema);
  }

  listReviews(job: ReviewListJob) {
    return this.#runJob(job, reviewListJobSchema, reviewListResultSchema);
  }

  getReviewDetail(job: ReviewDetailJob) {
    return this.#runJob(job, reviewDetailJobSchema, reviewDetailResultSchema);
  }

  listUpgrades(job: UpgradeListJob) {
    return this.#runJob(job, upgradeListJobSchema, upgradeListResultSchema);
  }

  resolveReview(job: ReviewResolveJob) {
    return this.#runJob(job, reviewResolveJobSchema, reviewResolveResultSchema);
  }

  publishCard(job: CardPublishJob) {
    return this.#runJob(job, cardPublishJobSchema, cardPublishResultSchema);
  }

  resolveUpgrade(job: UpgradeResolveJob) {
    return this.#runJob(job, upgradeResolveJobSchema, upgradeResolveResultSchema);
  }

  indexKnowledge(job: KnowledgeIndexJob) {
    return this.#runJob(job, knowledgeIndexJobSchema, knowledgeIndexResultSchema);
  }

  createPersonalCourse(job: PersonalCourseCreateJob) {
    return this.#runJob(job, personalCourseCreateJobSchema, personalCourseResultSchema);
  }

  getPersonalCourse(job: PersonalCourseStatusJob) {
    return this.#runJob(job, personalCourseStatusJobSchema, personalCourseResultSchema);
  }

  getPersonalCourseProjection(runId: string): Promise<CourseProjectionResponse> {
    if (!/^personal-run-[0-9a-f]{32}$/.test(runId)) {
      return Promise.reject(new Error(SAFE_HELPER_FAILURE_MESSAGE));
    }
    return this.#fetchJson(
      `/v1/personal-courses/${encodeURIComponent(runId)}/projection`,
      { method: "GET" },
      courseProjectionResponseSchema,
      READ_TIMEOUT_MS,
    );
  }

  resolvePersonalCourseAttention(job: PersonalCourseResolveJob) {
    return this.#runJob(job, personalCourseResolveJobSchema, personalCourseResultSchema);
  }

  composeCourse(job: CourseComposeJob) {
    return this.#runJob(job, courseComposeJobSchema, courseComposeResultSchema);
  }

  confirmOutline(job: OutlineConfirmJob) {
    return this.#runJob(job, outlineConfirmJobSchema, outlineConfirmResultSchema);
  }

  buildCharts(job: ChartBuildJob) {
    return this.#runJob(job, chartBuildJobSchema, chartBuildResultSchema);
  }

  searchVisuals(job: VisualSearchJob) {
    return this.#runJob(job, visualSearchJobSchema, visualSearchResultSchema);
  }

  acquireVisuals(job: VisualAcquireJob) {
    return this.#runJob(job, visualAcquireJobSchema, visualAcquireResultSchema);
  }

  revalidateVisual(job: VisualRevalidateJob) {
    return this.#runJob(job, visualRevalidateJobSchema, visualRevalidateResultSchema);
  }

  attachVisual(job: VisualAttachJob) {
    return this.#runJob(job, visualAttachJobSchema, visualAttachResultSchema);
  }

  detachVisual(job: VisualDetachJob) {
    return this.#runJob(job, visualDetachJobSchema, visualDetachResultSchema);
  }

  validateCourse(job: CourseValidateJob) {
    return this.#runJob(job, courseValidateJobSchema, courseValidateResultSchema);
  }

  publishCourse(job: CoursePublishJob) {
    return this.#runJob(job, coursePublishJobSchema, coursePublishResultSchema);
  }
}

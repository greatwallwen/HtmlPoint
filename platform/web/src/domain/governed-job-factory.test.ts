import { describe, expect, it } from "vitest";

import {
  cardPublishJobSchema,
  chartBuildJobSchema,
  coursePublishJobSchema,
  importStartJobSchema,
  knowledgeIndexJobSchema,
  personalCourseCreateJobSchema,
  reviewResolveJobSchema,
  visualAttachJobSchema,
} from "./helper-contracts-schema";
import {
  createCardPublishJob,
  createChartBuildJob,
  createCoursePublishJob,
  createImportStartJob,
  createKnowledgeIndexJob,
  createPersonalCourseJob,
  createReviewResolveJob,
  createVisualAttachJob,
} from "./governed-job-factory";
import { stableDigest } from "./validation";

const digest = "a".repeat(64);

describe("governed job factory", () => {
  it("binds import, review, publication, indexing, visual, and course jobs to canonical inputs", async () => {
    const imported = await createImportStartJob({
      schemaVersion: 1,
      uploadId: `upload-${"1".repeat(32)}`,
      safeName: "lesson.md",
      sourceKind: "markdown",
      mediaType: "text/markdown",
      byteSize: 10,
      contentDigest: digest,
      state: "available",
      expiresAt: "2026-07-19T08:00:00Z",
    });
    expect(importStartJobSchema.parse(imported)).toEqual(imported);
    expect(imported.requestDigest).toBe(await stableDigest({
      operation: "governed-import-start-v1",
      upload_id: imported.uploadId,
      expected_content_digest: digest,
    }));

    const review = await createReviewResolveJob({
      taskId: "task-1",
      decision: "accept",
      expectedReviewDigest: digest,
      evidenceIds: ["evidence-2", "evidence-1"],
    });
    expect(reviewResolveJobSchema.parse(review)).toEqual(review);
    expect(review.evidenceIds).toEqual(["evidence-1", "evidence-2"]);

    const published = await createCardPublishJob({ cardVersionId: "card-1", expectedCardDigest: digest });
    expect(cardPublishJobSchema.parse(published)).toEqual(published);
    const indexed = await createKnowledgeIndexJob("index-outbox-1");
    expect(knowledgeIndexJobSchema.parse(indexed)).toEqual(indexed);

    const chart = await createChartBuildJob([{
      requestId: "chart-request-1",
      chartType: "bar",
      datasetVersionId: "dataset-1",
      expectedDatasetDigest: digest,
      expectedSchemaDigest: digest,
      xColumn: "segment",
      xColumnDigest: digest,
      yColumn: "revenue",
      yColumnDigest: digest,
      aggregate: "count",
      title: "客户分群",
      description: "按客户分群汇总",
      maxResultRows: 50,
    }]);
    expect(chartBuildJobSchema.parse(chart)).toEqual(chart);
    expect(chart.requestDigest).toBe(await stableDigest({ kind: "chart_build", specs: chart.specs }));

    const visual = await createVisualAttachJob({
      courseVersionId: "course-1",
      expectedCourseDigest: digest,
      placementId: "visual-placement-1",
      visualVersionId: "visual-1",
      slideNodeId: "slide-1",
      slotId: "primary",
      fit: "contain",
      crop: null,
      altText: "真实图形",
      transformation: {
        transformationId: "transform-1",
        crop: null,
        scaleMode: "contain",
        colorAdjustments: [],
        changeNotice: null,
        derivativeLicenseDecision: "not-derivative",
        exportLicense: null,
        shareAlikeCompatible: true,
        gfdlCompatible: true,
        noDerivativesCompatible: true,
      },
      originatingCardVersionId: "card-1",
      originatingSourceVersionId: null,
      originatingDatasetVersionId: null,
    });
    expect(visualAttachJobSchema.parse(visual)).toEqual(visual);

    const course = await createCoursePublishJob({
      courseVersionId: "course-1",
      expectedCourseDigest: digest,
      visualPlacementIds: ["visual-placement-1"],
    });
    expect(coursePublishJobSchema.parse(course)).toEqual(course);
    expect(course.requestDigest).toBe(await stableDigest({
      confirmed_course_version_id: "course-1",
      expected_course_digest: digest,
      visual_placement_ids: ["visual-placement-1"],
      job_bindings: [],
    }));
  });

  it("digests personal course timestamps in the Helper canonical microsecond form", async () => {
    const job = await createPersonalCourseJob({
      requestId: `personal-request-${"1".repeat(32)}`,
      prompt: "制作 AI 实战课",
      sourceVersionIds: ["source-version-1"],
      createdAt: "2026-07-21T03:56:42.544Z",
    });

    expect(personalCourseCreateJobSchema.parse(job)).toEqual(job);
    expect(job.requestDigest).toBe(await stableDigest({
      kind: "personal_course_create",
      request: {
        ...job.request,
        createdAt: "2026-07-21T03:56:42.544000Z",
      },
    }));
  });
});

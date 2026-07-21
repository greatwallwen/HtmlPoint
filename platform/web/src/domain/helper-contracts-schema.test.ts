import { describe, expect, it } from "vitest";

import {
  cardPublishResultSchema,
  courseComposeResultSchema,
  courseOutlineSchema,
  courseValidateResultSchema,
  knowledgeIndexResultSchema,
  personalCourseCreateJobSchema,
  personalCourseResultSchema,
  slideDeckSchema,
  trustedExternalLinkSchema,
  visualDetachJobSchema,
} from "./helper-contracts-schema";

const id = (value: string) => value;
const digest = "a".repeat(64);
const timestamp = "2026-07-19T02:00:00Z";
const actor = {
  actorType: "human" as const,
  actorId: "user-1",
  displayName: null,
};
const placement = {
  schemaVersion: 1 as const,
  placementId: id("placement-1"),
  cardVersionId: id("card-v1"),
  chapterId: id("chapter-1"),
  lessonId: id("lesson-1"),
  purpose: "core" as const,
  allocatedMinutes: 10,
};
const outline = {
  schemaVersion: 1 as const,
  logicalId: id("outline"),
  versionId: id("outline-v1"),
  revision: 1,
  contentDigest: digest,
  supersedesVersionId: null,
  createdAt: timestamp,
  createdBy: actor,
  requirementId: id("requirement-1"),
  chapters: [
    {
      schemaVersion: 1 as const,
      chapterId: id("chapter-1"),
      title: "基础",
      objective: "理解基础概念",
      placements: [placement],
    },
  ],
  uncoveredGoals: [],
  retrievalEvidenceId: id("evidence-retrieval"),
  indexSnapshotId: id("snapshot-1"),
};
const slideNode = {
  schemaVersion: 1 as const,
  nodeId: id("slide-1"),
  nodeType: "slide" as const,
  text: null,
  items: [],
  placementIds: [id("placement-1")],
  cardVersionIds: [id("card-v1")],
  chunkIds: [],
  sourceVersionIds: [],
  evidenceIds: [id("evidence-1")],
  presenterNotes: null,
  assetBindings: [],
  children: [],
};
const deck = {
  schemaVersion: 1 as const,
  logicalId: id("deck"),
  versionId: id("deck-v1"),
  revision: 1,
  contentDigest: digest,
  supersedesVersionId: null,
  createdAt: timestamp,
  createdBy: actor,
  courseVersionId: id("course-v1"),
  nodes: [slideNode],
};

describe("governed web contracts", () => {
  it("accepts only exact HTTPS provenance links", () => {
    const link = {
      schemaVersion: 1,
      linkId: "link-1",
      linkType: "landing",
      href: "https://commons.wikimedia.org/wiki/File:Example.png",
      provenanceKind: "licensed-secondary",
      label: "来源页",
    };
    expect(trustedExternalLinkSchema.parse(link)).toEqual(link);
    expect(() => trustedExternalLinkSchema.parse({ ...link, href: "http://example.test/a" })).toThrow();
    expect(() => trustedExternalLinkSchema.parse({ ...link, href: "https://u:p@example.test/a" })).toThrow();
    expect(() => trustedExternalLinkSchema.parse({ ...link, href: "https://example.test/a#raw" })).toThrow();
    expect(() => trustedExternalLinkSchema.parse({ ...link, rawUrl: "https://example.test/raw" })).toThrow();
  });

  it("rejects duplicate outline placements and stale composition bindings", () => {
    expect(courseOutlineSchema.parse(outline)).toEqual(outline);
    const duplicateOutline = {
      ...outline,
      chapters: [
        outline.chapters[0],
        {
          ...outline.chapters[0],
          chapterId: "chapter-2",
          placements: [{ ...placement, chapterId: "chapter-2" }],
        },
      ],
    };
    expect(() => courseOutlineSchema.parse(duplicateOutline)).toThrow(/duplicate placement/);

    const composition = {
      operationId: "op-compose",
      operationStatus: "committed",
      requirementId: outline.requirementId,
      outlineVersionId: outline.versionId,
      outlineDigest: outline.contentDigest,
      indexSnapshotId: outline.indexSnapshotId,
      blockingGaps: [],
      compositionEvidenceId: "evidence-compose",
      retrievalEvidenceIds: ["evidence-retrieval"],
      confirmationSummary: {
        usageScope: "private-training",
        outlineVersionId: outline.versionId,
        outlineDigest: outline.contentDigest,
        requirementId: outline.requirementId,
        confirmationDigest: "b".repeat(64),
        text: "确认课程大纲",
      },
      outline,
    };
    expect(courseComposeResultSchema.parse(composition)).toEqual(composition);
    expect(() => courseComposeResultSchema.parse({ ...composition, outlineDigest: "c".repeat(64) })).toThrow(/stale/);
  });

  it("rejects duplicate slide identities, stale manifest bindings, and inconsistent index readiness", () => {
    expect(slideDeckSchema.parse(deck)).toEqual(deck);
    expect(() => slideDeckSchema.parse({ ...deck, nodes: [slideNode, slideNode] })).toThrow(/integrity/);

    const manifest = {
      schemaVersion: 1,
      logicalId: "runtime",
      versionId: "runtime-v1",
      revision: 1,
      contentDigest: "b".repeat(64),
      supersedesVersionId: null,
      createdAt: timestamp,
      createdBy: actor,
      courseVersionId: deck.courseVersionId,
      slideDeckVersionId: deck.versionId,
      slideDeckDigest: deck.contentDigest,
      jobBindings: [],
      artifactIds: [],
      evidenceIds: [],
    };
    const validation = {
      operationId: "op-validate",
      operationStatus: "committed",
      validationStatus: "passed",
      courseVersionId: deck.courseVersionId,
      courseDigest: digest,
      slideDeckId: deck.versionId,
      runtimeManifestId: manifest.versionId,
      runtimeManifestDigest: manifest.contentDigest,
      courseProjectionId: "projection-1",
      warnings: [],
      slideDeck: deck,
      runtimeManifest: manifest,
    };
    expect(courseValidateResultSchema.parse(validation)).toEqual(validation);
    expect(() => courseValidateResultSchema.parse({ ...validation, runtimeManifestDigest: "c".repeat(64) })).toThrow();

    const indexResult = {
      operationId: "op-index",
      operationStatus: "committed",
      consumedOutboxId: "outbox-1",
      indexSnapshotId: "snapshot-1",
      indexSnapshotDigest: digest,
      indexState: "degraded",
      retrievalMode: "fts-degraded",
      semanticIndexAvailable: false,
    };
    expect(knowledgeIndexResultSchema.parse(indexResult)).toEqual(indexResult);
    expect(() => knowledgeIndexResultSchema.parse({ ...indexResult, semanticIndexAvailable: true })).toThrow(/mismatch/);
  });

  it("binds queued card publication to the exact index outbox item", () => {
    const result = {
      operationId: "op-publish-card",
      operationStatus: "committed" as const,
      submittedCardVersionId: "card-review-v1",
      publishedCardVersionId: "card-published-v1",
      status: "published" as const,
      publicationEvidenceId: "evidence-publication",
      indexState: "queued" as const,
      indexOutboxId: "index-outbox-1",
      indexSnapshotId: null,
    };
    expect(cardPublishResultSchema.parse(result)).toEqual(result);
    expect(() =>
      cardPublishResultSchema.parse({ ...result, indexOutboxId: undefined }),
    ).toThrow();
  });

  it("rejects duplicate or missing visual detach selections at the request boundary", () => {
    const request = {
      type: "course_visual_detach",
      operationId: "op-detach",
      requestDigest: digest,
      actor: { actorType: "human", actorId: "user-1" },
      courseVersionId: "course-v1",
      expectedCourseDigest: digest,
      placementId: "visual-placement-1",
      activePlacementIds: ["visual-placement-1"],
    };
    expect(visualDetachJobSchema.parse(request)).toEqual(request);
    expect(() => visualDetachJobSchema.parse({ ...request, activePlacementIds: ["visual-placement-1", "visual-placement-1"] })).toThrow();
    expect(() => visualDetachJobSchema.parse({ ...request, activePlacementIds: ["visual-placement-2"] })).toThrow();
  });

  it("keeps personal course jobs exact and their result free of workflow internals", () => {
    const request = {
      type: "personal_course_create" as const,
      operationId: "personal-create-operation",
      requestDigest: digest,
      actor: { actorType: "human" as const, actorId: "local-user" },
      request: {
        requestId: `personal-request-${"b".repeat(32)}`,
        prompt: "制作个人 AI 工作流课程",
        sourceVersionIds: ["source-v1"],
        titleHint: null,
        createdAt: timestamp,
      },
    };
    expect(personalCourseCreateJobSchema.parse(request)).toEqual(request);
    expect(() => personalCourseCreateJobSchema.parse({ ...request, internalId: "leak" })).toThrow();

    const result = {
      runId: `personal-run-${"c".repeat(32)}`,
      view: {
        status: "creating" as const,
        phaseLabel: "正在整理知识",
        title: null,
        chapterCount: 0,
        attentionCount: 0,
        canResume: false,
        course: null,
      },
    };
    expect(personalCourseResultSchema.parse(result)).toEqual(result);
    expect(() => personalCourseResultSchema.parse({ ...result, internalId: "leak" })).toThrow();
  });
});

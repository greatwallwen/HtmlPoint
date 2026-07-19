import { z } from "zod";

export const opaqueIdSchema = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
export const digestSchema = z.string().regex(/^[0-9a-f]{64}$/);
const timestampSchema = z.string().datetime({ offset: true });
const jsonSchema = z.json();

function uniqueArray<T extends z.ZodTypeAny>(item: T, maximum: number) {
  return z
    .array(item)
    .max(maximum)
    .refine((items) => new Set(items).size === items.length, "items must be unique");
}

export const actorSchema = z
  .object({
    actorType: z.enum(["human", "service", "model", "system"]),
    actorId: opaqueIdSchema,
  })
  .strict();

const sourceLocatorSchema = z
  .object({ rootId: z.string().min(1), relativePath: z.string().min(1) })
  .strict();

const evidenceCheckSchema = z
  .object({
    code: z.string().min(1),
    status: z.enum(["passed", "warning", "failed", "skipped"]),
    message: z.string(),
    details: z.record(z.string(), jsonSchema),
  })
  .strict();

const evidenceErrorSchema = z
  .object({
    code: z.string().min(1),
    message: z.string(),
    retryable: z.boolean(),
    details: z.record(z.string(), jsonSchema),
  })
  .strict();

const evidenceArtifactSchema = z
  .object({
    artifactId: opaqueIdSchema,
    locator: sourceLocatorSchema,
    mediaType: z.string().min(1),
    contentDigest: digestSchema,
    byteSize: z.number().int().nonnegative(),
  })
  .strict();

export const evidenceSchema = z
  .object({
    evidenceId: opaqueIdSchema,
    kind: z.enum([
      "extraction",
      "retrieval",
      "dedup",
      "composition",
      "validation",
      "publish",
      "rehearsal",
      "dataset-profile",
      "execution",
      "runtime",
    ]),
    subjectVersionId: opaqueIdSchema.nullable(),
    status: z.enum(["verified", "warning", "failed", "degraded"]),
    inputSummary: z.record(z.string(), jsonSchema),
    outputSummary: z.record(z.string(), jsonSchema),
    producer: z.string().min(1),
    producerVersion: z.string().nullable(),
    startedAt: timestampSchema,
    finishedAt: timestampSchema,
    durationMs: z.number().int().nonnegative().nullable(),
    checks: z.array(evidenceCheckSchema).max(100),
    errors: z.array(evidenceErrorSchema).max(100),
    artifacts: z.array(evidenceArtifactSchema).max(100),
  })
  .strict();

export function jobResponseSchema<Result extends z.ZodTypeAny>(result: Result) {
  return z.object({ result, evidence: evidenceSchema }).strict();
}

export const uploadResponseSchema = z
  .object({
    schemaVersion: z.literal(1),
    uploadId: z.string().regex(/^upload-[0-9a-f]{32}$/),
    safeName: z.string().min(1).max(255),
    sourceKind: z.enum(["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]),
    mediaType: z.string().min(1),
    byteSize: z.number().int().min(1).max(20 * 1024 * 1024),
    contentDigest: digestSchema,
    state: z.literal("available"),
    expiresAt: timestampSchema,
  })
  .strict();

const sourceInventoryItemSchema = z
  .object({
    schemaVersion: z.literal(1),
    sourceId: opaqueIdSchema,
    sourceVersionId: opaqueIdSchema,
    displayName: z.string().min(1).max(500),
    sourceKind: z.enum(["pptx", "markdown", "csv", "parquet", "xls", "xlsx"]),
    mediaType: z.string().min(1),
    byteSize: z.number().int().min(1).max(20 * 1024 * 1024),
    contentDigest: digestSchema,
    status: z.enum(["active", "revoked"]),
  })
  .strict();

export const sourceInventoryResponseSchema = z
  .object({
    schemaVersion: z.literal(1),
    items: z.array(sourceInventoryItemSchema).max(100),
    nextCursor: z.string().min(1).max(256).nullable(),
  })
  .strict();

const operationStatusValueSchema = z.enum([
  "committed",
  "rolled-back",
  "unknown",
  "in-progress",
]);
const mutationStatusSchema = z.enum(["committed", "rolled-back"]);

export const operationStatusResultSchema = z
  .object({
    operationId: opaqueIdSchema,
    status: operationStatusValueSchema,
    requestDigest: digestSchema,
    resultRefs: z.record(z.string(), jsonSchema),
  })
  .strict();

const mutationEnvelope = {
  operationId: opaqueIdSchema,
  operationStatus: mutationStatusSchema,
} as const;

export const importStartResultSchema = z
  .object({
    ...mutationEnvelope,
    importId: z.string().regex(/^import-[0-9a-f]{32}$/),
    status: z.enum(["active", "promoted", "cancelled", "failed"]),
    sourceId: opaqueIdSchema,
    sourceVersionId: opaqueIdSchema,
    contentDigest: digestSchema,
    chunkCount: z.number().int().nonnegative(),
    visualCount: z.number().int().nonnegative(),
    visualVersionIds: uniqueArray(opaqueIdSchema, 10_000),
    candidateCardVersionIds: uniqueArray(opaqueIdSchema, 10_000),
    datasetVersionIds: uniqueArray(opaqueIdSchema, 1_000),
    datasetProfiles: z.array(z.object({
      datasetVersionId: opaqueIdSchema,
      contentDigest: digestSchema,
      schemaDigest: digestSchema,
      rowCount: z.number().int().nonnegative(),
      columns: z.array(z.object({
        name: z.string().min(1).max(128),
        dataType: z.string().min(1).max(128),
        digest: digestSchema,
      }).strict()).max(1_000),
    }).strict()).max(1_000),
    reviewTaskIds: uniqueArray(opaqueIdSchema, 10_000),
    extractionEvidenceId: opaqueIdSchema,
  })
  .strict();

export const importStatusResultSchema = z
  .object({
    importId: z.string().regex(/^import-[0-9a-f]{32}$/),
    status: z.enum(["active", "promoted", "cancelled", "failed"]),
    sourceVersionId: opaqueIdSchema.nullable(),
    createdAt: timestampSchema,
    updatedAt: timestampSchema,
  })
  .strict();

const reviewReasonSchema = z.enum([
  "source-changed",
  "near-duplicate",
  "unknown-tag",
  "deprecated-tag",
  "tag-conflict",
  "citation-missing",
  "visual-rights",
  "visual-unverified",
  "dataset-reference",
  "sensitive-sample",
  "grain-needs-review",
  "provenance",
  "manual-review",
  "exact-duplicate",
  "course-feedback",
]);

export const reviewCategorySchema = z.enum([
  "candidate-card",
  "exact-duplicate",
  "near-duplicate",
  "tag",
  "source-changed",
  "course-feedback",
  "visual-rights",
]);

const reviewListItemSchema = z
  .object({
    taskId: opaqueIdSchema,
    subjectVersionId: opaqueIdSchema,
    category: reviewCategorySchema,
    reasonCode: reviewReasonSchema,
    status: z.enum(["open", "resolved", "dismissed"]),
    blocking: z.boolean(),
    reviewDigest: digestSchema,
    evidenceCount: z.number().int().nonnegative(),
    createdAt: timestampSchema,
  })
  .strict();

export const reviewListResultSchema = z
  .object({
    items: z.array(reviewListItemSchema).max(100),
    nextCursor: z.string().regex(/^review-cursor-[0-9a-f]{32}$/).nullable(),
  })
  .strict();

const reviewContentExcerptSchema = z
  .object({
    path: z.array(z.number().int().nonnegative()).max(32),
    depth: z.number().int().positive(),
    nodeType: z.string().min(1).max(64),
    text: z.string().max(2_000).nullable(),
    level: z.number().int().min(1).max(6).nullable(),
    language: z.string().max(64).nullable(),
    rows: z.array(z.array(z.string())).max(5),
  })
  .strict();

const reviewCitationExcerptSchema = z
  .object({
    chunkId: opaqueIdSchema,
    sourceVersionId: opaqueIdSchema,
    quotedText: z.string().max(1_000).nullable(),
  })
  .strict();

export const reviewDetailResultSchema = z
  .object({
    task: reviewListItemSchema,
    evidenceIds: uniqueArray(opaqueIdSchema, 50),
    evidenceTotal: z.number().int().nonnegative(),
    evidenceTruncated: z.boolean(),
    cardVersionId: opaqueIdSchema.nullable(),
    cardContentDigest: digestSchema.nullable(),
    cardTitle: z.string().max(500).nullable(),
    learningObjective: z.string().max(1_000).nullable(),
    contentNodes: z.array(reviewContentExcerptSchema).max(50),
    contentNodeTotal: z.number().int().nonnegative(),
    contentNodesTruncated: z.boolean(),
    citations: z.array(reviewCitationExcerptSchema).max(50),
    citationTotal: z.number().int().nonnegative(),
    citationsTruncated: z.boolean(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.evidenceTotal < value.evidenceIds.length) {
      context.addIssue({ code: "custom", path: ["evidenceTotal"], message: "invalid total" });
    }
    if (value.contentNodeTotal < value.contentNodes.length) {
      context.addIssue({ code: "custom", path: ["contentNodeTotal"], message: "invalid total" });
    }
    if (value.citationTotal < value.citations.length) {
      context.addIssue({ code: "custom", path: ["citationTotal"], message: "invalid total" });
    }
    if ((value.cardVersionId === null) !== (value.cardContentDigest === null)) {
      context.addIssue({ code: "custom", path: ["cardContentDigest"], message: "card binding mismatch" });
    }
  });

const upgradeListItemSchema = z
  .object({
    suggestionId: opaqueIdSchema,
    currentVersionId: opaqueIdSchema,
    candidateVersionId: opaqueIdSchema,
    reviewTaskId: opaqueIdSchema,
    reasonCode: reviewReasonSchema,
    status: z.enum(["open", "resolved", "dismissed"]),
    suggestionDigest: digestSchema,
    reviewDigest: digestSchema,
    candidateDigest: digestSchema,
    evidenceCount: z.number().int().nonnegative(),
    createdAt: timestampSchema,
  })
  .strict();

export const upgradeListResultSchema = z
  .object({
    items: z.array(upgradeListItemSchema).max(100),
    nextCursor: z.string().regex(/^upgrade-cursor-[0-9a-f]{32}$/).nullable(),
  })
  .strict();

export const reviewResolveResultSchema = z
  .object({
    ...mutationEnvelope,
    taskId: opaqueIdSchema,
    resolutionId: opaqueIdSchema,
    decision: z.enum(["accept", "reject", "dismiss"]),
    reviewStatus: z.enum(["resolved", "dismissed"]),
  })
  .strict();

export const cardPublishResultSchema = z
  .object({
    ...mutationEnvelope,
    submittedCardVersionId: opaqueIdSchema,
    publishedCardVersionId: opaqueIdSchema,
    status: z.literal("published"),
    publicationEvidenceId: opaqueIdSchema,
    indexState: z.literal("queued"),
    indexOutboxId: opaqueIdSchema,
    indexSnapshotId: opaqueIdSchema.nullable(),
  })
  .strict();

export const upgradeResolveResultSchema = z
  .object({
    ...mutationEnvelope,
    suggestionId: opaqueIdSchema,
    candidateVersionId: opaqueIdSchema,
    decision: z.enum(["accept", "reject", "dismiss"]),
    resolutionId: opaqueIdSchema,
    nextRequiredReviewTaskIds: uniqueArray(opaqueIdSchema, 50),
    nextAction: z.string().min(1).max(128),
  })
  .strict();

export const knowledgeIndexResultSchema = z
  .object({
    ...mutationEnvelope,
    consumedOutboxId: opaqueIdSchema,
    indexSnapshotId: opaqueIdSchema,
    indexSnapshotDigest: digestSchema,
    indexState: z.enum(["ready", "degraded"]),
    retrievalMode: z.enum(["hybrid", "fts-degraded"]),
    semanticIndexAvailable: z.boolean(),
  })
  .strict()
  .superRefine((value, context) => {
    const hybrid = value.retrievalMode === "hybrid";
    if (hybrid !== value.semanticIndexAvailable) {
      context.addIssue({ code: "custom", path: ["semanticIndexAvailable"], message: "index mode mismatch" });
    }
  });

const versionMetaSchema = z
  .object({
    schemaVersion: z.literal(1),
    logicalId: opaqueIdSchema,
    versionId: opaqueIdSchema,
    revision: z.number().int().positive(),
    contentDigest: digestSchema,
    supersedesVersionId: opaqueIdSchema.nullable(),
    createdAt: timestampSchema,
    createdBy: z
      .object({
        actorType: z.enum(["human", "service", "model", "system"]),
        actorId: z.string().min(1),
        displayName: z.string().nullable(),
      })
      .strict(),
  })
  .strict();

const cardPlacementSchema = z
  .object({
    schemaVersion: z.literal(1),
    placementId: opaqueIdSchema,
    cardVersionId: opaqueIdSchema,
    chapterId: opaqueIdSchema,
    lessonId: opaqueIdSchema,
    purpose: z.enum(["core", "example", "exercise", "evidence", "warning"]),
    allocatedMinutes: z.number().int().min(5).max(480),
  })
  .strict();

const outlineChapterSchema = z
  .object({
    schemaVersion: z.literal(1),
    chapterId: opaqueIdSchema,
    title: z.string().min(1).max(200),
    objective: z.string().min(1).max(500),
    placements: z.array(cardPlacementSchema).max(100),
  })
  .strict()
  .superRefine((value, context) => {
    const ids = new Set<string>();
    const signatures = new Set<string>();
    value.placements.forEach((placement, index) => {
      if (placement.chapterId !== value.chapterId) {
        context.addIssue({ code: "custom", path: ["placements", index, "chapterId"], message: "chapter mismatch" });
      }
      const signature = `${placement.cardVersionId}\0${placement.lessonId}\0${placement.purpose}`;
      if (ids.has(placement.placementId) || signatures.has(signature)) {
        context.addIssue({ code: "custom", path: ["placements", index], message: "duplicate placement" });
      }
      ids.add(placement.placementId);
      signatures.add(signature);
    });
  });

export const courseOutlineSchema = versionMetaSchema
  .extend({
    requirementId: opaqueIdSchema,
    chapters: z.array(outlineChapterSchema).min(1).max(50),
    uncoveredGoals: uniqueArray(z.string().min(1).max(500), 20),
    retrievalEvidenceId: opaqueIdSchema,
    indexSnapshotId: opaqueIdSchema,
  })
  .strict()
  .superRefine((value, context) => {
    const chapterIds = new Set<string>();
    const placementIds = new Set<string>();
    value.chapters.forEach((chapter, chapterIndex) => {
      if (chapterIds.has(chapter.chapterId)) {
        context.addIssue({ code: "custom", path: ["chapters", chapterIndex], message: "duplicate chapter" });
      }
      chapterIds.add(chapter.chapterId);
      chapter.placements.forEach((placement, placementIndex) => {
        if (placementIds.has(placement.placementId)) {
          context.addIssue({ code: "custom", path: ["chapters", chapterIndex, "placements", placementIndex], message: "duplicate placement" });
        }
        placementIds.add(placement.placementId);
      });
    });
  });

const confirmationSummarySchema = z
  .object({
    usageScope: z.enum(["private-training", "internal", "public"]),
    outlineVersionId: opaqueIdSchema,
    outlineDigest: digestSchema,
    requirementId: opaqueIdSchema,
    confirmationDigest: digestSchema,
    text: z.string().min(1).max(1_000),
  })
  .strict();

export const courseComposeResultSchema = z
  .object({
    ...mutationEnvelope,
    requirementId: opaqueIdSchema,
    outlineVersionId: opaqueIdSchema,
    outlineDigest: digestSchema,
    indexSnapshotId: opaqueIdSchema,
    blockingGaps: uniqueArray(z.string().min(1).max(500), 20),
    compositionEvidenceId: opaqueIdSchema,
    retrievalEvidenceIds: uniqueArray(opaqueIdSchema, 20),
    confirmationSummary: confirmationSummarySchema,
    outline: courseOutlineSchema,
  })
  .strict()
  .superRefine((value, context) => {
    const summary = value.confirmationSummary;
    if (
      value.outlineVersionId !== value.outline.versionId ||
      value.outlineDigest !== value.outline.contentDigest ||
      value.indexSnapshotId !== value.outline.indexSnapshotId ||
      value.requirementId !== value.outline.requirementId ||
      summary.outlineVersionId !== value.outlineVersionId ||
      summary.outlineDigest !== value.outlineDigest ||
      summary.requirementId !== value.requirementId
    ) {
      context.addIssue({ code: "custom", message: "composition bindings are stale" });
    }
  });

export const trustedExternalLinkSchema = z
  .object({
    schemaVersion: z.literal(1),
    linkId: opaqueIdSchema,
    linkType: z.enum(["landing", "license"]),
    href: z.string().min(1).max(2_048),
    provenanceKind: z.enum(["official-primary", "source-provided", "licensed-secondary"]),
    label: z.string().min(1).max(200),
  })
  .strict()
  .superRefine((value, context) => {
    try {
      const url = new URL(value.href);
      if (
        url.protocol !== "https:" ||
        url.username !== "" ||
        url.password !== "" ||
        url.hash !== "" ||
        /\s|\0/.test(value.href)
      ) {
        throw new Error("unsafe");
      }
    } catch {
      context.addIssue({ code: "custom", path: ["href"], message: "trusted links require safe HTTPS" });
    }
  });

export const cropRectSchema = z
  .object({
    x: z.number().min(0).max(1),
    y: z.number().min(0).max(1),
    width: z.number().positive().max(1),
    height: z.number().positive().max(1),
  })
  .strict()
  .refine((value) => value.x + value.width <= 1 && value.y + value.height <= 1);

export const transformationSchema = z
  .object({
    schemaVersion: z.literal(1),
    transformationId: opaqueIdSchema,
    crop: cropRectSchema.nullable(),
    scaleMode: z.enum(["none", "contain", "cover"]),
    colorAdjustments: uniqueArray(z.string().min(1).max(200), 20),
    changeNotice: z.string().max(1_000).nullable(),
    derivativeLicenseDecision: z.enum([
      "not-derivative",
      "same-license",
      "compatible-license",
      "prohibited",
      "requires-review",
    ]),
    exportLicense: z.string().max(200).nullable(),
    shareAlikeCompatible: z.boolean(),
    gfdlCompatible: z.boolean(),
    noDerivativesCompatible: z.boolean(),
  })
  .strict();

export const attributionSchema = z
  .object({
    schemaVersion: z.literal(1),
    title: z.string().min(1).max(500),
    creator: z.string().max(500).nullable(),
    publisher: z.string().max(500).nullable(),
    licenseLabel: z.string().min(1).max(200),
    landingLink: trustedExternalLinkSchema.nullable(),
    licenseLink: trustedExternalLinkSchema.nullable(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.landingLink?.linkType !== undefined && value.landingLink.linkType !== "landing") {
      context.addIssue({ code: "custom", path: ["landingLink"], message: "wrong link role" });
    }
    if (value.licenseLink?.linkType !== undefined && value.licenseLink.linkType !== "license") {
      context.addIssue({ code: "custom", path: ["licenseLink"], message: "wrong link role" });
    }
  });

const slideAssetBindingSchema = z
  .object({
    schemaVersion: z.literal(1),
    bindingId: opaqueIdSchema,
    visualPlacementId: opaqueIdSchema,
    visualVersionId: opaqueIdSchema,
    artifactId: opaqueIdSchema,
    artifactDigest: digestSchema,
    mediaType: z.enum(["image/png", "image/jpeg", "image/webp", "image/svg+xml"]),
    altText: z.string().min(1).max(1_000),
    authenticityEvidenceId: opaqueIdSchema,
    licenseEvidenceId: opaqueIdSchema,
    attributionId: opaqueIdSchema,
    attribution: attributionSchema,
    transformationId: opaqueIdSchema,
    transformation: transformationSchema,
  })
  .strict()
  .refine((value) => value.transformationId === value.transformation.transformationId);

type SlideNodeContract = {
  schemaVersion: 1;
  nodeId: string;
  nodeType: "slide" | "title" | "heading" | "paragraph" | "bullet-list" | "quote" | "callout" | "code" | "table" | "visual" | "activity";
  text: string | null;
  items: string[];
  placementIds: string[];
  cardVersionIds: string[];
  chunkIds: string[];
  sourceVersionIds: string[];
  evidenceIds: string[];
  presenterNotes: string | null;
  assetBindings: z.infer<typeof slideAssetBindingSchema>[];
  children: SlideNodeContract[];
};

export const slideNodeSchema: z.ZodType<SlideNodeContract> = z.lazy(() =>
  z
    .object({
      schemaVersion: z.literal(1),
      nodeId: opaqueIdSchema,
      nodeType: z.enum(["slide", "title", "heading", "paragraph", "bullet-list", "quote", "callout", "code", "table", "visual", "activity"]),
      text: z.string().max(12_000).nullable(),
      items: z.array(z.string().min(1).max(2_000)).max(100),
      placementIds: uniqueArray(opaqueIdSchema, 100).min(1),
      cardVersionIds: uniqueArray(opaqueIdSchema, 100).min(1),
      chunkIds: uniqueArray(opaqueIdSchema, 100),
      sourceVersionIds: uniqueArray(opaqueIdSchema, 100),
      evidenceIds: uniqueArray(opaqueIdSchema, 100).min(1),
      presenterNotes: z.string().max(12_000).nullable(),
      assetBindings: z.array(slideAssetBindingSchema).max(20),
      children: z.array(slideNodeSchema).max(100),
    })
    .strict(),
);

export const slideDeckSchema = versionMetaSchema
  .extend({ courseVersionId: opaqueIdSchema, nodes: z.array(slideNodeSchema).min(1).max(500) })
  .strict()
  .superRefine((value, context) => {
    const nodeIds = new Set<string>();
    const placementIds = new Set<string>();
    const stack = value.nodes.map((node) => ({ node, depth: 1 }));
    let count = 0;
    while (stack.length > 0) {
      const current = stack.pop()!;
      count += 1;
      if (count > 500 || current.depth > 32 || nodeIds.has(current.node.nodeId)) {
        context.addIssue({ code: "custom", message: "slide tree integrity failed" });
        break;
      }
      nodeIds.add(current.node.nodeId);
      for (const binding of current.node.assetBindings) {
        if (placementIds.has(binding.visualPlacementId)) {
          context.addIssue({ code: "custom", message: "duplicate visual placement" });
        }
        placementIds.add(binding.visualPlacementId);
      }
      current.node.children.forEach((node) => stack.push({ node, depth: current.depth + 1 }));
    }
  });

const runtimeJobBindingSchema = z
  .object({
    schemaVersion: z.literal(1),
    jobId: opaqueIdSchema,
    jobType: z.enum(["python_snippet", "dataset_sql", "chart_build", "rag_query", "doc_export"]),
    specId: opaqueIdSchema,
    evidenceId: opaqueIdSchema,
    timeoutSeconds: z.number().int().min(1).max(300),
  })
  .strict();

export const runtimeManifestSchema = versionMetaSchema
  .extend({
    courseVersionId: opaqueIdSchema,
    slideDeckVersionId: opaqueIdSchema,
    slideDeckDigest: digestSchema,
    jobBindings: z.array(runtimeJobBindingSchema).max(100),
    artifactIds: uniqueArray(opaqueIdSchema, 500),
    evidenceIds: uniqueArray(opaqueIdSchema, 500),
  })
  .strict();

export const outlineConfirmResultSchema = z
  .object({
    ...mutationEnvelope,
    confirmationId: opaqueIdSchema,
    confirmationDigest: digestSchema,
    courseVersionId: opaqueIdSchema,
    courseDigest: digestSchema,
    courseStatus: z.literal("confirmed"),
    outlineVersionId: opaqueIdSchema,
    outlineDigest: digestSchema,
    placementIds: uniqueArray(opaqueIdSchema, 500).min(1),
    usageScope: z.enum(["private-training", "internal", "public"]),
    slideDeckId: opaqueIdSchema,
    runtimeManifestId: opaqueIdSchema,
    slideDeck: slideDeckSchema,
  })
  .strict()
  .refine((value) => value.slideDeckId === value.slideDeck.versionId && value.courseVersionId === value.slideDeck.courseVersionId);

const chartItemSchema = z.discriminatedUnion("status", [
  z.object({ requestId: opaqueIdSchema, status: z.literal("materialized"), artifactId: opaqueIdSchema, visualVersionId: opaqueIdSchema, evidenceId: opaqueIdSchema, reused: z.boolean() }).strict(),
  z.object({ requestId: opaqueIdSchema, status: z.literal("failed"), errorCode: z.string().min(1).max(128) }).strict(),
]);
export const chartBuildResultSchema = z.object({ ...mutationEnvelope, items: z.array(chartItemSchema).max(20) }).strict();

const visualCandidateSchema = z
  .object({ candidateId: z.string().regex(/^network-candidate-[0-9a-f]{64}$/), fileTitle: z.string().min(1).max(500), mediaType: z.enum(["image/png", "image/jpeg", "image/webp"]), width: z.number().int().positive(), height: z.number().int().positive(), licenseId: z.string().min(1).max(200), expiresAt: timestampSchema })
  .strict();
export const visualSearchResultSchema = z.object({ ...mutationEnvelope, items: z.array(visualCandidateSchema).max(10) }).strict();

const visualAcquireItemSchema = z.discriminatedUnion("status", [
  z.object({ candidateId: z.string().regex(/^network-candidate-[0-9a-f]{64}$/), status: z.literal("acquired"), artifactId: opaqueIdSchema, visualVersionId: opaqueIdSchema, evidenceId: opaqueIdSchema, reused: z.boolean(), landingLink: trustedExternalLinkSchema, licenseLink: trustedExternalLinkSchema }).strict(),
  z.object({ candidateId: z.string().regex(/^network-candidate-[0-9a-f]{64}$/), status: z.literal("failed"), errorCode: z.string().min(1).max(128) }).strict(),
]);
export const visualAcquireResultSchema = z.object({ ...mutationEnvelope, items: z.array(visualAcquireItemSchema).max(10) }).strict();

const visualRevalidationItemSchema = z.discriminatedUnion("status", [
  z.object({ visualVersionId: opaqueIdSchema, status: z.literal("revalidated"), verificationStatus: z.string().min(1).max(64), evidenceId: opaqueIdSchema, verifiedAt: timestampSchema, expiresAt: timestampSchema, revision: z.number().int().positive() }).strict(),
  z.object({ visualVersionId: opaqueIdSchema, status: z.literal("failed"), errorCode: z.string().min(1).max(128) }).strict(),
]);
export const visualRevalidateResultSchema = z.object({ ...mutationEnvelope, item: visualRevalidationItemSchema }).strict();

export const visualAttachResultSchema = z
  .object({ ...mutationEnvelope, placementId: opaqueIdSchema, visualVersionId: opaqueIdSchema, slideNodeId: opaqueIdSchema, slotId: opaqueIdSchema, attribution: attributionSchema, transformation: transformationSchema })
  .strict();
export const visualDetachResultSchema = z
  .object({ ...mutationEnvelope, detachedPlacementId: opaqueIdSchema, activePlacementIds: uniqueArray(opaqueIdSchema, 500) })
  .strict()
  .refine((value) => !value.activePlacementIds.includes(value.detachedPlacementId));

export const courseValidateResultSchema = z
  .object({
    ...mutationEnvelope,
    validationStatus: z.literal("passed"),
    courseVersionId: opaqueIdSchema,
    courseDigest: digestSchema,
    slideDeckId: opaqueIdSchema,
    runtimeManifestId: opaqueIdSchema,
    runtimeManifestDigest: digestSchema,
    courseProjectionId: opaqueIdSchema,
    warnings: z.array(z.string().max(1_000)).max(100),
    slideDeck: slideDeckSchema,
    runtimeManifest: runtimeManifestSchema,
  })
  .strict()
  .refine((value) =>
    value.courseVersionId === value.slideDeck.courseVersionId &&
    value.courseVersionId === value.runtimeManifest.courseVersionId &&
    value.slideDeckId === value.slideDeck.versionId &&
    value.runtimeManifestId === value.runtimeManifest.versionId &&
    value.runtimeManifestDigest === value.runtimeManifest.contentDigest &&
    value.runtimeManifest.slideDeckVersionId === value.slideDeckId &&
    value.runtimeManifest.slideDeckDigest === value.slideDeck.contentDigest,
  );

const coursePublicationIdentityShape = {
    courseVersionId: opaqueIdSchema,
    slideDeckId: opaqueIdSchema,
    runtimeManifestId: opaqueIdSchema,
    runtimeManifestDigest: digestSchema,
    courseProjectionId: opaqueIdSchema,
} as const;
export const coursePublicationIdentitySchema = z.object(coursePublicationIdentityShape).strict();
export const coursePublishResultSchema = z
  .object({
    ...mutationEnvelope,
    ...coursePublicationIdentityShape,
  })
  .strict();

export const importStartJobSchema = z.object({ type: z.literal("knowledge_import_start"), uploadId: z.string().regex(/^upload-[0-9a-f]{32}$/), expectedContentDigest: digestSchema, operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema }).strict();
export const importStatusJobSchema = z.object({ type: z.literal("knowledge_import_status"), importId: z.string().regex(/^import-[0-9a-f]{32}$/), actor: actorSchema }).strict();
export const importCancelJobSchema = z.object({ type: z.literal("knowledge_import_cancel"), importId: z.string().regex(/^import-[0-9a-f]{32}$/), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema }).strict();
export const operationStatusJobSchema = z.object({ type: z.literal("operation_status"), operationId: opaqueIdSchema, actor: actorSchema }).strict();
export const reviewListJobSchema = z.object({ type: z.literal("knowledge_review_list"), status: z.enum(["open", "resolved", "dismissed"]).nullable().optional(), category: reviewCategorySchema.nullable().optional(), limit: z.number().int().min(1).max(100), cursor: z.string().regex(/^review-cursor-[0-9a-f]{32}$/).nullable().optional() }).strict();
export const reviewDetailJobSchema = z.object({ type: z.literal("knowledge_review_detail"), taskId: opaqueIdSchema }).strict();
export const upgradeListJobSchema = z.object({ type: z.literal("knowledge_upgrade_list"), status: z.enum(["open", "resolved", "dismissed"]).nullable().optional(), limit: z.number().int().min(1).max(100), cursor: z.string().regex(/^upgrade-cursor-[0-9a-f]{32}$/).nullable().optional() }).strict();
const resolutionBase = { operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, decision: z.enum(["accept", "reject", "dismiss"]), evidenceIds: uniqueArray(opaqueIdSchema, 50) } as const;
export const reviewResolveJobSchema = z.object({ type: z.literal("knowledge_review_resolve"), ...resolutionBase, taskId: opaqueIdSchema, expectedReviewDigest: digestSchema }).strict();
export const cardPublishJobSchema = z.object({ type: z.literal("knowledge_card_publish"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, cardVersionId: opaqueIdSchema, expectedCardDigest: digestSchema }).strict();
export const upgradeResolveJobSchema = z.object({ type: z.literal("knowledge_upgrade_resolve"), ...resolutionBase, suggestionId: opaqueIdSchema, expectedSuggestionDigest: digestSchema, expectedReviewDigest: digestSchema, expectedCardDigest: digestSchema }).strict();
export const knowledgeIndexJobSchema = z.object({ type: z.literal("knowledge_index"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, expectedOutboxId: opaqueIdSchema }).strict();

export const courseRequirementSchema = z.object({ requirementId: opaqueIdSchema, title: z.string().min(1).max(200), audience: z.string().min(1).max(500), learningGoals: uniqueArray(z.string().min(1).max(500), 20).min(1), durationMinutes: z.number().int().min(5).max(480), requiredTagIds: uniqueArray(opaqueIdSchema, 50), excludedTagIds: uniqueArray(opaqueIdSchema, 50), usageScope: z.enum(["private-training", "internal", "public"]) }).strict().refine((value) => value.durationMinutes % 5 === 0 && value.requiredTagIds.every((item) => !value.excludedTagIds.includes(item)));
const persistedCourseRequirementSchema = z.object({ schemaVersion: z.literal(1), requirementId: opaqueIdSchema, title: z.string().min(1).max(200), audience: z.string().min(1).max(500), learningGoals: uniqueArray(z.string().min(1).max(500), 20).min(1), durationMinutes: z.number().int().min(5).max(480), requiredTagIds: uniqueArray(opaqueIdSchema, 50), excludedTagIds: uniqueArray(opaqueIdSchema, 50), usageScope: z.enum(["private-training", "internal", "public"]) }).strict().refine((value) => value.durationMinutes % 5 === 0 && value.requiredTagIds.every((item) => !value.excludedTagIds.includes(item)));
const compositionOptionsSchema = z.object({ audienceTagId: opaqueIdSchema.nullable(), difficultyTagId: opaqueIdSchema.nullable(), indexSnapshotId: opaqueIdSchema, includeCardVersionIds: uniqueArray(opaqueIdSchema, 100), excludeCardVersionIds: uniqueArray(opaqueIdSchema, 100), requireVisualRefs: z.boolean(), requireDatasetRefs: z.boolean() }).strict().refine((value) => value.includeCardVersionIds.every((item) => !value.excludeCardVersionIds.includes(item)));
export const courseComposeJobSchema = z.object({ type: z.literal("course_compose"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, requirement: courseRequirementSchema, options: compositionOptionsSchema, outlineLogicalId: opaqueIdSchema, outlineVersionId: opaqueIdSchema, outlineRevision: z.number().int().positive() }).strict();
export const outlineConfirmJobSchema = z.object({ type: z.literal("course_outline_confirm"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, confirmationId: opaqueIdSchema, requirementId: opaqueIdSchema, outlineVersionId: opaqueIdSchema, expectedOutlineDigest: digestSchema, confirmationDigest: digestSchema, courseLogicalId: opaqueIdSchema, courseVersionId: opaqueIdSchema, courseRevision: z.number().int().positive() }).strict();

const chartSpecSchema = z.object({ requestId: opaqueIdSchema, chartType: z.enum(["bar", "line", "scatter"]), datasetVersionId: opaqueIdSchema, expectedDatasetDigest: digestSchema, expectedSchemaDigest: digestSchema, xColumn: z.string().min(1).max(128), xColumnDigest: digestSchema, yColumn: z.string().min(1).max(128), yColumnDigest: digestSchema, aggregate: z.enum(["count", "sum", "avg", "min", "max", "none"]), title: z.string().min(1).max(120), description: z.string().min(1).max(320), maxResultRows: z.number().int().min(1).max(100) }).strict();
export const chartBuildJobSchema = z.object({ type: z.literal("chart_build"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, specs: z.array(chartSpecSchema).min(1).max(20) }).strict().refine((value) => new Set(value.specs.map((item) => item.requestId)).size === value.specs.length);
export const visualSearchJobSchema = z.object({ type: z.literal("visual_search"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, query: z.string().min(2).max(120), limit: z.number().int().min(1).max(10) }).strict();
export const visualAcquireJobSchema = z.object({ type: z.literal("visual_acquire"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, candidateIds: uniqueArray(z.string().regex(/^network-candidate-[0-9a-f]{64}$/), 10).min(1) }).strict();
export const visualRevalidateJobSchema = z.object({ type: z.literal("visual_revalidate"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, visualVersionId: opaqueIdSchema }).strict();
const transformationRequestSchema = transformationSchema.omit({ schemaVersion: true });
export const visualAttachJobSchema = z.object({ type: z.literal("course_visual_attach"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, courseVersionId: opaqueIdSchema, expectedCourseDigest: digestSchema, placementId: opaqueIdSchema, visualVersionId: opaqueIdSchema, slideNodeId: opaqueIdSchema, slotId: opaqueIdSchema, fit: z.enum(["contain", "cover", "fill"]), crop: cropRectSchema.nullable(), altText: z.string().min(1).max(1_000), transformation: transformationRequestSchema, originatingCardVersionId: opaqueIdSchema.nullable(), originatingSourceVersionId: opaqueIdSchema.nullable(), originatingDatasetVersionId: opaqueIdSchema.nullable() }).strict().refine((value) => [value.originatingCardVersionId, value.originatingSourceVersionId, value.originatingDatasetVersionId].some(Boolean) && JSON.stringify(value.crop) === JSON.stringify(value.transformation.crop));
export const visualDetachJobSchema = z.object({ type: z.literal("course_visual_detach"), operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, courseVersionId: opaqueIdSchema, expectedCourseDigest: digestSchema, placementId: opaqueIdSchema, activePlacementIds: uniqueArray(opaqueIdSchema, 500).min(1) }).strict().refine((value) => value.activePlacementIds.includes(value.placementId));
const courseProjectionShape = { operationId: opaqueIdSchema, requestDigest: digestSchema, actor: actorSchema, courseVersionId: opaqueIdSchema, expectedCourseDigest: digestSchema, visualPlacementIds: uniqueArray(opaqueIdSchema, 500) } as const;
export const courseValidateJobSchema = z.object({ type: z.literal("course_validate"), ...courseProjectionShape }).strict();
export const coursePublishJobSchema = z.object({ type: z.literal("course_publish"), ...courseProjectionShape }).strict();

export const courseProjectionResponseSchema = z.object({
  schemaVersion: z.literal(1),
  courseVersionId: opaqueIdSchema,
  courseDigest: digestSchema,
  usageScope: z.enum(["private-training", "internal", "public"]),
  status: z.literal("published"),
  requirement: persistedCourseRequirementSchema,
  outline: courseOutlineSchema,
  slideDeck: slideDeckSchema,
  runtimeManifest: runtimeManifestSchema,
}).strict().superRefine((value, context) => {
  if (
    value.requirement.requirementId !== value.outline.requirementId ||
    value.courseVersionId !== value.slideDeck.courseVersionId ||
    value.courseVersionId !== value.runtimeManifest.courseVersionId ||
    value.runtimeManifest.slideDeckVersionId !== value.slideDeck.versionId ||
    value.runtimeManifest.slideDeckDigest !== value.slideDeck.contentDigest ||
    value.usageScope !== value.requirement.usageScope
  ) {
    context.addIssue({ code: "custom", message: "published course projection bindings are stale" });
  }
});

export type UploadResponse = z.infer<typeof uploadResponseSchema>;
export type SourceInventoryResponse = z.infer<typeof sourceInventoryResponseSchema>;
export type Evidence = z.infer<typeof evidenceSchema>;
export type TrustedExternalLink = z.infer<typeof trustedExternalLinkSchema>;
export type ImportStartJob = z.infer<typeof importStartJobSchema>;
export type ImportStatusJob = z.infer<typeof importStatusJobSchema>;
export type ImportCancelJob = z.infer<typeof importCancelJobSchema>;
export type OperationStatusJob = z.infer<typeof operationStatusJobSchema>;
export type ReviewListJob = z.infer<typeof reviewListJobSchema>;
export type ReviewDetailJob = z.infer<typeof reviewDetailJobSchema>;
export type UpgradeListJob = z.infer<typeof upgradeListJobSchema>;
export type ReviewResolveJob = z.infer<typeof reviewResolveJobSchema>;
export type CardPublishJob = z.infer<typeof cardPublishJobSchema>;
export type UpgradeResolveJob = z.infer<typeof upgradeResolveJobSchema>;
export type KnowledgeIndexJob = z.infer<typeof knowledgeIndexJobSchema>;
export type CourseComposeJob = z.infer<typeof courseComposeJobSchema>;
export type OutlineConfirmJob = z.infer<typeof outlineConfirmJobSchema>;
export type ChartBuildJob = z.infer<typeof chartBuildJobSchema>;
export type VisualSearchJob = z.infer<typeof visualSearchJobSchema>;
export type VisualAcquireJob = z.infer<typeof visualAcquireJobSchema>;
export type VisualRevalidateJob = z.infer<typeof visualRevalidateJobSchema>;
export type VisualAttachJob = z.infer<typeof visualAttachJobSchema>;
export type VisualDetachJob = z.infer<typeof visualDetachJobSchema>;
export type CourseValidateJob = z.infer<typeof courseValidateJobSchema>;
export type CoursePublishJob = z.infer<typeof coursePublishJobSchema>;
export type CourseProjectionResponse = z.infer<typeof courseProjectionResponseSchema>;
export type ReviewListResult = z.infer<typeof reviewListResultSchema>;
export type ReviewDetailResult = z.infer<typeof reviewDetailResultSchema>;
export type KnowledgeIndexResult = z.infer<typeof knowledgeIndexResultSchema>;
export type SlideDeck = z.infer<typeof slideDeckSchema>;
export type SlideNode = z.infer<typeof slideNodeSchema>;
export type RuntimeManifest = z.infer<typeof runtimeManifestSchema>;
export type OutlineConfirmResult = z.infer<typeof outlineConfirmResultSchema>;
export type CourseValidateResult = z.infer<typeof courseValidateResultSchema>;

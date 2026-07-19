import { z } from "zod";

import type { KnowledgeSummary } from "./knowledge";

export const knowledgeSummarySchema = z
  .object({
    schemaVersion: z.literal(1),
    sourceCount: z.number().int().nonnegative(),
    publishedCardCount: z.number().int().nonnegative(),
    reviewTaskCount: z.number().int().nonnegative(),
    retrievalMode: z.enum(["hybrid", "fts-degraded"]),
    indexSnapshotId: z
      .string()
      .regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/)
      .nullable()
      .optional(),
    indexSnapshotDigest: z
      .string()
      .regex(/^[0-9a-f]{64}$/)
      .nullable()
      .optional(),
    indexState: z.enum(["ready", "degraded", "unavailable"]).optional(),
    tagLabels: z.array(z.string().min(1)),
    tagOptions: z
      .array(
        z
          .object({
            id: z.string().min(1).max(128),
            label: z.string().min(1).max(200),
            dimension: z.string().min(1).max(128),
          })
          .strict(),
      )
      .max(500)
      .optional(),
    updatedAt: z.string().datetime(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.indexState === undefined) {
      return;
    }
    const hasUnavailableBinding =
      value.indexSnapshotId === null && value.indexSnapshotDigest === null;
    const hasReadyBinding =
      typeof value.indexSnapshotId === "string" &&
      typeof value.indexSnapshotDigest === "string";
    if (
      (value.indexState === "unavailable" && !hasUnavailableBinding) ||
      (value.indexState !== "unavailable" && !hasReadyBinding)
    ) {
      context.addIssue({ code: "custom", message: "index snapshot binding is inconsistent" });
    }
    if (
      (value.indexState === "ready") !== (value.retrievalMode === "hybrid")
    ) {
      context.addIssue({ code: "custom", message: "index retrieval mode is inconsistent" });
    }
  }) satisfies z.ZodType<KnowledgeSummary>;

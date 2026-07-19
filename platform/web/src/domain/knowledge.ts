export interface KnowledgeSummary {
  schemaVersion: 1;
  sourceCount: number;
  publishedCardCount: number;
  reviewTaskCount: number;
  retrievalMode: "hybrid" | "fts-degraded";
  indexSnapshotId?: string | null;
  indexSnapshotDigest?: string | null;
  indexState?: "ready" | "degraded" | "unavailable";
  tagLabels: string[];
  tagOptions?: Array<{ id: string; label: string; dimension: string }>;
  updatedAt: string;
}

export interface KnowledgeSummaryClient {
  getSummary(): Promise<KnowledgeSummary>;
}

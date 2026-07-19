import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { KnowledgeClient } from "../services/knowledge-client";
import { KnowledgeReviewDrawer } from "./KnowledgeReviewDrawer";

const digest = "a".repeat(64);
const task = {
  taskId: "task-1",
  subjectVersionId: "card-1",
  category: "candidate-card" as const,
  reasonCode: "provenance" as const,
  status: "open" as const,
  blocking: true,
  reviewDigest: digest,
  evidenceCount: 1,
  createdAt: "2026-07-19T05:00:00Z",
};
const detail = {
  task,
  evidenceIds: ["evidence-1"],
  evidenceTotal: 1,
  evidenceTruncated: false,
  cardVersionId: "card-1",
  cardContentDigest: digest,
  cardTitle: "证据优先",
  learningObjective: "建立可验证工作流",
  contentNodes: [{ path: [0], depth: 1, nodeType: "paragraph", text: "先验证，再发布。", level: null, language: null, rows: [] }],
  contentNodeTotal: 1,
  contentNodesTruncated: false,
  citations: [{ chunkId: "chunk-1", sourceVersionId: "source-1", quotedText: "来源原文" }],
  citationTotal: 1,
  citationsTruncated: false,
};

describe("KnowledgeReviewDrawer", () => {
  it("keeps detail in memory, resolves by digest, publishes, and waits for the exact outbox", async () => {
    const user = userEvent.setup();
    const client = {
      listReviews: vi.fn().mockResolvedValue({ result: { items: [task], nextCursor: null } }),
      getReviewDetail: vi.fn()
        .mockResolvedValueOnce({ result: detail })
        .mockResolvedValueOnce({ result: { ...detail, task: { ...task, status: "resolved" } } }),
      resolveReview: vi.fn().mockResolvedValue({ result: { operationId: "op", operationStatus: "committed" } }),
      publishCard: vi.fn().mockResolvedValue({ result: {
        operationId: "publish-op",
        operationStatus: "committed",
        submittedCardVersionId: "card-1",
        publishedCardVersionId: "card-published-1",
        status: "published",
        publicationEvidenceId: "evidence-publication",
        indexState: "queued",
        indexOutboxId: "index-outbox-1",
        indexSnapshotId: null,
      } }),
      indexKnowledge: vi.fn().mockResolvedValue({ result: {
        operationId: "index-op",
        operationStatus: "committed",
        consumedOutboxId: "index-outbox-1",
        indexSnapshotId: "snapshot-1",
        indexSnapshotDigest: digest,
        indexState: "degraded",
        retrievalMode: "fts-degraded",
        semanticIndexAvailable: false,
      } }),
    };
    const onClose = vi.fn();
    const { rerender } = render(
      <KnowledgeReviewDrawer client={client as unknown as KnowledgeClient} open onClose={onClose} />,
    );

    await user.click(await screen.findByRole("button", { name: "查看" }));
    expect(await screen.findByText("先验证，再发布。")).toBeVisible();
    expect(screen.getByText("来源原文")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "接受" }));
    await user.click(await screen.findByRole("button", { name: "发布并等待索引" }));

    await waitFor(() => expect(client.indexKnowledge).toHaveBeenCalledTimes(1));
    expect(client.indexKnowledge.mock.calls[0][0].expectedOutboxId).toBe("index-outbox-1");
    expect(await screen.findByText(/全文检索降级模式/)).toBeVisible();

    rerender(<KnowledgeReviewDrawer client={client as unknown as KnowledgeClient} open={false} onClose={onClose} />);
    expect(screen.queryByText("先验证，再发布。")).toBeNull();
  });

  it("closes with Escape and returns control to the owner", async () => {
    const user = userEvent.setup();
    const client = { listReviews: vi.fn().mockResolvedValue({ result: { items: [], nextCursor: null } }) };
    const onClose = vi.fn();
    render(<KnowledgeReviewDrawer client={client as unknown as KnowledgeClient} open onClose={onClose} />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

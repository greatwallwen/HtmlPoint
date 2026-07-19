import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SourceAsset } from "../domain/course";
import type { KnowledgeSummary } from "../domain/knowledge";
import { KnowledgeClient } from "../services/knowledge-client";
import {
  WorkspaceProvider,
  createFreshWorkspaceState,
} from "../state/workspace";
import { ImportStep } from "./ImportStep";

const summary: KnowledgeSummary = {
  schemaVersion: 1,
  sourceCount: 1,
  publishedCardCount: 3,
  reviewTaskCount: 0,
  retrievalMode: "hybrid",
  tagLabels: ["课程设计"],
  updatedAt: "2026-07-17T02:00:00Z",
};

const storage: Pick<Storage, "getItem" | "setItem"> = {
  getItem: () => null,
  setItem: () => undefined,
};

afterEach(() => {
  cleanup();
});

function renderImportStep(
  client?: { getSummary(): Promise<KnowledgeSummary> },
  withSource = false,
): void {
  const initialState = createFreshWorkspaceState();
  const source: SourceAsset = {
    id: "source-ready",
    name: "guide.md",
    kind: "markdown",
    size: 20,
    status: "ready",
    extractedText: "# guide",
    addedAt: "2026-07-17T00:00:00.000Z",
  };

  render(
    <WorkspaceProvider
      storage={storage}
      initialState={
        withSource
          ? {
              ...initialState,
              course: { ...initialState.course, sources: [source] },
            }
          : initialState
      }
    >
      <ImportStep knowledgeClient={client} />
    </WorkspaceProvider>,
  );
}

describe("ImportStep knowledge preparation placement", () => {
  it.each([false, true])(
    "mounts knowledge preparation directly after the source list (withSource=%s)",
    (withSource) => {
      renderImportStep({ getSummary: vi.fn().mockResolvedValue(summary) }, withSource);

      const sourceList = screen.getByRole("list", { name: "已导入资料" });
      const knowledgeRegion = screen.getByRole("region", { name: "知识准备" });
      expect(sourceList.nextElementSibling).toBe(knowledgeRegion);
    },
  );

  it("does not let an offline helper block the ordinary next action", async () => {
    renderImportStep(
      { getSummary: vi.fn().mockRejectedValue(new Error("offline")) },
      true,
    );

    expect(await screen.findByRole("status")).toHaveTextContent(
      "本地知识服务未连接",
    );
    expect(screen.getByRole("button", { name: "下一步：生成课程" })).toBeEnabled();
  });

  it("runs each selected file through the governed upload and import pipeline", async () => {
    const user = userEvent.setup();
    const client = new KnowledgeClient({
      helperOrigin: "http://127.0.0.1:8765",
      sessionToken: "t".repeat(43),
    } as never);
    vi.spyOn(client, "getSummary").mockResolvedValue(summary);
    const upload = vi.spyOn(client, "uploadSource").mockResolvedValue({
      schemaVersion: 1,
      uploadId: `upload-${"1".repeat(32)}`,
      safeName: "lesson.md",
      sourceKind: "markdown",
      mediaType: "text/markdown",
      byteSize: 8,
      contentDigest: "a".repeat(64),
      state: "available",
      expiresAt: "2026-07-19T08:00:00Z",
    });
    const start = vi.spyOn(client, "startImport").mockResolvedValue({
      result: {
        operationId: "import-operation-1",
        operationStatus: "committed",
        importId: `import-${"2".repeat(32)}`,
        status: "promoted",
        sourceId: "source-1",
        sourceVersionId: "source-v1",
        contentDigest: "a".repeat(64),
        chunkCount: 1,
        visualCount: 1,
        visualVersionIds: ["visual-v1"],
        candidateCardVersionIds: ["card-v1"],
        datasetVersionIds: [],
        datasetProfiles: [],
        reviewTaskIds: ["review-1"],
        extractionEvidenceId: "evidence-1",
      },
      evidence: {} as never,
    });
    renderImportStep(client);

    await user.upload(
      screen.getByLabelText("导入资料"),
      new File(["# lesson"], "lesson.md", { type: "text/markdown" }),
    );

    expect(upload).toHaveBeenCalledTimes(1);
    expect(start).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("1 张候选卡 · 1 项审核")).toBeVisible();
    expect(screen.getByRole("button", { name: "审核与发布知识卡" })).toBeVisible();
  });
});

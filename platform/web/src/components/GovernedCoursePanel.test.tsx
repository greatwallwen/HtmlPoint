import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { KnowledgeClient } from "../services/knowledge-client";
import { WorkspaceProvider, createFreshWorkspaceState } from "../state/workspace";
import { GovernedCoursePanel } from "./GovernedCoursePanel";

const digest = "a".repeat(64);
const createdAt = "2026-07-19T05:00:00Z";
const actor = { actorType: "human" as const, actorId: "local-user", displayName: null };
const slideDeck = {
  schemaVersion: 1 as const,
  logicalId: "deck",
  versionId: "deck-v1",
  revision: 1,
  contentDigest: digest,
  supersedesVersionId: null,
  createdAt,
  createdBy: actor,
  courseVersionId: "course-v1",
  nodes: [{
    schemaVersion: 1 as const,
    nodeId: "slide-1",
    nodeType: "slide" as const,
    text: "证据优先",
    items: [],
    placementIds: ["card-placement-1"],
    cardVersionIds: ["card-v1"],
    chunkIds: ["chunk-1"],
    sourceVersionIds: ["source-other"],
    evidenceIds: ["evidence-1"],
    presenterNotes: "讲师提示",
    assetBindings: [],
    children: [],
  }, {
    schemaVersion: 1 as const,
    nodeId: "slide-2",
    nodeType: "slide" as const,
    text: "真实来源图形",
    items: [],
    placementIds: ["card-placement-2"],
    cardVersionIds: ["card-v2"],
    chunkIds: ["chunk-2"],
    sourceVersionIds: ["source-v1"],
    evidenceIds: ["evidence-2"],
    presenterNotes: "讲师提示",
    assetBindings: [],
    children: [],
  }],
};
const runtimeManifest = {
  schemaVersion: 1 as const,
  logicalId: "runtime",
  versionId: "runtime-v1",
  revision: 1,
  contentDigest: "b".repeat(64),
  supersedesVersionId: null,
  createdAt,
  createdBy: actor,
  courseVersionId: "course-v1",
  slideDeckVersionId: "deck-v1",
  slideDeckDigest: digest,
  jobBindings: [],
  artifactIds: [],
  evidenceIds: ["evidence-1"],
};

describe("GovernedCoursePanel", () => {
  it("binds a source visual, validates the pinned projection, and publishes through Helper", async () => {
    const user = userEvent.setup();
    const fresh = createFreshWorkspaceState();
    const course = {
      ...fresh.course,
      id: "course-v1",
      title: "真实 AI 课程",
      audience: "产品团队",
      goal: "建立证据链",
      chapters: [{
        id: "chapter-1",
        title: "证据优先",
        objective: "验证来源",
        lessons: [{ id: "lesson-1", title: "验证方法", summary: "核对来源与许可", durationMinutes: 45, sourceIds: [], status: "grounded" as const }],
      }],
      updatedAt: createdAt,
    };
    const client = {
      buildCharts: vi.fn(async (job: { specs: Array<{ requestId: string }> }) => ({ result: { items: [{ requestId: job.specs[0].requestId, status: "materialized", artifactId: "chart-artifact-1", visualVersionId: "chart-visual-1", evidenceId: "chart-evidence-1", reused: false }] } })),
      attachVisual: vi.fn().mockResolvedValue({ result: { placementId: "visual-placement-server" } }),
      validateCourse: vi.fn().mockResolvedValue({ result: {
        operationId: "validate-op",
        operationStatus: "committed",
        validationStatus: "passed",
        courseVersionId: "course-v1",
        courseDigest: digest,
        slideDeckId: "deck-v1",
        runtimeManifestId: "runtime-v1",
        runtimeManifestDigest: runtimeManifest.contentDigest,
        courseProjectionId: "projection-1",
        warnings: [],
        slideDeck,
        runtimeManifest,
      } }),
      publishCourse: vi.fn().mockResolvedValue({ result: {
        operationId: "publish-op",
        operationStatus: "committed",
        courseVersionId: "course-v1",
        slideDeckId: "deck-v1",
        runtimeManifestId: "runtime-v1",
        runtimeManifestDigest: runtimeManifest.contentDigest,
        courseProjectionId: "projection-1",
      } }),
      getCourseProjection: vi.fn().mockResolvedValue({
        schemaVersion: 1,
        courseVersionId: "course-v1",
        courseDigest: digest,
        usageScope: "internal",
        status: "published",
        requirement: {
          requirementId: "requirement-1",
          title: "真实 AI 课程",
          audience: "产品团队",
          learningGoals: ["建立证据链"],
          durationMinutes: 45,
          requiredTagIds: [],
          excludedTagIds: [],
          usageScope: "internal",
        },
        outline: {
          schemaVersion: 1,
          logicalId: "outline",
          versionId: "outline-v1",
          revision: 1,
          contentDigest: "c".repeat(64),
          supersedesVersionId: null,
          createdAt,
          createdBy: actor,
          requirementId: "requirement-1",
          chapters: [{ schemaVersion: 1, chapterId: "chapter-1", title: "证据优先", objective: "验证来源", placements: [{ schemaVersion: 1, placementId: "card-placement-1", cardVersionId: "card-v1", chapterId: "chapter-1", lessonId: "lesson-1", purpose: "core", allocatedMinutes: 45 }] }],
          uncoveredGoals: [],
          retrievalEvidenceId: "retrieval-evidence-1",
          indexSnapshotId: "snapshot-1",
        },
        slideDeck,
        runtimeManifest,
      }),
      recoverOperation: vi.fn(),
    };
    render(
      <WorkspaceProvider
        storage={{ getItem: () => null, setItem: () => undefined }}
        initialState={{
          ...fresh,
          step: "edit",
          course,
          governed: {
            requirementId: "requirement-1",
            outlineVersionId: "outline-v1",
            courseVersionId: "course-v1",
            slideDeckId: "deck-v1",
            runtimeManifestId: "runtime-v1",
            cardVersionIds: ["card-v1"],
            visualPlacementIds: [],
          },
          governedAssets: {
            sourceVisuals: [{ visualVersionId: "visual-source-v1", sourceVersionId: "source-v1", label: "lesson.pptx · 图形 1" }],
            datasetVersionIds: ["dataset-v1"],
            datasetProfiles: [{
              datasetVersionId: "dataset-v1",
              contentDigest: "c".repeat(64),
              schemaDigest: "d".repeat(64),
              rowCount: 40,
              columns: [
                { name: "segment", dataType: "VARCHAR", digest: "e".repeat(64) },
                { name: "revenue", dataType: "INTEGER", digest: "f".repeat(64) },
              ],
            }],
          },
          governedProjection: {
            courseDigest: digest,
            usageScope: "internal",
            courseUpdatedAt: createdAt,
            slideDeck,
            warnings: [],
            publicationStatus: "confirmed",
          },
        }}
      >
        <GovernedCoursePanel client={client as unknown as KnowledgeClient} />
      </WorkspaceProvider>,
    );

    expect(screen.getByText(/发布范围：组织内部/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "生成真实图表" }));
    await waitFor(() => expect(client.buildCharts).toHaveBeenCalledTimes(1));
    expect(client.attachVisual.mock.calls[0][0].originatingDatasetVersionId).toBe("dataset-v1");
    await user.click(screen.getByRole("button", { name: "绑定" }));
    await waitFor(() => expect(client.attachVisual).toHaveBeenCalledTimes(2));
    expect(client.attachVisual.mock.calls[1][0].originatingSourceVersionId).toBe("source-v1");
    expect(client.attachVisual.mock.calls[1][0].slideNodeId).toBe("slide-2");
    expect(client.attachVisual.mock.calls[0][0].slotId).not.toBe(client.attachVisual.mock.calls[1][0].slotId);
    expect(client.attachVisual.mock.calls[0][0].slotId).toMatch(/^visual-slot-visual-placement-/);
    expect(client.validateCourse).toHaveBeenCalledTimes(2);
    const publishButton = await screen.findByRole("button", { name: "发布课程" });
    expect(publishButton).toBeEnabled();
    await user.click(publishButton);
    await waitFor(() => expect(client.publishCourse).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/课程已发布/)).toBeVisible();
  });

  it("fails closed when local edits make the governed projection stale", () => {
    const fresh = createFreshWorkspaceState();
    render(
      <WorkspaceProvider
        storage={{ getItem: () => null, setItem: () => undefined }}
        initialState={{
          ...fresh,
          step: "edit",
          course: { ...fresh.course, updatedAt: "2026-07-19T06:00:00Z" },
          governed: { courseVersionId: "course-v1", cardVersionIds: [], visualPlacementIds: [] },
          governedProjection: {
            courseDigest: digest,
            usageScope: "private-training",
            courseUpdatedAt: createdAt,
            slideDeck,
            warnings: [],
            publicationStatus: "validated",
          },
        }}
      >
        <GovernedCoursePanel client={{} as KnowledgeClient} />
      </WorkspaceProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("旧的受控投影不能发布");
    expect(screen.getByRole("button", { name: "发布课程" })).toBeDisabled();
  });
});

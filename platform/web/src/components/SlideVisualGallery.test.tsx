import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SlideDeck } from "../domain/helper-contracts-schema";
import type { ArtifactClient } from "../services/artifact-client";
import { SlideVisualGallery } from "./SlideVisualGallery";

const digest = "a".repeat(64);
const binding = {
  schemaVersion: 1 as const,
  bindingId: "binding-1",
  visualPlacementId: "placement-1",
  visualVersionId: "visual-1",
  artifactId: "artifact-1",
  artifactDigest: digest,
  mediaType: "image/svg+xml" as const,
  altText: "真实数据图表",
  authenticityEvidenceId: "auth-evidence-1",
  licenseEvidenceId: "license-evidence-1",
  attributionId: "attribution-1",
  attribution: {
    schemaVersion: 1 as const,
    title: "官方统计图",
    creator: "数据团队",
    publisher: "课程平台",
    licenseLabel: "CC BY 4.0",
    landingLink: { schemaVersion: 1 as const, linkId: "landing-link-1", href: "https://example.com/source", label: "来源", linkType: "landing" as const, provenanceKind: "official-primary" as const },
    licenseLink: { schemaVersion: 1 as const, linkId: "license-link-1", href: "https://example.com/license", label: "许可", linkType: "license" as const, provenanceKind: "official-primary" as const },
  },
  transformationId: "transform-1",
  transformation: {
    schemaVersion: 1 as const,
    transformationId: "transform-1",
    crop: null,
    scaleMode: "contain" as const,
    colorAdjustments: [],
    changeNotice: "仅适配版面",
    derivativeLicenseDecision: "not-derivative" as const,
    exportLicense: null,
    shareAlikeCompatible: true,
    gfdlCompatible: true,
    noDerivativesCompatible: true,
  },
};

const slideDeck = {
  schemaVersion: 1 as const,
  logicalId: "deck",
  versionId: "deck-v1",
  revision: 1,
  contentDigest: digest,
  supersedesVersionId: null,
  createdAt: "2026-07-19T00:00:00Z",
  createdBy: { actorType: "human" as const, actorId: "local-user", displayName: null },
  courseVersionId: "course-v1",
  nodes: [{ schemaVersion: 1 as const, nodeId: "slide-1", nodeType: "slide" as const, text: "课程", items: [], placementIds: ["card-placement-1"], cardVersionIds: ["card-v1"], chunkIds: [], sourceVersionIds: [], evidenceIds: ["evidence-1"], presenterNotes: null, assetBindings: [binding], children: [] }],
} satisfies SlideDeck;

describe("SlideVisualGallery", () => {
  it("loads authenticated artifact bytes, renders attribution, and disposes the scoped URL", async () => {
    const dispose = vi.fn();
    const scoped = { fetchArtifact: vi.fn().mockResolvedValue({ artifactId: "artifact-1", mediaType: "image/svg+xml", byteSize: 20, objectUrl: "blob:verified-chart" }), dispose };
    const client = { fork: vi.fn(() => scoped) } as unknown as ArtifactClient;
    const view = render(<SlideVisualGallery slideDeck={slideDeck} artifactClient={client} />);
    expect(await screen.findByRole("img", { name: "真实数据图表" })).toHaveAttribute("src", "blob:verified-chart");
    expect(screen.getByText("官方统计图")).toBeVisible();
    expect(screen.getByRole("link", { name: "来源页" })).toHaveAttribute("rel", expect.stringContaining("noopener"));
    view.unmount();
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("fails visibly without an authenticated artifact client", async () => {
    render(<SlideVisualGallery slideDeck={slideDeck} />);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("图形暂不可用"));
    expect(screen.getByText("官方统计图")).toBeVisible();
  });
});

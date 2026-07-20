import { ImageSquare, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";

import type { SlideDeck, SlideNode } from "../domain/helper-contracts-schema";
import { trustedExternalLinkProps } from "../services/knowledge-client";
import type {
  LoadedArtifact,
  ProjectionArtifactReader,
} from "../services/artifact-client";

function flatten(nodes: SlideNode[]): SlideNode[] {
  const result: SlideNode[] = [];
  const stack = [...nodes].reverse();
  while (stack.length > 0) {
    const node = stack.pop()!;
    result.push(node);
    stack.push(...[...node.children].reverse());
  }
  return result;
}

export interface SlideVisualGalleryProps {
  slideDeck?: SlideDeck;
  artifactClient?: ProjectionArtifactReader;
  compact?: boolean;
}

export function SlideVisualGallery({
  slideDeck,
  artifactClient,
  compact = false,
}: SlideVisualGalleryProps) {
  const bindings = useMemo(() => {
    if (!slideDeck) return [];
    const seen = new Set<string>();
    return flatten(slideDeck.nodes)
      .flatMap((node) => node.assetBindings)
      .filter((binding) => {
        if (seen.has(binding.visualPlacementId)) return false;
        seen.add(binding.visualPlacementId);
        return true;
      });
  }, [slideDeck]);

  if (bindings.length === 0) return null;

  return (
    <section className={`slide-visual-gallery${compact ? " is-compact" : ""}`} aria-label="课程真实图形">
      {bindings.map((binding) => (
        <GovernedVisual
          key={binding.visualPlacementId}
          binding={binding}
          artifactClient={artifactClient}
        />
      ))}
    </section>
  );
}

function GovernedVisual({
  binding,
  artifactClient,
}: {
  binding: SlideNode["assetBindings"][number];
  artifactClient?: ProjectionArtifactReader;
}) {
  const scopedClient = useMemo(
    () => artifactClient?.fork(),
    [artifactClient, binding.artifactId],
  );
  const [artifact, setArtifact] = useState<LoadedArtifact>();
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setArtifact(undefined);
    setFailed(false);
    if (!scopedClient) {
      setFailed(true);
      return () => controller.abort();
    }
    void scopedClient.fetchArtifact(binding.artifactId, controller.signal).then(
      setArtifact,
      () => setFailed(true),
    );
    return () => {
      controller.abort();
      scopedClient.dispose();
    };
  }, [binding.artifactId, scopedClient]);

  const attribution = binding.attribution;
  return (
    <figure data-visual-placement-id={binding.visualPlacementId}>
      {artifact ? (
        <img src={artifact.objectUrl} alt={binding.altText} />
      ) : failed ? (
        <div className="visual-fallback" role="status">
          <WarningCircle aria-hidden="true" size={24} />图形暂不可用
        </div>
      ) : (
        <div className="visual-loading" role="status">
          <ImageSquare aria-hidden="true" size={24} />正在加载图形
        </div>
      )}
      <figcaption>
        <strong>{attribution.title}</strong>
        <span>{[attribution.creator, attribution.publisher, attribution.licenseLabel].filter(Boolean).join(" · ")}</span>
        {binding.transformation.changeNotice ? <span>{binding.transformation.changeNotice}</span> : null}
        <span className="visual-links">
          {attribution.landingLink ? <a {...trustedExternalLinkProps(attribution.landingLink)}>来源页</a> : null}
          {attribution.licenseLink ? <a {...trustedExternalLinkProps(attribution.licenseLink)}>许可</a> : null}
        </span>
      </figcaption>
    </figure>
  );
}

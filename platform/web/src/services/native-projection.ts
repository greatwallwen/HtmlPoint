import { useLayoutEffect } from "react";
import { z } from "zod";

import { teachingCourseSchema } from "../domain/course-schema";
import { slideDeckSchema } from "../domain/helper-contracts-schema";
import type {
  LoadedArtifact,
  ProjectionArtifactReader,
} from "./artifact-client";

const roleSchema = z.enum(["stage", "presenter"]);
const opaqueIdSchema = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
const sessionIdSchema = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/);
const digestSchema = z.string().regex(/^[0-9a-f]{64}$/);

const handshakeSchema = z
  .object({
    schemaVersion: z.literal(1),
    role: roleSchema,
    channelId: z.string().uuid(),
    origin: z.literal("https://projection.course-studio.test"),
  })
  .strict();

const teachingFrameSchema = z
  .object({
    sessionId: sessionIdSchema,
    courseId: opaqueIdSchema,
    lessonId: opaqueIdSchema,
    lessonIndex: z.number().int().nonnegative().max(10_000),
    lessonCount: z.number().int().positive().max(10_000),
    playing: z.boolean(),
    elapsedSeconds: z.number().int().nonnegative().max(172_800),
    sequence: z.number().int().nonnegative().max(2_147_483_647),
    sentAt: z.string().datetime({ offset: true }),
  })
  .strict()
  .refine((frame) => frame.lessonIndex < frame.lessonCount, {
    message: "lesson index must stay inside the frame",
  });

export const projectionFrameSchema = z
  .object({
    schemaVersion: z.literal(1),
    type: z.literal("projection_frame"),
    role: roleSchema,
    channelId: z.string().uuid(),
    sessionId: sessionIdSchema,
    courseVersionId: opaqueIdSchema,
    runtimeManifestDigest: digestSchema,
    navigationIdentity: digestSchema,
    generation: z.number().int().nonnegative().max(2_147_483_647),
    sequence: z.number().int().nonnegative().max(2_147_483_647),
    frameDigest: digestSchema,
    teachingFrame: teachingFrameSchema,
  })
  .strict()
  .superRefine((frame, context) => {
    if (frame.sequence !== frame.teachingFrame.sequence) {
      context.addIssue({ code: "custom", message: "frame sequence mismatch" });
    }
    if (frame.sessionId !== frame.teachingFrame.sessionId) {
      context.addIssue({ code: "custom", message: "frame session mismatch" });
    }
  });

export const projectionBootstrapSchema = z
  .object({
    schemaVersion: z.literal(1),
    type: z.literal("projection_bootstrap"),
    role: roleSchema,
    channelId: z.string().uuid(),
    sessionId: sessionIdSchema,
    courseVersionId: opaqueIdSchema,
    courseDigest: digestSchema,
    runtimeManifestDigest: digestSchema,
    navigationIdentity: digestSchema,
    generation: z.number().int().nonnegative().max(2_147_483_647),
    course: teachingCourseSchema.strict(),
    slideDeck: slideDeckSchema,
    frame: projectionFrameSchema,
  })
  .strict()
  .superRefine((bootstrap, context) => {
    const frame = bootstrap.frame;
    const identitiesMatch =
      bootstrap.role === frame.role &&
      bootstrap.channelId === frame.channelId &&
      bootstrap.sessionId === frame.sessionId &&
      bootstrap.courseVersionId === frame.courseVersionId &&
      bootstrap.runtimeManifestDigest === frame.runtimeManifestDigest &&
      bootstrap.navigationIdentity === frame.navigationIdentity &&
      bootstrap.generation === frame.generation;
    if (!identitiesMatch) {
      context.addIssue({ code: "custom", message: "bootstrap frame identity mismatch" });
    }
    if (
      bootstrap.course.id !== frame.teachingFrame.courseId ||
      bootstrap.courseVersionId !== bootstrap.slideDeck.courseVersionId
    ) {
      context.addIssue({ code: "custom", message: "bootstrap course identity mismatch" });
    }
  });

export type ProjectionFrame = z.infer<typeof projectionFrameSchema>;
export type ProjectionBootstrap = z.infer<typeof projectionBootstrapSchema>;
export type ProjectionHandshake = z.infer<typeof handshakeSchema>;

interface WebViewBridge {
  addEventListener(
    type: "message",
    listener: (event: MessageEvent<unknown>) => void,
  ): void;
  removeEventListener(
    type: "message",
    listener: (event: MessageEvent<unknown>) => void,
  ): void;
  postMessage(message: unknown): void;
}

declare global {
  interface Window {
    chrome?: { webview?: WebViewBridge };
    __courseStudioProjection?: ProjectionHandshake;
  }
}

export interface NativeProjectionAdapter {
  role: "stage" | "presenter";
  waitForBootstrap(): Promise<ProjectionBootstrap>;
  subscribeFrame(listener: (frame: ProjectionFrame) => void): () => void;
  reportMessageAccepted(frame: ProjectionFrame): void;
  reportFrameCommitted(frame: ProjectionFrame): void;
}

export interface NativeProjectionRenderContext {
  adapter: NativeProjectionAdapter;
  frame: ProjectionFrame;
}

export function createNativeProjectionArtifactReader(
  bootstrap: ProjectionBootstrap,
): ProjectionArtifactReader {
  const mediaTypes = new Map<string, LoadedArtifact["mediaType"]>();
  const stack = [...bootstrap.slideDeck.nodes];
  while (stack.length > 0) {
    const node = stack.pop()!;
    for (const binding of node.assetBindings) {
      mediaTypes.set(binding.artifactId, binding.mediaType);
    }
    stack.push(...node.children);
  }
  return new NativeProjectionArtifactReader(mediaTypes);
}

class NativeProjectionArtifactReader implements ProjectionArtifactReader {
  constructor(
    private readonly mediaTypes: ReadonlyMap<string, LoadedArtifact["mediaType"]>,
  ) {}

  fork(): ProjectionArtifactReader {
    return new NativeProjectionArtifactReader(this.mediaTypes);
  }

  async fetchArtifact(
    artifactId: string,
    externalSignal?: AbortSignal,
  ): Promise<LoadedArtifact> {
    if (externalSignal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (!opaqueIdSchema.safeParse(artifactId).success) {
      throw new Error("Native projection asset identity is invalid.");
    }
    const mediaType = this.mediaTypes.get(artifactId);
    if (mediaType === undefined) {
      throw new Error("Native projection asset is not mapped.");
    }
    return {
      artifactId,
      mediaType,
      byteSize: 0,
      objectUrl: new URL(
        `/session-assets/${encodeURIComponent(artifactId)}`,
        "https://projection.course-studio.test",
      ).toString(),
    };
  }

  dispose(): void {}
}

class WebViewNativeProjectionAdapter implements NativeProjectionAdapter {
  readonly role: "stage" | "presenter";

  private readonly listeners = new Set<(frame: ProjectionFrame) => void>();
  private readonly acceptedFrames = new Set<string>();
  private readonly knownFrames = new Set<string>();
  private bootstrap?: ProjectionBootstrap;
  private bootstrapPromise?: Promise<ProjectionBootstrap>;
  private resolveBootstrap?: (value: ProjectionBootstrap) => void;
  private listening = false;
  private readyReported = false;
  private latestSequence = -1;

  constructor(
    private readonly bridge: WebViewBridge,
    private readonly handshake: ProjectionHandshake,
  ) {
    this.role = handshake.role;
  }

  waitForBootstrap(): Promise<ProjectionBootstrap> {
    if (this.bootstrap !== undefined) return Promise.resolve(this.bootstrap);
    if (this.bootstrapPromise === undefined) {
      this.bootstrapPromise = new Promise<ProjectionBootstrap>((resolve) => {
        this.resolveBootstrap = resolve;
      });
      this.startListening();
    }
    return this.bootstrapPromise;
  }

  subscribeFrame(listener: (frame: ProjectionFrame) => void): () => void {
    this.listeners.add(listener);
    this.startListening();
    return () => this.listeners.delete(listener);
  }

  reportMessageAccepted(frame: ProjectionFrame): void {
    const key = frameKey(frame);
    if (!this.knownFrames.has(key)) {
      this.reject("unknown_frame_receipt");
      return;
    }
    this.acceptedFrames.add(key);
    this.bridge.postMessage(receipt("message_accepted", frame));
  }

  reportFrameCommitted(frame: ProjectionFrame): void {
    const key = frameKey(frame);
    if (!this.acceptedFrames.has(key)) {
      this.reject("frame_not_accepted");
      return;
    }
    this.bridge.postMessage(receipt("frame_committed", frame));
  }

  private readonly onMessage = (event: MessageEvent<unknown>): void => {
    if (this.bootstrap === undefined) {
      const parsed = projectionBootstrapSchema.safeParse(event.data);
      if (!parsed.success || !matchesHandshake(parsed.data, this.handshake)) {
        this.reject("invalid_bootstrap");
        return;
      }
      this.bootstrap = parsed.data;
      this.latestSequence = parsed.data.frame.sequence;
      this.knownFrames.add(frameKey(parsed.data.frame));
      this.resolveBootstrap?.(parsed.data);
      this.resolveBootstrap = undefined;
      return;
    }

    const parsed = projectionFrameSchema.safeParse(event.data);
    if (!parsed.success) {
      this.reject("invalid_frame");
      return;
    }
    const frame = parsed.data;
    if (!matchesBootstrap(frame, this.bootstrap)) {
      this.reject("frame_identity_mismatch");
      return;
    }
    if (frame.sequence <= this.latestSequence) {
      this.reject("frame_order_invalid");
      return;
    }

    this.latestSequence = frame.sequence;
    this.knownFrames.add(frameKey(frame));
    for (const listener of this.listeners) listener(frame);
  };

  private startListening(): void {
    if (this.listening) return;
    this.listening = true;
    this.bridge.addEventListener("message", this.onMessage);
    if (!this.readyReported) {
      this.readyReported = true;
      this.bridge.postMessage({
        schemaVersion: 1,
        type: "projection_ready",
        role: this.role,
        channelId: this.handshake.channelId,
      });
    }
  }

  private reject(code: string): void {
    this.bridge.postMessage({
      schemaVersion: 1,
      type: "projection_rejected",
      role: this.role,
      channelId: this.handshake.channelId,
      code,
    });
  }
}

export function detectNativeProjectionAdapter(
  scope: Window & typeof globalThis,
): NativeProjectionAdapter | undefined {
  const webview = scope.chrome?.webview;
  const handshake = handshakeSchema.safeParse(scope.__courseStudioProjection);
  if (
    webview === undefined ||
    typeof webview.addEventListener !== "function" ||
    typeof webview.removeEventListener !== "function" ||
    typeof webview.postMessage !== "function" ||
    !handshake.success
  ) {
    return undefined;
  }
  return new WebViewNativeProjectionAdapter(webview, handshake.data);
}

export function useNativeProjectionCommit(
  adapter: NativeProjectionAdapter | undefined,
  frame: ProjectionFrame | undefined,
  scope: Pick<Window, "requestAnimationFrame" | "cancelAnimationFrame"> = window,
): void {
  useLayoutEffect(() => {
    if (adapter === undefined || frame === undefined) return;
    let secondFrame = 0;
    const firstFrame = scope.requestAnimationFrame(() => {
      secondFrame = scope.requestAnimationFrame(() => {
        adapter.reportFrameCommitted(frame);
      });
    });
    return () => {
      scope.cancelAnimationFrame(firstFrame);
      if (secondFrame !== 0) scope.cancelAnimationFrame(secondFrame);
    };
  }, [adapter, frame, scope]);
}

function matchesHandshake(
  bootstrap: ProjectionBootstrap,
  handshake: ProjectionHandshake,
): boolean {
  return (
    bootstrap.role === handshake.role &&
    bootstrap.channelId === handshake.channelId
  );
}

function matchesBootstrap(
  frame: ProjectionFrame,
  bootstrap: ProjectionBootstrap,
): boolean {
  return (
    frame.role === bootstrap.role &&
    frame.channelId === bootstrap.channelId &&
    frame.sessionId === bootstrap.sessionId &&
    frame.courseVersionId === bootstrap.courseVersionId &&
    frame.runtimeManifestDigest === bootstrap.runtimeManifestDigest &&
    frame.navigationIdentity === bootstrap.navigationIdentity &&
    frame.generation === bootstrap.generation &&
    frame.teachingFrame.courseId === bootstrap.course.id
  );
}

function frameKey(frame: ProjectionFrame): string {
  return `${frame.generation}:${frame.sequence}:${frame.frameDigest}`;
}

function receipt(
  type: "message_accepted" | "frame_committed",
  frame: ProjectionFrame,
) {
  return {
    schemaVersion: 1 as const,
    type,
    role: frame.role,
    channelId: frame.channelId,
    sessionId: frame.sessionId,
    courseVersionId: frame.courseVersionId,
    runtimeManifestDigest: frame.runtimeManifestDigest,
    navigationIdentity: frame.navigationIdentity,
    generation: frame.generation,
    sequence: frame.sequence,
    frameDigest: frame.frameDigest,
  };
}

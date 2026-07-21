import { act, render } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  detectNativeProjectionAdapter,
  useNativeProjectionCommit,
  type NativeProjectionAdapter,
  type ProjectionBootstrap,
  type ProjectionFrame,
} from "./native-projection";

const digest = (character: string): string => character.repeat(64);
const timestamp = "2026-07-20T02:10:00Z";

const teachingFrame = {
  sessionId: "session-1",
  courseId: "course-1",
  lessonId: "lesson-1",
  lessonIndex: 0,
  lessonCount: 1,
  playing: false,
  elapsedSeconds: 0,
  sequence: 1,
  sentAt: timestamp,
};

const projectionFrame: ProjectionFrame = {
  schemaVersion: 1,
  type: "projection_frame",
  role: "stage",
  channelId: "11111111-1111-4111-8111-111111111111",
  sessionId: "session-1",
  courseVersionId: "course-version-1",
  runtimeManifestDigest: digest("b"),
  navigationIdentity: digest("c"),
  generation: 3,
  sequence: 1,
  frameDigest: digest("d"),
  teachingFrame,
};

const bootstrap: ProjectionBootstrap = {
  schemaVersion: 1,
  type: "projection_bootstrap",
  role: "stage",
  channelId: "11111111-1111-4111-8111-111111111111",
  sessionId: "session-1",
  courseVersionId: "course-version-1",
  courseDigest: digest("a"),
  runtimeManifestDigest: digest("b"),
  navigationIdentity: digest("c"),
  generation: 3,
  course: {
    schemaVersion: 1,
    id: "course-1",
    title: "AI course",
    audience: "Personal learner",
    goal: "Understand AI",
    durationMinutes: 30,
    chapters: [
      {
        id: "chapter-1",
        title: "Start",
        objective: "Learn",
        lessons: [
          {
            id: "lesson-1",
            title: "First lesson",
            summary: "A grounded lesson",
            durationMinutes: 30,
            sourceIds: [],
            status: "grounded",
          },
        ],
      },
    ],
    sources: [],
    updatedAt: timestamp,
  },
  slideDeck: {
    schemaVersion: 1,
    logicalId: "deck-1",
    versionId: "deck-version-1",
    revision: 1,
    contentDigest: digest("e"),
    supersedesVersionId: null,
    createdAt: timestamp,
    createdBy: {
      actorType: "system",
      actorId: "projection-host",
      displayName: null,
    },
    courseVersionId: "course-version-1",
    nodes: [
      {
        schemaVersion: 1,
        nodeId: "slide-1",
        nodeType: "slide",
        text: null,
        items: [],
        placementIds: ["placement-1"],
        cardVersionIds: ["card-version-1"],
        chunkIds: [],
        sourceVersionIds: [],
        evidenceIds: ["evidence-1"],
        presenterNotes: null,
        assetBindings: [],
        children: [],
      },
    ],
  },
  frame: projectionFrame,
};

class FakeWebView {
  readonly messages: unknown[] = [];
  private readonly listeners = new Set<(event: MessageEvent<unknown>) => void>();

  addEventListener(_type: "message", listener: (event: MessageEvent<unknown>) => void) {
    this.listeners.add(listener);
  }

  removeEventListener(_type: "message", listener: (event: MessageEvent<unknown>) => void) {
    this.listeners.delete(listener);
  }

  postMessage(message: unknown) {
    this.messages.push(message);
  }

  emit(data: unknown) {
    for (const listener of this.listeners) {
      listener({ data } as MessageEvent<unknown>);
    }
  }
}

function scope(
  webview?: FakeWebView,
  role: "stage" | "presenter" = "stage",
): Window & typeof globalThis {
  const candidate = {
    chrome: webview === undefined ? undefined : { webview },
    __courseStudioProjection: {
      schemaVersion: 1,
      role,
      channelId: "11111111-1111-4111-8111-111111111111",
      origin: "https://projection.course-studio.test",
    },
  } as unknown as Window & typeof globalThis;
  Object.defineProperty(candidate, "localStorage", {
    get: () => {
      throw new Error("native projection must not read localStorage");
    },
  });
  return candidate;
}

describe("native projection adapter", () => {
  it("exists only for a strict host-injected WebView handshake", () => {
    expect(detectNativeProjectionAdapter(scope())).toBeUndefined();

    const webview = new FakeWebView();
    const nativeScope = scope(webview);
    expect(detectNativeProjectionAdapter(nativeScope)?.role).toBe("stage");

    (nativeScope as unknown as { __courseStudioProjection: object }).__courseStudioProjection = {
      ...nativeScope.__courseStudioProjection,
      role: "controller",
    };
    expect(detectNativeProjectionAdapter(nativeScope)).toBeUndefined();
  });

  it("waits for a strict bootstrap without reading localStorage", async () => {
    const webview = new FakeWebView();
    const adapter = detectNativeProjectionAdapter(scope(webview));
    expect(adapter).toBeDefined();

    const pending = adapter!.waitForBootstrap();
    expect(webview.messages).toContainEqual({
      schemaVersion: 1,
      type: "projection_ready",
      role: "stage",
      channelId: "11111111-1111-4111-8111-111111111111",
    });
    webview.emit({ ...bootstrap, executablePath: "bad.exe" });
    expect(webview.messages).toContainEqual(
      expect.objectContaining({ type: "projection_rejected", code: "invalid_bootstrap" }),
    );

    webview.emit(bootstrap);
    await expect(pending).resolves.toEqual(bootstrap);
  });

  it("rejects wrong role course runtime navigation generation and stale order", async () => {
    const webview = new FakeWebView();
    const adapter = detectNativeProjectionAdapter(scope(webview))!;
    const received: ProjectionFrame[] = [];
    const pending = adapter.waitForBootstrap();
    webview.emit(bootstrap);
    await pending;
    adapter.subscribeFrame((frame) => received.push(frame));

    const invalidFrames = [
      { ...projectionFrame, role: "presenter" },
      { ...projectionFrame, courseVersionId: "course-version-2" },
      { ...projectionFrame, runtimeManifestDigest: digest("f") },
      { ...projectionFrame, navigationIdentity: digest("f") },
      { ...projectionFrame, generation: 4 },
      { ...projectionFrame, sequence: 0, teachingFrame: { ...teachingFrame, sequence: 0 } },
    ];
    invalidFrames.forEach((frame) => webview.emit(frame));
    expect(received).toEqual([]);

    const next = {
      ...projectionFrame,
      sequence: 2,
      frameDigest: digest("f"),
      teachingFrame: { ...teachingFrame, sequence: 2 },
    } satisfies ProjectionFrame;
    webview.emit(next);
    expect(received).toEqual([next]);
  });

  it("posts role-bound accepted and committed receipts", async () => {
    const webview = new FakeWebView();
    const adapter = detectNativeProjectionAdapter(scope(webview))!;
    const pending = adapter.waitForBootstrap();
    webview.emit(bootstrap);
    await pending;

    adapter.reportMessageAccepted(projectionFrame);
    adapter.reportFrameCommitted(projectionFrame);

    expect(webview.messages).toContainEqual(
      expect.objectContaining({ type: "message_accepted", role: "stage", sequence: 1 }),
    );
    expect(webview.messages).toContainEqual(
      expect.objectContaining({ type: "frame_committed", role: "stage", sequence: 1 }),
    );
  });

  it("allows only the presenter to request one bounded update per committed frame", async () => {
    const webview = new FakeWebView();
    const adapter = detectNativeProjectionAdapter(scope(webview, "presenter"))!;
    const presenterFrame = {
      ...projectionFrame,
      role: "presenter",
    } satisfies ProjectionFrame;
    const presenterBootstrap = {
      ...bootstrap,
      role: "presenter",
      frame: presenterFrame,
    } satisfies ProjectionBootstrap;
    const pending = adapter.waitForBootstrap();
    webview.emit(presenterBootstrap);
    await pending;

    expect(adapter.requestTeachingUpdate(presenterFrame, {
      ...teachingFrame,
      playing: true,
      sequence: 2,
    })).toBe(true);
    expect(webview.messages).toContainEqual({
      schemaVersion: 1,
      type: "projection_control",
      role: "presenter",
      channelId: presenterFrame.channelId,
      sessionId: presenterFrame.sessionId,
      courseVersionId: presenterFrame.courseVersionId,
      runtimeManifestDigest: presenterFrame.runtimeManifestDigest,
      navigationIdentity: presenterFrame.navigationIdentity,
      generation: presenterFrame.generation,
      baseSequence: 1,
      lessonId: "lesson-1",
      lessonIndex: 0,
      playing: true,
      elapsedSeconds: 0,
    });
    expect(adapter.requestTeachingUpdate(presenterFrame, {
      ...teachingFrame,
      playing: false,
      sequence: 2,
    })).toBe(false);

    const next = {
      ...presenterFrame,
      sequence: 2,
      frameDigest: digest("f"),
      teachingFrame: { ...teachingFrame, playing: true, sequence: 2 },
    } satisfies ProjectionFrame;
    webview.emit(next);
    expect(adapter.requestTeachingUpdate(next, {
      ...next.teachingFrame,
      playing: false,
      sequence: 3,
    })).toBe(true);

    const stage = detectNativeProjectionAdapter(scope(new FakeWebView()))!;
    expect(stage.requestTeachingUpdate(projectionFrame, teachingFrame)).toBe(false);
  });

  it("reports a frame commit only after layout effect and two animation frames", () => {
    const queued: FrameRequestCallback[] = [];
    const animationScope = {
      requestAnimationFrame: vi.fn((callback: FrameRequestCallback) => {
        queued.push(callback);
        return queued.length;
      }),
      cancelAnimationFrame: vi.fn(),
    } as unknown as Window & typeof globalThis;
    const adapter = {
      reportFrameCommitted: vi.fn(),
    } as unknown as NativeProjectionAdapter;

    function Probe() {
      useNativeProjectionCommit(adapter, projectionFrame, animationScope);
      return createElement("div", { "data-testid": "committed-content" });
    }

    render(createElement(Probe));
    expect(adapter.reportFrameCommitted).not.toHaveBeenCalled();
    expect(queued).toHaveLength(1);

    act(() => queued.shift()!(1));
    expect(adapter.reportFrameCommitted).not.toHaveBeenCalled();
    expect(queued).toHaveLength(1);

    act(() => queued.shift()!(2));
    expect(adapter.reportFrameCommitted).toHaveBeenCalledOnce();
    expect(adapter.reportFrameCommitted).toHaveBeenCalledWith(projectionFrame);
  });
});

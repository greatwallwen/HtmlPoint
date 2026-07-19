import type { CourseDocument } from "./course";
import { courseDocumentSchema } from "./course-schema";
import type { SlideDeck } from "./helper-contracts-schema";
import { slideDeckSchema } from "./helper-contracts-schema";

export interface TeachingProjectionSnapshot {
  course: CourseDocument;
  slideDeck?: SlideDeck;
}

type ProjectionMessage =
  | { type: "projection-request"; sessionId: string }
  | { type: "projection-snapshot"; sessionId: string; course: unknown; slideDeck?: unknown };

const channelName = (sessionId: string) => `course-studio-projection-${sessionId}`;

export function respondToProjectionRequests(
  sessionId: string,
  snapshot: TeachingProjectionSnapshot,
): () => void {
  if (typeof globalThis.BroadcastChannel !== "function") return () => undefined;
  const channel = new BroadcastChannel(channelName(sessionId));
  channel.addEventListener("message", (event: MessageEvent<ProjectionMessage>) => {
    if (event.data?.type !== "projection-request" || event.data.sessionId !== sessionId) return;
    channel.postMessage({
      type: "projection-snapshot",
      sessionId,
      course: snapshot.course,
      ...(snapshot.slideDeck ? { slideDeck: snapshot.slideDeck } : null),
    } satisfies ProjectionMessage);
  });
  return () => channel.close();
}

export function requestTeachingProjection(
  sessionId: string,
  onSnapshot: (snapshot: TeachingProjectionSnapshot) => void,
): () => void {
  if (typeof globalThis.BroadcastChannel !== "function") return () => undefined;
  const channel = new BroadcastChannel(channelName(sessionId));
  const request = () => channel.postMessage({ type: "projection-request", sessionId } satisfies ProjectionMessage);
  const listener = (event: MessageEvent<ProjectionMessage>) => {
    if (event.data?.type !== "projection-snapshot" || event.data.sessionId !== sessionId) return;
    const course = courseDocumentSchema.safeParse(event.data.course);
    const slideDeck = event.data.slideDeck === undefined
      ? { success: true as const, data: undefined }
      : slideDeckSchema.safeParse(event.data.slideDeck);
    if (course.success && slideDeck.success) {
      onSnapshot({ course: course.data, ...(slideDeck.data ? { slideDeck: slideDeck.data } : null) });
    }
  };
  channel.addEventListener("message", listener);
  request();
  const retry = globalThis.setInterval(request, 500);
  return () => {
    globalThis.clearInterval(retry);
    channel.removeEventListener("message", listener);
    channel.close();
  };
}

import {
  ArrowCounterClockwise,
  CaretLeft,
  CaretRight,
  Pause,
  Play,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
} from "react";

import type { CourseDocument } from "../domain/course";
import type { SlideDeck } from "../domain/helper-contracts-schema";
import type { ArtifactClient } from "../services/artifact-client";
import {
  isNewerTeachingFrame,
  isSameTeachingFrame,
  type TeachingFrame,
} from "../domain/teaching";
import {
  defaultTeachingRuntime,
  flattenCourseLessons,
  isValidTeachingSessionId,
  mainApplicationUrl,
  type TeachingRuntime,
} from "./TeachingSetup";
import { SlideVisualGallery } from "./SlideVisualGallery";

const CONNECTION_STALE_AFTER_MS = 3_500;

type ConnectionState = "connecting" | "connected" | "reconnected" | "reconnecting";

const connectionLabel: Record<ConnectionState, string> = {
  connecting: "正在连接",
  connected: "已连接",
  reconnected: "已重新连接",
  reconnecting: "正在重连 · 保留最后画面",
};

const formatElapsed = (seconds: number): string => {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes.toString().padStart(2, "0")}:${remainder
    .toString()
    .padStart(2, "0")}`;
};

export interface PresenterViewProps {
  course: CourseDocument;
  sessionId: string;
  runtime?: TeachingRuntime;
  slideDeck?: SlideDeck;
  artifactClient?: ArtifactClient;
}

export function PresenterView({
  course,
  sessionId,
  runtime = defaultTeachingRuntime,
  slideDeck,
  artifactClient,
}: PresenterViewProps): JSX.Element {
  const lessons = useMemo(() => flattenCourseLessons(course), [course]);
  const validSession = isValidTeachingSessionId(sessionId);
  const fallbackFrame = useMemo<TeachingFrame | undefined>(() => {
    const first = lessons[0];
    if (!first || !validSession) return undefined;
    return {
      sessionId,
      courseId: course.id,
      lessonId: first.lesson.id,
      lessonIndex: 0,
      lessonCount: lessons.length,
      playing: false,
      elapsedSeconds: 0,
      sequence: 0,
      sentAt: new Date(runtime.now()).toISOString(),
    };
  }, [course.id, lessons, runtime, sessionId, validSession]);
  const [frame, setFrame] = useState<TeachingFrame | undefined>(fallbackFrame);
  const frameRef = useRef<TeachingFrame | undefined>(fallbackFrame);
  const sequenceRef = useRef(fallbackFrame?.sequence ?? 0);
  const hasAuthoritativeFrameRef = useRef(false);
  const [hasAuthoritativeFrame, setHasAuthoritativeFrame] = useState(false);
  const busRef = useRef<ReturnType<TeachingRuntime["createBus"]> | null>(null);
  const [connection, setConnection] =
    useState<ConnectionState>("connecting");
  const lastLiveSignalRef = useRef<number | undefined>(undefined);

  const acceptFrame = useCallback((candidate: TeachingFrame): boolean => {
    const current = frameRef.current;
    if (hasAuthoritativeFrameRef.current) {
      if (isSameTeachingFrame(candidate, current)) return true;
      if (!isNewerTeachingFrame(candidate, current)) return false;
    }
    hasAuthoritativeFrameRef.current = true;
    setHasAuthoritativeFrame(true);
    frameRef.current = candidate;
    sequenceRef.current = Math.max(sequenceRef.current, candidate.sequence);
    setFrame(candidate);
    return true;
  }, []);

  const publishUpdate = useCallback(
    (
      update: (
        current: TeachingFrame,
      ) => Partial<Pick<TeachingFrame, "lessonId" | "lessonIndex" | "playing" | "elapsedSeconds">>,
    ): void => {
      const current = frameRef.current;
      const bus = busRef.current;
      if (!current || !bus || !hasAuthoritativeFrameRef.current) return;
      const next: TeachingFrame = {
        ...current,
        ...update(current),
        lessonCount: lessons.length,
        sequence: Math.max(sequenceRef.current, current.sequence) + 1,
        sentAt: new Date(runtime.now()).toISOString(),
      };
      sequenceRef.current = next.sequence;
      frameRef.current = next;
      setFrame(next);
      bus.publish(next);
    },
    [lessons.length, runtime],
  );

  useEffect(() => {
    if (!validSession || lessons.length === 0) return;

    const bus = runtime.createBus(sessionId);
    busRef.current = bus;
    const recovery = bus.readLastFrame();
    if (
      recovery &&
      recovery.sessionId === sessionId &&
      recovery.courseId === course.id
    ) {
      frameRef.current = recovery;
      sequenceRef.current = recovery.sequence;
      hasAuthoritativeFrameRef.current = true;
      setHasAuthoritativeFrame(true);
      setFrame(recovery);
      setConnection("reconnecting");
    }

    const markLive = (): void => {
      lastLiveSignalRef.current = runtime.now();
      setConnection((current) =>
        current === "reconnecting" || current === "reconnected"
          ? "reconnected"
          : "connected",
      );
    };

    const unsubscribe = bus.subscribe((envelope) => {
      if (envelope.type === "frame") {
        if (
          envelope.frame.sessionId === sessionId &&
          envelope.frame.courseId === course.id
        ) {
          if (acceptFrame(envelope.frame)) {
            bus.acknowledge("presenter", envelope.frame.sequence);
          }
          markLive();
        }
        return;
      }
      if (
        (envelope.type === "presence" || envelope.type === "heartbeat") &&
        envelope.role === "controller"
      ) {
        markLive();
      }
    });

    bus.announce("presenter");
    bus.heartbeat("presenter");
    const heartbeat = globalThis.window.setInterval(() => {
      bus.heartbeat("presenter");
      const lastSignal = lastLiveSignalRef.current;
      if (
        lastSignal !== undefined &&
        runtime.now() - lastSignal > CONNECTION_STALE_AFTER_MS
      ) {
        setConnection("reconnecting");
      }
    }, 1_000);

    return () => {
      globalThis.window.clearInterval(heartbeat);
      unsubscribe();
      bus.close();
      if (busRef.current === bus) busRef.current = null;
    };
  }, [acceptFrame, course.id, lessons.length, runtime, sessionId, validSession]);

  useEffect(() => {
    if (!frame?.playing) return;
    const timer = globalThis.window.setInterval(() => {
      publishUpdate((current) => ({
        playing: true,
        elapsedSeconds: current.elapsedSeconds + 1,
      }));
    }, 1_000);
    return () => globalThis.window.clearInterval(timer);
  }, [frame?.playing, publishUpdate]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== "Escape" || frameRef.current?.playing !== true) return;
      publishUpdate(() => ({ playing: false }));
    };
    globalThis.window.addEventListener("keydown", onKeyDown);
    return () => globalThis.window.removeEventListener("keydown", onKeyDown);
  }, [publishUpdate]);

  if (!validSession || lessons.length === 0) {
    return (
      <main className="teaching-error-page">
        <section className="teaching-error-card" role="alert">
          <p className="eyebrow">讲师屏</p>
          <h1>无法进入讲师控制台</h1>
          <p>
            {!validSession
              ? "授课会话无效，请从课程工作台重新开始。"
              : "当前课程没有可控制的小节。"}
          </p>
          <a className="secondary-button" href={mainApplicationUrl()}>
            返回课程工作台
          </a>
        </section>
      </main>
    );
  }

  const current = frame
    ? lessons.find(({ lesson }) => lesson.id === frame.lessonId)
    : lessons[0];
  const currentIndex = current?.index ?? 0;
  const next = lessons[currentIndex + 1];

  const navigate = (offset: -1 | 1): void => {
    const targetIndex = Math.min(
      lessons.length - 1,
      Math.max(0, currentIndex + offset),
    );
    if (targetIndex === currentIndex) return;
    const target = lessons[targetIndex];
    publishUpdate(() => ({
      lessonId: target.lesson.id,
      lessonIndex: targetIndex,
      playing: false,
      elapsedSeconds: 0,
    }));
  };

  return (
    <main className="presenter-view">
      <header className="presenter-view__header">
        <div>
          <p className="presenter-view__role">讲师屏</p>
          <p className="presenter-view__course">{course.title}</p>
        </div>
        <span className={`projection-connection projection-connection--${connection}`} role="status">
          {connectionLabel[connection]}
        </span>
      </header>

      {current ? (
        <div className="presenter-grid">
          <section className="presenter-current" aria-labelledby="presenter-lesson-title">
            <div className="presenter-current__ordinal">
              <span>{current.chapter.title}</span>
              <strong>{currentIndex + 1} / {lessons.length}</strong>
            </div>
            <h1 id="presenter-lesson-title">{current.lesson.title}</h1>
            <p className="presenter-notes">讲师提示：{current.lesson.summary}</p>
            <SlideVisualGallery slideDeck={slideDeck} artifactClient={artifactClient} compact />
            <div className="presenter-next" aria-label="下一节预告">
              {next ? `下一节：${next.lesson.title}` : "已到课程末节"}
            </div>
          </section>

          <aside className="presenter-timer" aria-label="授课计时">
            <p>本节计时</p>
            <strong aria-live="off">{formatElapsed(frame?.elapsedSeconds ?? 0)}</strong>
            <span>预计 {current.lesson.durationMinutes} 分钟</span>
          </aside>

          <nav className="presenter-controls" aria-label="授课控制">
            <button
              type="button"
              aria-label="上一节"
              title="上一节"
              disabled={!hasAuthoritativeFrame || currentIndex === 0}
              onClick={() => navigate(-1)}
            >
              <CaretLeft aria-hidden="true" />
              <span>上一节</span>
            </button>
            <button
              type="button"
              aria-label={frame?.playing ? "暂停计时" : "开始计时"}
              title={frame?.playing ? "暂停计时" : "开始计时"}
              disabled={!hasAuthoritativeFrame}
              onClick={() =>
                publishUpdate((currentFrame) => ({
                  playing: !currentFrame.playing,
                }))
              }
            >
              {frame?.playing ? (
                <Pause aria-hidden="true" />
              ) : (
                <Play aria-hidden="true" />
              )}
              <span>{frame?.playing ? "暂停计时" : "开始计时"}</span>
            </button>
            <button
              type="button"
              aria-label="重置计时"
              title="重置计时"
              disabled={!hasAuthoritativeFrame}
              onClick={() => publishUpdate(() => ({ playing: false, elapsedSeconds: 0 }))}
            >
              <ArrowCounterClockwise aria-hidden="true" />
              <span>重置计时</span>
            </button>
            <button
              type="button"
              aria-label="下一节"
              title="下一节"
              disabled={!hasAuthoritativeFrame || currentIndex === lessons.length - 1}
              onClick={() => navigate(1)}
            >
              <span>下一节</span>
              <CaretRight aria-hidden="true" />
            </button>
          </nav>
        </div>
      ) : (
        <section className="presenter-current" role="alert">
          <h1>授课画面与当前课程不匹配</h1>
          <p>请返回课程工作台重新开始排练。</p>
        </section>
      )}
    </main>
  );
}

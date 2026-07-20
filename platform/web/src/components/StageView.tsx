import { useEffect, useMemo, useRef, useState, type JSX } from "react";

import type { CourseDocument } from "../domain/course";
import type { SlideDeck } from "../domain/helper-contracts-schema";
import type { ProjectionArtifactReader } from "../services/artifact-client";
import {
  useNativeProjectionCommit,
  type NativeProjectionRenderContext,
} from "../services/native-projection";
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

export interface StageViewProps {
  course: CourseDocument;
  sessionId: string;
  runtime?: TeachingRuntime;
  slideDeck?: SlideDeck;
  artifactClient?: ProjectionArtifactReader;
  nativeProjection?: NativeProjectionRenderContext;
}

export function StageView({
  course,
  sessionId,
  runtime = defaultTeachingRuntime,
  slideDeck,
  artifactClient,
  nativeProjection,
}: StageViewProps): JSX.Element {
  const lessons = useMemo(() => flattenCourseLessons(course), [course]);
  const validSession = isValidTeachingSessionId(sessionId);
  const [frame, setFrame] = useState<TeachingFrame>();
  const frameRef = useRef<TeachingFrame | undefined>(undefined);
  const [connection, setConnection] =
    useState<ConnectionState>("connecting");
  const lastLiveSignalRef = useRef<number | undefined>(undefined);
  const activeFrame = nativeProjection?.frame.teachingFrame ?? frame;
  useNativeProjectionCommit(nativeProjection?.adapter, nativeProjection?.frame);

  useEffect(() => {
    if (nativeProjection !== undefined || !validSession || lessons.length === 0) return;

    const bus = runtime.createBus(sessionId);
    const recovery = bus.readLastFrame();
    if (
      recovery &&
      recovery.sessionId === sessionId &&
      recovery.courseId === course.id
    ) {
      frameRef.current = recovery;
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
          const current = frameRef.current;
          const shouldAdopt = isNewerTeachingFrame(envelope.frame, current);
          const isExactReplay = isSameTeachingFrame(envelope.frame, current);
          if (shouldAdopt) {
            frameRef.current = envelope.frame;
            setFrame(envelope.frame);
          }
          if (shouldAdopt || isExactReplay) {
            bus.acknowledge("stage", envelope.frame.sequence);
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

    bus.announce("stage");
    bus.heartbeat("stage");
    const heartbeat = globalThis.window.setInterval(() => {
      bus.heartbeat("stage");
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
    };
  }, [course.id, lessons.length, nativeProjection?.adapter, runtime, sessionId, validSession]);

  if (!validSession || lessons.length === 0) {
    return (
      <main className="teaching-error-page">
        <section className="teaching-error-card" role="alert">
          <p className="eyebrow">学员屏</p>
          <h1>无法进入授课画面</h1>
          <p>
            {!validSession
              ? "授课会话无效，请从课程工作台重新开始。"
              : "当前课程没有可播放的小节。"}
          </p>
          <a className="secondary-button" href={mainApplicationUrl()}>
            返回课程工作台
          </a>
        </section>
      </main>
    );
  }

  const current = activeFrame
    ? lessons.find(({ lesson }) => lesson.id === activeFrame.lessonId)
    : undefined;

  return (
    <main className="stage-view">
      <header className="stage-view__header">
        <div>
          <p className="stage-view__role">学员屏</p>
          <p className="stage-view__course">{course.title}</p>
        </div>
        <span className={`projection-connection projection-connection--${nativeProjection ? "connected" : connection}`} role="status">
          {connectionLabel[nativeProjection ? "connected" : connection]}
        </span>
      </header>

      {!activeFrame ? (
        <section className="stage-view__waiting" aria-live="polite">
          <p className="eyebrow">授课会话已建立</p>
          <h1>等待讲师开始</h1>
          <p>讲师确认同步后，课程内容会显示在这里。</p>
        </section>
      ) : !current ? (
        <section className="stage-view__waiting" role="alert">
          <p className="eyebrow">画面恢复</p>
          <h1>授课画面与当前课程不匹配</h1>
          <p>已保留连接，请让讲师重新选择课程小节。</p>
        </section>
      ) : (
        <article className="stage-slide" aria-labelledby="stage-lesson-title">
          <p className="stage-slide__chapter">{current.chapter.title}</p>
          <h1 id="stage-lesson-title">{current.lesson.title}</h1>
          <p className="stage-slide__summary">{current.lesson.summary}</p>
          <SlideVisualGallery slideDeck={slideDeck} artifactClient={artifactClient} compact />

          <div className="stage-slide__meta">
            <span>预计 {current.lesson.durationMinutes} 分钟</span>
            <span>第 {current.index + 1} / {lessons.length} 节</span>
            <span>{formatElapsed(activeFrame.elapsedSeconds)}</span>
          </div>
          <progress
            aria-label="课程进度"
            max={lessons.length}
            value={current.index + 1}
          />
        </article>
      )}
    </main>
  );
}

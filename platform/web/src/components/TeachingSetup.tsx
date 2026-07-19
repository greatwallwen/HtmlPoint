import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type JSX,
} from "react";

import type { ChapterNode, CourseDocument, LessonNode } from "../domain/course";
import type { SlideDeck } from "../domain/helper-contracts-schema";
import { respondToProjectionRequests } from "../domain/projection-bus";
import {
  createTeachingBus,
  initialTeachingSetup,
  isNewerTeachingFrame,
  reduceTeachingSetup,
  type TeachingBus,
  type TeachingFrame,
} from "../domain/teaching";

export interface TeachingScreenDetails {
  screens: readonly unknown[];
}

export interface TeachingWindowHandle {
  readonly closed?: boolean;
  close?(): void;
  focus?(): void;
}

export interface TeachingRuntime {
  getScreenDetails?: () => Promise<TeachingScreenDetails>;
  open(
    url: string,
    target: "course-stage" | "course-presenter",
  ): TeachingWindowHandle | null;
  now(): number;
  createBus(sessionId: string): TeachingBus;
}

type WindowWithScreenDetails = Window & {
  getScreenDetails?: () => Promise<TeachingScreenDetails>;
};

const nativeScreenDetails = (globalThis.window as WindowWithScreenDetails)
  .getScreenDetails;

export const defaultTeachingRuntime: TeachingRuntime = {
  getScreenDetails: nativeScreenDetails?.bind(globalThis.window),
  open: (url, target) => globalThis.window.open(url, target),
  now: () => Date.now(),
  createBus: (sessionId) => createTeachingBus(sessionId),
};

export interface CourseLessonProjection {
  chapter: ChapterNode;
  lesson: LessonNode;
  index: number;
}

export const flattenCourseLessons = (
  course: CourseDocument,
): CourseLessonProjection[] => {
  const lessons: CourseLessonProjection[] = [];
  for (const chapter of course.chapters) {
    for (const lesson of chapter.lessons) {
      lessons.push({ chapter, lesson, index: lessons.length });
    }
  }
  return lessons;
};

export const teachingWindowUrl = (
  role: "stage" | "presenter",
  sessionId: string,
): string => {
  const url = new URL(globalThis.window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("view", role);
  url.searchParams.set("session", sessionId);
  return url.toString();
};

export const mainApplicationUrl = (): string => {
  const url = new URL(globalThis.window.location.href);
  url.search = "";
  url.hash = "";
  return url.toString();
};

export const isValidTeachingSessionId = (sessionId: string): boolean =>
  /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(sessionId);

const isOpenHandle = (
  handle: TeachingWindowHandle | null,
): handle is TeachingWindowHandle => handle !== null && handle.closed !== true;

const ROLE_STALE_AFTER_MS = 3_500;

const statusLabel = (
  status: ReturnType<typeof initialTeachingSetup>["status"],
  rehearsalMode: boolean,
  physicalDualScreenCertified: boolean,
): string => {
  switch (status) {
    case "idle":
      return "等待检查";
    case "checking":
      return "正在检查屏幕";
    case "permission-required":
      return "等待选择运行方式";
    case "opening":
      return "正在打开授课窗口";
    case "syncing":
      return "正在同步授课窗口";
    case "ready":
      if (rehearsalMode) return "排练已就绪";
      return physicalDualScreenCertified ? "物理双屏已就绪" : "窗口同步已就绪";
    case "presenting":
      return "授课进行中";
    case "error":
      return "需要处理后继续";
  }
};

export interface TeachingSetupProps {
  course: CourseDocument;
  selectedLessonId?: string;
  runtime?: TeachingRuntime;
  slideDeck?: SlideDeck;
  onReturnToEdit(): void;
}

export function TeachingSetup({
  course,
  selectedLessonId,
  runtime = defaultTeachingRuntime,
  slideDeck,
  onReturnToEdit,
}: TeachingSetupProps): JSX.Element {
  const initialState = useMemo(() => initialTeachingSetup(), []);
  const [setup, dispatch] = useReducer(reduceTeachingSetup, initialState);
  const lessons = useMemo(() => flattenCourseLessons(course), [course]);
  const busRef = useRef<TeachingBus | null>(null);
  const stageWindowRef = useRef<TeachingWindowHandle | null>(null);
  const presenterWindowRef = useRef<TeachingWindowHandle | null>(null);
  const controllerFrameRef = useRef<TeachingFrame | null>(null);
  const roleLastSeenRef = useRef<Partial<Record<"stage" | "presenter", number>>>(
    {},
  );

  useEffect(
    () => respondToProjectionRequests(setup.sessionId, { course, slideDeck }),
    [course, setup.sessionId, slideDeck],
  );

  const closeTeachingWindows = useCallback((): void => {
    const stageWindow = stageWindowRef.current;
    const presenterWindow = presenterWindowRef.current;
    stageWindowRef.current = null;
    presenterWindowRef.current = null;

    for (const teachingWindow of [stageWindow, presenterWindow]) {
      try {
        teachingWindow?.close?.();
      } catch {
        // A projection may already have been closed by the browser or user.
      }
    }
  }, []);

  useEffect(
    () => () => {
      closeTeachingWindows();
    },
    [closeTeachingWindows],
  );

  useEffect(() => {
    const bus = runtime.createBus(setup.sessionId);
    busRef.current = bus;
    roleLastSeenRef.current = {};
    const unsubscribe = bus.subscribe((envelope) => {
      if (
        envelope.type === "frame" &&
        envelope.frame.sessionId === setup.sessionId &&
        envelope.frame.courseId === course.id &&
        isNewerTeachingFrame(
          envelope.frame,
          controllerFrameRef.current ?? undefined,
        )
      ) {
        controllerFrameRef.current = envelope.frame;
      }
      if (
        (envelope.type === "presence" || envelope.type === "heartbeat") &&
        (envelope.role === "stage" || envelope.role === "presenter")
      ) {
        roleLastSeenRef.current[envelope.role] = runtime.now();
        dispatch({ type: "ROLE_CONNECTED", role: envelope.role });
      }
      if (envelope.type === "frame-ack") {
        dispatch({
          type: "FRAME_ACKNOWLEDGED",
          role: envelope.role,
          sequence: envelope.sequence,
        });
      }
    });
    bus.announce("controller");
    bus.heartbeat("controller");
    const heartbeat = globalThis.window.setInterval(() => {
      bus.heartbeat("controller");
      const now = runtime.now();
      for (const role of ["stage", "presenter"] as const) {
        const lastSeen = roleLastSeenRef.current[role];
        if (lastSeen !== undefined && now - lastSeen > ROLE_STALE_AFTER_MS) {
          dispatch({ type: "ROLE_DISCONNECTED", role });
        }
      }
    }, 1_000);

    return () => {
      globalThis.window.clearInterval(heartbeat);
      unsubscribe();
      bus.close();
      if (busRef.current === bus) busRef.current = null;
    };
  }, [course.id, runtime, setup.sessionId]);

  useEffect(() => {
    if (setup.status !== "opening") return;

    if (isOpenHandle(stageWindowRef.current)) {
      stageWindowRef.current.focus?.();
    } else {
      stageWindowRef.current = runtime.open(
        teachingWindowUrl("stage", setup.sessionId),
        "course-stage",
      );
    }

    if (isOpenHandle(presenterWindowRef.current)) {
      presenterWindowRef.current.focus?.();
    } else {
      presenterWindowRef.current = runtime.open(
        teachingWindowUrl("presenter", setup.sessionId),
        "course-presenter",
      );
    }

    dispatch({
      type: "WINDOWS_OPENED",
      stage: isOpenHandle(stageWindowRef.current),
      presenter: isOpenHandle(presenterWindowRef.current),
    });
  }, [runtime, setup.sessionId, setup.status]);

  useEffect(() => {
    if (
      setup.status !== "syncing" ||
      !setup.stageConnected ||
      !setup.presenterConnected
    ) {
      return;
    }

    const bus = busRef.current;
    if (!bus || lessons.length === 0) return;

    if (setup.frameSequence === undefined) {
      const selectedIndex = Math.max(
        lessons.findIndex(({ lesson }) => lesson.id === selectedLessonId),
        0,
      );
      const selected = lessons[selectedIndex];
      const recovery = bus.readLastFrame();
      const recoveredLessonIndex = recovery
        ? lessons.findIndex(({ lesson }) => lesson.id === recovery.lessonId)
        : -1;
      const reusableFrame =
        controllerFrameRef.current?.sessionId === setup.sessionId &&
        controllerFrameRef.current.courseId === course.id
          ? controllerFrameRef.current
          : recovery?.sessionId === setup.sessionId &&
              recovery.courseId === course.id &&
              recoveredLessonIndex >= 0
            ? {
                ...recovery,
                lessonIndex: recoveredLessonIndex,
                lessonCount: lessons.length,
              }
            : undefined;
      const frame: TeachingFrame =
        reusableFrame ?? {
          sessionId: setup.sessionId,
          courseId: course.id,
          lessonId: selected.lesson.id,
          lessonIndex: selectedIndex,
          lessonCount: lessons.length,
          playing: false,
          elapsedSeconds: 0,
          sequence: 0,
          sentAt: new Date(runtime.now()).toISOString(),
        };
      controllerFrameRef.current = frame;
      dispatch({ type: "FRAME_PUBLISHED", sequence: frame.sequence });
      bus.publish(frame);
      return;
    }

    if (setup.stageFrameAcknowledged && setup.presenterFrameAcknowledged) {
      dispatch({ type: "SYNC_CONFIRMED" });
    }
  }, [
    course.id,
    lessons,
    runtime,
    selectedLessonId,
    setup.presenterConnected,
    setup.presenterFrameAcknowledged,
    setup.sessionId,
    setup.stageConnected,
    setup.stageFrameAcknowledged,
    setup.status,
    setup.frameSequence,
  ]);

  const terminateSession = (): void => {
    closeTeachingWindows();
    controllerFrameRef.current = null;
    roleLastSeenRef.current = {};
    dispatch({ type: "SESSION_ENDED" });
  };

  const checkScreens = async (): Promise<void> => {
    dispatch({ type: "CHECK_STARTED" });
    if (!runtime.getScreenDetails) {
      dispatch({ type: "CAPABILITY_RESOLVED", screenDetails: false });
      return;
    }

    try {
      const details = await runtime.getScreenDetails();
      const hasTwoScreens = details.screens.filter(Boolean).length >= 2;
      dispatch({ type: "CAPABILITY_RESOLVED", screenDetails: hasTwoScreens });
      if (hasTwoScreens) dispatch({ type: "PERMISSION_GRANTED" });
    } catch {
      dispatch({ type: "CAPABILITY_RESOLVED", screenDetails: true });
      dispatch({ type: "PERMISSION_DENIED" });
    }
  };

  const retryWindows = (): void => {
    dispatch({ type: "CHECK_STARTED" });
    dispatch({
      type: "CAPABILITY_RESOLVED",
      screenDetails: setup.screenDetailsAvailable === true,
    });
    dispatch(
      setup.rehearsalMode
        ? { type: "REHEARSAL_ACCEPTED" }
        : { type: "PERMISSION_GRANTED" },
    );
  };

  if (lessons.length === 0) {
    return (
      <main className="teaching-error-page">
        <section className="teaching-error-card" role="alert">
          <p className="eyebrow">授课准备</p>
          <h1>课程还没有可授课小节</h1>
          <p>请返回课程工作台补充课程结构，再进入双屏授课。</p>
          <a className="secondary-button" href={mainApplicationUrl()}>
            返回课程工作台
          </a>
        </section>
      </main>
    );
  }

  return (
    <main className="teaching-setup-page">
      <section className="teaching-setup-panel" aria-labelledby="teaching-title">
        <div className="teaching-setup-heading">
          <div>
            <p className="eyebrow">授课准备</p>
            <h1 id="teaching-title">双屏授课</h1>
            <p className="teaching-course-summary">
              <span>{course.title}</span>
              <span>
                {course.chapters.length} 章 · {lessons.length} 节课
              </span>
            </p>
          </div>
          <span className={`teaching-status teaching-status--${setup.status}`} role="status">
            {statusLabel(
              setup.status,
              setup.rehearsalMode,
              setup.physicalDualScreenCertified,
            )}
          </span>
        </div>

        <div className="teaching-certification">
          <span>
            物理双屏认证：
            {setup.physicalDualScreenCertified ? "已认证" : "未认证"}
          </span>
          <span>
            学员屏 {setup.stageConnected ? "已连接" : "待连接"} · 讲师屏{" "}
            {setup.presenterConnected ? "已连接" : "待连接"}
          </span>
        </div>

        {(setup.rehearsalMode ||
          (setup.status === "permission-required" &&
            setup.screenDetailsAvailable === false)) && (
            <div className="teaching-notice">
              <strong>排练模式，未认证物理双屏</strong>
              <p>
                {setup.rehearsalMode
                  ? "当前会话始终按同屏排练处理，不会产生物理双屏认证。"
                  : "当前环境未检测到两个可用屏幕。确认后可在同一屏幕打开两个授课窗口。"}
              </p>
            </div>
          )}

        {setup.screenDetailsAvailable === true &&
          !setup.rehearsalMode &&
          !setup.physicalDualScreenCertified &&
          (setup.status === "opening" ||
            setup.status === "syncing" ||
            setup.status === "ready" ||
            setup.status === "presenting") && (
            <div className="teaching-notice">
              <strong>窗口同步不等于物理双屏认证</strong>
              <p>
                尚未验证窗口的屏幕分配与全屏状态。本会话只证明窗口通信已同步，不代表物理投屏已确认。
              </p>
            </div>
          )}

        {setup.status === "error" && setup.error && (
          <div className="teaching-error" role="alert">
            <strong>{setup.error.message}</strong>
            <p>会话 {setup.sessionId} 已保留，可处理后继续。</p>
          </div>
        )}

        <div className="teaching-actions">
          {setup.status === "idle" && (
            <button className="primary-button" type="button" onClick={checkScreens}>
              检查屏幕并开始
            </button>
          )}
          {setup.status === "permission-required" &&
            setup.screenDetailsAvailable === false && (
              <button
                className="primary-button"
                type="button"
                onClick={() => dispatch({ type: "REHEARSAL_ACCEPTED" })}
              >
                进入同屏排练
              </button>
            )}
          {setup.status === "error" && setup.error?.code === "permission-denied" && (
            <>
              <button className="primary-button" type="button" onClick={checkScreens}>
                重试屏幕检查
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => dispatch({ type: "REHEARSAL_ACCEPTED" })}
              >
                进入同屏排练
              </button>
            </>
          )}
          {setup.status === "error" && setup.error?.code === "popup-blocked" && (
            <button className="primary-button" type="button" onClick={retryWindows}>
              重试打开窗口
            </button>
          )}
          {setup.status === "error" && setup.error?.code === "sync-failed" && (
            <button
              className="primary-button"
              type="button"
              onClick={() => dispatch({ type: "REOPEN_WINDOWS_REQUESTED" })}
            >
              重新连接授课窗口
            </button>
          )}
          {setup.status === "ready" && (
            <button
              className="primary-button"
              type="button"
              onClick={() => dispatch({ type: "START_PRESENTING" })}
            >
              开始授课
            </button>
          )}
          {setup.status === "presenting" && (
            <>
              <button
                className="primary-button"
                type="button"
                onClick={terminateSession}
              >
                结束授课
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  terminateSession();
                  onReturnToEdit();
                }}
              >
                返回编辑验证
              </button>
            </>
          )}
        </div>
      </section>
    </main>
  );
}

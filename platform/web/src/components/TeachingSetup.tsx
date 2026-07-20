import {
  ArrowsLeftRight,
  ArrowClockwise,
  CheckCircle,
  Desktop,
  ShieldCheck,
  X,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type JSX,
} from "react";

import type { ChapterNode, CourseDocument, LessonNode } from "../domain/course";
import type { SlideDeck } from "../domain/helper-contracts-schema";
import {
  initialProjectionSetup,
  projectionErrorMessage,
  reduceProjectionSetup,
  type ProjectionErrorCode,
  type ProjectionIdentity,
  type ProjectionPendingCommand,
  type ProjectionSetupAction,
  type ProjectionStepState,
} from "../domain/projection";
import { respondToProjectionRequests } from "../domain/projection-bus";
import {
  createTeachingBus,
  initialTeachingSetup,
  isNewerTeachingFrame,
  reduceTeachingSetup,
  type TeachingBus,
  type TeachingFrame,
} from "../domain/teaching";
import {
  ProjectionClientError,
  type ProjectionClient,
} from "../services/projection-client";

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
  handle: TeachingWindowHandle | null | undefined,
): handle is TeachingWindowHandle => handle != null && handle.closed !== true;

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
  projectionClient?: ProjectionClient;
  projectionIdentity?: ProjectionIdentity;
  onReturnToEdit(): void;
}

export function TeachingSetup({
  projectionClient,
  projectionIdentity,
  ...browserProps
}: TeachingSetupProps): JSX.Element {
  const [browserFallback, setBrowserFallback] = useState(false);
  if (browserFallback) {
    return <BrowserTeachingSetup {...browserProps} startInRehearsal />;
  }
  if (projectionIdentity !== undefined) {
    const identityKey = [
      projectionIdentity.courseVersionId,
      projectionIdentity.slideDeckId,
      projectionIdentity.runtimeManifestId,
      projectionIdentity.runtimeManifestDigest,
    ].join("|");
    return (
      <NativeTeachingSetup
        key={identityKey}
        {...browserProps}
        client={projectionClient}
        identity={projectionIdentity}
        onBrowserFallback={() => setBrowserFallback(true)}
      />
    );
  }
  return <BrowserTeachingSetup {...browserProps} />;
}

interface NativeTeachingSetupProps {
  course: CourseDocument;
  client?: ProjectionClient;
  identity: ProjectionIdentity;
  onBrowserFallback(): void;
  onReturnToEdit(): void;
}

const createOpaqueUuid = (): string => {
  const generated = globalThis.crypto?.randomUUID?.();
  if (generated !== undefined) return generated;
  const suffix = Math.floor(Math.random() * 1_000_000_000_000)
    .toString()
    .padStart(12, "0");
  return `00000000-0000-4000-8000-${suffix}`;
};

const errorFromClient = (error: unknown): ProjectionErrorCode => {
  if (!(error instanceof ProjectionClientError)) return "command-failed";
  switch (error.code) {
    case "projection_unavailable":
    case "projection_timeout":
      return "host-unavailable";
    case "projection_content_unavailable":
      return "content-unavailable";
    case "invalid_response":
    case "command_replay_conflict":
      return "stale-response";
    case "projection_command_failed":
      return "command-failed";
  }
};

const stepLabel = (state: ProjectionStepState): string => {
  switch (state) {
    case "complete":
      return "已完成";
    case "active":
      return "进行中";
    case "attention":
      return "需确认";
    case "waiting":
      return "待开始";
  }
};

const bestEffortClose = async (
  client: ProjectionClient,
  sessionId: string,
  expectedGeneration: number,
): Promise<boolean> => {
  let generation = expectedGeneration;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const receipt = await client.close({
        commandId: createOpaqueUuid(),
        sessionId,
        expectedGeneration: generation,
      });
      if (receipt.accepted && receipt.status === "closed") return true;
      if (receipt.generation === generation) return false;
      generation = receipt.generation;
    } catch {
      return false;
    }
  }
  return false;
};

function NativeTeachingSetup({
  course,
  client,
  identity,
  onBrowserFallback,
  onReturnToEdit,
}: NativeTeachingSetupProps): JSX.Element {
  const initialState = useMemo(() => initialProjectionSetup(identity), [identity]);
  const [setup, reactDispatch] = useReducer(reduceProjectionSetup, initialState);
  const [closeQueued, setCloseQueued] = useState(false);
  const setupRef = useRef(setup);
  const sessionIdRef = useRef(createOpaqueUuid());
  const busyRef = useRef(false);
  const sessionOpenedRef = useRef(false);
  const mountedRef = useRef(true);
  const closeRequestedRef = useRef(false);
  const clientRef = useRef(client);
  clientRef.current = client;
  const lessons = useMemo(() => flattenCourseLessons(course), [course]);

  const dispatch = useCallback((action: ProjectionSetupAction): void => {
    setupRef.current = reduceProjectionSetup(setupRef.current, action);
    reactDispatch(action);
  }, []);

  const performClose = useCallback(
    async (sessionId: string, expectedGeneration: number): Promise<boolean> => {
      if (client === undefined) return false;
      closeRequestedRef.current = false;
      if (mountedRef.current) setCloseQueued(false);
      const pending: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "close_projection_session",
        sessionId,
        expectedGeneration,
      };
      if (mountedRef.current) {
        dispatch({ type: "COMMAND_STARTED", pending });
      }
      try {
        const receipt = await client.close({
          ...pending,
          sessionId,
        });
        const closed = receipt.accepted && receipt.status === "closed";
        if (closed) sessionOpenedRef.current = false;
        if (mountedRef.current) {
          dispatch({ type: "RECEIPT_RECEIVED", identity, receipt });
        }
        return closed;
      } catch (error) {
        if (mountedRef.current) {
          dispatch({ type: "COMMAND_FAILED", code: errorFromClient(error) });
        }
        return false;
      }
    },
    [client, dispatch, identity],
  );

  const issue = useCallback(
    async (
      pending: ProjectionPendingCommand,
      operation: () => Promise<import("../domain/projection-schema").ProjectionReceipt>,
      swap = false,
    ): Promise<boolean> => {
      dispatch({ type: "COMMAND_STARTED", pending, swap });
      try {
        const receipt = await operation();
        if (!mountedRef.current) {
          if (
            client !== undefined &&
            pending.sessionId !== null &&
            pending.command !== "close_projection_session"
          ) {
            await bestEffortClose(client, pending.sessionId, receipt.generation);
          }
          return false;
        }
        dispatch({ type: "RECEIPT_RECEIVED", identity, receipt });
        if (closeRequestedRef.current) {
          if (pending.sessionId === null) {
            closeRequestedRef.current = false;
            setCloseQueued(false);
            dispatch({ type: "RESET" });
          } else {
            await performClose(pending.sessionId, receipt.generation);
          }
          return false;
        }
        return receipt.accepted && !["error", "invalidated"].includes(setupRef.current.status);
      } catch (error) {
        if (
          !mountedRef.current &&
          client !== undefined &&
          pending.sessionId !== null &&
          pending.command !== "close_projection_session"
        ) {
          const likelyGeneration =
            pending.command === "assign_projection_window"
              ? pending.expectedGeneration + 1
              : setupRef.current.generation;
          await bestEffortClose(client, pending.sessionId, likelyGeneration);
          return false;
        }
        if (mountedRef.current && closeRequestedRef.current) {
          if (sessionOpenedRef.current) {
            await performClose(
              sessionIdRef.current,
              setupRef.current.generation,
            );
          } else {
            closeRequestedRef.current = false;
            setCloseQueued(false);
            dispatch({ type: "RESET" });
          }
          return false;
        }
        if (mountedRef.current) {
          dispatch({ type: "COMMAND_FAILED", code: errorFromClient(error) });
        }
        return false;
      }
    },
    [client, dispatch, identity, performClose],
  );

  const startNativeFlow = useCallback(async (): Promise<void> => {
    if (client === undefined || busyRef.current) return;
    busyRef.current = true;
    try {
      if (sessionOpenedRef.current) {
        const closed = await performClose(
          sessionIdRef.current,
          setupRef.current.generation,
        );
        if (!closed) return;
        sessionIdRef.current = createOpaqueUuid();
      }
      dispatch({ type: "RESET" });
      const detect: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "detect_displays",
        sessionId: null,
        expectedGeneration: 0,
      };
      if (!(await issue(detect, () => client.detect({
        commandId: detect.commandId,
        sessionId: null,
        expectedGeneration: detect.expectedGeneration,
      })))) return;

      const open: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "open_projection_session",
        sessionId: sessionIdRef.current,
        expectedGeneration: setupRef.current.generation,
      };
      if (!(await issue(open, () => client.open({
        ...open,
        sessionId: sessionIdRef.current,
        courseVersionId: identity.courseVersionId,
        slideDeckId: identity.slideDeckId,
        runtimeManifestId: identity.runtimeManifestId,
      }))) || !mountedRef.current) return;
      sessionOpenedRef.current = true;

      const assign: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "assign_projection_window",
        sessionId: sessionIdRef.current,
        expectedGeneration: setupRef.current.generation,
      };
      if (!(await issue(assign, () => client.assign({
        ...assign,
        sessionId: sessionIdRef.current,
        swap: false,
      })))) return;

      const fullscreen: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "enter_projection_fullscreen",
        sessionId: sessionIdRef.current,
        expectedGeneration: setupRef.current.generation,
      };
      await issue(fullscreen, () => client.fullscreen({
        ...fullscreen,
        sessionId: sessionIdRef.current,
      }));
    } finally {
      busyRef.current = false;
    }
  }, [client, dispatch, identity, issue, performClose]);

  const beginWitness = useCallback(async (): Promise<void> => {
    if (client === undefined || busyRef.current || !sessionOpenedRef.current) return;
    busyRef.current = true;
    try {
      const verify: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "verify_projection_assignment",
        sessionId: sessionIdRef.current,
        expectedGeneration: setupRef.current.generation,
      };
      await issue(verify, () => client.verify({
        ...verify,
        sessionId: sessionIdRef.current,
      }));
    } finally {
      busyRef.current = false;
    }
  }, [client, issue]);

  const swapScreens = useCallback(async (): Promise<void> => {
    if (client === undefined || busyRef.current || !sessionOpenedRef.current) return;
    busyRef.current = true;
    try {
      const assign: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "assign_projection_window",
        sessionId: sessionIdRef.current,
        expectedGeneration: setupRef.current.generation,
      };
      if (!(await issue(assign, () => client.assign({
        ...assign,
        sessionId: sessionIdRef.current,
        swap: true,
      }), true))) return;
      const fullscreen: ProjectionPendingCommand = {
        commandId: createOpaqueUuid(),
        command: "enter_projection_fullscreen",
        sessionId: sessionIdRef.current,
        expectedGeneration: setupRef.current.generation,
      };
      await issue(fullscreen, () => client.fullscreen({
        ...fullscreen,
        sessionId: sessionIdRef.current,
      }));
    } finally {
      busyRef.current = false;
    }
  }, [client, issue]);

  const closeNativeSession = useCallback(async (): Promise<boolean> => {
    if (client === undefined) return false;
    closeRequestedRef.current = true;
    setCloseQueued(true);
    if (busyRef.current) return false;
    if (!sessionOpenedRef.current) {
      closeRequestedRef.current = false;
      setCloseQueued(false);
      dispatch({ type: "RESET" });
      return true;
    }
    busyRef.current = true;
    try {
      return await performClose(
        sessionIdRef.current,
        setupRef.current.generation,
      );
    } finally {
      busyRef.current = false;
    }
  }, [client, dispatch, performClose]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const currentClient = clientRef.current;
      if (
        currentClient !== undefined &&
        sessionOpenedRef.current &&
        !busyRef.current
      ) {
        const sessionId = sessionIdRef.current;
        const expectedGeneration = setupRef.current.generation;
        void bestEffortClose(
          currentClient,
          sessionId,
          expectedGeneration,
        ).then((closed) => {
          if (closed) sessionOpenedRef.current = false;
        });
      }
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") void closeNativeSession();
    };
    globalThis.window.addEventListener("keydown", onKeyDown);
    return () => globalThis.window.removeEventListener("keydown", onKeyDown);
  }, [closeNativeSession]);

  const enterBrowserFallback = useCallback(async (): Promise<void> => {
    if (busyRef.current) return;
    if (sessionOpenedRef.current && !(await closeNativeSession())) return;
    onBrowserFallback();
  }, [closeNativeSession, onBrowserFallback]);

  const returnToEditSafely = useCallback(async (): Promise<void> => {
    if (busyRef.current) return;
    if (sessionOpenedRef.current && !(await closeNativeSession())) return;
    onReturnToEdit();
  }, [closeNativeSession, onReturnToEdit]);

  const unavailable = client === undefined;
  const isBusy = setup.pending !== undefined;
  const assignmentVisible = setup.steps.assign === "complete";
  const showWitness = ["witness-ready", "witness-pending", "certified"].includes(
    setup.status,
  );
  const errorMessage = unavailable
    ? projectionErrorMessage("runtime-unavailable")
    : projectionErrorMessage(setup.error);
  const steps = [
    ["detect", "检测屏幕", setup.steps.detect],
    ["assign", "分配窗口", setup.steps.assign],
    ["fullscreen", "进入全屏", setup.steps.fullscreen],
    ["witness", "现场确认", setup.steps.witness],
  ] as const;

  return (
    <main className="teaching-setup-page teaching-setup-page--native">
      <section className="teaching-setup-panel native-teaching-panel" aria-labelledby="native-teaching-title">
        <div className="teaching-setup-heading native-teaching-heading">
          <div>
            <p className="eyebrow">授课准备</p>
            <h1 id="native-teaching-title">双屏授课</h1>
            <p className="teaching-course-summary">
              <span>{course.title}</span>
              <span>{course.chapters.length} 章 · {lessons.length} 节课</span>
            </p>
          </div>
          <span className={`native-session-status native-session-status--${setup.status}`} role="status">
            {closeQueued
              ? "正在安全关闭"
              : setup.status === "certified"
              ? "双屏已就绪"
              : setup.status === "witness-pending"
                ? "等待现场确认"
                : setup.status === "witness-ready"
                  ? "窗口已就绪"
                  : setup.status === "running"
                    ? "正在准备"
                    : setup.status === "error" || setup.status === "invalidated"
                      ? "需要处理"
                      : "待开始"}
          </span>
        </div>

        <div className="native-certification-badges" aria-label="认证状态">
          <span className={setup.physicalDualScreenCertified ? "is-certified" : "is-pending"}>
            <ShieldCheck aria-hidden="true" size={18} />
            {setup.physicalDualScreenCertified ? "个人设备会话已认证" : "个人设备会话未认证"}
          </span>
          <span className="is-pending">
            <ShieldCheck aria-hidden="true" size={18} />
            发布签名未认证
          </span>
        </div>

        <ol className="native-progress" aria-label="双屏准备进度">
          {steps.map(([key, label, state]) => (
            <li key={key} className={`native-progress__item native-progress__item--${state}`}>
              <span className="native-progress__icon" aria-hidden="true">
                {state === "complete" ? <CheckCircle weight="fill" size={20} /> : <Desktop size={20} />}
              </span>
              <span>
                <strong>{label}</strong>
                <small>{stepLabel(state)}</small>
              </span>
            </li>
          ))}
        </ol>

        {assignmentVisible && (
          <div className="native-role-assignment" aria-label="屏幕分配">
            <span>{setup.assignment === "external-stage" ? "学员屏 · 外接屏" : "学员屏 · 主屏"}</span>
            <span>{setup.assignment === "external-stage" ? "讲师屏 · 主屏" : "讲师屏 · 外接屏"}</span>
          </div>
        )}

        {showWitness && (
          <div className="native-witness-notice" role="note">
            <strong>现场确认由本机窗口完成</strong>
            <p>请分别查看两个屏幕上的验证码，并在本机确认窗口中输入。</p>
            <p>认证只对当前个人设备会话有效；窗口、屏幕或连接变化后会立即失效。</p>
          </div>
        )}

        {errorMessage && (
          <div className="native-projection-error" role="alert">
            <strong>{errorMessage}</strong>
            <p>
              {unavailable
                ? "请从桌面应用启动课程，或先使用同屏排练。"
                : setup.error === "topology-ineligible"
                  ? "请使用本机扩展模式连接两块独立屏幕；复制模式、远程会话和单屏都不会认证。"
                  : "可以重试；已显示的内部状态不会作为认证证据。"}
            </p>
          </div>
        )}

        <div className="native-teaching-actions">
          {!unavailable && (setup.status === "idle" || setup.status === "closed") && (
            <button className="primary-button native-primary-action" type="button" disabled={isBusy} onClick={() => void startNativeFlow()}>
              <Desktop aria-hidden="true" size={20} />
              开始双屏授课
            </button>
          )}
          {!unavailable && setup.status === "witness-ready" && (
            <button className="primary-button native-primary-action" type="button" disabled={isBusy} onClick={() => void beginWitness()}>
              <ShieldCheck aria-hidden="true" size={20} />
              开始现场确认
            </button>
          )}
          {!unavailable && setup.status === "error" && (
            <button className="primary-button native-primary-action" type="button" disabled={isBusy} onClick={() => void startNativeFlow()}>
              <ArrowClockwise aria-hidden="true" size={20} />
              重试
            </button>
          )}
          {!unavailable && (setup.status === "invalidated" || setup.status === "witness-pending") && (
            <button className="primary-button native-primary-action" type="button" disabled={isBusy} onClick={() => void startNativeFlow()}>
              <ArrowClockwise aria-hidden="true" size={20} />
              重新见证
            </button>
          )}

          <div className="native-secondary-actions">
            {assignmentVisible && setup.status !== "closed" && (
              <button className="secondary-button" type="button" aria-label="交换屏幕" disabled={isBusy} onClick={() => void swapScreens()}>
                <ArrowsLeftRight aria-hidden="true" size={19} />
                交换屏幕
              </button>
            )}
            <button
              className="icon-button native-close-button"
              type="button"
              aria-label="关闭双屏会话"
              title="关闭双屏会话"
              disabled={
                unavailable ||
                ((!sessionOpenedRef.current && !isBusy) || setup.status === "closed")
              }
              onClick={() => void closeNativeSession()}
            >
              <X aria-hidden="true" size={20} />
            </button>
            <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void enterBrowserFallback()}>
              进入同屏排练
            </button>
            <button className="secondary-button" type="button" disabled={isBusy} onClick={() => void returnToEditSafely()}>
              返回编辑
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}

interface BrowserTeachingSetupProps
  extends Omit<TeachingSetupProps, "projectionClient" | "projectionIdentity"> {
  startInRehearsal?: boolean;
}

function BrowserTeachingSetup({
  course,
  selectedLessonId,
  runtime = defaultTeachingRuntime,
  slideDeck,
  onReturnToEdit,
  startInRehearsal = false,
}: BrowserTeachingSetupProps): JSX.Element {
  const initialState = useMemo(() => {
    const idle = initialTeachingSetup();
    if (!startInRehearsal) return idle;
    const checking = reduceTeachingSetup(idle, { type: "CHECK_STARTED" });
    const unavailable = reduceTeachingSetup(checking, {
      type: "CAPABILITY_RESOLVED",
      screenDetails: false,
    });
    return reduceTeachingSetup(unavailable, { type: "REHEARSAL_ACCEPTED" });
  }, [startInRehearsal]);
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

export type TeachingSetupStatus =
  | "idle"
  | "checking"
  | "permission-required"
  | "opening"
  | "syncing"
  | "ready"
  | "presenting"
  | "error";

export type TeachingRole = "controller" | "stage" | "presenter";
export type TeachingWindowRole = Exclude<TeachingRole, "controller">;

export interface TeachingSetupError {
  code: "permission-denied" | "popup-blocked" | "sync-failed" | "unknown";
  message: string;
}

export interface TeachingSetupState {
  status: TeachingSetupStatus;
  sessionId: string;
  screenDetailsAvailable?: boolean;
  rehearsalMode: boolean;
  stageOpen: boolean;
  presenterOpen: boolean;
  stageConnected: boolean;
  presenterConnected: boolean;
  frameSequence?: number;
  stageFrameAcknowledged: boolean;
  presenterFrameAcknowledged: boolean;
  syncConfirmed: boolean;
  physicalDualScreenCertified: boolean;
  error?: TeachingSetupError;
}

export type TeachingSetupAction =
  | { type: "CHECK_STARTED" }
  | { type: "CAPABILITY_RESOLVED"; screenDetails: boolean }
  | { type: "PERMISSION_GRANTED" }
  | { type: "PERMISSION_DENIED" }
  | { type: "REHEARSAL_ACCEPTED" }
  | { type: "WINDOWS_OPENED"; stage: boolean; presenter: boolean }
  | { type: "ROLE_CONNECTED"; role: TeachingWindowRole }
  | { type: "ROLE_DISCONNECTED"; role: TeachingWindowRole }
  | { type: "FRAME_PUBLISHED"; sequence: number }
  | {
      type: "FRAME_ACKNOWLEDGED";
      role: TeachingWindowRole;
      sequence: number;
    }
  | { type: "SYNC_CONFIRMED" }
  | { type: "REOPEN_WINDOWS_REQUESTED" }
  | { type: "SESSION_ENDED" }
  | { type: "START_PRESENTING" }
  | { type: "STOP_PRESENTING" }
  | { type: "PHYSICAL_ASSIGNMENT_CONFIRMED" };

const createSessionId = (): string => {
  const generated = globalThis.crypto?.randomUUID?.();
  return generated ?? `teaching-${Date.now()}-${Math.random().toString(36).slice(2)}`;
};

export const initialTeachingSetup = (
  sessionId: string = createSessionId(),
): TeachingSetupState => ({
  status: "idle",
  sessionId,
  rehearsalMode: false,
  stageOpen: false,
  presenterOpen: false,
  stageConnected: false,
  presenterConnected: false,
  stageFrameAcknowledged: false,
  presenterFrameAcknowledged: false,
  syncConfirmed: false,
  physicalDualScreenCertified: false,
});

const permissionDeniedError = (): TeachingSetupError => ({
  code: "permission-denied",
  message: "未授予多屏权限。请允许访问屏幕信息，或选择排练模式继续。",
});

const popupBlockedError = (): TeachingSetupError => ({
  code: "popup-blocked",
  message: "教学窗口未能全部打开。请允许本站弹出窗口后重试。",
});

const syncFailedError = (role: TeachingWindowRole): TeachingSetupError => ({
  code: "sync-failed",
  message:
    role === "stage"
      ? "学员屏连接已中断。请重新连接授课窗口后继续。"
      : "讲师屏连接已中断。请重新连接授课窗口后继续。",
});

export const reduceTeachingSetup = (
  state: TeachingSetupState,
  action: TeachingSetupAction,
): TeachingSetupState => {
  switch (action.type) {
    case "CHECK_STARTED":
      if (state.status !== "idle" && state.status !== "error") return state;
      return {
        ...state,
        status: "checking",
        error: undefined,
        syncConfirmed: false,
        frameSequence: undefined,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
        physicalDualScreenCertified: false,
      };

    case "CAPABILITY_RESOLVED":
      if (state.status !== "checking") return state;
      return {
        ...state,
        status: "permission-required",
        screenDetailsAvailable: action.screenDetails,
        physicalDualScreenCertified: false,
      };

    case "PERMISSION_GRANTED":
      if (
        state.status !== "permission-required" ||
        state.screenDetailsAvailable !== true
      ) {
        return state;
      }
      return {
        ...state,
        status: "opening",
        rehearsalMode: false,
        stageConnected: false,
        presenterConnected: false,
        frameSequence: undefined,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
        syncConfirmed: false,
        physicalDualScreenCertified: false,
        error: undefined,
      };

    case "PERMISSION_DENIED":
      if (state.status !== "permission-required") return state;
      return {
        ...state,
        status: "error",
        syncConfirmed: false,
        physicalDualScreenCertified: false,
        error: permissionDeniedError(),
      };

    case "REHEARSAL_ACCEPTED":
      if (state.status !== "permission-required" && state.status !== "error") {
        return state;
      }
      return {
        ...state,
        status: "opening",
        rehearsalMode: true,
        stageConnected: false,
        presenterConnected: false,
        frameSequence: undefined,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
        syncConfirmed: false,
        physicalDualScreenCertified: false,
        error: undefined,
      };

    case "WINDOWS_OPENED": {
      if (state.status !== "opening") return state;
      const next = {
        ...state,
        stageOpen: action.stage,
        presenterOpen: action.presenter,
        stageConnected: false,
        presenterConnected: false,
        frameSequence: undefined,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
        syncConfirmed: false,
        physicalDualScreenCertified: false,
      };
      if (!action.stage || !action.presenter) {
        return { ...next, status: "error", error: popupBlockedError() };
      }
      return { ...next, status: "syncing", error: undefined };
    }

    case "ROLE_CONNECTED":
      if (state.status !== "syncing") return state;
      if (action.role === "stage") {
        if (state.stageConnected) return state;
        return { ...state, stageConnected: true };
      }
      if (state.presenterConnected) return state;
      return { ...state, presenterConnected: true };

    case "ROLE_DISCONNECTED": {
      if (
        state.status !== "syncing" &&
        state.status !== "ready" &&
        state.status !== "presenting"
      ) {
        return state;
      }
      const isConnected =
        action.role === "stage" ? state.stageConnected : state.presenterConnected;
      if (!isConnected) return state;
      return {
        ...state,
        status: "error",
        stageOpen: action.role === "stage" ? false : state.stageOpen,
        presenterOpen:
          action.role === "presenter" ? false : state.presenterOpen,
        stageConnected:
          action.role === "stage" ? false : state.stageConnected,
        presenterConnected:
          action.role === "presenter" ? false : state.presenterConnected,
        frameSequence: undefined,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
        syncConfirmed: false,
        physicalDualScreenCertified: false,
        error: syncFailedError(action.role),
      };
    }

    case "FRAME_PUBLISHED":
      if (
        state.status !== "syncing" ||
        !state.stageConnected ||
        !state.presenterConnected ||
        !isNonNegativeInteger(action.sequence)
      ) {
        return state;
      }
      return {
        ...state,
        frameSequence: action.sequence,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
      };

    case "FRAME_ACKNOWLEDGED":
      if (
        state.status !== "syncing" ||
        state.frameSequence === undefined ||
        action.sequence !== state.frameSequence
      ) {
        return state;
      }
      if (action.role === "stage") {
        return state.stageFrameAcknowledged
          ? state
          : { ...state, stageFrameAcknowledged: true };
      }
      return state.presenterFrameAcknowledged
        ? state
        : { ...state, presenterFrameAcknowledged: true };

    case "SYNC_CONFIRMED":
      if (
        state.status !== "syncing" ||
        !state.stageOpen ||
        !state.presenterOpen ||
        !state.stageConnected ||
        !state.presenterConnected ||
        state.frameSequence === undefined ||
        !state.stageFrameAcknowledged ||
        !state.presenterFrameAcknowledged
      ) {
        return state;
      }
      return {
        ...state,
        status: "ready",
        syncConfirmed: true,
        physicalDualScreenCertified: false,
      };

    case "REOPEN_WINDOWS_REQUESTED":
      if (state.status !== "error" || state.error?.code !== "sync-failed") {
        return state;
      }
      return {
        ...state,
        status: "opening",
        stageConnected: false,
        presenterConnected: false,
        frameSequence: undefined,
        stageFrameAcknowledged: false,
        presenterFrameAcknowledged: false,
        syncConfirmed: false,
        physicalDualScreenCertified: false,
        error: undefined,
      };

    case "SESSION_ENDED":
      return initialTeachingSetup(state.sessionId);

    case "START_PRESENTING":
      return state.status === "ready"
        ? { ...state, status: "presenting" }
        : state;

    case "STOP_PRESENTING":
      return state.status === "presenting" ? { ...state, status: "ready" } : state;

    case "PHYSICAL_ASSIGNMENT_CONFIRMED":
      if (
        (state.status !== "ready" && state.status !== "presenting") ||
        state.rehearsalMode ||
        state.screenDetailsAvailable !== true ||
        !state.stageOpen ||
        !state.presenterOpen ||
        !state.stageConnected ||
        !state.presenterConnected ||
        !state.syncConfirmed ||
        state.physicalDualScreenCertified
      ) {
        return state;
      }
      return { ...state, physicalDualScreenCertified: true };
  }
};

export interface TeachingFrame {
  sessionId: string;
  courseId: string;
  lessonId: string;
  lessonIndex: number;
  lessonCount: number;
  playing: boolean;
  elapsedSeconds: number;
  sequence: number;
  sentAt: string;
}

export interface TeachingFrameEnvelope {
  type: "frame";
  sessionId: string;
  sentAt: string;
  frame: TeachingFrame;
}

export interface TeachingPresenceEnvelope {
  type: "presence";
  sessionId: string;
  role: TeachingRole;
  sentAt: string;
}

export interface TeachingHeartbeatEnvelope {
  type: "heartbeat";
  sessionId: string;
  role: TeachingRole;
  sentAt: string;
}

export interface TeachingFrameAcknowledgementEnvelope {
  type: "frame-ack";
  sessionId: string;
  role: TeachingWindowRole;
  sequence: number;
  sentAt: string;
}

export type TeachingBusEnvelope =
  | TeachingFrameEnvelope
  | TeachingFrameAcknowledgementEnvelope
  | TeachingPresenceEnvelope
  | TeachingHeartbeatEnvelope;

export interface TeachingChannelEvent {
  data: unknown;
}

export type TeachingChannelListener = (event: TeachingChannelEvent) => void;

export interface TeachingMessageChannel {
  postMessage(message: unknown): void;
  addEventListener(type: "message", listener: TeachingChannelListener): void;
  removeEventListener(type: "message", listener: TeachingChannelListener): void;
  close(): void;
}

export interface TeachingStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface TeachingBusHarness {
  createChannel(name: string): TeachingMessageChannel;
  storage: TeachingStorage;
  now(): string;
}

export type TeachingBusListener = (envelope: TeachingBusEnvelope) => void;

export interface TeachingBus {
  publish(frame: TeachingFrame): void;
  acknowledge(role: TeachingWindowRole, sequence: number): void;
  announce(role: TeachingRole): void;
  heartbeat(role: TeachingRole): void;
  subscribe(listener: TeachingBusListener): () => void;
  readLastFrame(): TeachingFrame | undefined;
  close(): void;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && value.trim().length > 0;

const isTimestamp = (value: unknown): value is string =>
  typeof value === "string" && Number.isFinite(Date.parse(value));

const isNonNegativeInteger = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value >= 0;

const isTeachingRole = (value: unknown): value is TeachingRole =>
  value === "controller" || value === "stage" || value === "presenter";

const isTeachingWindowRole = (value: unknown): value is TeachingWindowRole =>
  value === "stage" || value === "presenter";

const isTeachingFrame = (value: unknown): value is TeachingFrame => {
  if (!isRecord(value)) return false;
  return (
    isNonEmptyString(value.sessionId) &&
    isNonEmptyString(value.courseId) &&
    isNonEmptyString(value.lessonId) &&
    isNonNegativeInteger(value.lessonIndex) &&
    typeof value.lessonCount === "number" &&
    Number.isInteger(value.lessonCount) &&
    value.lessonCount > 0 &&
    value.lessonIndex < value.lessonCount &&
    typeof value.playing === "boolean" &&
    typeof value.elapsedSeconds === "number" &&
    Number.isFinite(value.elapsedSeconds) &&
    value.elapsedSeconds >= 0 &&
    isNonNegativeInteger(value.sequence) &&
    isTimestamp(value.sentAt)
  );
};

const parseEnvelope = (
  value: unknown,
  sessionId: string,
): TeachingBusEnvelope | undefined => {
  if (
    !isRecord(value) ||
    value.sessionId !== sessionId ||
    !isTimestamp(value.sentAt)
  ) {
    return undefined;
  }

  if (value.type === "frame") {
    if (
      !isTeachingFrame(value.frame) ||
      value.frame.sessionId !== sessionId ||
      value.frame.sentAt !== value.sentAt
    ) {
      return undefined;
    }
    return value as unknown as TeachingFrameEnvelope;
  }

  if (
    value.type === "frame-ack" &&
    isTeachingWindowRole(value.role) &&
    isNonNegativeInteger(value.sequence)
  ) {
    return value as unknown as TeachingFrameAcknowledgementEnvelope;
  }

  if (
    (value.type === "presence" || value.type === "heartbeat") &&
    isTeachingRole(value.role)
  ) {
    return value as unknown as TeachingPresenceEnvelope | TeachingHeartbeatEnvelope;
  }

  return undefined;
};

const defaultHarness = (): TeachingBusHarness => ({
  storage: globalThis.localStorage,
  now: () => new Date().toISOString(),
  createChannel: (name) => {
    const channel = new BroadcastChannel(name);
    const listeners = new Map<TeachingChannelListener, EventListener>();
    return {
      postMessage: (message) => channel.postMessage(message),
      addEventListener: (_type, listener) => {
        const nativeListener: EventListener = (event) =>
          listener({ data: (event as MessageEvent<unknown>).data });
        listeners.set(listener, nativeListener);
        channel.addEventListener("message", nativeListener);
      },
      removeEventListener: (_type, listener) => {
        const nativeListener = listeners.get(listener);
        if (!nativeListener) return;
        channel.removeEventListener("message", nativeListener);
        listeners.delete(listener);
      },
      close: () => {
        listeners.clear();
        channel.close();
      },
    };
  },
});

export const isNewerTeachingFrame = (
  candidate: TeachingFrame,
  current?: TeachingFrame,
): boolean => {
  if (!current) return true;
  if (candidate.sequence !== current.sequence) {
    return candidate.sequence > current.sequence;
  }
  return Date.parse(candidate.sentAt) > Date.parse(current.sentAt);
};

export const isSameTeachingFrame = (
  candidate: TeachingFrame,
  current?: TeachingFrame,
): boolean =>
  current !== undefined &&
  candidate.sessionId === current.sessionId &&
  candidate.courseId === current.courseId &&
  candidate.lessonId === current.lessonId &&
  candidate.lessonIndex === current.lessonIndex &&
  candidate.lessonCount === current.lessonCount &&
  candidate.playing === current.playing &&
  candidate.elapsedSeconds === current.elapsedSeconds &&
  candidate.sequence === current.sequence &&
  candidate.sentAt === current.sentAt;

export const createTeachingBus = (
  sessionId: string,
  harness: TeachingBusHarness = defaultHarness(),
): TeachingBus => {
  const channelName = `course-teaching:${sessionId}`;
  const recoveryKey = `course-teaching:last-frame:${sessionId}`;
  const channel = harness.createChannel(channelName);
  const listeners = new Set<TeachingBusListener>();
  let closed = false;

  const readStoredFrame = (): TeachingFrame | undefined => {
    if (closed) return undefined;
    try {
      const stored = harness.storage.getItem(recoveryKey);
      if (stored === null) return undefined;
      const parsed: unknown = JSON.parse(stored);
      if (!isTeachingFrame(parsed) || parsed.sessionId !== sessionId) {
        return undefined;
      }
      return parsed;
    } catch {
      return undefined;
    }
  };

  const deliverEnvelope: TeachingChannelListener = (event) => {
    if (closed) return;
    const envelope = parseEnvelope(event.data, sessionId);
    if (!envelope) return;
    for (const listener of [...listeners]) listener(envelope);
  };

  channel.addEventListener("message", deliverEnvelope);

  const send = (envelope: TeachingBusEnvelope) => {
    if (closed) return;
    try {
      channel.postMessage(envelope);
    } catch {
      // A channel can close externally between the guard and postMessage.
    }
  };

  const timestamp = () => {
    try {
      const value = harness.now();
      return isTimestamp(value) ? value : undefined;
    } catch {
      return undefined;
    }
  };

  return {
    publish(frame) {
      if (closed || !isTeachingFrame(frame) || frame.sessionId !== sessionId) return;
      if (isNewerTeachingFrame(frame, readStoredFrame())) {
        try {
          harness.storage.setItem(recoveryKey, JSON.stringify(frame));
        } catch {
          // Recovery is best-effort; live delivery remains authoritative.
        }
      }
      send({
        type: "frame",
        sessionId,
        sentAt: frame.sentAt,
        frame,
      });
    },

    acknowledge(role, sequence) {
      if (
        closed ||
        !isTeachingWindowRole(role) ||
        !isNonNegativeInteger(sequence)
      ) {
        return;
      }
      const sentAt = timestamp();
      if (!sentAt) return;
      send({
        type: "frame-ack",
        sessionId,
        role,
        sequence,
        sentAt,
      });
    },

    announce(role) {
      if (closed || !isTeachingRole(role)) return;
      const sentAt = timestamp();
      if (!sentAt) return;
      send({ type: "presence", sessionId, role, sentAt });
    },

    heartbeat(role) {
      if (closed || !isTeachingRole(role)) return;
      const sentAt = timestamp();
      if (!sentAt) return;
      send({ type: "heartbeat", sessionId, role, sentAt });
    },

    subscribe(listener) {
      if (closed) return () => undefined;
      listeners.add(listener);
      let subscribed = true;
      return () => {
        if (!subscribed) return;
        subscribed = false;
        listeners.delete(listener);
      };
    },

    readLastFrame: readStoredFrame,

    close() {
      if (closed) return;
      closed = true;
      listeners.clear();
      channel.removeEventListener("message", deliverEnvelope);
      try {
        channel.close();
      } catch {
        // Closing is idempotent from the bus consumer's perspective.
      }
    },
  };
};

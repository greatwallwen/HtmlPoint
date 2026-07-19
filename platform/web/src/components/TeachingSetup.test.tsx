import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CourseDocument } from "../domain/course";
import type {
  TeachingBus,
  TeachingBusEnvelope,
  TeachingBusListener,
  TeachingFrame,
  TeachingRole,
} from "../domain/teaching";
import { PresenterView } from "./PresenterView";
import { StageView } from "./StageView";
import { TeachingSetup, type TeachingRuntime } from "./TeachingSetup";

const COURSE: CourseDocument = {
  schemaVersion: 1,
  id: "course-rehearsal",
  title: "AI 协作实战",
  audience: "产品团队",
  goal: "建立可验证的 AI 工作流",
  durationMinutes: 45,
  chapters: [
    {
      id: "chapter-foundation",
      title: "协作基础",
      objective: "理解协作边界",
      lessons: [
        {
          id: "lesson-context",
          title: "建立上下文",
          summary: "先建立共享上下文，再开始协作。",
          durationMinutes: 10,
          sourceIds: [],
          status: "grounded",
        },
        {
          id: "lesson-evidence",
          title: "验证证据",
          summary: "用可复查证据确认每一步结果。",
          durationMinutes: 15,
          sourceIds: [],
          status: "grounded",
        },
      ],
    },
    {
      id: "chapter-practice",
      title: "协作演练",
      objective: "完成一次教学演练",
      lessons: [
        {
          id: "lesson-rehearsal",
          title: "同步演练",
          summary: "让讲师屏和学员屏保持同步。",
          durationMinutes: 20,
          sourceIds: [],
          status: "grounded",
        },
      ],
    },
  ],
  sources: [],
  updatedAt: "2026-07-16T00:00:00.000Z",
};

interface Client {
  listeners: Set<TeachingBusListener>;
  closed: boolean;
}

class InMemoryTeachingHub {
  readonly published: TeachingFrame[] = [];
  readonly acknowledgements: Array<{
    sessionId: string;
    role: "stage" | "presenter";
    sequence: number;
  }> = [];
  readonly announcements: Array<{ sessionId: string; role: TeachingRole }> = [];
  readonly heartbeats: Array<{ sessionId: string; role: TeachingRole }> = [];
  readonly clients = new Map<string, Set<Client>>();
  readonly recovery = new Map<string, TeachingFrame>();

  createBus = (sessionId: string): TeachingBus => {
    const client: Client = { listeners: new Set(), closed: false };
    const clients = this.clients.get(sessionId) ?? new Set<Client>();
    clients.add(client);
    this.clients.set(sessionId, clients);

    const emit = (envelope: TeachingBusEnvelope) => {
      for (const target of this.clients.get(sessionId) ?? []) {
        for (const listener of target.listeners) listener(envelope);
      }
    };

    return {
      publish: (frame) => {
        this.published.push(frame);
        this.recovery.set(sessionId, frame);
        emit({ type: "frame", sessionId, sentAt: frame.sentAt, frame });
      },
      acknowledge: (role, sequence) => {
        this.acknowledgements.push({ sessionId, role, sequence });
        emit({
          type: "frame-ack",
          sessionId,
          role,
          sequence,
          sentAt: "2026-07-16T00:00:00.000Z",
        });
      },
      announce: (role) => {
        this.announcements.push({ sessionId, role });
        this.emitPresence(sessionId, role);
      },
      heartbeat: (role) => {
        this.heartbeats.push({ sessionId, role });
        emit({
          type: "heartbeat",
          sessionId,
          role,
          sentAt: "2026-07-16T00:00:00.000Z",
        });
      },
      subscribe: (listener) => {
        client.listeners.add(listener);
        return () => client.listeners.delete(listener);
      },
      readLastFrame: () => this.recovery.get(sessionId),
      close: () => {
        client.closed = true;
        client.listeners.clear();
        clients.delete(client);
      },
    };
  };

  emitPresence(sessionId: string, role: TeachingRole): void {
    const envelope: TeachingBusEnvelope = {
      type: "presence",
      sessionId,
      role,
      sentAt: "2026-07-16T00:00:00.000Z",
    };
    for (const client of this.clients.get(sessionId) ?? []) {
      for (const listener of client.listeners) listener(envelope);
    }
  }

  emitHeartbeat(sessionId: string, role: TeachingRole): void {
    const envelope: TeachingBusEnvelope = {
      type: "heartbeat",
      sessionId,
      role,
      sentAt: "2026-07-16T00:00:00.000Z",
    };
    for (const client of this.clients.get(sessionId) ?? []) {
      for (const listener of client.listeners) listener(envelope);
    }
  }

  emitFrameAcknowledgement(
    sessionId: string,
    role: "stage" | "presenter",
    sequence: number,
  ): void {
    const envelope: TeachingBusEnvelope = {
      type: "frame-ack",
      sessionId,
      role,
      sequence,
      sentAt: "2026-07-16T00:00:00.000Z",
    };
    for (const client of this.clients.get(sessionId) ?? []) {
      for (const listener of client.listeners) listener(envelope);
    }
  }
}

const createRuntime = (
  hub: InMemoryTeachingHub,
  patch: Partial<TeachingRuntime> = {},
): TeachingRuntime => ({
  getScreenDetails: undefined,
  open: vi.fn(() => ({ closed: false, focus: vi.fn() })),
  now: () => Date.parse("2026-07-16T00:00:00.000Z"),
  createBus: hub.createBus,
  ...patch,
});

const makeFrame = (
  sessionId: string,
  patch: Partial<TeachingFrame> = {},
): TeachingFrame => ({
  sessionId,
  courseId: COURSE.id,
  lessonId: "lesson-context",
  lessonIndex: 0,
  lessonCount: 3,
  playing: false,
  elapsedSeconds: 0,
  sequence: 0,
  sentAt: "2026-07-16T00:00:00.000Z",
  ...patch,
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("TeachingSetup", () => {
  it("requires explicit rehearsal and both role presences before becoming ready", async () => {
    const user = userEvent.setup();
    const hub = new InMemoryTeachingHub();
    const stageHandle = {
      closed: false,
      focus: vi.fn(),
      close: vi.fn(),
    };
    const presenterHandle = {
      closed: false,
      focus: vi.fn(),
      close: vi.fn(),
    };
    const runtime = createRuntime(hub, {
      open: vi
        .fn<TeachingRuntime["open"]>()
        .mockReturnValueOnce(stageHandle)
        .mockReturnValueOnce(presenterHandle),
    });
    const onReturnToEdit = vi.fn();

    render(
      <TeachingSetup
        course={COURSE}
        selectedLessonId="lesson-evidence"
        runtime={runtime}
        onReturnToEdit={onReturnToEdit}
      />,
    );

    expect(screen.getByRole("heading", { name: "双屏授课" })).toBeVisible();
    expect(screen.getByText("2 章 · 3 节课")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "检查屏幕并开始" }));
    expect(
      await screen.findByText("排练模式，未认证物理双屏"),
    ).toBeVisible();
    expect(runtime.open).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "进入同屏排练" }));

    await waitFor(() => expect(runtime.open).toHaveBeenCalledTimes(2));
    const open = vi.mocked(runtime.open);
    expect(open.mock.calls.map(([, name]) => name)).toEqual([
      "course-stage",
      "course-presenter",
    ]);
    const stageUrl = new URL(open.mock.calls[0][0]);
    const presenterUrl = new URL(open.mock.calls[1][0]);
    expect(stageUrl.origin).toBe(window.location.origin);
    expect(stageUrl.searchParams.get("view")).toBe("stage");
    expect(presenterUrl.searchParams.get("view")).toBe("presenter");
    expect(presenterUrl.searchParams.get("session")).toBe(
      stageUrl.searchParams.get("session"),
    );
    expect(screen.getByText("正在同步授课窗口")).toBeVisible();

    const sessionId = stageUrl.searchParams.get("session")!;
    act(() => hub.emitPresence(sessionId, "stage"));
    expect(screen.queryByRole("button", { name: "开始授课" })).toBeNull();
    act(() => hub.emitPresence(sessionId, "presenter"));
    expect(screen.queryByRole("button", { name: "开始授课" })).toBeNull();
    await waitFor(() =>
      expect(hub.published.at(-1)).toMatchObject({ sequence: 0 }),
    );
    act(() => hub.emitFrameAcknowledgement(sessionId, "stage", 0));
    expect(screen.queryByRole("button", { name: "开始授课" })).toBeNull();
    act(() => hub.emitFrameAcknowledgement(sessionId, "presenter", 0));

    expect(
      await screen.findByRole("button", { name: "开始授课" }),
    ).toBeEnabled();
    expect(screen.getByText("排练已就绪")).toBeVisible();
    expect(screen.getByText("排练模式，未认证物理双屏")).toBeVisible();
    expect(screen.getByText("物理双屏认证：未认证")).toBeVisible();
    expect(screen.queryByText("物理双屏认证：已认证")).toBeNull();
    expect(hub.published.at(-1)).toMatchObject({
      sessionId,
      courseId: COURSE.id,
      lessonId: "lesson-evidence",
      lessonIndex: 1,
      lessonCount: 3,
      sequence: 0,
      playing: false,
      elapsedSeconds: 0,
    });

    await user.click(screen.getByRole("button", { name: "开始授课" }));
    expect(screen.getByText("授课进行中")).toBeVisible();
    expect(screen.getByText("排练模式，未认证物理双屏")).toBeVisible();
    expect(screen.getByRole("button", { name: "结束授课" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "返回编辑验证" }));
    expect(onReturnToEdit).toHaveBeenCalledTimes(1);
    expect(stageHandle.close).toHaveBeenCalledTimes(1);
    expect(presenterHandle.close).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "检查屏幕并开始" })).toBeEnabled();
  });

  it("ends the session by closing both projection windows and returning to idle", async () => {
    const user = userEvent.setup();
    const hub = new InMemoryTeachingHub();
    const stageHandle = { closed: false, close: vi.fn() };
    const presenterHandle = { closed: false, close: vi.fn() };
    const open = vi
      .fn<TeachingRuntime["open"]>()
      .mockReturnValueOnce(stageHandle)
      .mockReturnValueOnce(presenterHandle);
    const runtime = createRuntime(hub, { open });

    render(
      <TeachingSetup
        course={COURSE}
        runtime={runtime}
        onReturnToEdit={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "检查屏幕并开始" }));
    await user.click(
      await screen.findByRole("button", { name: "进入同屏排练" }),
    );
    await waitFor(() => expect(open).toHaveBeenCalledTimes(2));
    const sessionId = new URL(open.mock.calls[0][0]).searchParams.get("session")!;
    act(() => {
      hub.emitPresence(sessionId, "stage");
      hub.emitPresence(sessionId, "presenter");
    });
    await waitFor(() => expect(hub.published).toHaveLength(1));
    act(() => {
      hub.emitFrameAcknowledgement(sessionId, "stage", 0);
      hub.emitFrameAcknowledgement(sessionId, "presenter", 0);
    });
    await user.click(await screen.findByRole("button", { name: "开始授课" }));
    await user.click(screen.getByRole("button", { name: "结束授课" }));

    expect(stageHandle.close).toHaveBeenCalledTimes(1);
    expect(presenterHandle.close).toHaveBeenCalledTimes(1);
    expect(screen.getByText("等待检查")).toBeVisible();
    expect(screen.getByRole("button", { name: "检查屏幕并开始" })).toBeEnabled();
  });

  it("closes any opened projection windows when the controller unmounts", async () => {
    const user = userEvent.setup();
    const hub = new InMemoryTeachingHub();
    const stageHandle = { closed: false, close: vi.fn() };
    const presenterHandle = { closed: false, close: vi.fn() };
    const open = vi
      .fn<TeachingRuntime["open"]>()
      .mockReturnValueOnce(stageHandle)
      .mockReturnValueOnce(presenterHandle);
    const { unmount } = render(
      <TeachingSetup
        course={COURSE}
        runtime={createRuntime(hub, { open })}
        onReturnToEdit={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "检查屏幕并开始" }));
    await user.click(
      await screen.findByRole("button", { name: "进入同屏排练" }),
    );
    await waitFor(() => expect(open).toHaveBeenCalledTimes(2));
    unmount();

    expect(stageHandle.close).toHaveBeenCalledTimes(1);
    expect(presenterHandle.close).toHaveBeenCalledTimes(1);
  });

  it("retains the session and reopens only a popup-blocked presenter window", async () => {
    const user = userEvent.setup();
    const hub = new InMemoryTeachingHub();
    const stageHandle = { closed: false, focus: vi.fn() };
    const presenterHandle = { closed: false, focus: vi.fn() };
    const open = vi
      .fn<TeachingRuntime["open"]>()
      .mockReturnValueOnce(stageHandle)
      .mockReturnValueOnce(null)
      .mockReturnValueOnce(presenterHandle);
    const runtime = createRuntime(hub, { open });

    render(
      <TeachingSetup
        course={COURSE}
        selectedLessonId="lesson-context"
        runtime={runtime}
        onReturnToEdit={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "检查屏幕并开始" }));
    await user.click(
      await screen.findByRole("button", { name: "进入同屏排练" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "教学窗口未能全部打开",
    );
    expect(open).toHaveBeenCalledTimes(2);
    const firstSession = new URL(open.mock.calls[0][0]).searchParams.get(
      "session",
    );

    await user.click(screen.getByRole("button", { name: "重试打开窗口" }));

    await waitFor(() => expect(open).toHaveBeenCalledTimes(3));
    expect(open.mock.calls.map(([, name]) => name)).toEqual([
      "course-stage",
      "course-presenter",
      "course-presenter",
    ]);
    expect(new URL(open.mock.calls[2][0]).searchParams.get("session")).toBe(
      firstSession,
    );
    expect(stageHandle.focus).toHaveBeenCalled();
    expect(screen.getByText("正在同步授课窗口")).toBeVisible();
  });

  it("offers permission retry and rehearsal after Screen Details rejection", async () => {
    const user = userEvent.setup();
    const hub = new InMemoryTeachingHub();
    const getScreenDetails = vi
      .fn<NonNullable<TeachingRuntime["getScreenDetails"]>>()
      .mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"))
      .mockResolvedValueOnce({ screens: [{}, {}] });
    const runtime = createRuntime(hub, { getScreenDetails });

    render(
      <TeachingSetup
        course={COURSE}
        runtime={runtime}
        onReturnToEdit={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "检查屏幕并开始" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "未授予多屏权限",
    );
    expect(screen.getByRole("button", { name: "重试屏幕检查" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "进入同屏排练" })).toBeEnabled();
    expect(runtime.open).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "重试屏幕检查" }));
    await waitFor(() => expect(runtime.open).toHaveBeenCalledTimes(2));
    expect(getScreenDetails).toHaveBeenCalledTimes(2);
    expect(screen.getByText("正在同步授课窗口")).toBeVisible();
    expect(screen.getByText("物理双屏认证：未认证")).toBeVisible();

    const open = vi.mocked(runtime.open);
    const sessionId = new URL(open.mock.calls[0][0]).searchParams.get("session")!;
    act(() => {
      hub.emitPresence(sessionId, "stage");
      hub.emitPresence(sessionId, "presenter");
    });
    await waitFor(() => expect(hub.published).toHaveLength(1));
    act(() => {
      hub.emitFrameAcknowledgement(sessionId, "stage", 0);
      hub.emitFrameAcknowledgement(sessionId, "presenter", 0);
    });
    expect(await screen.findByText("窗口同步已就绪")).toBeVisible();
    expect(screen.getByText("窗口同步不等于物理双屏认证")).toBeVisible();
    expect(screen.queryByText("物理双屏已就绪")).toBeNull();
  });

  it("falls back to syncing on stale heartbeats and becomes ready after both roles recover", async () => {
    vi.useFakeTimers();
    let now = Date.parse("2026-07-16T00:00:00.000Z");
    const hub = new InMemoryTeachingHub();
    const runtime = createRuntime(hub, { now: () => now });

    render(
      <TeachingSetup
        course={COURSE}
        runtime={runtime}
        onReturnToEdit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "检查屏幕并开始" }));
    fireEvent.click(screen.getByRole("button", { name: "进入同屏排练" }));
    await act(async () => Promise.resolve());
    const open = vi.mocked(runtime.open);
    const sessionId = new URL(open.mock.calls[0][0]).searchParams.get("session")!;

    act(() => {
      hub.emitPresence(sessionId, "stage");
      hub.emitPresence(sessionId, "presenter");
    });
    await act(async () => Promise.resolve());
    expect(hub.published).toHaveLength(1);
    act(() => {
      hub.emitFrameAcknowledgement(sessionId, "stage", 0);
      hub.emitFrameAcknowledgement(sessionId, "presenter", 0);
    });
    expect(screen.getByText("排练已就绪")).toBeVisible();

    const presenterBus = hub.createBus(sessionId);
    act(() => {
      presenterBus.publish(
        makeFrame(sessionId, {
          lessonId: "lesson-evidence",
          lessonIndex: 1,
          sequence: 4,
          sentAt: "2026-07-16T00:00:04.000Z",
        }),
      );
    });
    presenterBus.close();
    expect(hub.published.at(-1)).toMatchObject({
      lessonId: "lesson-evidence",
      sequence: 4,
    });

    now += 5_000;
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(screen.getByRole("alert")).toHaveTextContent("学员屏连接已中断");
    expect(
      screen.getByRole("button", { name: "重新连接授课窗口" }),
    ).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "重新连接授课窗口" }));
    await act(async () => Promise.resolve());
    expect(screen.getByText("正在同步授课窗口")).toBeVisible();

    act(() => {
      hub.emitHeartbeat(sessionId, "stage");
      hub.emitHeartbeat(sessionId, "presenter");
    });
    await act(async () => Promise.resolve());
    expect(hub.published.at(-1)).toMatchObject({
      lessonId: "lesson-evidence",
      lessonIndex: 1,
      sequence: 4,
    });
    act(() => {
      hub.emitFrameAcknowledgement(sessionId, "stage", 4);
      hub.emitFrameAcknowledgement(sessionId, "presenter", 4);
    });
    expect(screen.getByText("排练已就绪")).toBeVisible();
    expect(new URL(open.mock.calls[0][0]).searchParams.get("session")).toBe(
      sessionId,
    );

    vi.useRealTimers();
  });
});

describe("StageView", () => {
  it("renders the recovery frame and updates from a published navigation frame", async () => {
    const sessionId = "session-stage-recovery";
    const hub = new InMemoryTeachingHub();
    hub.recovery.set(
      sessionId,
      makeFrame(sessionId, { elapsedSeconds: 12, playing: true, sequence: 4 }),
    );
    const runtime = createRuntime(hub);

    render(<StageView course={COURSE} sessionId={sessionId} runtime={runtime} />);

    expect(screen.getByText(COURSE.title)).toBeVisible();
    expect(screen.getByText("协作基础")).toBeVisible();
    expect(screen.getByRole("heading", { name: "建立上下文" })).toBeVisible();
    expect(screen.getByText("先建立共享上下文，再开始协作。")).toBeVisible();
    expect(screen.getByText("预计 10 分钟")).toBeVisible();
    expect(screen.getByText("第 1 / 3 节")).toBeVisible();
    expect(screen.getByText("00:12")).toBeVisible();
    expect(hub.announcements).toContainEqual({ sessionId, role: "stage" });

    const replayBus = hub.createBus(sessionId);
    act(() => {
      replayBus.publish(
        makeFrame(sessionId, { elapsedSeconds: 12, playing: true, sequence: 4 }),
      );
    });
    expect(hub.acknowledgements).toContainEqual({
      sessionId,
      role: "stage",
      sequence: 4,
    });
    const acknowledgementCount = hub.acknowledgements.length;
    act(() => {
      replayBus.publish(
        makeFrame(sessionId, {
          lessonId: "lesson-rehearsal",
          lessonIndex: 2,
          sequence: 3,
          sentAt: "2026-07-16T00:00:01.000Z",
        }),
      );
    });
    replayBus.close();
    expect(hub.acknowledgements).toHaveLength(acknowledgementCount);
    expect(screen.getByRole("heading", { name: "建立上下文" })).toBeVisible();

    act(() => {
      hub.createBus(sessionId).publish(
        makeFrame(sessionId, {
          lessonId: "lesson-evidence",
          lessonIndex: 1,
          elapsedSeconds: 0,
          playing: false,
          sequence: 5,
          sentAt: "2026-07-16T00:00:01.000Z",
        }),
      );
    });

    expect(
      await screen.findByRole("heading", { name: "验证证据" }),
    ).toBeVisible();
    expect(screen.getByText("用可复查证据确认每一步结果。")).toBeVisible();
    expect(screen.getByText("第 2 / 3 节")).toBeVisible();
    expect(hub.acknowledgements).toContainEqual({
      sessionId,
      role: "stage",
      sequence: 5,
    });
  });

  it("shows a calm wait state when no frame has been published", () => {
    const sessionId = "session-stage-empty";
    const hub = new InMemoryTeachingHub();

    render(
      <StageView
        course={COURSE}
        sessionId={sessionId}
        runtime={createRuntime(hub)}
      />,
    );

    expect(screen.getByRole("heading", { name: "等待讲师开始" })).toBeVisible();
    expect(screen.queryByRole("progressbar", { name: "载入授课画面" })).toBeNull();
  });
});

describe("PresenterView", () => {
  it("accepts the controller initial frame even when the presenter clock is ahead", async () => {
    const sessionId = "session-presenter-clock-skew";
    const hub = new InMemoryTeachingHub();
    const runtime = createRuntime(hub, {
      now: () => Date.parse("2026-07-16T00:00:10.000Z"),
    });

    render(
      <PresenterView
        course={COURSE}
        sessionId={sessionId}
        runtime={runtime}
      />,
    );
    expect(screen.getByRole("heading", { name: "建立上下文" })).toBeVisible();
    expect(screen.getAllByRole("button")).toHaveLength(4);
    for (const control of screen.getAllByRole("button")) {
      expect(control).toBeDisabled();
    }

    fireEvent.click(screen.getByRole("button", { name: "下一节" }));
    expect(hub.published).toHaveLength(0);

    act(() => {
      hub.createBus(sessionId).publish(
        makeFrame(sessionId, {
          lessonId: "lesson-evidence",
          lessonIndex: 1,
          sequence: 0,
          sentAt: "2026-07-16T00:00:00.000Z",
        }),
      );
    });

    expect(
      await screen.findByRole("heading", { name: "验证证据" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "上一节" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "下一节" })).toBeEnabled();
    expect(hub.acknowledgements).toContainEqual({
      sessionId,
      role: "presenter",
      sequence: 0,
    });
  });

  it("publishes monotonic bounded navigation frames and shows notes plus the next lesson", async () => {
    const user = userEvent.setup();
    const sessionId = "session-presenter-navigation";
    const hub = new InMemoryTeachingHub();
    hub.recovery.set(sessionId, makeFrame(sessionId, { sequence: 10 }));
    const runtime = createRuntime(hub);

    render(
      <PresenterView
        course={COURSE}
        sessionId={sessionId}
        runtime={runtime}
      />,
    );

    expect(screen.getByRole("heading", { name: "建立上下文" })).toBeVisible();
    expect(screen.getByText("讲师提示：先建立共享上下文，再开始协作。")).toBeVisible();
    expect(screen.getByText("下一节：验证证据")).toBeVisible();
    expect(screen.getByText("1 / 3")).toBeVisible();
    expect(screen.getByRole("button", { name: "上一节" })).toBeDisabled();
    expect(hub.announcements).toContainEqual({ sessionId, role: "presenter" });

    const staleBus = hub.createBus(sessionId);
    act(() => {
      staleBus.publish(
        makeFrame(sessionId, {
          lessonId: "lesson-rehearsal",
          lessonIndex: 2,
          sequence: 9,
          sentAt: "2026-07-16T00:00:01.000Z",
        }),
      );
    });
    staleBus.close();
    expect(hub.acknowledgements).not.toContainEqual({
      sessionId,
      role: "presenter",
      sequence: 9,
    });
    expect(screen.getByRole("heading", { name: "建立上下文" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "下一节" }));
    expect(screen.getByRole("heading", { name: "验证证据" })).toBeVisible();
    expect(hub.published.at(-1)).toMatchObject({
      lessonId: "lesson-evidence",
      lessonIndex: 1,
      sequence: 11,
      elapsedSeconds: 0,
      playing: false,
    });

    await user.click(screen.getByRole("button", { name: "下一节" }));
    expect(screen.getByRole("heading", { name: "同步演练" })).toBeVisible();
    expect(screen.getByText("已到课程末节")).toBeVisible();
    expect(screen.getByRole("button", { name: "下一节" })).toBeDisabled();
    expect(hub.published.at(-1)?.sequence).toBe(12);

    await user.click(screen.getByRole("button", { name: "上一节" }));
    expect(screen.getByRole("heading", { name: "验证证据" })).toBeVisible();
    expect(hub.published.at(-1)).toMatchObject({
      lessonId: "lesson-evidence",
      lessonIndex: 1,
      sequence: 13,
    });
  });

  it("publishes timer ticks, pauses on Escape, resets, and cleans up deterministically", async () => {
    vi.useFakeTimers();
    const sessionId = "session-presenter-timer";
    const hub = new InMemoryTeachingHub();
    hub.recovery.set(
      sessionId,
      makeFrame(sessionId, {
        lessonId: "lesson-evidence",
        lessonIndex: 1,
        sequence: 20,
      }),
    );
    const runtime = createRuntime(hub);
    const { unmount } = render(
      <PresenterView
        course={COURSE}
        sessionId={sessionId}
        runtime={runtime}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "开始计时" }));
    expect(hub.published.at(-1)).toMatchObject({
      playing: true,
      elapsedSeconds: 0,
      sequence: 21,
    });
    expect(screen.getByRole("button", { name: "暂停计时" })).toBeVisible();

    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(screen.getByText("00:02")).toBeVisible();
    expect(hub.published.slice(-2).map(({ elapsedSeconds, sequence }) => ({
      elapsedSeconds,
      sequence,
    }))).toEqual([
      { elapsedSeconds: 1, sequence: 22 },
      { elapsedSeconds: 2, sequence: 23 },
    ]);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "开始计时" })).toBeVisible();
    expect(hub.published.at(-1)).toMatchObject({
      playing: false,
      elapsedSeconds: 2,
      sequence: 24,
    });

    fireEvent.click(screen.getByRole("button", { name: "重置计时" }));
    expect(screen.getByText("00:00")).toBeVisible();
    expect(hub.published.at(-1)).toMatchObject({
      playing: false,
      elapsedSeconds: 0,
      sequence: 25,
    });

    const countBeforeUnmount = hub.published.length;
    unmount();
    await act(async () => vi.advanceTimersByTimeAsync(3_000));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(hub.published).toHaveLength(countBeforeUnmount);
    expect(hub.clients.get(sessionId)?.size ?? 0).toBe(0);
  });
});

describe("teaching projection recovery", () => {
  it("keeps healthy controller heartbeats labeled as connected", () => {
    const sessionId = "session-healthy-heartbeats";
    const hub = new InMemoryTeachingHub();
    const runtime = createRuntime(hub);

    render(
      <>
        <StageView course={COURSE} sessionId={sessionId} runtime={runtime} />
        <PresenterView course={COURSE} sessionId={sessionId} runtime={runtime} />
      </>,
    );

    act(() => hub.emitPresence(sessionId, "controller"));
    expect(screen.getAllByText("已连接")).toHaveLength(2);

    act(() => hub.emitHeartbeat(sessionId, "controller"));
    expect(screen.getAllByText("已连接")).toHaveLength(2);
    expect(screen.queryByText("已重新连接")).toBeNull();
  });

  it("keeps the last frame while stage and presenter reconnect", async () => {
    vi.useFakeTimers();
    let now = Date.parse("2026-07-16T00:00:00.000Z");
    const sessionId = "session-projection-recovery";
    const hub = new InMemoryTeachingHub();
    hub.recovery.set(
      sessionId,
      makeFrame(sessionId, {
        lessonId: "lesson-evidence",
        lessonIndex: 1,
        sequence: 9,
      }),
    );
    const runtime = createRuntime(hub, { now: () => now });

    render(
      <>
        <StageView course={COURSE} sessionId={sessionId} runtime={runtime} />
        <PresenterView course={COURSE} sessionId={sessionId} runtime={runtime} />
      </>,
    );

    expect(screen.getAllByText("正在重连 · 保留最后画面")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "验证证据" })).toHaveLength(2);

    act(() => hub.emitHeartbeat(sessionId, "controller"));
    expect(screen.getAllByText("已重新连接")).toHaveLength(2);

    now += 5_000;
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(screen.getAllByText("正在重连 · 保留最后画面")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { name: "验证证据" })).toHaveLength(2);

    act(() => hub.emitHeartbeat(sessionId, "controller"));
    expect(screen.getAllByText("已重新连接")).toHaveLength(2);
  });

  it("cleans subscriptions and heartbeat timers across StrictMode effect replay", async () => {
    vi.useFakeTimers();
    const sessionId = "session-strict-mode";
    const hub = new InMemoryTeachingHub();
    const runtime = createRuntime(hub);

    const { unmount } = render(
      <StrictMode>
        <TeachingSetup
          course={COURSE}
          selectedLessonId="lesson-context"
          runtime={runtime}
          onReturnToEdit={vi.fn()}
        />
        <StageView course={COURSE} sessionId={sessionId} runtime={runtime} />
        <PresenterView course={COURSE} sessionId={sessionId} runtime={runtime} />
      </StrictMode>,
    );

    const activeClients = () =>
      [...hub.clients.values()].reduce((total, clients) => total + clients.size, 0);

    expect(activeClients()).toBe(3);
    expect(hub.announcements.filter(({ role }) => role === "controller")).toHaveLength(2);
    expect(hub.announcements.filter(({ role }) => role === "stage")).toHaveLength(2);
    expect(hub.announcements.filter(({ role }) => role === "presenter")).toHaveLength(2);

    unmount();
    expect(activeClients()).toBe(0);
    const heartbeatCount = hub.heartbeats.length;
    await act(async () => vi.advanceTimersByTimeAsync(3_000));
    expect(hub.heartbeats).toHaveLength(heartbeatCount);
  });
});

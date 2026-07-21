// Vitest executes in Node, while the browser-only tsconfig intentionally omits Node types.
// @ts-expect-error -- node:fs is available to the test runner at runtime.
import { readFileSync } from "node:fs";

import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EvidenceReceipt, SourceAsset } from "../domain/course";
import { LocalCourseAgent } from "../domain/course-agent";
import type { TeachingBus, TeachingFrame } from "../domain/teaching";
import type { TeachingRuntime } from "../components/TeachingSetup";
import {
  WORKSPACE_STORAGE_KEY,
  type WorkspaceSnapshot,
} from "../state/storage";
import {
  createFreshWorkspaceState,
  type WorkspaceState,
} from "../state/workspace";
import {
  HELPER_SESSION_STORAGE_KEY,
  resetHelperSessionBootstrapForTests,
} from "../services/helper-session";
import { App } from "./App";

class TestStorage implements Pick<Storage, "getItem" | "setItem"> {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  snapshot(): WorkspaceSnapshot | undefined {
    const value = this.values.get(WORKSPACE_STORAGE_KEY);
    return value === undefined
      ? undefined
      : (JSON.parse(value) as WorkspaceSnapshot);
  }
}

function textFile(contents: string, name: string): File {
  const file = new File([contents], name);
  Object.defineProperty(file, "text", {
    value: () => Promise.resolve(contents),
  });
  return file;
}

function pendingTextFile(contents: string, name: string): {
  file: File;
  resolve(): void;
} {
  let resolveText!: (value: string) => void;
  const promise = new Promise<string>((resolve) => {
    resolveText = resolve;
  });
  const file = new File([contents], name);
  Object.defineProperty(file, "text", { value: () => promise });
  return { file, resolve: () => resolveText(contents) };
}

function failedTextFile(name: string): File {
  const file = new File(["broken"], name);
  Object.defineProperty(file, "text", {
    value: () => Promise.reject(new Error("read failed")),
  });
  return file;
}

function retryableTextFile(name: string, recoveredText: string): {
  file: File;
  readonly attempts: number;
} {
  let attempts = 0;
  const file = new File(["broken"], name);
  Object.defineProperty(file, "text", {
    value: () => {
      attempts += 1;
      return attempts === 1
        ? Promise.reject(new Error("read failed"))
        : Promise.resolve(recoveredText);
    },
  });
  return {
    file,
    get attempts() {
      return attempts;
    },
  };
}

function readySource(name = "guide.md"): SourceAsset {
  return {
    id: `source-${name}`,
    name,
    kind: "markdown",
    size: 24,
    status: "ready",
    extractedText: "# 可验证课程资料",
    addedAt: "2026-07-15T00:00:00.000Z",
  };
}

function generationState(patch: Partial<WorkspaceState> = {}): WorkspaceState {
  const fresh = createFreshWorkspaceState();
  return {
    ...fresh,
    step: "generate",
    course: { ...fresh.course, sources: [readySource()] },
    ...patch,
  };
}

function validatedEditState(
  level: "pass" | "warning" | "error",
): WorkspaceState {
  const fresh = createFreshWorkspaceState();
  const source = readySource("validation.md");
  const course = {
    ...fresh.course,
    id: "course-validation",
    title: "验证门课程",
    audience: "业务团队",
    goal: "验证后进入排练",
    durationMinutes: 30,
    sources: [source],
    chapters: [
      {
        id: "chapter-validation",
        title: "验证章节",
        objective: "验证教学门",
        lessons: [
          {
            id: "lesson-validation",
            title: "验证小节",
            summary: "使用证据验证课程。",
            durationMinutes: 30,
            sourceIds: [source.id],
            status: "grounded" as const,
          },
        ],
      },
    ],
  };
  const validationReceipt: EvidenceReceipt = {
    id: `receipt-${level}`,
    courseId: course.id,
    kind: "validation",
    createdAt: "2026-07-15T00:00:00.000Z",
    inputDigest: "abcdef1234567890",
    summary: `课程校验完成：${level === "error" ? 1 : 0} 个错误，${level === "warning" ? 1 : 0} 个警告。`,
    checks: [{ id: `check-${level}`, level, message: `${level} finding` }],
  };
  return {
    ...fresh,
    step: "edit",
    course,
    brief: {
      title: course.title,
      audience: course.audience,
      goal: course.goal,
      durationMinutes: course.durationMinutes,
    },
    receipts: [validationReceipt],
    selectedChapterId: "chapter-validation",
    selectedLessonId: "lesson-validation",
    generation: "success",
    validation: "success",
    validationWarningsAcknowledged: false,
  };
}

function validatedTeachState(): WorkspaceState {
  return { ...validatedEditState("pass"), step: "teach" };
}

function projectionRuntime(frame?: TeachingFrame): TeachingRuntime {
  const bus: TeachingBus = {
    publish: vi.fn(),
    acknowledge: vi.fn(),
    announce: vi.fn(),
    heartbeat: vi.fn(),
    subscribe: () => () => undefined,
    readLastFrame: () => frame,
    close: vi.fn(),
  };
  return {
    getScreenDetails: undefined,
    open: vi.fn(() => ({ closed: false, focus: vi.fn() })),
    now: () => Date.parse("2026-07-16T00:00:00.000Z"),
    createBus: () => bus,
  };
}

async function uploadReadySource(
  user: ReturnType<typeof userEvent.setup>,
  name = "guide.md",
): Promise<void> {
  await user.upload(
    screen.getByLabelText("导入资料", { selector: 'input[type="file"]' }),
    textFile("# 为什么现在需要 AI", name),
  );
  await screen.findByText(name);
}

function expectIconButton(button: HTMLElement, name: string): void {
  expect(button).toHaveAttribute("aria-label", name);
  expect(button).toHaveAttribute("title", name);
  expect(button.querySelector("svg")).not.toBeNull();
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

beforeEach(() => {
  resetHelperSessionBootstrapForTests();
  document.documentElement.lang = "zh-CN";
  window.history.replaceState(null, "", "/");
  window.sessionStorage.clear();
  window.sessionStorage.setItem(
    HELPER_SESSION_STORAGE_KEY,
    JSON.stringify({
      helperOrigin: "http://127.0.0.1:8765",
      sessionToken: "t".repeat(43),
    }),
  );
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  resetHelperSessionBootstrapForTests();
  window.sessionStorage.clear();
  window.localStorage.clear();
  document.documentElement.removeAttribute("lang");
  window.history.replaceState(null, "", "/");
});

describe("课程工作台", () => {
  it("does not expose template generation without a verified Helper", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    window.sessionStorage.clear();

    render(<App storage={new TestStorage()} />);

    expect(
      await screen.findByRole("heading", { name: "请从课程工作台启动" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "生成课程结构" }),
    ).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders the ordered workflow header and accessible icon control", () => {
    render(<App storage={new TestStorage()} />);

    const workflow = screen.getByRole("navigation", { name: "课程工作流" });
    const steps = within(workflow).getAllByRole("button");
    expect(steps.map((button) => button.getAttribute("aria-label"))).toEqual([
      "导入资料",
      "生成课程",
      "编辑验证",
      "双屏授课",
    ]);
    expect(steps[0]).toHaveAttribute("aria-current", "step");
    expect(steps.slice(1).every((button) => !button.hasAttribute("aria-current"))).toBe(
      true,
    );
    expect(steps[2]).toBeDisabled();
    expect(steps[3]).toBeDisabled();

    const newCourse = screen.getByRole("button", { name: "新建课程" });
    expectIconButton(newCourse, "新建课程");
    expect(document.documentElement).toHaveAttribute("lang", "zh-CN");
  });

  it.each([
    ["generate", () => generationState()],
    ["edit", () => validatedEditState("pass")],
    ["teach", () => validatedTeachState()],
  ])("bootstraps the helper session from the %s step", async (_step, state) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState(
      null,
      "",
      `/#helper=${encodeURIComponent("http://127.0.0.1:8765")}&nonce=${"n".repeat(43)}`,
    );

    render(<App storage={new TestStorage()} initialState={state()} />);

    expect(window.location.hash).toBe("");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/v1/session/exchange",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.queryByRole("region", { name: "知识准备" })).toBeNull();
  });

  it("reuses the scrubbed helper session when navigation returns to import", async () => {
    const user = userEvent.setup();
    const helperOrigin = "http://127.0.0.1:8765";
    const nonce = "n".repeat(43);
    const token = "t".repeat(43);
    window.history.replaceState(
      null,
      "",
      `/#helper=${encodeURIComponent(helperOrigin)}&nonce=${nonce}`,
    );
    const fetchMock = vi.fn((url: string) => {
      if (url === `${helperOrigin}/v1/session/exchange`) {
        return Promise.resolve(jsonResponse({ sessionToken: token }));
      }
      return Promise.resolve(
        jsonResponse({
          schemaVersion: 1,
          sourceCount: 2,
          publishedCardCount: 4,
          reviewTaskCount: 0,
          retrievalMode: "hybrid",
          tagLabels: ["课程设计"],
          updatedAt: "2026-07-17T02:00:00Z",
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <App storage={new TestStorage()} initialState={generationState()} />,
    );

    expect(window.location.hash).toBe("");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(
          ([url]) => url === `${helperOrigin}/v1/session/exchange`,
        ),
      ).toHaveLength(1),
    );

    await user.click(screen.getByRole("button", { name: "导入资料" }));

    expect(await screen.findByText("4 张已发布知识卡")).toBeVisible();
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => url === `${helperOrigin}/v1/session/exchange`,
      ),
    ).toHaveLength(1);
  });

  it("exchanges launch material once under React StrictMode and injects the verified client", async () => {
    const helperOrigin = "http://127.0.0.1:8765";
    const nonce = "n".repeat(43);
    const token = "t".repeat(43);
    window.history.replaceState(
      null,
      "",
      `/#helper=${encodeURIComponent(helperOrigin)}&nonce=${nonce}`,
    );
    const fetchMock = vi.fn((url: string) => {
      if (url === `${helperOrigin}/v1/session/exchange`) {
        return Promise.resolve(jsonResponse({ sessionToken: token }));
      }
      if (url === `${helperOrigin}/v1/knowledge/summary`) {
        return Promise.resolve(
          jsonResponse({
            schemaVersion: 1,
            sourceCount: 2,
            publishedCardCount: 4,
            reviewTaskCount: 0,
            retrievalMode: "hybrid",
            tagLabels: ["课程设计"],
            updatedAt: "2026-07-17T02:00:00Z",
          }),
        );
      }
      return Promise.resolve(jsonResponse({}, 404));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <StrictMode>
        <App storage={new TestStorage()} />
      </StrictMode>,
    );

    expect(window.location.hash).toBe("");
    expect(await screen.findByText("4 张已发布知识卡")).toBeVisible();
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => url === `${helperOrigin}/v1/session/exchange`,
      ),
    ).toHaveLength(1);
    expect(window.localStorage).toHaveLength(0);
    expect(window.location.href).not.toContain(nonce);
    expect(window.location.href).not.toContain(token);
  });

  it("reopens the exact published course projection after a workspace reload", async () => {
    const helperOrigin = "http://127.0.0.1:8765";
    const nonce = "n".repeat(43);
    const token = "t".repeat(43);
    const digest = "a".repeat(64);
    const createdAt = "2026-07-19T06:00:00Z";
    const actor = { actorType: "human", actorId: "local-user", displayName: null };
    const courseVersionId = "course-published-1";
    const slideDeckId = "deck-published-1";
    const runtimeManifestId = "runtime-published-1";
    const projection = {
      schemaVersion: 1,
      courseVersionId,
      courseDigest: digest,
      usageScope: "internal",
      status: "published",
      requirement: { schemaVersion: 1, requirementId: "requirement-1", title: "重开后的课程", audience: "产品团队", learningGoals: ["验证固定投影"], durationMinutes: 45, requiredTagIds: [], excludedTagIds: [], usageScope: "internal" },
      outline: {
        schemaVersion: 1, logicalId: "outline", versionId: "outline-v1", revision: 1, contentDigest: "b".repeat(64), supersedesVersionId: null, createdAt, createdBy: actor, requirementId: "requirement-1", uncoveredGoals: [], retrievalEvidenceId: "retrieval-evidence-1", indexSnapshotId: "snapshot-1",
        chapters: [{ schemaVersion: 1, chapterId: "chapter-reopened", title: "恢复章节", objective: "验证重开", placements: [{ schemaVersion: 1, placementId: "placement-1", cardVersionId: "card-v1", chapterId: "chapter-reopened", lessonId: "lesson-1", purpose: "core", allocatedMinutes: 45 }] }],
      },
      slideDeck: {
        schemaVersion: 1, logicalId: "deck", versionId: slideDeckId, revision: 1, contentDigest: "c".repeat(64), supersedesVersionId: null, createdAt, createdBy: actor, courseVersionId,
        nodes: [{ schemaVersion: 1, nodeId: "slide-1", nodeType: "slide", text: "恢复课程", items: [], placementIds: ["placement-1"], cardVersionIds: ["card-v1"], chunkIds: [], sourceVersionIds: [], evidenceIds: ["evidence-1"], presenterNotes: "讲师提示", assetBindings: [], children: [] }],
      },
      runtimeManifest: {
        schemaVersion: 1, logicalId: "runtime", versionId: runtimeManifestId, revision: 1, contentDigest: "d".repeat(64), supersedesVersionId: null, createdAt, createdBy: actor, courseVersionId, slideDeckVersionId: slideDeckId, slideDeckDigest: "c".repeat(64), jobBindings: [], artifactIds: [], evidenceIds: ["evidence-1"],
      },
    };
    window.history.replaceState(null, "", `/#helper=${encodeURIComponent(helperOrigin)}&nonce=${nonce}`);
    const projectionPath = `${helperOrigin}/v1/courses/${courseVersionId}/projection?slideDeckId=${slideDeckId}&runtimeManifestId=${runtimeManifestId}`;
    const fetchMock = vi.fn((url: string) => Promise.resolve(
      url === `${helperOrigin}/v1/session/exchange`
        ? jsonResponse({ sessionToken: token })
        : url === projectionPath
          ? jsonResponse(projection)
          : jsonResponse({}, 404),
    ));
    vi.stubGlobal("fetch", fetchMock);
    const fresh = createFreshWorkspaceState();
    render(<App storage={new TestStorage()} initialState={{
      ...fresh,
      step: "edit",
      governed: { courseVersionId, slideDeckId, runtimeManifestId, cardVersionIds: ["card-v1"], visualPlacementIds: [] },
    }} />);

    expect((await screen.findAllByText("恢复章节")).length).toBeGreaterThanOrEqual(1);
    expect(fetchMock).toHaveBeenCalledWith(projectionPath, expect.objectContaining({ method: "GET" }));
    expect(screen.getByRole("button", { name: "双屏授课" })).toBeEnabled();
  });

  it("shows pending import state, renders a ready source, clears the input, and removes it", async () => {
    const user = userEvent.setup();
    render(<App storage={new TestStorage()} agent={new LocalCourseAgent()} />);
    const input = screen.getByLabelText("导入资料", {
      selector: 'input[type="file"]',
    }) as HTMLInputElement;
    const pending = pendingTextFile("# source", "course.md");

    fireEvent.change(input, { target: { files: [pending.file] } });

    expect(screen.getByText("正在读取资料…")).toBeVisible();
    pending.resolve();

    const source = await screen.findByRole("listitem", {
      name: "资料 course.md",
    });
    expect(within(source).getByText(/Markdown/)).toBeVisible();
    expect(within(source).getByText("可用")).toBeVisible();
    expect(within(source).getByText(/\d+ B/)).toBeVisible();
    expect(screen.getByRole("button", { name: "下一步：生成课程" })).toBeEnabled();
    expect(input.value).toBe("");

    const remove = within(source).getByRole("button", { name: "移除 course.md" });
    expectIconButton(remove, "移除 course.md");
    await user.click(remove);

    expect(screen.queryByText("course.md")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一步：生成课程" })).toBeDisabled();
  });

  it("keeps the reading status visible until all overlapping imports settle", async () => {
    render(<App storage={new TestStorage()} agent={new LocalCourseAgent()} />);
    const input = screen.getByLabelText("导入资料", {
      selector: 'input[type="file"]',
    });
    const first = pendingTextFile("# first", "first.md");
    const second = pendingTextFile("# second", "second.md");

    fireEvent.change(input, { target: { files: [first.file] } });
    fireEvent.change(input, { target: { files: [second.file] } });

    expect(screen.getByText("正在读取资料…")).toBeVisible();

    await act(async () => {
      first.resolve();
      await Promise.resolve();
    });
    expect(await screen.findByText("first.md")).toBeVisible();
    expect(screen.getByText("正在读取资料…")).toBeVisible();
    expect(screen.queryByText("second.md")).not.toBeInTheDocument();

    await act(async () => {
      second.resolve();
      await Promise.resolve();
    });
    expect(await screen.findByText("second.md")).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByText("正在读取资料…")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("first.md")).toBeVisible();
  });

  it("keeps unsupported and failed sources visible without blocking a ready sibling", async () => {
    render(<App storage={new TestStorage()} />);
    const dropTarget = screen.getByLabelText("资料拖放区");

    fireEvent.drop(dropTarget, {
      dataTransfer: {
        files: [
          new File(["zip"], "archive.zip"),
          failedTextFile("broken.md"),
          textFile("usable", "usable.txt"),
        ],
      },
    });

    const unsupported = await screen.findByRole("listitem", {
      name: "资料 archive.zip",
    });
    const failed = screen.getByRole("listitem", { name: "资料 broken.md" });
    const ready = screen.getByRole("listitem", { name: "资料 usable.txt" });
    expect(within(unsupported).getByText("暂不支持")).toBeVisible();
    expect(within(failed).getByText("读取失败")).toBeVisible();
    expect(within(ready).getByText("可用")).toBeVisible();
    expect(screen.getByRole("button", { name: "下一步：生成课程" })).toBeEnabled();
  });

  it("shows a read failure reason and retries the same file safely", async () => {
    const user = userEvent.setup();
    const retryable = retryableTextFile("retry.md", "# recovered source");
    const storage = new TestStorage();
    render(<App storage={storage} />);

    await user.upload(
      screen.getByLabelText("导入资料", { selector: 'input[type="file"]' }),
      retryable.file,
    );

    const failed = await screen.findByRole("listitem", {
      name: "资料 retry.md",
    });
    expect(within(failed).getByText("读取失败")).toBeVisible();
    expect(within(failed).getByText("无法读取文件：read failed")).toBeVisible();
    expect(retryable.attempts).toBe(1);
    await waitFor(() =>
      expect(storage.snapshot()?.legacyUnlinked).toMatchObject({
        status: "legacy-unlinked",
        sourceCount: 1,
      }),
    );

    await user.click(
      within(failed).getByRole("button", { name: "重试读取 retry.md" }),
    );

    await waitFor(() => expect(retryable.attempts).toBe(2));
    const recovered = await screen.findByRole("listitem", {
      name: "资料 retry.md",
    });
    expect(within(recovered).getByText("可用")).toBeVisible();
    expect(within(recovered).queryByText("无法读取文件：read failed")).toBeNull();
    expect(
      within(recovered).queryByRole("button", { name: "重试读取 retry.md" }),
    ).toBeNull();
    expect(screen.getAllByRole("listitem", { name: "资料 retry.md" })).toHaveLength(1);
  });

  it("replaces a persisted failed source after the user reselects its file", async () => {
    const user = userEvent.setup();
    const fresh = createFreshWorkspaceState();
    const failedSource: SourceAsset = {
      id: "source-persisted-failure",
      name: "retry.md",
      kind: "markdown",
      size: 6,
      status: "failed",
      failureReason: "无法读取文件：previous failure",
      addedAt: "2026-07-15T00:00:00.000Z",
    };
    render(
      <App
        storage={new TestStorage()}
        initialState={{
          ...fresh,
          course: { ...fresh.course, sources: [failedSource] },
        }}
      />,
    );
    const input = screen.getByLabelText("导入资料", {
      selector: 'input[type="file"]',
    });
    const inputClick = vi.spyOn(input, "click");

    await user.click(screen.getByRole("button", { name: "重试读取 retry.md" }));
    expect(inputClick).toHaveBeenCalledTimes(1);
    expect(screen.getByText("无法读取文件：previous failure")).toBeVisible();

    await user.upload(input, textFile("# recovered source", "retry.md"));

    const recovered = await screen.findByRole("listitem", {
      name: "资料 retry.md",
    });
    expect(within(recovered).getByText("可用")).toBeVisible();
    expect(screen.getAllByRole("listitem", { name: "资料 retry.md" })).toHaveLength(1);
    expect(screen.queryByText("无法读取文件：previous failure")).toBeNull();
  });

  it("enters generation with the imported source in context", async () => {
    const user = userEvent.setup();
    render(<App storage={new TestStorage()} agent={new LocalCourseAgent()} />);
    await uploadReadySource(user, "context.md");

    await user.click(screen.getByRole("button", { name: "下一步：生成课程" }));

    expect(screen.getByRole("heading", { name: "生成课程" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "知识准备" })).toBeNull();
    expect(screen.getByText("已就绪资料 1 份")).toBeVisible();
    expect(screen.getByText("context.md")).toBeVisible();
    const importStep = screen.getByRole("button", { name: "导入资料" });
    expect(importStep.querySelector("svg")).not.toBeNull();
    expect(screen.getByRole("button", { name: "生成课程" })).toHaveAttribute(
      "aria-current",
      "step",
    );
  });

  it("validates blank audience and goal, focuses audience first, and does not invoke the agent", async () => {
    const user = userEvent.setup();
    const agent = new LocalCourseAgent();
    const generate = vi.spyOn(agent, "generate");
    render(
      <App
        storage={new TestStorage()}
        agent={agent}
        initialState={generationState()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "生成课程结构" }));

    expect(screen.getByText("请输入课程受众。")).toBeVisible();
    expect(screen.getByText("请输入课程目标。")).toBeVisible();
    expect(screen.getByLabelText("课程受众")).toHaveFocus();
    expect(generate).not.toHaveBeenCalled();
  });

  it("rejects a duration outside the five-minute grid before invoking the agent", async () => {
    const user = userEvent.setup();
    const agent = new LocalCourseAgent();
    const generate = vi.spyOn(agent, "generate");
    render(
      <App
        storage={new TestStorage()}
        agent={agent}
        initialState={generationState({
          brief: {
            title: "AI 课程",
            audience: "业务团队",
            goal: "建立 AI 工作流",
            durationMinutes: 42,
          },
        })}
      />,
    );

    await user.click(screen.getByRole("button", { name: "生成课程结构" }));

    expect(
      screen.getByText("课程时长需为 40–480 分钟且为 5 的倍数。"),
    ).toBeVisible();
    expect(screen.getByLabelText("课程时长（分钟）")).toHaveFocus();
    expect(generate).not.toHaveBeenCalled();
  });

  it("generates with the real local agent and starts a clean new course", async () => {
    const user = userEvent.setup();
    const storage = new TestStorage();
    const agent = new LocalCourseAgent();
    const generate = vi.spyOn(agent, "generate");
    render(<App storage={storage} agent={agent} />);
    await uploadReadySource(user, "evidence.md");
    await user.click(screen.getByRole("button", { name: "下一步：生成课程" }));

    const title = screen.getByLabelText("课程名称");
    await user.clear(title);
    await user.type(title, "  企业 AI 实战课  ");
    await user.type(screen.getByLabelText("课程受众"), "  业务负责人  ");
    await user.type(
      screen.getByLabelText("课程目标"),
      "  建立可验证的 AI 工作流  ",
    );
    const duration = screen.getByLabelText("课程时长（分钟）");
    await user.clear(duration);
    await user.type(duration, "90");
    await user.click(screen.getByRole("button", { name: "生成课程结构" }));

    expect(
      await screen.findByRole("heading", {
        name: "为什么现在需要 AI",
        level: 1,
      }),
    ).toBeVisible();
    expect(generate).toHaveBeenCalledTimes(1);
    expect(generate.mock.calls[0][0]).toEqual({
      title: "企业 AI 实战课",
      audience: "业务负责人",
      goal: "建立可验证的 AI 工作流",
      durationMinutes: 90,
    });
    expect(screen.getByRole("region", { name: "课程结构" })).toBeVisible();
    expect(screen.getByRole("region", { name: "当前章节" })).toBeVisible();
    expect(screen.getByRole("region", { name: "证据与来源" })).toBeVisible();
    expect(
      screen.queryByText("课程结构已生成，正在进入编辑验证工作区。"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑验证" })).toHaveAttribute(
      "aria-current",
      "step",
    );
    const teachStep = screen.getByRole("button", { name: "双屏授课" });
    expect(teachStep).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "验证课程" }));
    await screen.findByText("课程校验完成：0 个错误，0 个警告。");
    expect(teachStep).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "新建课程" }));

    expect(screen.getByRole("heading", { name: "导入课程资料" })).toBeVisible();
    expect(screen.queryByText("evidence.md")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑验证" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "双屏授课" })).toBeDisabled();

    await uploadReadySource(user, "new-evidence.md");
    await user.click(screen.getByRole("button", { name: "下一步：生成课程" }));
    await user.type(screen.getByLabelText("课程受众"), "新课程受众");
    await user.type(screen.getByLabelText("课程目标"), "新课程目标");
    await user.click(screen.getByRole("button", { name: "生成课程结构" }));
    await screen.findByRole("heading", { name: "为什么现在需要 AI", level: 1 });

    await waitFor(() => {
      expect(storage.snapshot()).toMatchObject({
        version: 2,
        view: { step: "edit" },
        legacyUnlinked: { sourceCount: 1, receiptCount: 1 },
      });
    });
    expect(storage.snapshot()).not.toHaveProperty("receipts");
    expect(generate).toHaveBeenCalledTimes(2);
  });

  it("keeps validation errors from unlocking teaching despite course chapters", () => {
    render(
      <App
        storage={new TestStorage()}
        initialState={validatedEditState("error")}
      />,
    );

    expect(screen.getByRole("button", { name: "编辑验证" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "双屏授课" })).toBeDisabled();
    expect(
      screen.queryByRole("checkbox", {
        name: "我已知悉校验警告，可以进入排练",
      }),
    ).not.toBeInTheDocument();
  });

  it("unlocks teaching for warning-only validation only after explicit acknowledgement", async () => {
    const user = userEvent.setup();
    render(
      <App
        storage={new TestStorage()}
        initialState={validatedEditState("warning")}
      />,
    );
    const teachStep = screen.getByRole("button", { name: "双屏授课" });
    const acknowledgement = screen.getByRole("checkbox", {
      name: "我已知悉校验警告，可以进入排练",
    });

    expect(teachStep).toBeDisabled();
    expect(acknowledgement).not.toBeChecked();
    await user.click(acknowledgement);
    expect(acknowledgement).toBeChecked();
    expect(teachStep).toBeEnabled();
  });

  it("unlocks teaching for a clean validation without warning acknowledgement", () => {
    render(
      <App
        storage={new TestStorage()}
        initialState={validatedEditState("pass")}
      />,
    );

    expect(screen.getByRole("button", { name: "双屏授课" })).toBeEnabled();
    expect(
      screen.queryByRole("checkbox", {
        name: "我已知悉校验警告，可以进入排练",
      }),
    ).not.toBeInTheDocument();
  });

  it("projects stage and presenter query roles without the workflow header", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const state = validatedTeachState();
    const lesson = state.course.chapters[0].lessons[0];
    const frame: TeachingFrame = {
      sessionId: "session-query-role",
      courseId: state.course.id,
      lessonId: lesson.id,
      lessonIndex: 0,
      lessonCount: 1,
      playing: false,
      elapsedSeconds: 8,
      sequence: 3,
      sentAt: "2026-07-16T00:00:00.000Z",
    };
    const runtime = projectionRuntime(frame);

    window.history.replaceState(
      null,
      "",
      "/?view=stage&session=session-query-role",
    );
    const { unmount } = render(
      <App initialState={state} storage={new TestStorage()} teachingRuntime={runtime} />,
    );

    expect(screen.getByText("学员屏")).toBeVisible();
    expect(screen.getByRole("heading", { name: lesson.title })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "课程工作流" })).toBeNull();
    unmount();

    window.history.replaceState(
      null,
      "",
      "/?view=presenter&session=session-query-role",
    );
    render(
      <App initialState={state} storage={new TestStorage()} teachingRuntime={runtime} />,
    );

    expect(screen.getByText("讲师屏")).toBeVisible();
    expect(screen.getByRole("navigation", { name: "授课控制" })).toBeVisible();
    expect(screen.queryByRole("navigation", { name: "课程工作流" })).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["stage", "presenter"])(
    "scrubs launch material without networking in the %s projection",
    (view) => {
      const fetchMock = vi.fn();
      vi.stubGlobal("fetch", fetchMock);
      const state = validatedTeachState();
      window.history.replaceState(
        null,
        "",
        `/?view=${view}&session=session-query-role#helper=${encodeURIComponent("http://127.0.0.1:8765")}&nonce=${"n".repeat(43)}`,
      );

      render(
        <App
          initialState={state}
          storage={new TestStorage()}
          teachingRuntime={projectionRuntime()}
        />,
      );

      expect(window.location.hash).toBe("");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("renders setup for the teach step and safe errors for invalid query projections", () => {
    const state = validatedTeachState();
    const runtime = projectionRuntime();

    const { unmount } = render(
      <App initialState={state} storage={new TestStorage()} teachingRuntime={runtime} />,
    );
    expect(screen.getByRole("navigation", { name: "课程工作流" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "双屏授课" })).toBeVisible();
    unmount();

    window.history.replaceState(null, "", "/?view=stage");
    const invalidProjection = render(
      <App initialState={state} storage={new TestStorage()} teachingRuntime={runtime} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("授课会话无效");
    expect(screen.getByRole("link", { name: "返回课程工作台" })).toHaveAttribute(
      "href",
      `${window.location.origin}/`,
    );
    expect(screen.queryByRole("navigation", { name: "课程工作流" })).toBeNull();
    invalidProjection.unmount();

    window.history.replaceState(
      null,
      "",
      "/?view=stage&session=bad%2Fsession",
    );
    const malformedProjection = render(
      <App initialState={state} storage={new TestStorage()} teachingRuntime={runtime} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("授课会话无效");
    malformedProjection.unmount();

    const emptyState = createFreshWorkspaceState();
    window.history.replaceState(null, "", "/?view=stage&session=session-empty");
    const emptyStage = render(
      <App
        initialState={emptyState}
        storage={new TestStorage()}
        teachingRuntime={runtime}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("没有可播放的小节");
    emptyStage.unmount();

    window.history.replaceState(
      null,
      "",
      "/?view=presenter&session=session-empty",
    );
    render(
      <App
        initialState={emptyState}
        storage={new TestStorage()}
        teachingRuntime={runtime}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("没有可控制的小节");
  });

  it("shows operation errors without clearing the course brief", () => {
    const initialState = generationState({
      generation: "error",
      operationError: "课程生成失败：服务暂不可用",
      brief: {
        title: "保留的课程",
        audience: "产品团队",
        goal: "保留输入以便重试",
        durationMinutes: 120,
      },
    });

    render(<App storage={new TestStorage()} initialState={initialState} />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "课程生成失败：服务暂不可用",
    );
    expect(screen.getByLabelText("课程名称")).toHaveValue("保留的课程");
    expect(screen.getByLabelText("课程受众")).toHaveValue("产品团队");
    expect(screen.getByLabelText("课程目标")).toHaveValue("保留输入以便重试");
  });

  it("keeps keyboard focus and desktop fallback as explicit CSS contracts", () => {
    const css = readFileSync("src/app/app.css", "utf8");

    expect(css).toMatch(/:focus-visible\s*\{/);
    expect(css).toMatch(/outline:\s*3px solid var\(--color-brand\)/);
    expect(css).toMatch(/min-(?:width|height):\s*44px/);
    expect(css).toMatch(/@media\s*\(max-width:\s*819px\)/);
    expect(css).not.toContain(["grad", "ient"].join(""));
    expect(css).toMatch(
      /\.stage-view,\s*\.presenter-view\s*\{[^}]*background:\s*var\(--color-page\)/s,
    );
    expect(css).toMatch(
      /\.stage-slide\s*\{[^}]*background:\s*var\(--color-surface\)/s,
    );
    expect(css).toMatch(
      /\.presenter-current\s*\{[^}]*background:\s*var\(--color-surface\)/s,
    );
  });
});

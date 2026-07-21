// Vitest executes in Node, while the browser-only tsconfig intentionally omits Node types.
// @ts-expect-error -- node:fs is available to the test runner at runtime.
import { readFileSync } from "node:fs";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SourceAsset } from "../domain/course";
import { LocalCourseAgent, type CourseAgent } from "../domain/course-agent";
import {
  WorkspaceProvider,
  type WorkspaceState,
} from "../state/workspace";
import { CourseEditor } from "./CourseEditor";

const sources: SourceAsset[] = [
  {
    id: "source-document",
    name: "AI 基础手册.md",
    kind: "markdown",
    size: 1_024,
    status: "ready",
    extractedText: "# AI 基础",
    addedAt: "2026-07-15T00:00:00.000Z",
  },
  {
    id: "source-web",
    name: "企业案例网页",
    kind: "web",
    size: 2_048,
    status: "ready",
    extractedText: "企业工作流案例",
    addedAt: "2026-07-15T00:01:00.000Z",
  },
  {
    id: "source-note",
    name: "讲师备课笔记",
    kind: "note",
    size: 512,
    status: "ready",
    extractedText: "课堂提示",
    addedAt: "2026-07-15T00:02:00.000Z",
  },
  {
    id: "source-unsupported",
    name: "历史归档.txt",
    kind: "text",
    size: 256,
    status: "unsupported",
    addedAt: "2026-07-15T00:03:00.000Z",
  },
];

function editorState(): WorkspaceState {
  return {
    step: "edit",
    course: {
      schemaVersion: 1,
      id: "course-editor-fixture",
      title: "企业 AI 实战课",
      audience: "业务团队",
      goal: "建立可验证的 AI 工作流",
      durationMinutes: 90,
      chapters: [
        {
          id: "chapter-one",
          title: "为什么现在需要 AI",
          objective: "理解 AI 技术发展的关键趋势与商业价值。",
          lessons: [
            {
              id: "lesson-one",
              title: "AI 的发展与现状",
              summary: "理解 AI 技术演进的关键里程碑与当前能力边界。",
              durationMinutes: 15,
              sourceIds: ["source-document"],
              status: "grounded",
            },
            {
              id: "lesson-two",
              title: "企业面临的变化",
              summary: "分析市场、客户与竞争格局的变化。",
              durationMinutes: 20,
              sourceIds: [],
              status: "needs-source",
            },
          ],
        },
        {
          id: "chapter-two",
          title: "从任务到工作流",
          objective: "把高价值任务拆解为可验证的工作流。",
          lessons: [
            {
              id: "lesson-three",
              title: "识别高价值任务",
              summary: "识别适合使用 AI 改造的业务任务。",
              durationMinutes: 25,
              sourceIds: ["source-web"],
              status: "grounded",
            },
            {
              id: "lesson-four",
              title: "任务拆解与流程化",
              summary: "把复杂任务拆分为清晰、可验证的步骤。",
              durationMinutes: 30,
              sourceIds: ["source-note"],
              status: "grounded",
            },
          ],
        },
      ],
      sources,
      updatedAt: "2026-07-15T00:00:00.000Z",
    },
    brief: {
      title: "企业 AI 实战课",
      audience: "业务团队",
      goal: "建立可验证的 AI 工作流",
      durationMinutes: 90,
    },
    receipts: [],
    governed: {
      cardVersionIds: [],
      visualPlacementIds: [],
    },
    selectedChapterId: "chapter-one",
    selectedLessonId: "lesson-one",
    courseRevision: 0,
    generation: "success",
    validation: "idle",
    validationWarningsAcknowledged: false,
    assistant: "idle",
  };
}

function warningEditorState(): WorkspaceState {
  const initial = editorState();
  return {
    ...initial,
    course: {
      ...initial.course,
      chapters: initial.course.chapters.map((chapter) => ({
        ...chapter,
        lessons: chapter.lessons.map((lesson, index) => ({
          ...lesson,
          sourceIds: chapter.id === "chapter-one" && index === 0 ? lesson.sourceIds : [],
          status:
            chapter.id === "chapter-one" && index === 0
              ? lesson.status
              : "needs-source",
        })),
      })),
    },
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function renderEditor(
  initialState: WorkspaceState = editorState(),
  agent?: CourseAgent,
): void {
  render(
    <WorkspaceProvider
      initialState={initialState}
      storage={{ getItem: () => null, setItem: vi.fn() }}
      agent={agent}
    >
      <CourseEditor />
    </WorkspaceProvider>,
  );
}

afterEach(cleanup);

describe("轻量课程编辑器", () => {
  it("renders the three semantic regions with the selected chapter and lessons", () => {
    renderEditor();

    expect(screen.getByRole("region", { name: "课程结构" })).toBeVisible();
    const currentChapter = screen.getByRole("region", { name: "当前章节" });
    expect(currentChapter).toBeVisible();
    expect(screen.getByRole("region", { name: "证据与来源" })).toBeVisible();
    expect(
      within(currentChapter).getByRole("heading", {
        name: "为什么现在需要 AI",
        level: 1,
      }),
    ).toBeVisible();
    expect(within(currentChapter).getByText("AI 的发展与现状")).toBeVisible();
    expect(within(currentChapter).getByText("企业面临的变化")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "第 1 章 为什么现在需要 AI" }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByRole("button", { name: "第 1.1 节 AI 的发展与现状" }),
    ).toHaveAttribute("aria-current", "true");
  });

  it("selects the first lesson of another chapter and updates source context", async () => {
    const user = userEvent.setup();
    renderEditor();

    await user.click(
      screen.getByRole("button", { name: "第 2 章 从任务到工作流" }),
    );

    expect(
      screen.getByRole("heading", { name: "从任务到工作流", level: 1 }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "第 2.1 节 识别高价值任务" }),
    ).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("识别高价值任务 · 已关联 1 个来源")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "取消关联 企业案例网页" }),
    ).toBeEnabled();
  });

  it("validates, saves trimmed lesson values, and restores edit-trigger focus", async () => {
    const user = userEvent.setup();
    renderEditor();
    const editTrigger = screen.getByRole("button", {
      name: "编辑 AI 的发展与现状",
    });

    await user.click(editTrigger);
    const dialog = screen.getByRole("dialog", {
      name: "编辑 AI 的发展与现状",
    });
    const title = within(dialog).getByLabelText("小节标题");
    const summary = within(dialog).getByLabelText("内容摘要");
    const duration = within(dialog).getByLabelText("时长（分钟）");
    expect(title).toHaveFocus();

    await user.clear(title);
    await user.clear(summary);
    await user.clear(duration);
    await user.type(duration, "4");
    await user.click(within(dialog).getByRole("button", { name: "保存小节" }));

    expect(within(dialog).getByText("请输入小节标题。")).toBeVisible();
    expect(within(dialog).getByText("请输入内容摘要。")).toBeVisible();
    expect(
      within(dialog).getByText("时长必须是 5 到 90 之间的整数。"),
    ).toBeVisible();

    await user.clear(duration);
    await user.type(duration, "5.5");
    await user.click(within(dialog).getByRole("button", { name: "保存小节" }));
    expect(
      within(dialog).getByText("时长必须是 5 到 90 之间的整数。"),
    ).toBeVisible();

    await user.clear(duration);
    await user.type(duration, "91");
    await user.click(within(dialog).getByRole("button", { name: "保存小节" }));
    expect(
      within(dialog).getByText("时长必须是 5 到 90 之间的整数。"),
    ).toBeVisible();

    await user.type(title, "  AI 商业价值  ");
    await user.type(summary, "  连接技术趋势与业务结果。  ");
    expect(title).toHaveValue("  AI 商业价值  ");
    expect(summary).toHaveValue("  连接技术趋势与业务结果。  ");
    await user.clear(duration);
    await user.type(duration, "35");
    await user.click(within(dialog).getByRole("button", { name: "保存小节" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getAllByText("AI 商业价值").length).toBeGreaterThan(0);
    expect(screen.getByText("连接技术趋势与业务结果。")).toBeVisible();
    expect(screen.getByText("35 分钟")).toBeVisible();
    expect(editTrigger).toHaveFocus();
  });

  it("contains modal focus, inerts its background, and handles Escape globally", async () => {
    const user = userEvent.setup();
    renderEditor();
    const shell = screen.getByRole("main");
    const outsideControl = screen.getByRole("button", {
      name: "打开证据与来源",
    });
    const editTrigger = screen.getByRole("button", {
      name: "编辑 AI 的发展与现状",
    });

    await user.click(editTrigger);
    const dialog = screen.getByRole("dialog", {
      name: "编辑 AI 的发展与现状",
    });
    expect(shell).toHaveAttribute("inert");
    expect(shell).toHaveAttribute("aria-hidden", "true");

    const save = within(dialog).getByRole("button", { name: "保存小节" });
    save.focus();
    await user.tab();
    expect(
      within(dialog).getByRole("button", { name: "关闭编辑小节" }),
    ).toHaveFocus();

    outsideControl.focus();
    expect(within(dialog).getByLabelText("小节标题")).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(shell).not.toHaveAttribute("inert");
    expect(shell).not.toHaveAttribute("aria-hidden");
    expect(editTrigger).toHaveFocus();
  });

  it("closes the edit dialog with Escape and restores trigger focus", async () => {
    const user = userEvent.setup();
    renderEditor();
    const editTrigger = screen.getByRole("button", {
      name: "编辑 AI 的发展与现状",
    });

    await user.click(editTrigger);
    expect(screen.getByRole("dialog")).toBeVisible();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(editTrigger).toHaveFocus();
  });

  it("moves lessons one position and disables boundary controls", async () => {
    const user = userEvent.setup();
    renderEditor();
    const currentChapter = screen.getByRole("region", { name: "当前章节" });
    expect(
      screen.getByRole("button", { name: "上移 AI 的发展与现状" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "下移 企业面临的变化" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "下移 AI 的发展与现状" }),
    );

    const cards = within(currentChapter).getAllByRole("article");
    expect(within(cards[0]).getByText("企业面临的变化")).toBeVisible();
    expect(within(cards[1]).getByText("AI 的发展与现状")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "上移 企业面临的变化" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "下移 AI 的发展与现状" }),
    ).toBeDisabled();
  });

  it("keeps lesson selection on semantic tree buttons instead of pointer-only cards", async () => {
    const user = userEvent.setup();
    renderEditor();

    await user.click(
      screen.getByRole("article", { name: "小节 1.2 企业面临的变化" }),
    );
    expect(
      screen.getByRole("button", { name: "第 1.1 节 AI 的发展与现状" }),
    ).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("AI 的发展与现状 · 已关联 1 个来源")).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "第 1.2 节 企业面临的变化" }),
    );
    expect(
      screen.getByRole("button", { name: "第 1.2 节 企业面临的变化" }),
    ).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("企业面临的变化 · 已关联 0 个来源")).toBeVisible();
  });

  it("toggles ready source association while keeping unsupported sources disabled", async () => {
    const user = userEvent.setup();
    renderEditor();
    const unsupported = screen.getByRole("listitem", {
      name: "来源 历史归档.txt",
    });
    expect(within(unsupported).getByText("暂不支持")).toBeVisible();
    expect(
      within(unsupported).getByRole("button", { name: "关联 历史归档.txt" }),
    ).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "取消关联 AI 基础手册.md" }),
    );
    expect(screen.getByText("AI 的发展与现状 · 已关联 0 个来源")).toBeVisible();
    expect(
      within(
        screen.getByRole("article", {
          name: "小节 1.1 AI 的发展与现状",
        }),
      ).getByText("待补充来源"),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "关联 AI 基础手册.md" }),
    ).toBeEnabled();

    await user.click(
      screen.getByRole("button", { name: "关联 AI 基础手册.md" }),
    );
    expect(screen.getByText("AI 的发展与现状 · 已关联 1 个来源")).toBeVisible();
    expect(screen.getByText("已解析")).toBeVisible();
  });

  it("filters contextual sources with pressed buttons and shows empty feedback", async () => {
    const user = userEvent.setup();
    renderEditor();

    const allFilter = screen.getByRole("button", { name: "全部" });
    const webFilter = screen.getByRole("button", { name: "网页" });
    expect(allFilter).toHaveAttribute("aria-pressed", "true");
    expect(webFilter).toHaveAttribute("aria-pressed", "false");

    await user.click(webFilter);
    expect(webFilter).toHaveAttribute("aria-pressed", "true");
    expect(allFilter).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("企业案例网页")).toBeVisible();
    expect(screen.queryByText("AI 基础手册.md")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "笔记" }));
    expect(screen.getByText("讲师备课笔记")).toBeVisible();
    expect(screen.queryByText("企业案例网页")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "图片" }));
    expect(screen.getByText("当前筛选下暂无来源。")).toBeVisible();
    expect(screen.getByRole("button", { name: "图片" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await user.click(allFilter);
    expect(screen.getByText("AI 基础手册.md")).toBeVisible();
    expect(screen.getByText("企业案例网页")).toBeVisible();
    expect(screen.getByText("讲师备课笔记")).toBeVisible();
    expect(screen.getByText("历史归档.txt")).toBeVisible();
  });

  it("adds a selected chapter and then a selected lesson through provider actions", async () => {
    const user = userEvent.setup();
    renderEditor();

    await user.click(screen.getByRole("button", { name: "添加章节" }));
    expect(
      screen.getByRole("heading", { name: "新章节 3", level: 1 }),
    ).toBeVisible();
    expect(screen.getByText("本章还没有小节。")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "第 3 章 新章节 3" }),
    ).toHaveAttribute("aria-current", "true");

    await user.click(screen.getByRole("button", { name: "添加小节" }));
    expect(screen.getAllByText("新小节 1").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "第 3.1 节 新小节 1" }),
    ).toHaveAttribute("aria-current", "true");
    expect(screen.getByText("新小节 1 · 已关联 0 个来源")).toBeVisible();
  });

  it("closes the source drawer with Escape and restores its trigger focus", async () => {
    const user = userEvent.setup();
    renderEditor();
    const trigger = screen.getByRole("button", { name: "打开证据与来源" });
    expect(trigger).toHaveAttribute("aria-controls", "course-source-panel");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Escape}");

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("exposes exactly the three supported assistant intents", () => {
    renderEditor();

    const assistant = screen.getByRole("region", { name: "课程助手" });
    const supported = [
      "缩短课程到 90 分钟",
      "为本章补充案例",
      "检查来源覆盖",
    ];
    const suggestions = within(assistant).getByRole("group", {
      name: "课程助手建议",
    });
    expect(
      within(suggestions)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(supported);
    expect(within(assistant).getByLabelText("向课程助手说明调整需求")).toBeVisible();
    const send = within(assistant).getByRole("button", {
      name: "发送给课程助手",
    });
    expect(send.querySelector("svg")).not.toBeNull();
  });

  it("submits a supported intent through the real provider and clears input only after recognized success", async () => {
    const user = userEvent.setup();
    const agent = new LocalCourseAgent();
    const originalApplyIntent = agent.applyIntent.bind(agent);
    const gate = deferred<void>();
    const applyIntent = vi
      .spyOn(agent, "applyIntent")
      .mockImplementation(async (...args) => {
        await gate.promise;
        return originalApplyIntent(...args);
      });
    renderEditor(editorState(), agent);
    const assistant = screen.getByRole("region", { name: "课程助手" });
    const input = within(assistant).getByLabelText("向课程助手说明调整需求");

    await user.click(
      within(assistant).getByRole("button", { name: "缩短课程到 90 分钟" }),
    );
    expect(input).toHaveValue("缩短课程到 90 分钟");
    await user.click(
      within(assistant).getByRole("button", { name: "发送给课程助手" }),
    );

    expect(applyIntent).toHaveBeenCalledWith(
      expect.objectContaining({ id: "course-editor-fixture" }),
      "缩短课程到 90 分钟",
      "chapter-one",
    );
    expect(
      within(assistant).getByRole("button", { name: "发送给课程助手" }),
    ).toBeDisabled();
    expect(within(assistant).getByRole("status")).toHaveTextContent(
      "课程助手正在处理",
    );

    gate.resolve();
    expect(
      await within(assistant).findByText("已将课程时长调整为 90 分钟。"),
    ).toBeVisible();
    expect(input).toHaveValue("");
    expect(within(assistant).getByText("助手收据")).toBeVisible();
    expect(
      within(
        screen.getByRole("article", { name: "小节 1.1 AI 的发展与现状" }),
      ).getByText("20 分钟"),
    ).toBeVisible();
  });

  it("preserves blank and unrecognized assistant input with actionable alerts", async () => {
    const user = userEvent.setup();
    renderEditor(editorState(), new LocalCourseAgent());
    const assistant = screen.getByRole("region", { name: "课程助手" });
    const input = within(assistant).getByLabelText("向课程助手说明调整需求");
    const send = within(assistant).getByRole("button", {
      name: "发送给课程助手",
    });

    await user.type(input, "   ");
    await user.click(send);
    expect(within(assistant).getByRole("alert")).toHaveTextContent(
      "请输入希望课程助手执行的内容。",
    );
    expect(input).toHaveValue("   ");

    await user.clear(input);
    await user.type(input, "请随便改一下");
    await user.click(send);
    expect(await within(assistant).findByRole("alert")).toHaveTextContent(
      "我没有修改课程",
    );
    expect(input).toHaveValue("请随便改一下");
    expect(screen.getAllByText("AI 的发展与现状").length).toBeGreaterThan(0);
  });

  it("preserves failed assistant input and exposes the provider failure", async () => {
    const user = userEvent.setup();
    const agent = new LocalCourseAgent();
    vi.spyOn(agent, "applyIntent").mockRejectedValueOnce(
      new Error("服务暂不可用"),
    );
    renderEditor(editorState(), agent);
    const assistant = screen.getByRole("region", { name: "课程助手" });
    const input = within(assistant).getByLabelText("向课程助手说明调整需求");

    await user.type(input, "为本章补充案例");
    await user.click(
      within(assistant).getByRole("button", { name: "发送给课程助手" }),
    );

    expect(await within(assistant).findByRole("alert")).toHaveTextContent(
      "课程助手执行失败：服务暂不可用",
    );
    expect(input).toHaveValue("为本章补充案例");
  });

  it("settles a running assistant after a structural edit and ignores its stale result", async () => {
    const user = userEvent.setup();
    const agent = new LocalCourseAgent();
    const originalApplyIntent = agent.applyIntent.bind(agent);
    const gate = deferred<void>();
    vi.spyOn(agent, "applyIntent").mockImplementation(async (...args) => {
      await gate.promise;
      return originalApplyIntent(...args);
    });
    renderEditor(editorState(), agent);
    const assistant = screen.getByRole("region", { name: "课程助手" });
    const input = within(assistant).getByLabelText("向课程助手说明调整需求");
    const send = within(assistant).getByRole("button", {
      name: "发送给课程助手",
    });

    await user.type(input, "缩短课程到 90 分钟");
    await user.click(send);
    expect(send).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "取消关联 AI 基础手册.md" }),
    );
    expect(send).toBeEnabled();
    expect(input).toBeEnabled();
    expect(input).toHaveValue("缩短课程到 90 分钟");

    gate.resolve();
    await waitFor(() => expect(send).toBeEnabled());
    expect(input).toHaveValue("缩短课程到 90 分钟");
    expect(within(assistant).queryByText("助手收据")).not.toBeInTheDocument();
  });

  it("shows validation and assistant failures only in their owning regions", () => {
    renderEditor({
      ...editorState(),
      validation: "error",
      assistant: "error",
      validationError: "校验专属错误",
      assistantError: "助手专属错误",
      operationError: "最新共享错误",
    });

    const validation = screen.getByRole("region", { name: "课程验证" });
    const assistant = screen.getByRole("region", { name: "课程助手" });
    expect(within(validation).getByRole("alert")).toHaveTextContent(
      "校验专属错误",
    );
    expect(within(validation).queryByText("助手专属错误")).not.toBeInTheDocument();
    expect(within(assistant).getByRole("alert")).toHaveTextContent(
      "助手专属错误",
    );
    expect(within(assistant).queryByText("校验专属错误")).not.toBeInTheDocument();
  });

  it("keeps the assistant preview pinned to its own receipt", () => {
    const initial = editorState();
    renderEditor({
      ...initial,
      assistant: "success",
      assistantMessage: "助手调整完成。",
      assistantReceiptId: "assistant-receipt",
      validation: "success",
      receipts: [
        {
          id: "assistant-receipt",
          courseId: initial.course.id,
          kind: "generation",
          createdAt: "2026-07-15T00:10:00.000Z",
          inputDigest: "assistant-digest-owned",
          summary: "助手调整完成。",
          checks: [],
        },
        {
          id: "later-validation-receipt",
          courseId: initial.course.id,
          kind: "validation",
          createdAt: "2026-07-15T00:11:00.000Z",
          inputDigest: "validation-digest-later",
          summary: "课程校验完成。",
          checks: [],
        },
      ],
    });

    const assistant = screen.getByRole("region", { name: "课程助手" });
    expect(within(assistant).getByText("assistant-di")).toBeVisible();
    expect(within(assistant).queryByText("validation-d")).not.toBeInTheDocument();
  });

  it("runs real course validation with visible counts, checks, running state, and receipt digest", async () => {
    const user = userEvent.setup();
    const digestGate = deferred<ArrayBuffer>();
    const digest = vi
      .spyOn(crypto.subtle, "digest")
      .mockReturnValueOnce(digestGate.promise);
    try {
      renderEditor();
      const validation = screen.getByRole("region", { name: "课程验证" });
      const button = within(validation).getByRole("button", { name: "验证课程" });

      await user.click(button);
      expect(button).toBeDisabled();
      expect(button).toHaveTextContent("正在验证");
      digestGate.resolve(new ArrayBuffer(32));

      expect(
        await within(validation).findByText("课程校验完成：0 个错误，0 个警告。"),
      ).toBeVisible();
      expect(within(validation).getByText("0 个错误")).toBeVisible();
      expect(within(validation).getByText("0 个警告")).toBeVisible();
      expect(within(validation).getByText(/项通过/)).toBeVisible();
      expect(within(validation).getByText("校验收据")).toBeVisible();
      expect(within(validation).getByText("000000000000")).toBeVisible();
    } finally {
      digest.mockRestore();
    }
  });

  it("shows warning evidence and the acknowledgement only for warning-only validation", async () => {
    const user = userEvent.setup();
    renderEditor(warningEditorState());
    const validation = screen.getByRole("region", { name: "课程验证" });

    await user.click(within(validation).getByRole("button", { name: "验证课程" }));

    expect(await within(validation).findByText(/低于七成/)).toBeVisible();
    expect(within(validation).getByText("0 个错误")).toBeVisible();
    expect(within(validation).getByText("1 个警告")).toBeVisible();
    const acknowledgement = within(validation).getByRole("checkbox", {
      name: "我已知悉校验警告，可以进入排练",
    });
    expect(acknowledgement).not.toBeChecked();
    await user.click(acknowledgement);
    expect(acknowledgement).toBeChecked();
  });

  it("turns the source-coverage assistant intent into the same visible validation evidence", async () => {
    const user = userEvent.setup();
    renderEditor(warningEditorState(), new LocalCourseAgent());
    const assistant = screen.getByRole("region", { name: "课程助手" });
    await user.click(
      within(assistant).getByRole("button", { name: "检查来源覆盖" }),
    );
    await user.click(
      within(assistant).getByRole("button", { name: "发送给课程助手" }),
    );

    expect(await within(assistant).findByText("已完成来源覆盖检查")).toBeVisible();
    const validation = screen.getByRole("region", { name: "课程验证" });
    expect(within(validation).getByText(/课程校验完成：0 个错误，1 个警告/)).toBeVisible();
    expect(
      within(validation).getByRole("checkbox", {
        name: "我已知悉校验警告，可以进入排练",
      }),
    ).toBeVisible();
  });

  it("keeps the measured light layout as explicit CSS contracts", () => {
    const css = readFileSync("src/app/app.css", "utf8");

    expect(css).toMatch(
      /grid-template-columns:\s*364px minmax\(560px, 1fr\) 394px/,
    );
    expect(css).toMatch(
      /grid-template-columns:\s*300px minmax\(520px, 1fr\) 330px/,
    );
    expect(css).toMatch(/height:\s*calc\(100vh - 76px\)/);
    expect(css).toMatch(
      /\.course-editor-shell[\s\S]*grid-template-rows:\s*minmax\(0, 1fr\) 165px/,
    );
    expect(css).toMatch(/\.chapter-tree[\s\S]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.lesson-list[\s\S]*overflow-y:\s*auto/);
    expect(css).toMatch(/\.source-panel[\s\S]*overflow-y:\s*auto/);
    expect(css).toMatch(
      /\.lesson-list\s*{[\s\S]*?padding:\s*24px 32px 28px/,
    );
    expect(css).toMatch(
      /\.lesson-list__heading\s*{[\s\S]*?margin-bottom:\s*18px/,
    );
    expect(css).toMatch(/\.lesson-list__cards\s*{[\s\S]*?gap:\s*10px/);
    expect(css).toMatch(/\.lesson-card\s*{[\s\S]*?height:\s*92px/);
    expect(css).toMatch(
      /\.lesson-card__content > p\s*{[\s\S]*?overflow:\s*hidden[\s\S]*?text-overflow:\s*ellipsis[\s\S]*?white-space:\s*nowrap/,
    );
    expect(css).toMatch(
      /\.lesson-add-button\s*{[\s\S]*?margin-top:\s*12px/,
    );
    expect(css).toMatch(/@media\s*\(width < 1180px\)/);
    expect(css).toMatch(/@media\s*\(max-width:\s*819px\)/);
    expect(css).toMatch(/(?:width|min-width|height|min-height):\s*44px/);
    expect(css).toContain("background: var(--color-page)");
    expect(css).toContain("background: var(--color-surface)");
    expect(css).toContain("background: var(--color-surface-muted)");
    expect(css).not.toContain(["grad", "ient"].join(""));
    expect(css).not.toContain("color-scheme: dark");
  });
});

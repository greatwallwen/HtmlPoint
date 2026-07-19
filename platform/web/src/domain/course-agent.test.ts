import { describe, expect, it } from "vitest";

import type { CourseBrief, CourseDocument, SourceAsset } from "./course";
import { LocalCourseAgent } from "./course-agent";
import { stableDigest, validateCourse } from "./validation";

const brief: CourseBrief = {
  title: "  企业 AI 实战  ",
  audience: "  业务负责人  ",
  goal: "  提升工作效率  ",
  durationMinutes: 90,
};

const sources: SourceAsset[] = [
  {
    id: "source-ready-a",
    name: "企业.AI.md",
    kind: "markdown",
    size: 120,
    status: "ready",
    extractedText: "企业 AI 资料",
    addedAt: "2026-07-15T00:00:00.000Z",
  },
  {
    id: "source-failed",
    name: "待处理.pdf",
    kind: "pdf",
    size: 80,
    status: "failed",
    addedAt: "2026-07-15T00:01:00.000Z",
  },
  {
    id: "source-ready-b",
    name: "工作流.txt",
    kind: "text",
    size: 60,
    status: "ready",
    extractedText: "工作流资料",
    addedAt: "2026-07-15T00:02:00.000Z",
  },
];

const expectedChapterShape = [
  {
    title: "为什么现在需要 AI",
    objective: "建立共同认知",
    lessons: ["AI 的发展与现状", "企业面临的变化", "AI 能为企业带来什么"],
  },
  {
    title: "从任务到工作流",
    objective: "把 AI 放进真实工作",
    lessons: ["识别高价值任务", "任务拆解与流程化", "工具选择与组合"],
  },
  {
    title: "建立验证习惯",
    objective: "用证据判断结果",
    lessons: ["输出不等于结果", "验证方法与改进"],
  },
];

const expectIsoTimestamp = (value: string) => {
  expect(new Date(value).toISOString()).toBe(value);
};

describe("LocalCourseAgent.generate", () => {
  it("generates the exact grounded course structure with deterministic durations and source rotation", async () => {
    const originalSources = structuredClone(sources);
    const result = await new LocalCourseAgent().generate(brief, sources);

    expect(result.course).toMatchObject({
      schemaVersion: 1,
      title: "企业 AI 实战",
      audience: "业务负责人",
      goal: "提升工作效率",
      durationMinutes: 90,
    });
    expect(result.course.id).toMatch(/^course-/);
    expectIsoTimestamp(result.course.updatedAt);
    expect(result.course.sources).toEqual(sources);
    expect(sources).toEqual(originalSources);

    expect(
      result.course.chapters.map((chapter) => ({
        title: chapter.title,
        objective: chapter.objective,
        lessons: chapter.lessons.map((lesson) => lesson.title),
      })),
    ).toEqual(expectedChapterShape);

    const lessons = result.course.chapters.flatMap((chapter) => chapter.lessons);
    expect(lessons.map((lesson) => lesson.durationMinutes)).toEqual([
      10, 10, 10, 10, 10, 10, 10, 20,
    ]);
    expect(lessons.reduce((total, lesson) => total + lesson.durationMinutes, 0)).toBe(
      90,
    );
    expect(lessons.every((lesson) => lesson.durationMinutes % 5 === 0)).toBe(true);
    expect(lessons.every((lesson) => lesson.status === "grounded")).toBe(true);
    expect(lessons.map((lesson) => lesson.sourceIds)).toEqual([
      ["source-ready-a"],
      ["source-ready-b"],
      ["source-ready-a"],
      ["source-ready-b"],
      ["source-ready-a"],
      ["source-ready-b"],
      ["source-ready-a"],
      ["source-ready-b"],
    ]);
    expect(lessons.map((lesson) => lesson.summary)).toEqual([
      "围绕“提升工作效率”，结合来源“企业.AI”设计本节。",
      "围绕“提升工作效率”，结合来源“工作流”设计本节。",
      "围绕“提升工作效率”，结合来源“企业.AI”设计本节。",
      "围绕“提升工作效率”，结合来源“工作流”设计本节。",
      "围绕“提升工作效率”，结合来源“企业.AI”设计本节。",
      "围绕“提升工作效率”，结合来源“工作流”设计本节。",
      "围绕“提升工作效率”，结合来源“企业.AI”设计本节。",
      "围绕“提升工作效率”，结合来源“工作流”设计本节。",
    ]);
    expect(result.course.chapters.every((chapter) => /^chapter-/.test(chapter.id))).toBe(
      true,
    );
    expect(lessons.every((lesson) => /^lesson-/.test(lesson.id))).toBe(true);
  });

  it("creates a generation receipt with a stable digest of normalized inputs", async () => {
    const agent = new LocalCourseAgent();
    const first = await agent.generate(brief, sources);
    const second = await agent.generate(brief, sources);
    const expectedDigest = await stableDigest({
      brief: {
        title: "企业 AI 实战",
        audience: "业务负责人",
        goal: "提升工作效率",
        durationMinutes: 90,
      },
      sources: sources.map(({ id, name, status }) => ({ id, name, status })),
    });

    expect(first.receipt).toMatchObject({
      courseId: first.course.id,
      kind: "generation",
      inputDigest: expectedDigest,
      summary: "已基于 2 份可用资料生成 3 章 8 节课程。",
      checks: [
        {
          id: "generation-structure",
          level: "pass",
          message: "课程结构已生成。",
        },
      ],
    });
    expect(first.receipt.id).toMatch(/^evidence-/);
    expectIsoTimestamp(first.receipt.createdAt);
    expect(second.receipt.inputDigest).toBe(first.receipt.inputDigest);
  });

  it.each([
    ["标题", { title: "   " }],
    ["受众", { audience: "\t" }],
    ["目标", { goal: "\n" }],
  ])("rejects a blank %s", async (message, patch) => {
    await expect(
      new LocalCourseAgent().generate({ ...brief, ...patch }, sources),
    ).rejects.toThrow(message);
  });

  it("rejects generation without a ready source", async () => {
    const unavailableSources = sources.map((source) => ({
      ...source,
      status: "failed" as const,
    }));

    await expect(
      new LocalCourseAgent().generate(brief, unavailableSources),
    ).rejects.toThrow("就绪");
  });

  it.each([35, 485, 42])("rejects invalid duration %s", async (durationMinutes) => {
    await expect(
      new LocalCourseAgent().generate({ ...brief, durationMinutes }, sources),
    ).rejects.toThrow("时长");
  });
});

describe("LocalCourseAgent.applyIntent", () => {
  it("redistributes duration exactly, returns evidence, and does not mutate the input", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const before = structuredClone(generated.course);

    const result = await agent.applyIntent(
      generated.course,
      "  缩短课程到 90 分钟  ",
    );

    expect(generated.course).toEqual(before);
    expect(result.course).not.toBe(generated.course);
    expect(result.course.durationMinutes).toBe(90);
    expect(
      result.course.chapters.flatMap((chapter) =>
        chapter.lessons.map((lesson) => lesson.durationMinutes),
      ),
    ).toEqual([10, 10, 10, 10, 10, 10, 10, 20]);
    expectIsoTimestamp(result.course.updatedAt);
    expect(result.message).toBe("已将课程时长调整为 90 分钟。");
    expect(result.receipt).toMatchObject({
      courseId: result.course.id,
      kind: "generation",
    });
    expect(result.receipt.id).toMatch(/^evidence-/);
    expectIsoTimestamp(result.receipt.createdAt);
    expect(result.receipt.checks).toHaveLength(1);
    expect(result.receipt.checks[0]).toMatchObject({
      id: "duration-adjusted",
      level: "pass",
    });
  });

  it("hashes a duration change from the input course and trimmed intent", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const before = structuredClone(generated.course);

    const result = await agent.applyIntent(
      generated.course,
      "  缩短课程到 80 分钟  ",
    );

    expect(generated.course).toEqual(before);
    expect(result.receipt.inputDigest).toBe(
      await stableDigest({
        course: generated.course,
        intent: "缩短课程到 80 分钟",
      }),
    );
  });

  it("rejects a duration infeasible for the current lesson count", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const oneLessonCourse: CourseDocument = {
      ...generated.course,
      durationMinutes: 45,
      chapters: [
        {
          ...generated.course.chapters[0],
          lessons: [
            {
              ...generated.course.chapters[0].lessons[0],
              durationMinutes: 45,
            },
          ],
        },
      ],
    };

    await expect(
      agent.applyIntent(oneLessonCourse, "缩短课程到 100 分钟"),
    ).rejects.toThrow("时长");
  });

  it("rejects an assistant duration below 40 even when one lesson is feasible", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const oneLessonCourse: CourseDocument = {
      ...generated.course,
      durationMinutes: 45,
      chapters: [
        {
          ...generated.course.chapters[0],
          lessons: [
            {
              ...generated.course.chapters[0].lessons[0],
              durationMinutes: 45,
            },
          ],
        },
      ],
    };

    await expect(
      agent.applyIntent(oneLessonCourse, "缩短课程到 35 分钟"),
    ).rejects.toThrow(/[\u4e00-\u9fff]/);
  });

  it("appends an exact grounded case lesson with evidence and without mutation", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const before = structuredClone(generated.course);
    const chapterId = generated.course.chapters[1].id;

    const result = await agent.applyIntent(
      generated.course,
      "  为本章补充案例  ",
      chapterId,
    );

    expect(generated.course).toEqual(before);
    expect(result.course).not.toBe(generated.course);
    expect(result.course.durationMinutes).toBe(105);
    expect(result.course.chapters[0]).toEqual(generated.course.chapters[0]);
    expect(result.course.chapters[2]).toEqual(generated.course.chapters[2]);
    const changedChapter = result.course.chapters[1];
    expect(changedChapter.lessons.slice(0, -1)).toEqual(
      generated.course.chapters[1].lessons,
    );
    expect(changedChapter.lessons.at(-1)).toEqual({
      id: expect.stringMatching(/^lesson-/),
      title: "业务案例：从资料到行动",
      summary: "围绕“提升工作效率”，结合来源“企业.AI”设计业务案例。",
      durationMinutes: 15,
      sourceIds: ["source-ready-a"],
      status: "grounded",
    });
    expectIsoTimestamp(result.course.updatedAt);
    expect(result.message).toBe("已为当前章节补充一个业务案例。");
    expect(result.receipt).toMatchObject({
      courseId: result.course.id,
      kind: "generation",
    });
    expect(result.receipt.checks).toHaveLength(1);
    expect(result.receipt.checks[0]).toMatchObject({ id: "case-added", level: "pass" });
  });

  it("hashes a case change from the input course and trimmed intent", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const chapterId = generated.course.chapters[1].id;

    const result = await agent.applyIntent(
      generated.course,
      "  为本章补充案例  ",
      chapterId,
    );

    expect(result.receipt.inputDigest).toBe(
      await stableDigest({
        course: generated.course,
        intent: "为本章补充案例",
      }),
    );
  });

  it("keeps a case digest stable across distinct generated lesson IDs", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const chapterId = generated.course.chapters[1].id;

    const first = await agent.applyIntent(
      generated.course,
      "  为本章补充案例  ",
      chapterId,
    );
    const second = await agent.applyIntent(
      generated.course,
      "  为本章补充案例  ",
      chapterId,
    );

    expect(first.course.chapters[1].lessons.at(-1)?.id).not.toBe(
      second.course.chapters[1].lessons.at(-1)?.id,
    );
    expect(first.receipt.inputDigest).toBe(second.receipt.inputDigest);
  });

  it("rejects case intent without an existing chapter", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);

    await expect(
      agent.applyIntent(generated.course, "补充案例"),
    ).rejects.toThrow("章节");
    await expect(
      agent.applyIntent(generated.course, "补充案例", "missing-chapter"),
    ).rejects.toThrow("章节");
  });

  it("rejects case intent without a ready course source", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const unavailableCourse: CourseDocument = {
      ...generated.course,
      sources: generated.course.sources.map((source) => ({
        ...source,
        status: "failed" as const,
      })),
    };

    await expect(
      agent.applyIntent(
        unavailableCourse,
        "补充案例",
        unavailableCourse.chapters[0].id,
      ),
    ).rejects.toThrow("就绪");
  });

  it("returns the exact course and validation receipt for source coverage", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);
    const expected = await validateCourse(generated.course);

    const result = await agent.applyIntent(generated.course, "  检查来源覆盖  ");

    expect(result.course).toBe(generated.course);
    expect(result.message).toBe("已完成来源覆盖检查");
    expect(result.receipt).toMatchObject({
      courseId: expected.courseId,
      kind: "validation",
      inputDigest: expected.inputDigest,
      summary: expected.summary,
      checks: expected.checks,
    });
    expect(result.receipt.id).toMatch(/^evidence-/);
    expectIsoTimestamp(result.receipt.createdAt);
  });

  it("returns the exact course and an unrecognized-intent receipt for unknown text", async () => {
    const agent = new LocalCourseAgent();
    const generated = await agent.generate(brief, sources);

    const result = await agent.applyIntent(generated.course, "  随便看看  ");

    expect(result.course).toBe(generated.course);
    expect(result.message).toBe(
      "我没有修改课程。可以尝试“缩短课程到 90 分钟”“为本章补充案例”或“检查来源覆盖”。",
    );
    expect(result.receipt).toMatchObject({
      courseId: generated.course.id,
      kind: "generation",
      inputDigest: await stableDigest({
        course: generated.course,
        intent: "随便看看",
      }),
      summary: "未识别可执行意图，课程未修改。",
      checks: [
        {
          id: "intent-unrecognized",
          level: "warning",
          message: "未识别可执行意图。",
        },
      ],
    });
    expect(result.receipt.id).toMatch(/^evidence-/);
    expectIsoTimestamp(result.receipt.createdAt);
  });
});

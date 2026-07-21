import { describe, expect, it } from "vitest";

import { createEmptyCourse } from "./course";
import {
  personalCourseResponseSchema,
  personalCourseViewSchema,
  projectPersonalCourseView,
} from "./personal-course-schema";


describe("personal course public projection", () => {
  it.each([
    ["queued", "creating", "准备创建课程"],
    ["importing", "creating", "正在读取资料"],
    ["organizing_knowledge", "creating", "正在整理知识"],
    ["composing", "creating", "正在编排课程"],
    ["assigning_visuals", "creating", "正在匹配真实图形"],
    ["validating", "creating", "正在验证课程"],
    ["needs_attention", "needs-attention", "需要你的确认"],
    ["ready", "ready", "课程已就绪"],
    ["failed", "failed", "创建未完成"],
  ] as const)("maps %s to one concise personal view", (status, publicStatus, phaseLabel) => {
    expect(
      projectPersonalCourseView({
        status,
        title: null,
        chapterCount: 0,
        attentionCount: status === "needs_attention" ? 1 : 0,
        canResume: status === "needs_attention",
        course: null,
      }),
    ).toEqual({
      status: publicStatus,
      phaseLabel,
      title: null,
      chapterCount: 0,
      attentionCount: status === "needs_attention" ? 1 : 0,
      canResume: status === "needs_attention",
      course: null,
    });
  });

  it("keeps run ID only in the service response wrapper", () => {
    const view = projectPersonalCourseView({
      status: "ready",
      title: "AI 产品实战",
      chapterCount: 0,
      attentionCount: 0,
      canResume: false,
      course: {
        ...createEmptyCourse(),
        audience: "产品团队",
        goal: "掌握可验证的 AI 产品方法",
      },
    });

    expect(
      personalCourseResponseSchema.parse({
        runId: `personal-run-${"a".repeat(32)}`,
        view,
      }),
    ).toEqual({ runId: `personal-run-${"a".repeat(32)}`, view });
    expect(() =>
      personalCourseViewSchema.parse({
        ...view,
        runId: `personal-run-${"a".repeat(32)}`,
      }),
    ).toThrow();
    expect(() =>
      personalCourseViewSchema.parse({
        ...view,
        courseVersionId: "course-v1",
        contentDigest: "b".repeat(64),
      }),
    ).toThrow();
  });

  it("rejects malformed or extra response internals", () => {
    const view = projectPersonalCourseView({
      status: "queued",
      title: null,
      chapterCount: 0,
      attentionCount: 0,
      canResume: false,
      course: null,
    });

    expect(() =>
      personalCourseResponseSchema.parse({ runId: "personal-run-short", view }),
    ).toThrow();
    expect(() =>
      personalCourseResponseSchema.parse({
        runId: `personal-run-${"c".repeat(32)}`,
        view,
        requestDigest: "d".repeat(64),
      }),
    ).toThrow();
  });
});

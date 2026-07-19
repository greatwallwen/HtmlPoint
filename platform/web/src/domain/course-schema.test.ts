import { describe, expect, it } from "vitest";
import { createEmptyCourse, createId } from "./course";
import {
  courseDocumentSchema,
  sourceAssetSchema,
  validateForTeaching,
} from "./course-schema";

describe("courseDocumentSchema", () => {
  it("rejects whitespace-only extracted text without trimming valid source text", () => {
    const source = {
      id: "source-1",
      name: "notes.md",
      kind: "markdown",
      size: 3,
      status: "ready",
      addedAt: "2026-07-15T00:00:00.000Z",
    };

    expect(() => sourceAssetSchema.parse({
      ...source,
      extractedText: " \n\t ",
    })).toThrow();
    expect(sourceAssetSchema.parse({
      ...source,
      extractedText: "  source text  ",
    }).extractedText).toBe("  source text  ");
  });

  it("persists a failure reason only for failed sources", () => {
    const source = {
      id: "source-1",
      name: "broken.md",
      kind: "markdown",
      size: 12,
      addedAt: "2026-07-15T00:00:00.000Z",
    };

    expect(sourceAssetSchema.parse({
      ...source,
      status: "failed",
      failureReason: "无法读取文件：read failed",
    })).toMatchObject({
      status: "failed",
      failureReason: "无法读取文件：read failed",
    });
    expect(() => sourceAssetSchema.parse({
      ...source,
      status: "ready",
      failureReason: "stale failure",
    })).toThrow();
    expect(() => sourceAssetSchema.parse({
      ...source,
      status: "failed",
      failureReason: "   ",
    })).toThrow();
  });

  it("accepts a structured course and rejects an empty lesson title", () => {
    const course = {
      schemaVersion: 1,
      id: "course-1",
      title: "企业 AI 入门课",
      audience: "业务团队",
      goal: "理解 AI 并形成可执行工作流",
      durationMinutes: 120,
      chapters: [{
        id: "chapter-1",
        title: "为什么现在需要 AI",
        objective: "建立共同认知",
        lessons: [{
          id: "lesson-1",
          title: "AI 的发展与现状",
          summary: "识别关键趋势和能力边界",
          durationMinutes: 30,
          sourceIds: ["source-1"],
          status: "grounded",
        }],
      }],
      sources: [{
        id: "source-1",
        name: "趋势.md",
        kind: "markdown",
        size: 42,
        status: "ready",
        extractedText: "趋势材料",
        addedAt: "2026-07-15T00:00:00.000Z",
      }],
      updatedAt: "2026-07-15T00:00:00.000Z",
    };
    expect(courseDocumentSchema.parse(course).title).toBe("企业 AI 入门课");
    expect(() => courseDocumentSchema.parse({
      ...course,
      chapters: [{ ...course.chapters[0], lessons: [{ ...course.chapters[0].lessons[0], title: "" }] }],
    })).toThrow();
  });

  it("rejects an empty draft for teaching and returns a valid course", () => {
    const course = {
      schemaVersion: 1,
      id: "course-1",
      title: "企业 AI 入门课",
      audience: "业务团队",
      goal: "理解 AI 并形成可执行工作流",
      durationMinutes: 120,
      chapters: [{
        id: "chapter-1",
        title: "为什么现在需要 AI",
        objective: "建立共同认知",
        lessons: [{
          id: "lesson-1",
          title: "AI 的发展与现状",
          summary: "识别关键趋势和能力边界",
          durationMinutes: 30,
          sourceIds: ["source-1"],
          status: "grounded",
        }],
      }],
      sources: [{
        id: "source-1",
        name: "趋势.md",
        kind: "markdown",
        size: 42,
        status: "ready",
        extractedText: "趋势材料",
        addedAt: "2026-07-15T00:00:00.000Z",
      }],
      updatedAt: "2026-07-15T00:00:00.000Z",
    };

    expect(() => validateForTeaching({ ...course, chapters: [] })).toThrow();
    expect(() => validateForTeaching({
      ...course,
      chapters: [{ ...course.chapters[0], lessons: [] }],
    })).toThrow();
    expect(validateForTeaching(course).id).toBe("course-1");
  });
});

describe("course constructors", () => {
  it("creates an ID with the requested prefix", () => {
    expect(createId("source")).toMatch(/^source-[0-9a-f-]{36}$/);
  });

  it("creates the exact empty-course defaults", () => {
    const course = createEmptyCourse("2026-07-15T00:00:00.000Z");

    expect(course).toMatchObject({
      schemaVersion: 1,
      title: "未命名课程",
      audience: "",
      goal: "",
      durationMinutes: 90,
      chapters: [],
      sources: [],
      updatedAt: "2026-07-15T00:00:00.000Z",
    });
    expect(course.id).toMatch(/^course-[0-9a-f-]{36}$/);
  });
});

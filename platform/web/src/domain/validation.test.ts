import { describe, expect, it } from "vitest";

import type { CourseDocument, EvidenceReceipt } from "./course";
import { stableDigest, validateCourse } from "./validation";

type Lesson = CourseDocument["chapters"][number]["lessons"][number];

const NOW = "2026-07-15T00:00:00.000Z";

const makeLesson = (overrides: Partial<Lesson> = {}): Lesson => ({
  id: "lesson-1",
  title: "第一课",
  summary: "课程摘要",
  durationMinutes: 30,
  sourceIds: ["source-ready"],
  status: "grounded",
  ...overrides,
});

const makeCourse = (overrides: Partial<CourseDocument> = {}): CourseDocument => ({
  schemaVersion: 1,
  id: "course-1",
  title: "AI 入门",
  audience: "业务团队",
  goal: "理解 AI 基础",
  durationMinutes: 30,
  chapters: [
    {
      id: "chapter-1",
      title: "基础",
      objective: "建立共同语言",
      lessons: [makeLesson()],
    },
  ],
  sources: [
    {
      id: "source-ready",
      name: "讲义",
      kind: "note",
      size: 12,
      status: "ready",
      extractedText: "证据",
      addedAt: NOW,
    },
  ],
  updatedAt: NOW,
  ...overrides,
});

const checkLevel = (receipt: EvidenceReceipt, id: string) =>
  receipt.checks.find((check) => check.id === id)?.level;

const expectChineseMessages = (receipt: EvidenceReceipt) => {
  for (const check of receipt.checks) {
    expect(check.message.trim()).not.toBe("");
    expect(check.message).toMatch(/[\u3400-\u9fff]/);
  }
};

describe("stableDigest", () => {
  it("sorts keys with deterministic lexicographic ordering", async () => {
    const expectedBytes = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode('{"Z":1,"a":2}'),
    );
    const expectedDigest = Array.from(new Uint8Array(expectedBytes), (byte) =>
      byte.toString(16).padStart(2, "0"),
    ).join("");

    await expect(stableDigest({ a: 2, Z: 1 })).resolves.toBe(expectedDigest);
  });

  it("is stable across recursive object key insertion order", async () => {
    const first = {
      z: [{ beta: 2, alpha: 1 }],
      a: { right: true, left: false },
    };
    const second = {
      a: { left: false, right: true },
      z: [{ alpha: 1, beta: 2 }],
    };

    const firstDigest = await stableDigest(first);

    expect(firstDigest).toBe(await stableDigest(second));
    expect(firstDigest).toMatch(/^[a-f0-9]{64}$/);
  });

  it("preserves array order when hashing", async () => {
    await expect(stableDigest({ values: [1, 2, 3] })).resolves.not.toBe(
      await stableDigest({ values: [3, 2, 1] }),
    );
  });
});

describe("validateCourse", () => {
  it("returns an all-pass validation receipt for a fully valid course", async () => {
    const course = makeCourse();

    const receipt = await validateCourse(course);

    expect(receipt.id).toMatch(/^evidence-/);
    expect(receipt.courseId).toBe(course.id);
    expect(receipt.kind).toBe("validation");
    expect(new Date(receipt.createdAt).toISOString()).toBe(receipt.createdAt);
    expect(receipt.inputDigest).toBe(await stableDigest(course));
    expect(receipt.inputDigest).toMatch(/^[a-f0-9]{64}$/);
    expect(receipt.summary).toBe("课程校验完成：0 个错误，0 个警告。");
    expect(receipt.checks.every((check) => check.level === "pass")).toBe(true);
    expect(receipt.checks.map((check) => check.id)).toEqual(
      expect.arrayContaining([
        "course-title",
        "course-audience",
        "course-goal",
        "chapter-count",
        "chapter:chapter-1:lesson-count",
        "lesson:lesson-1:title",
        "lesson:lesson-1:summary",
        "lesson:lesson-1:duration",
        "chapter:chapter-1:duplicate-titles",
        "lesson:lesson-1:source:source-ready",
        "source-coverage",
        "course-duration",
        "duration-alignment",
      ]),
    );
    expectChineseMessages(receipt);
  });

  it("reports blank metadata and an absent chapter", async () => {
    const receipt = await validateCourse(
      makeCourse({ title: " \t", audience: "\n", goal: "  ", chapters: [] }),
    );

    expect(checkLevel(receipt, "course-title")).toBe("error");
    expect(checkLevel(receipt, "course-audience")).toBe("error");
    expect(checkLevel(receipt, "course-goal")).toBe("error");
    expect(checkLevel(receipt, "chapter-count")).toBe("error");
    expect(checkLevel(receipt, "source-coverage")).toBe("warning");
    expect(checkLevel(receipt, "duration-alignment")).toBe("warning");
    expect(receipt.summary).toBe("课程校验完成：4 个错误，2 个警告。");
    expectChineseMessages(receipt);
  });

  it("reports an empty chapter and invalid lesson fields", async () => {
    const badLesson = makeLesson({
      id: "lesson-bad",
      title: "  ",
      summary: "\t",
      durationMinutes: 4,
    });
    const receipt = await validateCourse(
      makeCourse({
        durationMinutes: 4,
        chapters: [
          {
            id: "chapter-empty",
            title: "空章节",
            objective: "验证空章节",
            lessons: [],
          },
          {
            id: "chapter-bad",
            title: "异常章节",
            objective: "验证字段",
            lessons: [badLesson],
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "chapter:chapter-empty:lesson-count")).toBe("error");
    expect(checkLevel(receipt, "lesson:lesson-bad:title")).toBe("error");
    expect(checkLevel(receipt, "lesson:lesson-bad:summary")).toBe("error");
    expect(checkLevel(receipt, "lesson:lesson-bad:duration")).toBe("error");
  });

  it("detects duplicate lesson titles after trimming and case folding", async () => {
    const receipt = await validateCourse(
      makeCourse({
        durationMinutes: 60,
        chapters: [
          {
            id: "chapter-duplicate",
            title: "重复标题",
            objective: "验证去重",
            lessons: [
              makeLesson({ id: "lesson-a", title: "  AI Basics  " }),
              makeLesson({ id: "lesson-b", title: "ai basics" }),
            ],
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "chapter:chapter-duplicate:duplicate-titles")).toBe(
      "error",
    );
  });

  it("reports missing and non-ready source references separately", async () => {
    const receipt = await validateCourse(
      makeCourse({
        chapters: [
          {
            id: "chapter-1",
            title: "来源",
            objective: "验证来源",
            lessons: [
              makeLesson({
                sourceIds: ["source-missing", "source-reading", "source-ready"],
              }),
            ],
          },
        ],
        sources: [
          ...makeCourse().sources,
          {
            id: "source-reading",
            name: "解析中资料",
            kind: "text",
            size: 8,
            status: "reading",
            addedAt: NOW,
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "lesson:lesson-1:source:source-missing")).toBe(
      "error",
    );
    expect(checkLevel(receipt, "lesson:lesson-1:source:source-reading")).toBe(
      "error",
    );
    expect(checkLevel(receipt, "lesson:lesson-1:source:source-ready")).toBe("pass");
    expect(checkLevel(receipt, "source-coverage")).toBe("pass");
  });

  it("warns at exactly 69 percent source coverage", async () => {
    const lessons = Array.from({ length: 100 }, (_, index) =>
      makeLesson({
        id: `lesson-${index + 1}`,
        title: `第 ${index + 1} 课`,
        durationMinutes: 5,
        sourceIds: index < 69 ? ["source-ready"] : [],
        status: index < 69 ? "grounded" : "needs-source",
      }),
    );
    const receipt = await validateCourse(
      makeCourse({
        durationMinutes: 500,
        chapters: [
          {
            id: "chapter-coverage",
            title: "覆盖率",
            objective: "验证阈值",
            lessons,
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "source-coverage")).toBe("warning");
  });

  it("passes at exactly 70 percent source coverage", async () => {
    const lessons = Array.from({ length: 10 }, (_, index) =>
      makeLesson({
        id: `lesson-${index + 1}`,
        title: `第 ${index + 1} 课`,
        durationMinutes: 5,
        sourceIds: index < 7 ? ["source-ready"] : [],
        status: index < 7 ? "grounded" : "needs-source",
      }),
    );
    const receipt = await validateCourse(
      makeCourse({
        durationMinutes: 50,
        chapters: [
          {
            id: "chapter-coverage",
            title: "覆盖率",
            objective: "验证阈值",
            lessons,
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "source-coverage")).toBe("pass");
  });

  it("passes at exactly ten percent duration deviation", async () => {
    const receipt = await validateCourse(
      makeCourse({
        durationMinutes: 100,
        chapters: [
          {
            id: "chapter-duration",
            title: "时长",
            objective: "验证边界",
            lessons: [makeLesson({ durationMinutes: 90 })],
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "duration-alignment")).toBe("pass");
  });

  it("warns above ten percent duration deviation", async () => {
    const receipt = await validateCourse(
      makeCourse({
        durationMinutes: 100,
        chapters: [
          {
            id: "chapter-duration",
            title: "时长",
            objective: "验证边界",
            lessons: [makeLesson({ durationMinutes: 89 })],
          },
        ],
      }),
    );

    expect(checkLevel(receipt, "duration-alignment")).toBe("warning");
  });

  it("reports a non-positive target and keeps alignment as a warning", async () => {
    const receipt = await validateCourse(makeCourse({ durationMinutes: 0 }));

    expect(checkLevel(receipt, "course-duration")).toBe("error");
    expect(checkLevel(receipt, "duration-alignment")).toBe("warning");
    expect(receipt.summary).toBe("课程校验完成：1 个错误，1 个警告。");
    expectChineseMessages(receipt);
  });
});

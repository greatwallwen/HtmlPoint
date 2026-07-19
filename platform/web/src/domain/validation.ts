import {
  createId,
  type CourseDocument,
  type EvidenceReceipt,
  type ValidationLevel,
} from "./course";

type ValidationCheck = EvidenceReceipt["checks"][number];

const canonicalize = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }

  if (value !== null && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return Object.keys(object)
      .sort()
      .reduce<Record<string, unknown>>((sorted, key) => {
        sorted[key] = canonicalize(object[key]);
        return sorted;
      }, {});
  }

  return value;
};

export async function stableDigest(value: unknown): Promise<string> {
  const serialized = JSON.stringify(canonicalize(value));
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(serialized),
  );

  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export async function validateCourse(
  course: CourseDocument,
): Promise<EvidenceReceipt> {
  const checks: ValidationCheck[] = [];
  const addCheck = (
    id: string,
    satisfied: boolean,
    passMessage: string,
    failMessage: string,
    failLevel: Exclude<ValidationLevel, "pass"> = "error",
  ) => {
    checks.push({
      id,
      level: satisfied ? "pass" : failLevel,
      message: satisfied ? passMessage : failMessage,
    });
  };

  addCheck(
    "course-title",
    course.title.trim().length > 0,
    "课程标题已填写。",
    "课程标题不能为空。",
  );
  addCheck(
    "course-audience",
    course.audience.trim().length > 0,
    "课程受众已填写。",
    "课程受众不能为空。",
  );
  addCheck(
    "course-goal",
    course.goal.trim().length > 0,
    "课程目标已填写。",
    "课程目标不能为空。",
  );
  addCheck(
    "chapter-count",
    course.chapters.length > 0,
    "课程至少包含一个章节。",
    "课程必须至少包含一个章节。",
  );

  const sourceById = new Map(course.sources.map((source) => [source.id, source]));
  const lessons = course.chapters.flatMap((chapter) => chapter.lessons);

  for (const chapter of course.chapters) {
    addCheck(
      `chapter:${chapter.id}:lesson-count`,
      chapter.lessons.length > 0,
      `章节“${chapter.title}”至少包含一节课。`,
      `章节“${chapter.title}”必须至少包含一节课。`,
    );

    const normalizedTitles = chapter.lessons.map((lesson) =>
      lesson.title.trim().toLowerCase(),
    );
    const hasDuplicateTitle =
      new Set(normalizedTitles).size !== normalizedTitles.length;
    addCheck(
      `chapter:${chapter.id}:duplicate-titles`,
      !hasDuplicateTitle,
      `章节“${chapter.title}”没有重复课节标题。`,
      `章节“${chapter.title}”存在重复课节标题。`,
    );

    for (const lesson of chapter.lessons) {
      addCheck(
        `lesson:${lesson.id}:title`,
        lesson.title.trim().length > 0,
        "课节标题已填写。",
        "课节标题不能为空。",
      );
      addCheck(
        `lesson:${lesson.id}:summary`,
        lesson.summary.trim().length > 0,
        "课节摘要已填写。",
        "课节摘要不能为空。",
      );
      addCheck(
        `lesson:${lesson.id}:duration`,
        Number.isInteger(lesson.durationMinutes) &&
          lesson.durationMinutes >= 5 &&
          lesson.durationMinutes <= 90,
        "课节时长在五到九十分钟范围内。",
        "课节时长必须是五到九十分钟的整数。",
      );

      for (const sourceId of lesson.sourceIds) {
        const source = sourceById.get(sourceId);
        addCheck(
          `lesson:${lesson.id}:source:${sourceId}`,
          source?.status === "ready",
          `课节引用的来源“${sourceId}”已就绪。`,
          source
            ? `课节引用的来源“${sourceId}”尚未就绪。`
            : `课节引用的来源“${sourceId}”不存在。`,
        );
      }
    }
  }

  const groundedLessonCount = lessons.filter((lesson) =>
    lesson.sourceIds.some((sourceId) => sourceById.get(sourceId)?.status === "ready"),
  ).length;
  const coverage = lessons.length === 0 ? 0 : groundedLessonCount / lessons.length;
  addCheck(
    "source-coverage",
    coverage >= 0.7,
    `就绪来源覆盖率为 ${(coverage * 100).toFixed(0)}%，达到要求。`,
    `就绪来源覆盖率为 ${(coverage * 100).toFixed(0)}%，低于七成。`,
    "warning",
  );

  const hasPositiveDuration = course.durationMinutes > 0;
  addCheck(
    "course-duration",
    hasPositiveDuration,
    "课程目标时长为正数。",
    "课程目标时长必须为正数。",
  );

  const lessonDuration = lessons.reduce(
    (total, lesson) => total + lesson.durationMinutes,
    0,
  );
  const durationAligned =
    hasPositiveDuration &&
    Math.abs(lessonDuration - course.durationMinutes) / course.durationMinutes <= 0.1;
  addCheck(
    "duration-alignment",
    durationAligned,
    "课节总时长与课程目标时长偏差不超过一成。",
    hasPositiveDuration
      ? "课节总时长与课程目标时长偏差超过一成。"
      : "课程目标时长无效，无法完成时长对齐。",
    "warning",
  );

  const errors = checks.filter((check) => check.level === "error").length;
  const warnings = checks.filter((check) => check.level === "warning").length;

  return {
    id: createId("evidence"),
    courseId: course.id,
    kind: "validation",
    createdAt: new Date().toISOString(),
    inputDigest: await stableDigest(course),
    summary: `课程校验完成：${errors} 个错误，${warnings} 个警告。`,
    checks,
  };
}

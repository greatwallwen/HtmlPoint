import { z } from "zod";

import { courseDocumentSchema } from "./course-schema";
import type { CourseDocument } from "./course";


export const personalCourseViewSchema = z
  .object({
    status: z.enum(["creating", "needs-attention", "ready", "failed"]),
    phaseLabel: z.string().min(1).max(80),
    title: z.string().min(1).max(200).nullable(),
    chapterCount: z.number().int().nonnegative(),
    attentionCount: z.number().int().nonnegative(),
    canResume: z.boolean(),
    course: courseDocumentSchema.nullable(),
  })
  .strict();

export const personalCourseResponseSchema = z
  .object({
    runId: z.string().regex(/^personal-run-[0-9a-f]{32}$/),
    view: personalCourseViewSchema,
  })
  .strict();

export type PersonalCourseInternalStatus =
  | "queued"
  | "importing"
  | "organizing_knowledge"
  | "composing"
  | "assigning_visuals"
  | "validating"
  | "needs_attention"
  | "ready"
  | "failed";

export interface PersonalCourseProjectionInput {
  status: PersonalCourseInternalStatus;
  title: string | null;
  chapterCount: number;
  attentionCount: number;
  canResume: boolean;
  course: CourseDocument | null;
}

export type PersonalCourseView = z.infer<typeof personalCourseViewSchema>;
export type PersonalCourseResponse = z.infer<typeof personalCourseResponseSchema>;

const phaseProjection: Record<
  PersonalCourseInternalStatus,
  Pick<PersonalCourseView, "status" | "phaseLabel">
> = {
  queued: { status: "creating", phaseLabel: "准备创建课程" },
  importing: { status: "creating", phaseLabel: "正在读取资料" },
  organizing_knowledge: { status: "creating", phaseLabel: "正在整理知识" },
  composing: { status: "creating", phaseLabel: "正在编排课程" },
  assigning_visuals: { status: "creating", phaseLabel: "正在匹配真实图形" },
  validating: { status: "creating", phaseLabel: "正在验证课程" },
  needs_attention: { status: "needs-attention", phaseLabel: "需要你的确认" },
  ready: { status: "ready", phaseLabel: "课程已就绪" },
  failed: { status: "failed", phaseLabel: "创建未完成" },
};

export function projectPersonalCourseView(
  input: PersonalCourseProjectionInput,
): PersonalCourseView {
  return personalCourseViewSchema.parse({
    ...phaseProjection[input.status],
    title: input.title,
    chapterCount: input.chapterCount,
    attentionCount: input.attentionCount,
    canResume: input.canResume,
    course: input.course,
  });
}

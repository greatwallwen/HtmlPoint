import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PersonalCourseView } from "../domain/personal-course-schema";
import { PersonalCourseHome } from "./PersonalCourseHome";

const view: PersonalCourseView = {
  status: "ready",
  phaseLabel: "课程已就绪",
  title: "个人 AI 工作流实战",
  chapterCount: 1,
  attentionCount: 0,
  canResume: false,
  course: {
    schemaVersion: 1,
    id: "personal-course",
    title: "个人 AI 工作流实战",
    audience: "个人学习者",
    goal: "建立可靠工作流",
    durationMinutes: 60,
    updatedAt: "2026-07-21T04:00:00Z",
    sources: [{ id: "source-1", name: "ai.md", kind: "markdown", size: 10, status: "ready", addedAt: "2026-07-21T04:00:00Z" }],
    chapters: [{ id: "chapter-1", title: "工作流", objective: "建立流程", lessons: [{ id: "lesson-1-1", title: "开始", summary: "可靠开始", durationMinutes: 60, sourceIds: ["source-1"], status: "grounded" }] }],
  },
};

it("shows useful actions without rendering opaque identifiers", async () => {
  const user = userEvent.setup();
  const edit = vi.fn();
  render(<PersonalCourseHome view={view} onEdit={edit} onTeach={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "个人 AI 工作流实战" })).toBeVisible();
  expect(document.body.textContent).not.toMatch(/personal-run-|course-version-|[0-9a-f]{64}/);
  await user.click(screen.getByRole("button", { name: "编辑课程" }));
  expect(edit).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "开始授课" })).toBeEnabled();
});

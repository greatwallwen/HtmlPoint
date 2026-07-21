import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PersonalCourseProgress } from "./PersonalCourseProgress";

it("renders only the persisted Helper phase", () => {
  render(
    <PersonalCourseProgress
      view={{ status: "creating", phaseLabel: "正在整理知识", title: null, chapterCount: 0, attentionCount: 0, canResume: false, course: null }}
    />,
  );
  expect(screen.getByRole("heading", { name: "正在整理知识" })).toBeVisible();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { CourseRequirementDraft } from "../domain/course-agent";
import { CourseRequirementPanel } from "./CourseRequirementPanel";

const initial: CourseRequirementDraft = {
  title: "AI 课程",
  audience: "产品团队",
  learningGoals: ["理解证据", "设计工作流"],
  durationMinutes: 90,
  requiredTagIds: [],
  excludedTagIds: [],
  usageScope: "private-training",
  includeCardVersionIds: [],
  excludeCardVersionIds: [],
  requireVisualRefs: false,
  requireDatasetRefs: false,
};

function Harness({ onSubmit }: { onSubmit(): void }) {
  const [value, setValue] = useState(initial);
  return (
    <CourseRequirementPanel
      value={value}
      tagOptions={[
        { id: "topic:evidence", label: "证据", dimension: "topic" },
      ]}
      submitLabel="组合课程大纲"
      onChange={setValue}
      onSubmit={onSubmit}
    />
  );
}

describe("CourseRequirementPanel", () => {
  it("edits every requirement dimension and keeps required/excluded tags disjoint", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.clear(screen.getByLabelText("课程名称"));
    await user.type(screen.getByLabelText("课程名称"), "个人 AI 训练");
    await user.clear(screen.getByLabelText("课程受众"));
    await user.type(screen.getByLabelText("课程受众"), "业务负责人");
    await user.clear(screen.getByLabelText("课程目标"));
    await user.type(screen.getByLabelText("课程目标"), "目标一{enter}目标二");
    await user.clear(screen.getByLabelText("课程时长（分钟）"));
    await user.type(screen.getByLabelText("课程时长（分钟）"), "120");
    await user.selectOptions(screen.getByLabelText("使用范围"), "internal");
    await user.click(screen.getByLabelText("需要真实视觉资料"));
    await user.click(screen.getByLabelText("需要数据集证据"));

    const required = screen.getByLabelText("必选");
    const excluded = screen.getByLabelText("排除");
    await user.click(required);
    expect(required).toBeChecked();
    expect(excluded).not.toBeChecked();
    await user.click(excluded);
    expect(required).not.toBeChecked();
    expect(excluded).toBeChecked();

    await user.click(screen.getByRole("button", { name: "组合课程大纲" }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("课程名称")).toHaveValue("个人 AI 训练");
    expect(screen.getByLabelText("课程目标")).toHaveValue("目标一\n目标二");
    expect(screen.getByLabelText("课程时长（分钟）")).toHaveValue(120);
    expect(screen.getByLabelText("使用范围")).toHaveValue("internal");
  });
});

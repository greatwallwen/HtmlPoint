import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PersonalCourseCreate } from "./PersonalCourseCreate";

describe("PersonalCourseCreate", () => {
  it("starts one course from selected files and one request", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn().mockResolvedValue(undefined);
    render(<PersonalCourseCreate onStart={onStart} />);
    const file = new File(["# AI"], "ai.md", { type: "text/markdown" });

    await user.upload(screen.getByLabelText("选择课程资料"), file);
    await user.type(
      screen.getByLabelText("你想做一门什么课？"),
      "为个人讲师制作 60 分钟 AI 工作流实战课",
    );
    await user.click(screen.getByRole("button", { name: "开始组课" }));

    expect(onStart).toHaveBeenCalledWith(
      [file],
      "为个人讲师制作 60 分钟 AI 工作流实战课",
    );
  });

  it("accepts a directory as one source selection", async () => {
    const user = userEvent.setup();
    render(<PersonalCourseCreate onStart={vi.fn()} />);
    const files = ["a.md", "b.md", "c.csv"].map(
      (name) => new File([name], name, { type: "text/plain" }),
    );

    await user.upload(screen.getByLabelText("选择资料目录"), files);

    expect(screen.getByText("已选择 3 个文件")).toBeVisible();
  });
});

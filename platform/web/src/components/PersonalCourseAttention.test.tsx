import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PersonalCourseAttention } from "./PersonalCourseAttention";

it("compresses attention into one decision surface", async () => {
  const user = userEvent.setup();
  const accept = vi.fn();
  render(<PersonalCourseAttention count={2} onAccept={accept} />);
  expect(screen.getByText("2 项问题已集中整理。")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "接受建议并继续" }));
  expect(accept).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "查看详情" }));
  expect(screen.getByText(/系统只在资料冲突/)).toBeVisible();
});

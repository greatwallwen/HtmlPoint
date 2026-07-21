import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HelperRequiredScreen } from "./HelperRequiredScreen";


describe("HelperRequiredScreen", () => {
  it("gives one concise recovery action without exposing internal details", () => {
    render(<HelperRequiredScreen />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveAccessibleName("请从课程工作台启动");
    expect(alert).toHaveTextContent("关闭此页面，然后双击“启动课程平台”重新打开。");
    expect(alert).not.toHaveTextContent(/token|nonce|127\.0\.0\.1|Helper/i);
  });
});

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { KnowledgeSummary } from "../domain/knowledge";
import { KnowledgePreparationPanel } from "./KnowledgePreparationPanel";

const summary: KnowledgeSummary = {
  schemaVersion: 1,
  sourceCount: 5,
  publishedCardCount: 12,
  reviewTaskCount: 0,
  retrievalMode: "hybrid",
  tagLabels: ["大语言模型", "数据分析", "Prompt 工程"],
  updatedAt: "2026-07-17T02:00:00Z",
};

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
});

describe("KnowledgePreparationPanel", () => {
  it("shows the normal hybrid knowledge preparation summary", async () => {
    const client = { getSummary: vi.fn().mockResolvedValue(summary) };

    render(<KnowledgePreparationPanel client={client} />);

    expect(await screen.findByText("12 张已发布知识卡")).toBeVisible();
    expect(screen.getByText("5 个知识来源")).toBeVisible();
    expect(screen.getByText("混合检索已就绪")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("知识准备已完成");
    expect(screen.getByRole("status")).toHaveClass("knowledge-status--ready");
    expect(screen.getByText("Prompt 工程")).toBeVisible();
  });

  it("shows published cards, governed tags, and degraded retrieval without blocking file import", async () => {
    const client = {
      getSummary: vi.fn().mockResolvedValue({
        ...summary,
        reviewTaskCount: 2,
        retrievalMode: "fts-degraded" as const,
      }),
    };

    render(<KnowledgePreparationPanel client={client} />);

    expect(await screen.findByText("12 张已发布知识卡")).toBeVisible();
    expect(screen.getByText("全文检索模式")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("2 项待审核");
    expect(screen.getByText("大语言模型")).toBeVisible();
    expect(screen.getByText("数据分析")).toBeVisible();
  });

  it("keeps the import workflow usable when the helper is offline", async () => {
    const client = { getSummary: vi.fn().mockRejectedValue(new Error("secret")) };

    render(<KnowledgePreparationPanel client={client} />);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "本地知识服务未连接",
    );
    const retry = screen.getByRole("button", { name: "重试连接" });
    expect(retry).toBeEnabled();
    expect(retry).toHaveAttribute("title", "重试连接");
    expect(retry.querySelector("svg")).not.toBeNull();
    expect(screen.queryByText("secret")).toBeNull();
  });

  it("retries from offline state with an accessible disabled loading control", async () => {
    const user = userEvent.setup();
    const retryResult = deferred<KnowledgeSummary>();
    const client = {
      getSummary: vi
        .fn()
        .mockRejectedValueOnce(new Error("offline"))
        .mockReturnValueOnce(retryResult.promise),
    };
    render(<KnowledgePreparationPanel client={client} />);
    const retry = await screen.findByRole("button", { name: "重试连接" });

    await user.click(retry);

    expect(retry).toBeDisabled();
    expect(retry).toHaveAccessibleName("重试连接");
    expect(screen.getByRole("status")).toHaveTextContent("正在连接本地知识服务");
    retryResult.resolve(summary);
    expect(await screen.findByText("混合检索已就绪")).toBeVisible();
    expect(client.getSummary).toHaveBeenCalledTimes(2);
  });

  it("stays safely offline when no verified client is available", () => {
    render(<KnowledgePreparationPanel />);

    expect(screen.getByRole("region", { name: "知识准备" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("本地知识服务未连接");
    expect(screen.queryByRole("button", { name: "重试连接" })).toBeNull();
  });
});

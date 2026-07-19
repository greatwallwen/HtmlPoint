import { describe, expect, it } from "vitest";

import {
  MAX_SOURCE_BYTES,
  readSourceFiles,
  sourceKindForName,
} from "./source-import";

const textFile = (contents: string, name: string): File => {
  const file = new File([contents], name);
  Object.defineProperty(file, "text", {
    value: () => Promise.resolve(contents),
  });
  return file;
};

describe("source import", () => {
  it("reads markdown exactly and keeps PPTX metadata-only", async () => {
    const markdownText = "# Course\n\nExact source text.";
    const markdown = textFile(markdownText, "course.md");
    const presentation = new File([new Uint8Array([1, 2, 3])], "deck.pptx");

    const assets = await readSourceFiles([markdown, presentation]);

    expect(assets).toHaveLength(2);
    expect(assets[0]).toMatchObject({
      name: "course.md",
      kind: "markdown",
      size: markdown.size,
      status: "ready",
      extractedText: markdownText,
    });
    expect(assets[1]).toMatchObject({
      name: "deck.pptx",
      kind: "pptx",
      size: presentation.size,
      status: "ready",
    });
    expect(assets[1].extractedText).toBeUndefined();
  });

  it.each([
    ["empty", ""],
    ["whitespace-only", " \n\t "],
  ])("marks %s text as failed instead of ready", async (_label, contents) => {
    const [asset] = await readSourceFiles([
      textFile(contents, "empty-course.md"),
    ]);

    expect(asset).toMatchObject({
      name: "empty-course.md",
      kind: "markdown",
      status: "failed",
      failureReason: "文件为空或仅含空白字符，请添加内容后重试。",
    });
    expect(asset.extractedText).toBeUndefined();
  });

  it("classifies supported extensions case-insensitively", () => {
    expect(sourceKindForName("lesson.MARKDOWN")).toBe("markdown");
    expect(sourceKindForName("notes.TXT")).toBe("text");
    expect(sourceKindForName("handout.PDF")).toBe("pdf");
    expect(sourceKindForName("slides.PPTX")).toBe("pptx");
    expect(sourceKindForName("workbook.DOCX")).toBe("docx");
  });

  it("fails an oversized file while preserving a ready sibling and input order", async () => {
    const oversized = new File(
      [new Uint8Array(MAX_SOURCE_BYTES + 1)],
      "oversized.PDF",
    );
    const valid = textFile("still readable", "valid.txt");

    const assets = await readSourceFiles([oversized, valid]);

    expect(assets.map(({ name, kind, status }) => ({ name, kind, status }))).toEqual([
      { name: "oversized.PDF", kind: "pdf", status: "failed" },
      { name: "valid.txt", kind: "text", status: "ready" },
    ]);
    expect(assets[1].extractedText).toBe("still readable");
  });

  it("marks an unknown ZIP as an unsupported note", async () => {
    const [asset] = await readSourceFiles([new File(["zip"], "archive.zip")]);

    expect(asset).toMatchObject({
      name: "archive.zip",
      kind: "note",
      status: "unsupported",
    });
    expect(asset.extractedText).toBeUndefined();
  });

  it("keeps an oversized unknown ZIP unsupported instead of failed", async () => {
    const archive = new File(
      [new Uint8Array(MAX_SOURCE_BYTES + 1)],
      "archive.zip",
    );

    const [asset] = await readSourceFiles([archive]);

    expect(asset).toMatchObject({
      name: "archive.zip",
      kind: "note",
      size: archive.size,
      status: "unsupported",
    });
    expect(asset.extractedText).toBeUndefined();
  });

  it("contains a text read rejection and continues with the following file", async () => {
    const rejectedFile = {
      name: "broken.md",
      size: 12,
      text: () => Promise.reject(new Error("read failed")),
    } as unknown as File;
    const readableFile = textFile("recovered", "following.txt");

    const assets = await readSourceFiles([rejectedFile, readableFile]);

    expect(assets).toHaveLength(2);
    expect(assets[0]).toMatchObject({
      name: "broken.md",
      kind: "markdown",
      status: "failed",
      failureReason: "无法读取文件：read failed",
    });
    expect(assets[0].extractedText).toBeUndefined();
    expect(assets[1]).toMatchObject({
      name: "following.txt",
      kind: "text",
      status: "ready",
      extractedText: "recovered",
    });
  });

  it("creates source IDs and parseable ISO timestamps", async () => {
    const assets = await readSourceFiles([
      textFile("one", "one.md"),
      new File([new Uint8Array([1])], "two.docx"),
    ]);

    for (const asset of assets) {
      expect(asset.id).toMatch(/^source-/);
      expect(asset.addedAt).not.toBe("");
      expect(new Date(asset.addedAt).toISOString()).toBe(asset.addedAt);
    }
  });
});

import { createId, type SourceAsset, type SourceKind } from "./course";

export const MAX_SOURCE_BYTES = 20 * 1024 * 1024;

const SOURCE_KINDS_BY_EXTENSION: Record<string, SourceKind> = {
  md: "markdown",
  markdown: "markdown",
  txt: "text",
  pdf: "pdf",
  pptx: "pptx",
  docx: "docx",
};

export function sourceKindForName(name: string): SourceKind | undefined {
  const extension = name.toLowerCase().match(/\.([^.]+)$/)?.[1];
  return extension ? SOURCE_KINDS_BY_EXTENSION[extension] : undefined;
}

function failSource(asset: SourceAsset, reason: string): SourceAsset {
  return { ...asset, status: "failed", failureReason: reason };
}

export function sourceFailureReason(source: SourceAsset): string | undefined {
  if (source.status !== "failed") {
    return undefined;
  }

  if (source.failureReason) {
    return source.failureReason;
  }
  if (source.size > MAX_SOURCE_BYTES) {
    return "文件超过 20 MB，请选择较小文件后重试。";
  }
  if (source.kind === "markdown" || source.kind === "text") {
    return "未读取到可用文本，请检查文件内容后重试。";
  }
  return "文件读取失败，请重新选择后重试。";
}

async function readSourceFile(file: File): Promise<SourceAsset> {
  const recognizedKind = sourceKindForName(file.name);
  const asset: SourceAsset = {
    id: createId("source"),
    name: file.name,
    kind: recognizedKind ?? "note",
    size: file.size,
    status: "ready",
    addedAt: new Date().toISOString(),
  };

  if (!recognizedKind) {
    return { ...asset, status: "unsupported" };
  }

  if (file.size > MAX_SOURCE_BYTES) {
    return failSource(asset, "文件超过 20 MB，请选择较小文件后重试。");
  }

  if (recognizedKind === "markdown" || recognizedKind === "text") {
    try {
      const extractedText = await file.text();
      if (extractedText.trim().length === 0) {
        return failSource(asset, "文件为空或仅含空白字符，请添加内容后重试。");
      }
      return { ...asset, extractedText };
    } catch (error: unknown) {
      const detail = error instanceof Error ? error.message.trim() : "";
      return failSource(
        asset,
        detail ? `无法读取文件：${detail}` : "无法读取文件，请重新选择后重试。",
      );
    }
  }

  return asset;
}

export function readSourceFiles(files: Iterable<File>): Promise<SourceAsset[]> {
  return Promise.all(Array.from(files, readSourceFile));
}

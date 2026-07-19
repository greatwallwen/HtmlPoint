import { ArrowClockwise, ClipboardText, Trash, UploadSimple } from "@phosphor-icons/react";
import { useRef, useState, type ChangeEvent, type DragEvent } from "react";

import type { SourceAsset, SourceKind } from "../domain/course";
import type { KnowledgeSummaryClient } from "../domain/knowledge";
import { createImportStartJob } from "../domain/governed-job-factory";
import { sourceFailureReason } from "../domain/source-import";
import { KnowledgeClient } from "../services/knowledge-client";
import { useWorkspace } from "../state/workspace";
import { KnowledgePreparationPanel } from "./KnowledgePreparationPanel";
import { KnowledgeReviewDrawer } from "./KnowledgeReviewDrawer";

const kindLabels: Record<SourceKind, string> = {
  markdown: "Markdown",
  text: "纯文本",
  pdf: "PDF",
  pptx: "PowerPoint",
  docx: "Word",
  web: "网页",
  note: "未知格式",
};

const statusLabels: Record<SourceAsset["status"], string> = {
  queued: "等待读取",
  reading: "正在读取",
  ready: "可用",
  unsupported: "暂不支持",
  failed: "读取失败",
};

function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export interface ImportStepProps {
  knowledgeClient?: KnowledgeSummaryClient;
}

export function ImportStep({ knowledgeClient }: ImportStepProps) {
  const { state, dispatch, importFiles } = useWorkspace();
  const [pendingImports, setPendingImports] = useState(0);
  const [governedImports, setGovernedImports] = useState<Array<{
    id: string;
    name: string;
    phase: "uploading" | "processing" | "ready" | "failed";
    importId?: string;
    sourceVersionId?: string;
    message?: string;
  }>>([]);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [knowledgeRefresh, setKnowledgeRefresh] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const reviewTriggerRef = useRef<HTMLButtonElement>(null);
  const retryFilesRef = useRef(new Map<string, File>());
  const replacementSourceIdRef = useRef<string | null>(null);
  const isImporting = pendingImports > 0;
  const hasReadySource =
    state.course.sources.some((source) => source.status === "ready") ||
    governedImports.some((item) => item.phase === "ready");
  const governedClient =
    knowledgeClient instanceof KnowledgeClient ? knowledgeClient : undefined;

  const governedMediaType = (file: File): string => {
    if (file.type.trim()) return file.type;
    const extension = file.name.split(".").pop()?.toLowerCase();
    return {
      md: "text/markdown",
      markdown: "text/markdown",
      pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      csv: "text/csv",
      parquet: "application/vnd.apache.parquet",
      xls: "application/vnd.ms-excel",
      xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[extension ?? ""] ?? "application/octet-stream";
  };

  const processGovernedFile = async (file: File): Promise<void> => {
    if (!governedClient) return;
    const id = crypto.randomUUID();
    setGovernedImports((items) => [...items, { id, name: file.name, phase: "uploading" }]);
    try {
      const typedFile = file.type.trim()
        ? file
        : file.slice(0, file.size, governedMediaType(file));
      const upload = await governedClient.uploadSource(typedFile, file.name);
      setGovernedImports((items) => items.map((item) =>
        item.id === id ? { ...item, phase: "processing" } : item,
      ));
      const imported = await governedClient.startImport(await createImportStartJob(upload));
      dispatch({
        type: "REGISTER_GOVERNED_ASSETS",
        assets: {
          sourceVisuals: imported.result.visualVersionIds.map((visualVersionId, index) => ({
            visualVersionId,
            sourceVersionId: imported.result.sourceVersionId,
            label: `${file.name} · 图形 ${index + 1}`,
          })),
          datasetVersionIds: imported.result.datasetVersionIds,
          datasetProfiles: imported.result.datasetProfiles,
        },
      });
      setGovernedImports((items) => items.map((item) =>
        item.id === id
          ? {
              ...item,
              phase: "ready",
              importId: imported.result.importId,
              sourceVersionId: imported.result.sourceVersionId,
              message: `${imported.result.candidateCardVersionIds.length} 张候选卡 · ${imported.result.reviewTaskIds.length} 项审核`,
            }
          : item,
      ));
      setKnowledgeRefresh((value) => value + 1);
    } catch {
      setGovernedImports((items) => items.map((item) =>
        item.id === id
          ? { ...item, phase: "failed", message: "受控导入失败；本地预览不会被当作已发布知识。" }
          : item,
      ));
    }
  };

  const processFiles = async (files: Iterable<File>): Promise<SourceAsset[]> => {
    const fileList = Array.from(files);
    if (fileList.length === 0) {
      return [];
    }
    setPendingImports((pending) => pending + 1);
    try {
      const [sources] = await Promise.all([
        importFiles(fileList),
        Promise.all(fileList.map((file) => processGovernedFile(file))),
      ]);
      sources.forEach((source, index) => {
        const sourceFile = fileList[index];
        if (source.status === "failed" && sourceFile) {
          retryFilesRef.current.set(source.id, sourceFile);
        }
      });
      return sources;
    } finally {
      setPendingImports((pending) => Math.max(0, pending - 1));
    }
  };

  const retrySource = async (source: SourceAsset): Promise<void> => {
    const sourceFile = retryFilesRef.current.get(source.id);
    if (!sourceFile) {
      const input = inputRef.current;
      if (input) {
        replacementSourceIdRef.current = source.id;
        input.value = "";
        input.click();
      }
      return;
    }

    retryFilesRef.current.delete(source.id);
    dispatch({ type: "REMOVE_SOURCE", sourceId: source.id });
    await processFiles([sourceFile]);
  };

  const handleChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const files = input.files ?? [];
    if (files.length === 0) return;
    const replacementSourceId = replacementSourceIdRef.current;
    replacementSourceIdRef.current = null;
    if (replacementSourceId) {
      retryFilesRef.current.delete(replacementSourceId);
      dispatch({ type: "REMOVE_SOURCE", sourceId: replacementSourceId });
    }
    await processFiles(files);
    input.value = "";
  };

  const handleDrop = async (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    await processFiles(event.dataTransfer.files);
  };

  return (
    <main className="workflow-page">
      <section className="workflow-panel" aria-labelledby="import-heading">
        <div className="panel-heading">
          <p className="eyebrow">第 1 步</p>
          <h1 id="import-heading">导入课程资料</h1>
          <p>添加课程依据，系统会保留来源状态并用于生成课程结构。</p>
        </div>

        <label
          className="import-dropzone"
          aria-label="资料拖放区"
          onDragOver={(event) => event.preventDefault()}
          onDrop={handleDrop}
        >
          <input
            ref={inputRef}
            className="import-input"
            type="file"
            multiple
            aria-label="导入资料"
            accept=".md,.markdown,.txt,.pdf,.pptx,.docx,.csv,.parquet,.xls,.xlsx"
            onChange={handleChange}
          />
          <span className="dropzone-icon" aria-hidden="true">
            <UploadSimple size={28} weight="bold" />
          </span>
          <strong>选择文件或拖放到这里</strong>
          <span>支持 Markdown、文本、PDF、PPTX、DOCX、CSV、Parquet 和 Excel</span>
        </label>

        {isImporting ? (
          <p className="operation-status" role="status" aria-live="polite">
            正在读取资料…
          </p>
        ) : null}

        <ul className="source-list" aria-label="已导入资料">
          {state.course.sources.map((source) => (
              <li
                key={source.id}
                className="source-item"
                aria-label={`资料 ${source.name}`}
              >
                <div className="source-main">
                  <strong>{source.name}</strong>
                  <span>
                    {formatBytes(source.size)} · {kindLabels[source.kind]}
                  </span>
                  {source.status === "failed" ? (
                    <span role="alert">{sourceFailureReason(source)}</span>
                  ) : null}
                </div>
                <div className="source-actions">
                  <span className={`source-status status-${source.status}`}>
                    {statusLabels[source.status]}
                  </span>
                  {source.status === "failed" ? (
                    <button
                      type="button"
                      className="secondary-button"
                      aria-label={`重试读取 ${source.name}`}
                      onClick={() => void retrySource(source)}
                    >
                      <ArrowClockwise aria-hidden="true" size={18} weight="bold" />
                      重试
                    </button>
                  ) : null}
                </div>
                <button
                  type="button"
                  className="icon-button source-remove"
                  aria-label={`移除 ${source.name}`}
                  title={`移除 ${source.name}`}
                  onClick={() => {
                    retryFilesRef.current.delete(source.id);
                    if (replacementSourceIdRef.current === source.id) {
                      replacementSourceIdRef.current = null;
                    }
                    dispatch({ type: "REMOVE_SOURCE", sourceId: source.id });
                  }}
                >
                  <Trash aria-hidden="true" size={20} weight="bold" />
                </button>
              </li>
          ))}
        </ul>

        {governedImports.length > 0 ? (
          <section className="governed-imports" aria-labelledby="governed-imports-heading">
            <div className="panel-heading compact-heading">
              <h2 id="governed-imports-heading">受控导入进度</h2>
              <p>上传、解析、候选卡与审核均由本地 Helper 记录。</p>
            </div>
            <ul>
              {governedImports.map((item) => (
                <li key={item.id} className={`governed-import--${item.phase}`}>
                  <div><strong>{item.name}</strong><span>{item.message ?? (item.phase === "uploading" ? "正在上传" : item.phase === "processing" ? "正在解析与建立候选卡" : "导入完成")}</span></div>
                  <span>{item.importId ?? item.phase}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <KnowledgePreparationPanel client={knowledgeClient} refreshKey={knowledgeRefresh} />

        {governedClient ? (
          <button
            ref={reviewTriggerRef}
            type="button"
            className="secondary-button review-drawer-trigger"
            onClick={() => setReviewOpen(true)}
          >
            <ClipboardText aria-hidden="true" size={19} weight="bold" />
            审核与发布知识卡
          </button>
        ) : null}

        <div className="panel-actions">
          <button
            type="button"
            className="primary-button"
            disabled={!hasReadySource}
            onClick={() => dispatch({ type: "GO_TO_STEP", step: "generate" })}
          >
            下一步：生成课程
          </button>
        </div>
      </section>
      {governedClient ? (
        <KnowledgeReviewDrawer
          client={governedClient}
          open={reviewOpen}
          onClose={() => {
            setReviewOpen(false);
            queueMicrotask(() => reviewTriggerRef.current?.focus());
          }}
          onChanged={() => setKnowledgeRefresh((value) => value + 1)}
        />
      ) : null}
    </main>
  );
}

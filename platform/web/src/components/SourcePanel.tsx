import {
  FileText,
  GlobeHemisphereWest,
  ImageSquare,
  LinkBreak,
  LinkSimple,
  Note,
  X,
} from "@phosphor-icons/react";
import { useState, type JSX } from "react";

import type { LessonNode, SourceAsset, SourceKind } from "../domain/course";
import { ValidationPanel } from "./ValidationPanel";

type SourceFilter = "all" | "document" | "web" | "image" | "note";

interface SourcePanelProps {
  sources: SourceAsset[];
  selectedLesson?: LessonNode;
  drawerOpen: boolean;
  onToggleSource(sourceId: string): void;
  onCloseDrawer(): void;
}

const filters: Array<{ id: SourceFilter; label: string }> = [
  { id: "all", label: "全部" },
  { id: "document", label: "文档" },
  { id: "web", label: "网页" },
  { id: "image", label: "图片" },
  { id: "note", label: "笔记" },
];

const sourceTypeLabels: Record<SourceKind, string> = {
  markdown: "Markdown",
  text: "文本",
  pdf: "PDF",
  pptx: "PPTX",
  docx: "DOCX",
  web: "网页",
  note: "笔记",
};

function sourceCategory(kind: SourceKind): Exclude<SourceFilter, "all" | "image"> {
  if (kind === "web") {
    return "web";
  }
  if (kind === "note") {
    return "note";
  }
  return "document";
}

function sourceStatusLabel(status: SourceAsset["status"]): string {
  switch (status) {
    case "ready":
      return "可用";
    case "unsupported":
      return "暂不支持";
    case "failed":
      return "读取失败";
    case "queued":
      return "等待解析";
    case "reading":
      return "读取中";
  }
}

function sourceIcon(kind: SourceKind): JSX.Element {
  if (kind === "web") {
    return <GlobeHemisphereWest aria-hidden="true" size={24} weight="duotone" />;
  }
  if (kind === "note") {
    return <Note aria-hidden="true" size={24} weight="duotone" />;
  }
  return <FileText aria-hidden="true" size={24} weight="duotone" />;
}

export function SourcePanel({
  sources,
  selectedLesson,
  drawerOpen,
  onToggleSource,
  onCloseDrawer,
}: SourcePanelProps) {
  const [filter, setFilter] = useState<SourceFilter>("all");
  const filteredSources = sources.filter(
    (source) => filter === "all" || sourceCategory(source.kind) === filter,
  );
  const linkedCount = selectedLesson?.sourceIds.length ?? 0;

  return (
    <aside
      id="course-source-panel"
      className={`source-panel${drawerOpen ? " is-drawer-open" : ""}`}
      role="region"
      aria-labelledby="source-panel-heading"
      data-drawer-open={drawerOpen ? "true" : "false"}
    >
      <header className="editor-panel-heading source-panel__heading">
        <div>
          <p className="editor-panel-kicker">课程依据</p>
          <h2 id="source-panel-heading">证据与来源</h2>
        </div>
        <button
          type="button"
          className="icon-button editor-icon-button source-panel__close"
          aria-label="关闭证据与来源"
          title="关闭证据与来源"
          onClick={onCloseDrawer}
        >
          <X aria-hidden="true" size={20} weight="bold" />
        </button>
      </header>

      <div className="source-filter-tabs" role="group" aria-label="来源类型">
        {filters.map((item) => (
          <button
            key={item.id}
            type="button"
            className={filter === item.id ? "is-selected" : undefined}
            aria-pressed={filter === item.id ? "true" : "false"}
            onClick={() => setFilter(item.id)}
          >
            {item.id === "image" ? (
              <ImageSquare aria-hidden="true" size={16} />
            ) : null}
            {item.label}
          </button>
        ))}
      </div>

      <p className="source-panel__coverage">
        {selectedLesson !== undefined
          ? `${selectedLesson.title} · 已关联 ${linkedCount} 个来源`
          : "请选择小节以关联课程来源。"}
      </p>

      {filteredSources.length > 0 ? (
        <ul className="source-panel__list">
          {filteredSources.map((source) => {
            const linked =
              selectedLesson?.sourceIds.includes(source.id) ?? false;
            const ready = source.status === "ready";
            const actionName = `${linked ? "取消关联" : "关联"} ${source.name}`;
            return (
              <li
                key={source.id}
                className={`source-panel__item${linked ? " is-linked" : ""}`}
                aria-label={`来源 ${source.name}`}
              >
                <span className="source-panel__icon">
                  {sourceIcon(source.kind)}
                </span>
                <span className="source-panel__main">
                  <strong>{source.name}</strong>
                  <span>
                    <span>{sourceTypeLabels[source.kind]}</span>
                    <span aria-hidden="true"> · </span>
                    <span>{sourceStatusLabel(source.status)}</span>
                  </span>
                </span>
                <button
                  type="button"
                  className="icon-button editor-icon-button source-panel__link"
                  aria-label={actionName}
                  title={actionName}
                  disabled={!ready || selectedLesson === undefined}
                  onClick={() => onToggleSource(source.id)}
                >
                  {linked ? (
                    <LinkBreak aria-hidden="true" size={20} weight="bold" />
                  ) : (
                    <LinkSimple aria-hidden="true" size={20} weight="bold" />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="source-panel__empty">
          <ImageSquare aria-hidden="true" size={28} weight="duotone" />
          <p>当前筛选下暂无来源。</p>
        </div>
      )}

      <ValidationPanel />
    </aside>
  );
}

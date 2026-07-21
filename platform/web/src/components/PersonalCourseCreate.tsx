import { FileArrowUp, FolderOpen, Sparkle, X } from "@phosphor-icons/react";
import { useState, type ChangeEvent, type FormEvent } from "react";

export interface PersonalCourseCreateProps {
  onStart(files: File[], prompt: string): Promise<void> | void;
}

function mergeFiles(current: File[], incoming: FileList | null): File[] {
  const merged = new Map(
    current.map((file) => [`${file.webkitRelativePath || file.name}:${file.size}`, file]),
  );
  Array.from(incoming ?? []).forEach((file) => {
    merged.set(`${file.webkitRelativePath || file.name}:${file.size}`, file);
  });
  return Array.from(merged.values()).slice(0, 50);
}

export function PersonalCourseCreate({ onStart }: PersonalCourseCreateProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const addFiles = (event: ChangeEvent<HTMLInputElement>) => {
    setFiles((current) => mergeFiles(current, event.currentTarget.files));
    event.currentTarget.value = "";
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (files.length === 0 || !prompt.trim() || submitting) return;
    setSubmitting(true);
    setError(undefined);
    try {
      await onStart(files, prompt.trim());
    } catch {
      setError("课程创建未能开始，请检查资料后重试。");
      setSubmitting(false);
    }
  };

  return (
    <main className="personal-page">
      <form className="personal-create-card" aria-labelledby="personal-course-heading" onSubmit={submit}>
        <header className="personal-heading">
          <span className="personal-mark" aria-hidden="true"><Sparkle size={24} weight="fill" /></span>
          <div>
            <p className="eyebrow">个人课程工作台</p>
            <h1 id="personal-course-heading">把资料变成一门可直接使用的课</h1>
            <p>选择文件或整个目录，再用一句话说明课程目标。知识整理、结构编排和真实图形匹配会自动完成。</p>
          </div>
        </header>

        <div className="personal-source-actions">
          <label className="personal-source-button">
            <FileArrowUp size={22} weight="bold" aria-hidden="true" />
            <span><strong>选择资料</strong><small>Markdown、PPTX、CSV、Excel</small></span>
            <input type="file" multiple aria-label="选择课程资料" accept=".md,.markdown,.pptx,.csv,.parquet,.xls,.xlsx" onChange={addFiles} />
          </label>
          <label className="personal-source-button">
            <FolderOpen size={22} weight="bold" aria-hidden="true" />
            <span><strong>选择目录</strong><small>一次加入一组课程资料</small></span>
            <input type="file" multiple aria-label="选择资料目录" onChange={addFiles} {...({ webkitdirectory: "" } as Record<string, string>)} />
          </label>
        </div>

        <div className="personal-selection" aria-live="polite">
          <strong>{files.length === 0 ? "尚未选择资料" : `已选择 ${files.length} 个文件`}</strong>
          {files.length > 0 ? (
            <button type="button" className="personal-clear" onClick={() => setFiles([])} aria-label="清空已选资料">
              <X size={18} weight="bold" aria-hidden="true" />清空
            </button>
          ) : null}
        </div>
        {files.length > 0 ? (
          <ul className="personal-file-list" aria-label="已选择的资料">
            {files.slice(0, 6).map((file) => <li key={`${file.webkitRelativePath || file.name}:${file.size}`}>{file.webkitRelativePath || file.name}</li>)}
            {files.length > 6 ? <li>另有 {files.length - 6} 个文件</li> : null}
          </ul>
        ) : null}

        <label className="personal-prompt-field">
          <span>你想做一门什么课？</span>
          <textarea value={prompt} maxLength={2000} placeholder="例如：为个人讲师制作 60 分钟 AI 工作流实战课" onChange={(event) => setPrompt(event.currentTarget.value)} />
        </label>
        {error ? <p className="operation-error" role="alert">{error}</p> : null}
        <button className="primary-button personal-start" disabled={files.length === 0 || !prompt.trim() || submitting}>
          <Sparkle size={19} weight="fill" aria-hidden="true" />
          {submitting ? "正在开始…" : "开始组课"}
        </button>
      </form>
    </main>
  );
}

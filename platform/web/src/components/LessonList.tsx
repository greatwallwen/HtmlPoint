import {
  ArrowDown,
  ArrowUp,
  CheckCircle,
  Clock,
  LinkSimple,
  PencilSimple,
  Plus,
  X,
} from "@phosphor-icons/react";
import { createPortal } from "react-dom";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import type { ChapterNode, LessonNode } from "../domain/course";

interface LessonListProps {
  chapter?: ChapterNode;
  chapterIndex: number;
  selectedLessonId?: string;
  onUpdateLesson(lessonId: string, patch: Pick<LessonNode, "title" | "summary" | "durationMinutes">): void;
  onMoveLesson(lessonId: string, direction: -1 | 1): void;
  onAddLesson(): void;
}

interface LessonDraft {
  title: string;
  summary: string;
  duration: string;
}

type DraftField = keyof LessonDraft;
type LessonErrors = Partial<Record<DraftField, string>>;

function lessonStatusLabel(status: LessonNode["status"]): string {
  switch (status) {
    case "grounded":
      return "已解析";
    case "needs-source":
      return "待补充来源";
    case "draft":
      return "草稿";
  }
}

export function LessonList({
  chapter,
  chapterIndex,
  selectedLessonId,
  onUpdateLesson,
  onMoveLesson,
  onAddLesson,
}: LessonListProps) {
  const [editingLesson, setEditingLesson] = useState<LessonNode>();
  const [draft, setDraft] = useState<LessonDraft>({
    title: "",
    summary: "",
    duration: "",
  });
  const [errors, setErrors] = useState<LessonErrors>({});
  const editTriggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const summaryRef = useRef<HTMLTextAreaElement>(null);
  const durationRef = useRef<HTMLInputElement>(null);

  const openDialog = (lesson: LessonNode, trigger: HTMLButtonElement) => {
    editTriggerRef.current = trigger;
    setEditingLesson(lesson);
    setDraft({
      title: lesson.title,
      summary: lesson.summary,
      duration: String(lesson.durationMinutes),
    });
    setErrors({});
  };

  const closeDialog = () => {
    const trigger = editTriggerRef.current;
    setEditingLesson(undefined);
    setErrors({});
    queueMicrotask(() => trigger?.focus());
  };

  useEffect(() => {
    if (editingLesson === undefined) {
      return;
    }

    const dialog = dialogRef.current;
    const background =
      document.querySelector<HTMLElement>(".desktop-workflow") ??
      document.querySelector<HTMLElement>(".course-editor-shell");
    const backgroundWasInert = background?.hasAttribute("inert") ?? false;
    const previousAriaHidden = background?.getAttribute("aria-hidden") ?? null;
    background?.setAttribute("inert", "");
    background?.setAttribute("aria-hidden", "true");
    titleRef.current?.focus();

    const focusableElements = (): HTMLElement[] =>
      dialog === null
        ? []
        : Array.from(
            dialog.querySelectorAll<HTMLElement>(
              'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
            ),
          );

    const handleDocumentKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        closeDialog();
        return;
      }
      if (event.key !== "Tab" || dialog === null) {
        return;
      }

      const focusable = focusableElements();
      const first = focusable[0];
      const last = focusable.at(-1);
      if (first === undefined || last === undefined) {
        event.preventDefault();
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      } else if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        first.focus();
      }
    };

    const handleDocumentFocus = (event: FocusEvent) => {
      if (
        dialog !== null &&
        event.target instanceof Node &&
        !dialog.contains(event.target)
      ) {
        titleRef.current?.focus();
      }
    };

    document.addEventListener("keydown", handleDocumentKeyDown, true);
    document.addEventListener("focusin", handleDocumentFocus);
    return () => {
      document.removeEventListener("keydown", handleDocumentKeyDown, true);
      document.removeEventListener("focusin", handleDocumentFocus);
      if (!backgroundWasInert) {
        background?.removeAttribute("inert");
      }
      if (previousAriaHidden === null) {
        background?.removeAttribute("aria-hidden");
      } else {
        background?.setAttribute("aria-hidden", previousAriaHidden);
      }
    };
  }, [editingLesson]);

  const updateDraft = (field: DraftField, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setErrors((current) => {
      if (current[field] === undefined) {
        return current;
      }
      const next = { ...current };
      delete next[field];
      return next;
    });
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (editingLesson === undefined) {
      return;
    }

    const title = draft.title.trim();
    const summary = draft.summary.trim();
    const durationMinutes = Number(draft.duration);
    const nextErrors: LessonErrors = {};
    if (title.length === 0) {
      nextErrors.title = "请输入小节标题。";
    }
    if (summary.length === 0) {
      nextErrors.summary = "请输入内容摘要。";
    }
    if (
      draft.duration.trim().length === 0 ||
      !Number.isInteger(durationMinutes) ||
      durationMinutes < 5 ||
      durationMinutes > 90
    ) {
      nextErrors.duration = "时长必须是 5 到 90 之间的整数。";
    }
    setErrors(nextErrors);

    if (nextErrors.title !== undefined) {
      titleRef.current?.focus();
      return;
    }
    if (nextErrors.summary !== undefined) {
      summaryRef.current?.focus();
      return;
    }
    if (nextErrors.duration !== undefined) {
      durationRef.current?.focus();
      return;
    }

    onUpdateLesson(editingLesson.id, {
      title,
      summary,
      durationMinutes,
    });
    closeDialog();
  };

  return (
    <section
      className="lesson-list"
      aria-label="当前章节"
    >
      <header className="lesson-list__heading">
        <div>
          <p className="eyebrow">第 {chapterIndex + 1} 章</p>
          <h1 id="lesson-list-heading">{chapter?.title ?? "课程结构"}</h1>
          <p>{chapter?.objective ?? "选择章节后编辑小节内容。"}</p>
        </div>
        <span className="lesson-list__count">
          {chapter?.lessons.length ?? 0} 个小节
        </span>
      </header>

      <div className="lesson-list__cards">
        {chapter?.lessons.length ? (
          chapter.lessons.map((lesson, lessonIndex) => {
            const selected = lesson.id === selectedLessonId;
            return (
              <article
                key={lesson.id}
                className={`lesson-card${selected ? " is-selected" : ""}`}
                aria-label={`小节 ${chapterIndex + 1}.${lessonIndex + 1} ${lesson.title}`}
              >
                <div className="lesson-card__ordinal" aria-hidden="true">
                  {chapterIndex + 1}.{lessonIndex + 1}
                </div>
                <div className="lesson-card__content">
                  <div className="lesson-card__title-row">
                    <h2>{lesson.title}</h2>
                    <span
                      className={`lesson-card__status status-${lesson.status}`}
                    >
                      <CheckCircle aria-hidden="true" size={17} weight="bold" />
                      {lessonStatusLabel(lesson.status)}
                    </span>
                  </div>
                  <p>{lesson.summary}</p>
                  <div className="lesson-card__meta">
                    <span>
                      <Clock aria-hidden="true" size={16} />
                      {lesson.durationMinutes} 分钟
                    </span>
                    <span>
                      <LinkSimple aria-hidden="true" size={16} />
                      {lesson.sourceIds.length} 个来源
                    </span>
                  </div>
                </div>
                <div className="lesson-card__actions">
                  <button
                    type="button"
                    className="icon-button editor-icon-button"
                    aria-label={`编辑 ${lesson.title}`}
                    title={`编辑 ${lesson.title}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      openDialog(lesson, event.currentTarget);
                    }}
                  >
                    <PencilSimple aria-hidden="true" size={19} weight="bold" />
                  </button>
                  <button
                    type="button"
                    className="icon-button editor-icon-button"
                    aria-label={`上移 ${lesson.title}`}
                    title={`上移 ${lesson.title}`}
                    disabled={lessonIndex === 0}
                    onClick={(event) => {
                      event.stopPropagation();
                      onMoveLesson(lesson.id, -1);
                    }}
                  >
                    <ArrowUp aria-hidden="true" size={19} weight="bold" />
                  </button>
                  <button
                    type="button"
                    className="icon-button editor-icon-button"
                    aria-label={`下移 ${lesson.title}`}
                    title={`下移 ${lesson.title}`}
                    disabled={lessonIndex === chapter.lessons.length - 1}
                    onClick={(event) => {
                      event.stopPropagation();
                      onMoveLesson(lesson.id, 1);
                    }}
                  >
                    <ArrowDown aria-hidden="true" size={19} weight="bold" />
                  </button>
                </div>
              </article>
            );
          })
        ) : (
          <p className="lesson-list__empty">本章还没有小节。</p>
        )}
      </div>

      <button type="button" className="lesson-add-button" onClick={onAddLesson}>
        <Plus aria-hidden="true" size={19} weight="bold" />
        添加小节
      </button>

      {editingLesson !== undefined
        ? createPortal(
        <div className="lesson-dialog-backdrop">
          <div
            ref={dialogRef}
            className="lesson-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="lesson-dialog-heading"
          >
            <header className="lesson-dialog__heading">
              <div>
                <p className="editor-panel-kicker">编辑课程内容</p>
                <h2 id="lesson-dialog-heading">编辑 {editingLesson.title}</h2>
              </div>
              <button
                type="button"
                className="icon-button editor-icon-button"
                aria-label="关闭编辑小节"
                title="关闭编辑小节"
                onClick={closeDialog}
              >
                <X aria-hidden="true" size={20} weight="bold" />
              </button>
            </header>

            <form className="lesson-dialog__form" noValidate onSubmit={handleSubmit}>
              <div className="form-field">
                <label htmlFor="lesson-edit-title">小节标题</label>
                <input
                  ref={titleRef}
                  id="lesson-edit-title"
                  value={draft.title}
                  aria-invalid={errors.title === undefined ? undefined : "true"}
                  aria-describedby={
                    errors.title === undefined ? undefined : "lesson-edit-title-error"
                  }
                  onChange={(event) => updateDraft("title", event.target.value)}
                />
                {errors.title ? (
                  <span id="lesson-edit-title-error" className="field-error">
                    {errors.title}
                  </span>
                ) : null}
              </div>

              <div className="form-field">
                <label htmlFor="lesson-edit-summary">内容摘要</label>
                <textarea
                  ref={summaryRef}
                  id="lesson-edit-summary"
                  rows={4}
                  value={draft.summary}
                  aria-invalid={errors.summary === undefined ? undefined : "true"}
                  aria-describedby={
                    errors.summary === undefined
                      ? undefined
                      : "lesson-edit-summary-error"
                  }
                  onChange={(event) => updateDraft("summary", event.target.value)}
                />
                {errors.summary ? (
                  <span id="lesson-edit-summary-error" className="field-error">
                    {errors.summary}
                  </span>
                ) : null}
              </div>

              <div className="form-field lesson-dialog__duration">
                <label htmlFor="lesson-edit-duration">时长（分钟）</label>
                <input
                  ref={durationRef}
                  id="lesson-edit-duration"
                  type="number"
                  min={5}
                  max={90}
                  step={1}
                  value={draft.duration}
                  aria-invalid={
                    errors.duration === undefined ? undefined : "true"
                  }
                  aria-describedby={
                    errors.duration === undefined
                      ? undefined
                      : "lesson-edit-duration-error"
                  }
                  onChange={(event) => updateDraft("duration", event.target.value)}
                />
                {errors.duration ? (
                  <span id="lesson-edit-duration-error" className="field-error">
                    {errors.duration}
                  </span>
                ) : null}
              </div>

              <footer className="lesson-dialog__actions">
                <button type="button" className="secondary-button" onClick={closeDialog}>
                  取消
                </button>
                <button type="submit" className="primary-button">
                  保存小节
                </button>
              </footer>
            </form>
          </div>
        </div>,
            document.body,
          )
        : null}
    </section>
  );
}

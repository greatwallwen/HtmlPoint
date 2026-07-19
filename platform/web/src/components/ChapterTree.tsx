import { CaretDown, Plus } from "@phosphor-icons/react";

import type { ChapterNode } from "../domain/course";

interface ChapterTreeProps {
  chapters: ChapterNode[];
  selectedChapterId?: string;
  selectedLessonId?: string;
  onSelectChapter(chapterId: string): void;
  onSelectLesson(chapterId: string, lessonId: string): void;
  onAddChapter(): void;
}

export function ChapterTree({
  chapters,
  selectedChapterId,
  selectedLessonId,
  onSelectChapter,
  onSelectLesson,
  onAddChapter,
}: ChapterTreeProps) {
  return (
    <section className="chapter-tree" aria-labelledby="chapter-tree-heading">
      <header className="editor-panel-heading">
        <div>
          <p className="editor-panel-kicker">课程大纲</p>
          <h2 id="chapter-tree-heading">课程结构</h2>
        </div>
        <span className="editor-panel-count">{chapters.length} 章</span>
      </header>

      <ol className="chapter-tree__list">
        {chapters.map((chapter, chapterIndex) => {
          const selected = chapter.id === selectedChapterId;
          return (
            <li key={chapter.id} className="chapter-tree__chapter">
              <button
                type="button"
                className={`chapter-tree__chapter-button${
                  selected ? " is-selected" : ""
                }`}
                aria-label={`第 ${chapterIndex + 1} 章 ${chapter.title}`}
                aria-current={selected ? "true" : undefined}
                onClick={() => onSelectChapter(chapter.id)}
              >
                <span className="chapter-tree__ordinal" aria-hidden="true">
                  {chapterIndex + 1}
                </span>
                <span className="chapter-tree__chapter-title">{chapter.title}</span>
                <CaretDown aria-hidden="true" size={16} weight="bold" />
              </button>

              <ol className="chapter-tree__lessons">
                {chapter.lessons.map((lesson, lessonIndex) => {
                  const lessonSelected =
                    selected && lesson.id === selectedLessonId;
                  return (
                    <li key={lesson.id}>
                      <button
                        type="button"
                        className={`chapter-tree__lesson-button${
                          lessonSelected ? " is-selected" : ""
                        }`}
                        aria-label={`第 ${chapterIndex + 1}.${lessonIndex + 1} 节 ${lesson.title}`}
                        aria-current={lessonSelected ? "true" : undefined}
                        onClick={() => onSelectLesson(chapter.id, lesson.id)}
                      >
                        <span aria-hidden="true">
                          {chapterIndex + 1}.{lessonIndex + 1}
                        </span>
                        <span>{lesson.title}</span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </li>
          );
        })}
      </ol>

      <footer className="editor-panel-footer">
        <button
          type="button"
          className="editor-add-button"
          onClick={onAddChapter}
        >
          <Plus aria-hidden="true" size={18} weight="bold" />
          添加章节
        </button>
      </footer>
    </section>
  );
}

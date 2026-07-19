import { ClipboardText, Files, ImageSquare, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import { useWorkspace } from "../state/workspace";
import type { ArtifactClient } from "../services/artifact-client";
import type { KnowledgeClient } from "../services/knowledge-client";
import { AssistantDock } from "./AssistantDock";
import { ChapterTree } from "./ChapterTree";
import { LessonList } from "./LessonList";
import { SourcePanel } from "./SourcePanel";
import { GovernedCoursePanel } from "./GovernedCoursePanel";
import { KnowledgeReviewDrawer } from "./KnowledgeReviewDrawer";
import { SlideVisualGallery } from "./SlideVisualGallery";

export interface CourseEditorProps {
  knowledgeClient?: KnowledgeClient;
  artifactClient?: ArtifactClient;
}

export function CourseEditor({ knowledgeClient, artifactClient }: CourseEditorProps = {}) {
  const { state, dispatch } = useWorkspace();
  const [sourceDrawerOpen, setSourceDrawerOpen] = useState(false);
  const [reviewDrawerOpen, setReviewDrawerOpen] = useState(false);
  const [governedPanelOpen, setGovernedPanelOpen] = useState(false);
  const sourceDrawerTriggerRef = useRef<HTMLButtonElement>(null);
  const reviewDrawerTriggerRef = useRef<HTMLButtonElement>(null);
  const governedPanelTriggerRef = useRef<HTMLButtonElement>(null);
  const governedPanelCloseRef = useRef<HTMLButtonElement>(null);
  const selectedChapterIndex = state.course.chapters.findIndex(
    (chapter) => chapter.id === state.selectedChapterId,
  );
  const chapterIndex = selectedChapterIndex >= 0 ? selectedChapterIndex : 0;
  const selectedChapter = state.course.chapters[chapterIndex];
  const selectedLesson =
    selectedChapter?.lessons.find(
      (lesson) => lesson.id === state.selectedLessonId,
    ) ?? selectedChapter?.lessons[0];

  const closeSourceDrawer = () => {
    setSourceDrawerOpen(false);
    queueMicrotask(() => sourceDrawerTriggerRef.current?.focus());
  };

  useEffect(() => {
    if (!sourceDrawerOpen) {
      return;
    }
    const handleEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) {
        return;
      }
      event.preventDefault();
      closeSourceDrawer();
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [sourceDrawerOpen]);

  useEffect(() => {
    if (!governedPanelOpen) return;
    const handleEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        event.preventDefault();
        setGovernedPanelOpen(false);
        queueMicrotask(() => governedPanelTriggerRef.current?.focus());
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = document.querySelectorAll<HTMLElement>(
        '.governed-editor-drawer button:not([disabled]), .governed-editor-drawer input:not([disabled]), .governed-editor-drawer a[href]',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [governedPanelOpen]);

  return (
    <main className="course-editor-shell">
      <button
        ref={sourceDrawerTriggerRef}
        type="button"
        className="icon-button source-drawer-trigger"
        aria-label="打开证据与来源"
        title="打开证据与来源"
        aria-controls="course-source-panel"
        aria-expanded={sourceDrawerOpen ? "true" : "false"}
        onClick={() => setSourceDrawerOpen(true)}
      >
        <Files aria-hidden="true" size={22} weight="bold" />
      </button>
      {knowledgeClient ? (
        <button
          ref={reviewDrawerTriggerRef}
          type="button"
          className="icon-button review-editor-trigger"
          aria-label="打开知识审核"
          title="打开知识审核"
          aria-expanded={reviewDrawerOpen}
          onClick={() => setReviewDrawerOpen(true)}
        >
          <ClipboardText aria-hidden="true" size={22} weight="bold" />
        </button>
      ) : null}
      <button
        ref={governedPanelTriggerRef}
        type="button"
        className="icon-button governed-editor-trigger"
        aria-label="打开真实图形与发布"
        title="打开真实图形与发布"
        aria-expanded={governedPanelOpen}
        onClick={() => {
          setGovernedPanelOpen(true);
          queueMicrotask(() => governedPanelCloseRef.current?.focus());
        }}
      >
        <ImageSquare aria-hidden="true" size={22} weight="bold" />
      </button>

      <div className="course-editor-grid">
        <ChapterTree
          chapters={state.course.chapters}
          selectedChapterId={selectedChapter?.id}
          selectedLessonId={selectedLesson?.id}
          onSelectChapter={(chapterId) =>
            dispatch({ type: "SELECT_CHAPTER", chapterId })
          }
          onSelectLesson={(chapterId, lessonId) => {
            if (chapterId !== state.selectedChapterId) {
              dispatch({ type: "SELECT_CHAPTER", chapterId });
            }
            dispatch({ type: "SELECT_LESSON", lessonId });
          }}
          onAddChapter={() => dispatch({ type: "ADD_CHAPTER" })}
        />

        <LessonList
          chapter={selectedChapter}
          chapterIndex={chapterIndex}
          selectedLessonId={selectedLesson?.id}
          onUpdateLesson={(lessonId, patch) => {
            if (selectedChapter === undefined) {
              return;
            }
            dispatch({
              type: "UPDATE_LESSON",
              chapterId: selectedChapter.id,
              lessonId,
              patch,
            });
          }}
          onMoveLesson={(lessonId, direction) => {
            if (selectedChapter === undefined) {
              return;
            }
            dispatch({
              type: "MOVE_LESSON",
              chapterId: selectedChapter.id,
              lessonId,
              direction,
            });
          }}
          onAddLesson={() => {
            if (selectedChapter !== undefined) {
              dispatch({ type: "ADD_LESSON", chapterId: selectedChapter.id });
            }
          }}
        />

        <SourcePanel
          sources={state.course.sources}
          selectedLesson={selectedLesson}
          drawerOpen={sourceDrawerOpen}
          onToggleSource={(sourceId) => {
            if (selectedChapter === undefined || selectedLesson === undefined) {
              return;
            }
            dispatch({
              type: "TOGGLE_LESSON_SOURCE",
              chapterId: selectedChapter.id,
              lessonId: selectedLesson.id,
              sourceId,
            });
          }}
          onCloseDrawer={closeSourceDrawer}
        />
      </div>
      <AssistantDock />
      {governedPanelOpen ? (
        <div className="drawer-backdrop" role="presentation">
          <aside className="governed-editor-drawer" role="dialog" aria-modal="true" aria-label="真实图形与发布">
            <button
              ref={governedPanelCloseRef}
              type="button"
              className="icon-button governed-drawer-close"
              aria-label="关闭真实图形与发布"
              title="关闭真实图形与发布"
              onClick={() => {
                setGovernedPanelOpen(false);
                queueMicrotask(() => governedPanelTriggerRef.current?.focus());
              }}
            >
              <X aria-hidden="true" size={20} />
            </button>
            <SlideVisualGallery
              slideDeck={state.governedProjection?.slideDeck}
              artifactClient={artifactClient}
            />
            <GovernedCoursePanel client={knowledgeClient} />
          </aside>
        </div>
      ) : null}
      {knowledgeClient ? (
        <KnowledgeReviewDrawer
          client={knowledgeClient}
          open={reviewDrawerOpen}
          onClose={() => {
            setReviewDrawerOpen(false);
            queueMicrotask(() => reviewDrawerTriggerRef.current?.focus());
          }}
        />
      ) : null}
    </main>
  );
}

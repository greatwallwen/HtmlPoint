import { Eye, PencilSimple, Play, Presentation } from "@phosphor-icons/react";
import { useState } from "react";

import type { PersonalCourseView } from "../domain/personal-course-schema";

export function PersonalCourseHome({ view, onEdit, onTeach }: { view: PersonalCourseView; onEdit(): void; onTeach(): void }) {
  const [preview, setPreview] = useState(false);
  const course = view.course;
  if (course === null) return null;
  const lessonCount = course.chapters.reduce((sum, chapter) => sum + chapter.lessons.length, 0);
  return (
    <main className="personal-page personal-home">
      <section className="personal-home-hero">
        <p className="eyebrow">课程已就绪</p>
        <h1>{course.title}</h1>
        <p>{course.audience} · {course.durationMinutes} 分钟 · {course.chapters.length} 章 {lessonCount} 节</p>
        <div className="personal-home-actions">
          <button className="secondary-button" aria-expanded={preview} onClick={() => setPreview((value) => !value)}><Eye size={19} weight="bold" />预览课程</button>
          <button className="secondary-button" onClick={onEdit}><PencilSimple size={19} weight="bold" />编辑课程</button>
          <button className="primary-button" onClick={onTeach}><Presentation size={19} weight="bold" />开始授课</button>
        </div>
      </section>
      <section className="personal-course-summary" aria-label="课程结构">
        <header><div><p className="eyebrow">课程结构</p><h2>围绕目标自动编排</h2></div><span>{course.sources.length} 份真实资料</span></header>
        <ol>
          {course.chapters.map((chapter, chapterIndex) => (
            <li key={chapter.id}>
              <span className="personal-chapter-number">{String(chapterIndex + 1).padStart(2, "0")}</span>
              <div><h3>{chapter.title}</h3><p>{chapter.objective}</p>{preview ? <ul>{chapter.lessons.map((lesson) => <li key={lesson.id}><Play size={14} weight="fill" />{lesson.title}<span>{lesson.durationMinutes} 分钟</span></li>)}</ul> : null}</div>
            </li>
          ))}
        </ol>
        <details className="personal-evidence-disclosure"><summary>运行证据</summary><p>课程内容、资料引用、图形来源与运行清单已由本地 Helper 校验并保存。</p></details>
      </section>
    </main>
  );
}

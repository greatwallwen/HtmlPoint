import { CheckCircle, SpinnerGap } from "@phosphor-icons/react";

import type { PersonalCourseView } from "../domain/personal-course-schema";

export function PersonalCourseProgress({ view }: { view: PersonalCourseView }) {
  return (
    <main className="personal-page">
      <section className="personal-status-card" role="status" aria-live="polite">
        <span className="personal-progress-icon" aria-hidden="true"><SpinnerGap size={34} weight="bold" /></span>
        <p className="eyebrow">正在创建你的课程</p>
        <h1>{view.phaseLabel}</h1>
        <p>系统正在使用已保存的真实进度。关闭页面后再次打开，也会从最近完成的阶段继续。</p>
        <div className="personal-progress-track" aria-hidden="true"><span /></div>
        <ul className="personal-progress-facts">
          <li><CheckCircle size={18} weight="fill" />资料与知识来源会保留引用</li>
          <li><CheckCircle size={18} weight="fill" />图形优先使用来源与可核验素材</li>
        </ul>
      </section>
    </main>
  );
}

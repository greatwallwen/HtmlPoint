import type { JSX } from "react";


export function HelperRequiredScreen(): JSX.Element {
  return (
    <main className="helper-required-page">
      <section
        aria-labelledby="helper-required-heading"
        className="helper-required-card"
        role="alert"
      >
        <span aria-hidden="true" className="helper-required-mark">CS</span>
        <div>
          <p className="eyebrow">课程工作台</p>
          <h1 id="helper-required-heading">请从课程工作台启动</h1>
          <p>关闭此页面，然后双击“启动课程平台”重新打开。</p>
        </div>
      </section>
    </main>
  );
}

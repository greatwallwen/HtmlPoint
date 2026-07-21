import { CaretDown, ShieldCheck, WarningCircle } from "@phosphor-icons/react";
import { useState } from "react";

export function PersonalCourseAttention({ count, onAccept }: { count: number; onAccept(): Promise<void> | void }) {
  const [details, setDetails] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  return (
    <main className="personal-page">
      <section className="personal-status-card personal-attention" aria-labelledby="attention-heading">
        <span className="personal-attention-icon" aria-hidden="true"><WarningCircle size={34} weight="fill" /></span>
        <p className="eyebrow">安全检查</p>
        <h1 id="attention-heading">有几项内容需要你确认</h1>
        <p>{count} 项问题已集中整理。</p>
        <div className="personal-attention-actions">
          <button className="primary-button" disabled={submitting} onClick={() => { setSubmitting(true); void Promise.resolve(onAccept()).finally(() => setSubmitting(false)); }}>
            <ShieldCheck size={19} weight="bold" aria-hidden="true" />接受建议并继续
          </button>
          <button className="secondary-button" aria-expanded={details} onClick={() => setDetails((value) => !value)}>
            查看详情<CaretDown size={17} weight="bold" aria-hidden="true" />
          </button>
        </div>
        {details ? <p className="personal-attention-detail">系统只在资料冲突、来源无法读取或图形授权无法核验时暂停；默认建议会优先保留真实来源，并排除不确定内容。</p> : null}
      </section>
    </main>
  );
}

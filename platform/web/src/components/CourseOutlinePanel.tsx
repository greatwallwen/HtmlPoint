import { CheckCircle, WarningCircle } from "@phosphor-icons/react";
import type { JSX } from "react";

import type { CourseCompositionPreview } from "../domain/course-agent";

export interface CourseOutlinePanelProps {
  preview: CourseCompositionPreview;
  stale: boolean;
  confirming?: boolean;
  onCardDisposition(
    cardVersionId: string,
    disposition: "auto" | "include" | "exclude",
  ): void;
  onConfirm(): void;
}

export function CourseOutlinePanel({
  preview,
  stale,
  confirming = false,
  onCardDisposition,
  onConfirm,
}: CourseOutlinePanelProps): JSX.Element {
  const outline = preview.result.outline;
  const placements = outline.chapters.flatMap((chapter) => chapter.placements);
  const cardIds = [...new Set(placements.map((item) => item.cardVersionId))];
  const minutes = placements.reduce(
    (total, placement) => total + placement.allocatedMinutes,
    0,
  );
  const blocked = preview.result.blockingGaps.length > 0;

  return (
    <section className="outline-panel" aria-labelledby="outline-heading">
      <div className="panel-heading">
        <p className="eyebrow">组合结果</p>
        <h2 id="outline-heading">可调整课程大纲</h2>
        <p>{preview.result.confirmationSummary.text}</p>
      </div>

      <div className="outline-evidence" aria-label="组合证据">
        <div>
          {preview.retrievalMode === "hybrid" ? (
            <CheckCircle aria-hidden="true" size={20} weight="fill" />
          ) : (
            <WarningCircle aria-hidden="true" size={20} weight="fill" />
          )}
          <strong>
            {preview.retrievalMode === "hybrid"
              ? "混合检索证据已绑定"
              : "仅全文检索（降级模式）"}
          </strong>
        </div>
        <dl>
          <div>
            <dt>索引快照</dt>
            <dd>{preview.indexSnapshotId}</dd>
          </div>
          <div>
            <dt>组合证据</dt>
            <dd>{preview.result.compositionEvidenceId}</dd>
          </div>
          <div>
            <dt>目标覆盖</dt>
            <dd>
              {preview.draft.learningGoals.length - outline.uncoveredGoals.length}/
              {preview.draft.learningGoals.length}
            </dd>
          </div>
          <div>
            <dt>总时长</dt>
            <dd>{minutes} 分钟</dd>
          </div>
          <div>
            <dt>标签匹配</dt>
            <dd>
              {preview.draft.requiredTagIds.length > 0
                ? preview.draft.requiredTagIds.join("、")
                : "未限定"}
            </dd>
          </div>
          <div>
            <dt>前置要求</dt>
            <dd>使用已发布知识卡与当前索引快照</dd>
          </div>
        </dl>
      </div>

      {stale ? (
        <p className="outline-warning" role="status">
          需求或卡片选择已更改，请重新组合后再确认。
        </p>
      ) : null}

      {preview.result.blockingGaps.length > 0 ? (
        <div className="outline-gaps" role="alert">
          <strong>仍有覆盖缺口</strong>
          <ul>
            {preview.result.blockingGaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <ol className="outline-chapters">
        {outline.chapters.map((chapter) => (
          <li key={chapter.chapterId}>
            <div>
              <h3>{chapter.title}</h3>
              <p>{chapter.objective}</p>
            </div>
            <ul>
              {chapter.placements.map((placement) => (
                <li key={placement.placementId}>
                  <div>
                    <strong>{placement.cardVersionId}</strong>
                    <span>
                      {placement.purpose} · {placement.allocatedMinutes} 分钟 ·
                      课程单元 {placement.lessonId}
                    </span>
                  </div>
                  <div className="card-disposition" aria-label={`知识卡 ${placement.cardVersionId} 组合设置`}>
                    <button
                      type="button"
                      aria-pressed={preview.draft.includeCardVersionIds.includes(
                        placement.cardVersionId,
                      )}
                      onClick={() =>
                        onCardDisposition(
                          placement.cardVersionId,
                          preview.draft.includeCardVersionIds.includes(
                            placement.cardVersionId,
                          )
                            ? "auto"
                            : "include",
                        )
                      }
                    >
                      固定选用
                    </button>
                    <button
                      type="button"
                      aria-pressed={preview.draft.excludeCardVersionIds.includes(
                        placement.cardVersionId,
                      )}
                      onClick={() =>
                        onCardDisposition(
                          placement.cardVersionId,
                          preview.draft.excludeCardVersionIds.includes(
                            placement.cardVersionId,
                          )
                            ? "auto"
                            : "exclude",
                        )
                      }
                    >
                      排除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>

      <p className="outline-card-summary">
        已召回 {cardIds.length} 张卡片：{cardIds.join("、") || "无"}
      </p>
      <div className="panel-actions">
        <button
          type="button"
          className="primary-button"
          disabled={stale || blocked || confirming}
          onClick={onConfirm}
        >
          {confirming ? "正在确认…" : "确认大纲并创建课程"}
        </button>
      </div>
    </section>
  );
}

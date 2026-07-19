import {
  ArrowClockwise,
  CheckCircle,
  Database,
  MagnifyingGlass,
  WarningCircle,
} from "@phosphor-icons/react";
import { useEffect, useState, type JSX } from "react";

import type {
  KnowledgeSummary,
  KnowledgeSummaryClient,
} from "../domain/knowledge";

export interface KnowledgePreparationPanelProps {
  client?: KnowledgeSummaryClient;
  refreshKey?: number;
}

type PanelState =
  | { phase: "loading" }
  | { phase: "offline" }
  | { phase: "ready"; summary: KnowledgeSummary };

export function KnowledgePreparationPanel({
  client,
  refreshKey = 0,
}: KnowledgePreparationPanelProps): JSX.Element {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<PanelState>(() =>
    client === undefined ? { phase: "offline" } : { phase: "loading" },
  );

  useEffect(() => {
    let current = true;
    if (client === undefined) {
      setState({ phase: "offline" });
      return () => {
        current = false;
      };
    }

    setState({ phase: "loading" });
    void client.getSummary().then(
      (summary) => {
        if (current) {
          setState({ phase: "ready", summary });
        }
      },
      () => {
        if (current) {
          setState({ phase: "offline" });
        }
      },
    );
    return () => {
      current = false;
    };
  }, [attempt, client, refreshKey]);

  const retryVisible =
    client !== undefined &&
    (state.phase === "offline" ||
      (state.phase === "loading" && attempt > 0));

  return (
    <section
      className="knowledge-preparation"
      role="region"
      aria-labelledby="knowledge-preparation-heading"
    >
      <div className="knowledge-preparation__heading">
        <span className="knowledge-preparation__icon" aria-hidden="true">
          <Database size={22} weight="bold" />
        </span>
        <div>
          <h2 id="knowledge-preparation-heading">知识准备</h2>
          <p>查看本地知识卡与检索准备状态</p>
        </div>
      </div>

      {state.phase === "ready" ? (
        <div className="knowledge-preparation__content">
          <div className="knowledge-metrics" aria-label="知识准备概览">
            <div>
              <strong>{state.summary.publishedCardCount} 张已发布知识卡</strong>
              <span>{state.summary.sourceCount} 个知识来源</span>
            </div>
            <div>
              <strong className="knowledge-retrieval-mode">
                <MagnifyingGlass aria-hidden="true" size={18} weight="bold" />
                {state.summary.retrievalMode === "hybrid"
                  ? "混合检索已就绪"
                  : "全文检索模式"}
              </strong>
              <span>
                {state.summary.retrievalMode === "hybrid"
                  ? "语义与全文检索可用"
                  : "仍可继续导入和生成课程"}
              </span>
            </div>
          </div>

          <div className="knowledge-tags-block">
            <span className="knowledge-tags-label">受控标签</span>
            {state.summary.tagLabels.length > 0 ? (
              <ul className="knowledge-tags" aria-label="受控标签">
                {state.summary.tagLabels.map((label) => (
                  <li key={label}>{label}</li>
                ))}
              </ul>
            ) : (
              <span className="knowledge-tags-empty">暂无受控标签</span>
            )}
          </div>
        </div>
      ) : null}

      <div className="knowledge-preparation__footer">
        <p
          className={`knowledge-preparation__status knowledge-status--${state.phase}`}
          role="status"
          aria-live="polite"
        >
          {state.phase === "loading" ? (
            <>
              <ArrowClockwise aria-hidden="true" size={18} weight="bold" />
              正在连接本地知识服务
            </>
          ) : state.phase === "offline" ? (
            <>
              <WarningCircle aria-hidden="true" size={18} weight="bold" />
              本地知识服务未连接
            </>
          ) : state.summary.reviewTaskCount > 0 ? (
            <>
              <WarningCircle aria-hidden="true" size={18} weight="bold" />
              {state.summary.reviewTaskCount} 项待审核
            </>
          ) : (
            <>
              <CheckCircle aria-hidden="true" size={18} weight="fill" />
              知识准备已完成
            </>
          )}
        </p>

        {retryVisible ? (
          <button
            type="button"
            className="knowledge-retry"
            aria-label="重试连接"
            title="重试连接"
            disabled={state.phase === "loading"}
            onClick={() => setAttempt((current) => current + 1)}
          >
            <ArrowClockwise aria-hidden="true" size={18} weight="bold" />
            {state.phase === "loading" ? "正在重试" : "重试"}
          </button>
        ) : null}
      </div>
    </section>
  );
}

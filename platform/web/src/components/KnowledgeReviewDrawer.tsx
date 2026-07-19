import {
  Check,
  Database,
  Eye,
  Prohibit,
  X,
} from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";

import type {
  KnowledgeIndexResult,
  ReviewDetailResult,
  ReviewListResult,
} from "../domain/helper-contracts-schema";
import {
  createCardPublishJob,
  createKnowledgeIndexJob,
  createReviewResolveJob,
} from "../domain/governed-job-factory";
import type { KnowledgeClient } from "../services/knowledge-client";

export interface KnowledgeReviewDrawerProps {
  client: KnowledgeClient;
  open: boolean;
  onClose(): void;
  onChanged?(): void;
}

const categoryLabels: Record<string, string> = {
  "candidate-card": "候选知识卡",
  "exact-duplicate": "完全重复",
  "near-duplicate": "相似内容",
  tag: "标签",
  "source-changed": "来源更新",
  "course-feedback": "课程反馈",
  "visual-rights": "图形权利",
};

export function KnowledgeReviewDrawer({
  client,
  open,
  onClose,
  onChanged,
}: KnowledgeReviewDrawerProps) {
  const [page, setPage] = useState<ReviewListResult>();
  const [detail, setDetail] = useState<ReviewDetailResult>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();
  const [indexResult, setIndexResult] = useState<KnowledgeIndexResult>();
  const closeRef = useRef<HTMLButtonElement>(null);

  const load = async () => {
    setBusy(true);
    try {
      const response = await client.listReviews({
        type: "knowledge_review_list",
        status: "open",
        limit: 50,
      });
      setPage(response.result);
    } catch {
      setMessage("无法读取待审核项目，请重试。");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setDetail(undefined);
    setIndexResult(undefined);
    setMessage(undefined);
    if (open) {
      void load();
      queueMicrotask(() => closeRef.current?.focus());
    } else {
      setPage(undefined);
    }
  }, [client, open]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const drawer = document.getElementById("knowledge-review-drawer");
      const focusable = drawer?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;
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
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, open]);

  if (!open) return null;

  const inspect = async (taskId: string) => {
    setBusy(true);
    setMessage(undefined);
    try {
      const response = await client.getReviewDetail({
        type: "knowledge_review_detail",
        taskId,
      });
      setDetail(response.result);
    } catch {
      setMessage("无法读取审核详情，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (decision: "accept" | "dismiss") => {
    if (!detail) return;
    setBusy(true);
    setMessage(undefined);
    try {
      await client.resolveReview(
        await createReviewResolveJob({
          taskId: detail.task.taskId,
          decision,
          expectedReviewDigest: detail.task.reviewDigest,
          evidenceIds: detail.evidenceIds,
        }),
      );
      onChanged?.();
      await load();
      if (decision === "accept") {
        const refreshed = await client.getReviewDetail({
          type: "knowledge_review_detail",
          taskId: detail.task.taskId,
        });
        setDetail(refreshed.result);
        setMessage("审核已接受。完成同一卡片的其他阻塞项后即可发布。");
      } else {
        setDetail(undefined);
        setMessage("审核项已驳回并关闭。");
      }
    } catch {
      setMessage("审核结果未提交；原有状态保持不变。");
    } finally {
      setBusy(false);
    }
  };

  const publishAndIndex = async () => {
    if (!detail?.cardVersionId || !detail.cardContentDigest) return;
    setBusy(true);
    setMessage("正在发布知识卡并等待索引提交…");
    try {
      const published = await client.publishCard(
        await createCardPublishJob({
          cardVersionId: detail.cardVersionId,
          expectedCardDigest: detail.cardContentDigest,
        }),
      );
      const indexed = await client.indexKnowledge(
        await createKnowledgeIndexJob(published.result.indexOutboxId),
      );
      setIndexResult(indexed.result);
      setMessage(
        indexed.result.retrievalMode === "hybrid"
          ? "知识卡已发布，混合索引快照已就绪。"
          : "知识卡已发布；语义索引不可用，已明确记录为全文检索降级模式。",
      );
      setDetail(undefined);
      onChanged?.();
      await load();
    } catch {
      setMessage("发布或索引未完成；不会把未确认结果标记为就绪。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="drawer-backdrop" role="presentation">
      <aside
        id="knowledge-review-drawer"
        className="knowledge-review-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="knowledge-review-heading"
      >
        <header>
          <div>
            <p className="eyebrow">知识治理</p>
            <h2 id="knowledge-review-heading">审核与发布</h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon-button"
            aria-label="关闭知识审核"
            title="关闭知识审核"
            onClick={onClose}
          >
            <X aria-hidden="true" size={20} weight="bold" />
          </button>
        </header>

        <p className="review-blocking-count" role="status">
          {page ? `${page.items.filter((item) => item.blocking).length} 项阻塞审核` : "正在读取审核队列"}
        </p>

        {message ? <p className="operation-status">{message}</p> : null}
        {indexResult ? (
          <dl className="index-result-summary">
            <div><dt>索引快照</dt><dd>{indexResult.indexSnapshotId}</dd></div>
            <div><dt>检索模式</dt><dd>{indexResult.retrievalMode === "hybrid" ? "混合检索" : "仅全文检索"}</dd></div>
          </dl>
        ) : null}

        <div className="knowledge-review-layout">
          <section aria-label="待审核列表">
            {page?.items.length === 0 ? <p className="empty-state">当前没有待审核项目。</p> : null}
            <ul className="review-task-list">
              {page?.items.map((item) => (
                <li key={item.taskId}>
                  <div>
                    <strong>{categoryLabels[item.category] ?? item.category}</strong>
                    <span>{item.reasonCode} · {item.evidenceCount} 项证据</span>
                  </div>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => void inspect(item.taskId)}
                  >
                    <Eye aria-hidden="true" size={18} />查看
                  </button>
                </li>
              ))}
            </ul>
          </section>

          {detail ? (
            <section className="review-detail" aria-label="审核详情">
              <h3>{detail.cardTitle ?? categoryLabels[detail.task.category] ?? "审核详情"}</h3>
              {detail.learningObjective ? <p>{detail.learningObjective}</p> : null}
              <dl>
                <div><dt>任务</dt><dd>{detail.task.taskId}</dd></div>
                <div><dt>证据</dt><dd>{detail.evidenceTotal}</dd></div>
                <div><dt>引用</dt><dd>{detail.citationTotal}</dd></div>
              </dl>
              <ul className="review-content-list">
                {detail.contentNodes.map((node) => (
                  <li key={node.path.join(".")}>
                    <span>{node.nodeType}</span>
                    <p>{node.text ?? node.rows.flat().join(" · ")}</p>
                  </li>
                ))}
              </ul>
              {detail.citations.length > 0 ? (
                <ul className="review-citation-list" aria-label="引用证据">
                  {detail.citations.map((citation) => (
                    <li key={`${citation.sourceVersionId}:${citation.chunkId}`}>
                      <strong>{citation.sourceVersionId}</strong>
                      <span>{citation.quotedText ?? citation.chunkId}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="panel-actions">
                {detail.task.status === "open" ? (
                  <>
                    <button type="button" className="secondary-button" disabled={busy} onClick={() => void resolve("dismiss")}>
                      <Prohibit aria-hidden="true" size={18} />驳回
                    </button>
                    <button type="button" className="primary-button" disabled={busy} onClick={() => void resolve("accept")}>
                      <Check aria-hidden="true" size={18} />接受
                    </button>
                  </>
                ) : detail.cardVersionId && detail.cardContentDigest ? (
                  <button type="button" className="primary-button" disabled={busy} onClick={() => void publishAndIndex()}>
                    <Database aria-hidden="true" size={18} />发布并等待索引
                  </button>
                ) : null}
              </div>
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

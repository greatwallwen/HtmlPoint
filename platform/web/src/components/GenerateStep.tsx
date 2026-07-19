import { Files, WarningCircle } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";

import {
  HelperCourseAgent,
  type CourseCompositionPreview,
  type CourseRequirementDraft,
} from "../domain/course-agent";
import type { KnowledgeSummary } from "../domain/knowledge";
import type { KnowledgeClient } from "../services/knowledge-client";
import { useWorkspace } from "../state/workspace";
import { CourseOutlinePanel } from "./CourseOutlinePanel";
import { CourseRequirementPanel } from "./CourseRequirementPanel";

export interface GenerateStepProps {
  knowledgeClient?: KnowledgeClient;
}

type FieldErrors = Partial<
  Record<"title" | "audience" | "learningGoals" | "durationMinutes", string>
>;

function initialDraft(
  title: string,
  audience: string,
  goal: string,
  durationMinutes: number,
): CourseRequirementDraft {
  return {
    title,
    audience,
    learningGoals: goal ? [goal] : [""],
    durationMinutes,
    requiredTagIds: [],
    excludedTagIds: [],
    usageScope: "private-training",
    includeCardVersionIds: [],
    excludeCardVersionIds: [],
    requireVisualRefs: false,
    requireDatasetRefs: false,
  };
}

function validateDraft(draft: CourseRequirementDraft): FieldErrors {
  const errors: FieldErrors = {};
  if (!draft.title.trim()) {
    errors.title = "请输入课程名称。";
  }
  if (!draft.audience.trim()) {
    errors.audience = "请输入课程受众。";
  }
  if (!draft.learningGoals.some((goal) => goal.trim())) {
    errors.learningGoals = "请输入课程目标。";
  }
  if (
    !Number.isFinite(draft.durationMinutes) ||
    draft.durationMinutes < 40 ||
    draft.durationMinutes > 480 ||
    draft.durationMinutes % 5 !== 0
  ) {
    errors.durationMinutes = "课程时长需为 40–480 分钟且为 5 的倍数。";
  }
  return errors;
}

export function GenerateStep({ knowledgeClient }: GenerateStepProps) {
  const { state, dispatch, generateCourse } = useWorkspace();
  const [draft, setDraft] = useState(() =>
    initialDraft(
      state.brief.title,
      state.brief.audience,
      state.brief.goal,
      state.brief.durationMinutes,
    ),
  );
  const [summary, setSummary] = useState<KnowledgeSummary>();
  const [preview, setPreview] = useState<CourseCompositionPreview>();
  const [previewStale, setPreviewStale] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [operationError, setOperationError] = useState<string>();
  const [phase, setPhase] = useState<"idle" | "composing" | "confirming">("idle");
  const helperAgent = useMemo(
    () => (knowledgeClient === undefined ? undefined : new HelperCourseAgent(knowledgeClient)),
    [knowledgeClient],
  );
  const readySources = state.course.sources.filter(
    (source) => source.status === "ready",
  );

  useEffect(() => {
    let current = true;
    if (knowledgeClient === undefined) {
      setSummary(undefined);
      return () => {
        current = false;
      };
    }
    void knowledgeClient.getSummary().then(
      (value) => {
        if (current) {
          setSummary(value);
        }
      },
      () => {
        if (current) {
          setSummary(undefined);
        }
      },
    );
    return () => {
      current = false;
    };
  }, [knowledgeClient]);

  const changeDraft = (next: CourseRequirementDraft) => {
    setDraft(next);
    setFieldErrors({});
    setOperationError(undefined);
    if (preview !== undefined) {
      setPreviewStale(true);
    }
    dispatch({
      type: "SET_BRIEF",
      patch: {
        title: next.title.trim(),
        audience: next.audience.trim(),
        goal: next.learningGoals.map((goal) => goal.trim()).filter(Boolean).join("；"),
        durationMinutes: next.durationMinutes,
      },
    });
  };

  const submit = async () => {
    const errors = validateDraft(draft);
    setFieldErrors(errors);
    const firstInvalid = (
      ["title", "audience", "learningGoals", "durationMinutes"] as const
    ).find((field) => errors[field] !== undefined);
    if (firstInvalid !== undefined) {
      const ids = {
        title: "course-title",
        audience: "course-audience",
        learningGoals: "course-learning-goals",
        durationMinutes: "course-duration",
      };
      document.getElementById(ids[firstInvalid])?.focus();
      return;
    }

    setOperationError(undefined);
    if (helperAgent === undefined) {
      await generateCourse();
      return;
    }
    setPhase("composing");
    try {
      const nextPreview = await helperAgent.compose(draft);
      setPreview(nextPreview);
      setPreviewStale(false);
    } catch (error: unknown) {
      setOperationError(
        error instanceof Error && error.message.trim()
          ? error.message
          : "课程大纲组合失败，请重试。",
      );
    } finally {
      setPhase("idle");
    }
  };

  const confirm = async () => {
    if (helperAgent === undefined || preview === undefined || previewStale) {
      return;
    }
    setPhase("confirming");
    setOperationError(undefined);
    try {
      const confirmed = await helperAgent.confirm(preview);
      dispatch({
        type: "GOVERNED_COURSE_CONFIRMED",
        course: confirmed.course,
        receipt: confirmed.receipt,
        governed: confirmed.governed,
        projection: {
          courseDigest: confirmed.result.courseDigest,
          usageScope: confirmed.result.usageScope,
          courseUpdatedAt: confirmed.course.updatedAt,
          slideDeck: confirmed.result.slideDeck,
          warnings: [],
          publicationStatus: "confirmed",
        },
      });
    } catch (error: unknown) {
      setOperationError(
        error instanceof Error && error.message.trim()
          ? error.message
          : "课程大纲确认失败，请重试。",
      );
      setPhase("idle");
    }
  };

  const setCardDisposition = (
    cardVersionId: string,
    disposition: "auto" | "include" | "exclude",
  ) => {
    const include = new Set(draft.includeCardVersionIds);
    const exclude = new Set(draft.excludeCardVersionIds);
    include.delete(cardVersionId);
    exclude.delete(cardVersionId);
    if (disposition === "include") {
      include.add(cardVersionId);
    } else if (disposition === "exclude") {
      exclude.add(cardVersionId);
    }
    changeDraft({
      ...draft,
      includeCardVersionIds: [...include],
      excludeCardVersionIds: [...exclude],
    });
  };

  const busy = phase !== "idle" || state.generation === "running";
  const tagOptions = summary?.tagOptions ?? [];

  return (
    <main className="workflow-page">
      <section className="workflow-panel" aria-labelledby="generate-heading">
        <div className="panel-heading">
          <p className="eyebrow">第 2 步</p>
          <h1 id="generate-heading">生成课程</h1>
          <p>先确认完整需求，再组合并确认一份证据绑定的大纲。</p>
        </div>

        <aside className="source-context" aria-label="生成所用资料">
          <div className="source-context__heading">
            <Files aria-hidden="true" size={22} weight="duotone" />
            <strong>已就绪资料 {readySources.length} 份</strong>
          </div>
          <ul>
            {readySources.map((source) => (
              <li key={source.id}>{source.name}</li>
            ))}
          </ul>
          {helperAgent === undefined ? (
            <p className="legacy-fallback-note" role="status">
              <WarningCircle aria-hidden="true" size={18} weight="fill" />
              离线演练模式：生成结果不会关联知识索引，也不可发布。
            </p>
          ) : (
            <p className="helper-index-note" role="status">
              {summary?.indexState === "ready"
                ? "混合知识索引已就绪"
                : summary?.indexState === "degraded"
                  ? "仅全文检索（降级证据会被记录）"
                  : "正在确认知识索引"}
            </p>
          )}
        </aside>

        <CourseRequirementPanel
          value={draft}
          tagOptions={tagOptions}
          disabled={busy}
          error={operationError ?? state.operationError}
          fieldErrors={fieldErrors}
          submitLabel={
            phase === "composing"
              ? "正在组合大纲…"
              : helperAgent === undefined
                ? "生成课程结构"
                : previewStale
                  ? "重新组合大纲"
                  : "组合课程大纲"
          }
          onChange={changeDraft}
          onSubmit={() => void submit()}
        />
      </section>

      {preview !== undefined ? (
        <CourseOutlinePanel
          preview={preview}
          stale={previewStale}
          confirming={phase === "confirming"}
          onCardDisposition={setCardDisposition}
          onConfirm={() => void confirm()}
        />
      ) : null}
    </main>
  );
}

import {
  CheckCircle,
  GlobeHemisphereWest,
  ImageSquare,
  MagnifyingGlass,
  ShieldCheck,
  UploadSimple,
} from "@phosphor-icons/react";
import { useState } from "react";

import { createId } from "../domain/course";
import { projectReopenedCourse } from "../domain/course-agent";
import { coursePublicationIdentitySchema } from "../domain/helper-contracts-schema";
import {
  createChartBuildJob,
  createCoursePublishJob,
  createCourseValidateJob,
  createVisualAcquireJob,
  createVisualAttachJob,
  createVisualRevalidateJob,
  createVisualSearchJob,
} from "../domain/governed-job-factory";
import type { KnowledgeClient } from "../services/knowledge-client";
import { useWorkspace } from "../state/workspace";

type VisualCandidate = Awaited<ReturnType<KnowledgeClient["searchVisuals"]>>["result"]["items"][number];
const scopeLabels = {
  "private-training": "私有培训",
  internal: "组织内部",
  public: "公开发布",
} as const;

export interface GovernedCoursePanelProps {
  client?: KnowledgeClient;
}

export function GovernedCoursePanel({ client }: GovernedCoursePanelProps) {
  const { state, dispatch, validateCurrentCourse } = useWorkspace();
  const [query, setQuery] = useState("真实 AI 工作场景");
  const [candidates, setCandidates] = useState<VisualCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();
  const projection = state.governedProjection;
  const governedAssets = state.governedAssets ?? { sourceVisuals: [], datasetVersionIds: [], datasetProfiles: [] };
  const stale = projection !== undefined && projection.courseUpdatedAt !== state.course.updatedAt;
  const available =
    client !== undefined &&
    projection !== undefined &&
    state.governed.courseVersionId !== undefined &&
    !stale;

  if (client === undefined || projection === undefined) {
    return (
      <section className="governed-course-panel" aria-labelledby="governed-course-heading">
        <h3 id="governed-course-heading">受控课程发布</h3>
        <p>当前为离线演练课程，不能作为已发布课程。</p>
      </section>
    );
  }

  const validateProjection = async (visualPlacementIds = state.governed.visualPlacementIds) => {
    if (!available || !state.governed.courseVersionId) return;
    const validated = await client.validateCourse(
      await createCourseValidateJob({
        courseVersionId: state.governed.courseVersionId,
        expectedCourseDigest: projection.courseDigest,
        visualPlacementIds,
      }),
    );
    dispatch({
      type: "GOVERNED_PROJECTION_UPDATED",
      governed: {
        ...state.governed,
        slideDeckId: validated.result.slideDeckId,
        runtimeManifestId: validated.result.runtimeManifestId,
        visualPlacementIds,
      },
      projection: {
        ...projection,
        courseUpdatedAt: state.course.updatedAt,
        slideDeck: validated.result.slideDeck,
        runtimeManifest: validated.result.runtimeManifest,
        courseProjectionId: validated.result.courseProjectionId,
        warnings: validated.result.warnings,
        publicationStatus: "validated",
      },
    });
    await validateCurrentCourse();
    return validated.result;
  };

  const attach = async (input: {
    visualVersionId: string;
    originatingCardVersionId: string | null;
    originatingSourceVersionId: string | null;
    originatingDatasetVersionId?: string | null;
  }) => {
    if (!available || !state.governed.courseVersionId) return;
    const targetNode = projection.slideDeck.nodes.find((node) =>
      input.originatingSourceVersionId
        ? node.sourceVersionIds.includes(input.originatingSourceVersionId)
        : input.originatingCardVersionId
          ? node.cardVersionIds.includes(input.originatingCardVersionId)
          : true,
    );
    if (!targetNode) throw new Error("课程没有与素材来源一致的可绑定页面节点");
    const placementId = createId("visual-placement");
    const transformationId = createId("transformation");
    await client.attachVisual(
      await createVisualAttachJob({
        courseVersionId: state.governed.courseVersionId,
        expectedCourseDigest: projection.courseDigest,
        placementId,
        visualVersionId: input.visualVersionId,
        slideNodeId: targetNode.nodeId,
        slotId: `visual-slot-${placementId}`,
        fit: "contain",
        crop: null,
        altText: `${state.course.title} 的真实来源图形`,
        transformation: {
          transformationId,
          crop: null,
          scaleMode: "contain",
          colorAdjustments: [],
          changeNotice: "仅按比例适配版面，未改变图形事实内容。",
          derivativeLicenseDecision: "not-derivative",
          exportLicense: null,
          shareAlikeCompatible: true,
          gfdlCompatible: true,
          noDerivativesCompatible: true,
        },
        originatingCardVersionId: input.originatingCardVersionId,
        originatingSourceVersionId: input.originatingSourceVersionId,
        originatingDatasetVersionId: input.originatingDatasetVersionId ?? null,
      }),
    );
    const nextPlacements = [...new Set([...state.governed.visualPlacementIds, placementId])];
    await validateProjection(nextPlacements);
    setMessage("图形已绑定，并已从同一 Slide AST 重新验证编辑、学员和讲师视图。");
  };

  const attachSourceVisual = async (visualVersionId: string, sourceVersionId: string) => {
    setBusy(true);
    setMessage(undefined);
    try {
      await attach({ visualVersionId, originatingCardVersionId: null, originatingSourceVersionId: sourceVersionId });
    } catch {
      setMessage("来源图形未绑定；既有课程投影保持不变。");
    } finally {
      setBusy(false);
    }
  };

  const buildAndAttachChart = async (datasetVersionId: string) => {
    const profile = governedAssets.datasetProfiles.find(
      (candidate) => candidate.datasetVersionId === datasetVersionId,
    );
    const [xColumn, yColumn] = profile?.columns ?? [];
    if (!profile || !xColumn || !yColumn) {
      setMessage("数据集缺少两个可公开使用的稳定字段，未生成图表。");
      return;
    }
    setBusy(true);
    setMessage(undefined);
    try {
      const requestId = createId("chart-request");
      const response = await client.buildCharts(await createChartBuildJob([{
        requestId,
        chartType: "bar",
        datasetVersionId,
        expectedDatasetDigest: profile.contentDigest,
        expectedSchemaDigest: profile.schemaDigest,
        xColumn: xColumn.name,
        xColumnDigest: xColumn.digest,
        yColumn: yColumn.name,
        yColumnDigest: yColumn.digest,
        aggregate: "count",
        title: `${state.course.title} 数据概览`,
        description: `按 ${xColumn.name} 汇总的真实数据集图表`,
        maxResultRows: 50,
      }]));
      const item = response.result.items.find((candidate) => candidate.requestId === requestId);
      if (!item || item.status !== "materialized") throw new Error("chart materialization failed");
      await attach({
        visualVersionId: item.visualVersionId,
        originatingCardVersionId: null,
        originatingSourceVersionId: null,
        originatingDatasetVersionId: datasetVersionId,
      });
      setMessage("图表已从固定数据集与字段摘要生成，并绑定到同一 Slide AST。");
    } catch {
      setMessage("图表未通过数据摘要、字段或执行门禁；课程保持原样。");
    } finally {
      setBusy(false);
    }
  };

  const search = async () => {
    setBusy(true);
    setMessage(undefined);
    try {
      const response = await client.searchVisuals(await createVisualSearchJob(query.trim(), 6));
      setCandidates(response.result.items);
      if (response.result.items.length === 0) setMessage("没有找到符合来源与许可策略的图形。");
    } catch {
      setMessage("网络图形检索不可用；不会用占位图冒充真实来源。");
    } finally {
      setBusy(false);
    }
  };

  const acquireAndAttach = async (candidateId: string) => {
    setBusy(true);
    setMessage(undefined);
    try {
      const response = await client.acquireVisuals(await createVisualAcquireJob([candidateId]));
      const item = response.result.items[0];
      if (!item || item.status !== "acquired") throw new Error("acquisition failed");
      const revalidated = await client.revalidateVisual(
        await createVisualRevalidateJob(item.visualVersionId),
      );
      if (revalidated.result.item.status !== "revalidated") throw new Error("revalidation failed");
      const cardVersionId = state.governed.cardVersionIds[0];
      if (!cardVersionId) throw new Error("card lineage unavailable");
      await attach({
        visualVersionId: item.visualVersionId,
        originatingCardVersionId: cardVersionId,
        originatingSourceVersionId: null,
      });
      setCandidates([]);
    } catch {
      setMessage("网络图形没有通过获取、时效复核或绑定门槛；课程保持原样。");
    } finally {
      setBusy(false);
    }
  };

  const validate = async () => {
    setBusy(true);
    setMessage(undefined);
    try {
      const result = await validateProjection();
      if (result) setMessage(result.warnings.length ? `验证通过，含 ${result.warnings.length} 项警告。` : "课程验证通过。");
    } catch {
      setMessage("课程验证失败；既有课程投影保持不变。");
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!available || !state.governed.courseVersionId || projection.publicationStatus !== "validated") return;
    setBusy(true);
    setMessage(undefined);
    const job = await createCoursePublishJob({
      courseVersionId: state.governed.courseVersionId,
      expectedCourseDigest: projection.courseDigest,
      visualPlacementIds: state.governed.visualPlacementIds,
    });
    try {
      let published;
      try {
        const response = await client.publishCourse(job);
        published = coursePublicationIdentitySchema.parse({
          courseVersionId: response.result.courseVersionId,
          slideDeckId: response.result.slideDeckId,
          runtimeManifestId: response.result.runtimeManifestId,
          runtimeManifestDigest: response.result.runtimeManifestDigest,
          courseProjectionId: response.result.courseProjectionId,
        });
      } catch {
        const recovery = await client.recoverOperation({
          type: "operation_status",
          operationId: job.operationId,
          actor: { actorType: "human", actorId: "local-user" },
        });
        if (recovery.result.status !== "committed") throw new Error("publication unresolved");
        published = coursePublicationIdentitySchema.parse(recovery.result.resultRefs);
      }
      const reopened = await client.getCourseProjection({
        courseVersionId: published.courseVersionId,
        slideDeckId: published.slideDeckId,
        runtimeManifestId: published.runtimeManifestId,
      });
      const reopenedCourse = projectReopenedCourse(reopened);
      dispatch({
        type: "GOVERNED_COURSE_RESTORED",
        course: reopenedCourse,
        governed: {
          ...state.governed,
          courseVersionId: published.courseVersionId,
          slideDeckId: published.slideDeckId,
          runtimeManifestId: published.runtimeManifestId,
        },
        projection: {
          courseDigest: reopened.courseDigest,
          usageScope: reopened.usageScope,
          courseUpdatedAt: reopenedCourse.updatedAt,
          slideDeck: reopened.slideDeck,
          runtimeManifest: reopened.runtimeManifest,
          courseProjectionId: published.courseProjectionId,
          warnings: [],
          publicationStatus: "published",
        },
        receipt: {
          id: `publish-${published.courseProjectionId}`,
          courseId: published.courseVersionId,
          kind: "validation",
          createdAt: reopened.runtimeManifest.createdAt,
          inputDigest: reopened.runtimeManifest.contentDigest,
          summary: "发布课程已从本地 Helper 重开并核验。",
          checks: [{ id: "published-projection", level: "pass", message: "最终课程、Slide AST 与运行清单绑定一致。" }],
        },
      });
      setMessage("课程已发布，并保留可恢复的操作结果与固定版本标识。 ");
    } catch {
      setMessage("课程未能确认发布；不会把未知或回滚状态显示为成功。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="governed-course-panel" aria-labelledby="governed-course-heading">
      <header>
        <div>
          <p className="editor-panel-kicker">证据优先</p>
          <h3 id="governed-course-heading">真实图形与发布</h3>
        </div>
        <span className={`publication-state is-${projection.publicationStatus}`}>
          <CheckCircle aria-hidden="true" size={18} weight="fill" />
          {projection.publicationStatus === "published" ? "已发布" : projection.publicationStatus === "validated" ? "已验证" : "待验证"}
        </span>
      </header>

      {stale ? <p className="operation-status" role="alert">课程正文已在浏览器中改动。请重新组合，旧的受控投影不能发布。</p> : null}
      <p className="governed-scope">发布范围：{scopeLabels[projection.usageScope]}。Helper 会按该范围执行素材许可与发布门禁。</p>
      {message ? <p className="operation-status" role="status">{message}</p> : null}

      <div className="source-visual-picker">
        <h4><ImageSquare aria-hidden="true" size={19} />来源图形</h4>
        {governedAssets.sourceVisuals.length ? (
          <ul>
            {governedAssets.sourceVisuals.map((visual) => (
              <li key={visual.visualVersionId}>
                <span>{visual.label}</span>
                <button type="button" className="secondary-button" disabled={!available || busy} onClick={() => void attachSourceVisual(visual.visualVersionId, visual.sourceVersionId)}>
                  <UploadSimple aria-hidden="true" size={17} />绑定
                </button>
              </li>
            ))}
          </ul>
        ) : <p>导入带图形的 PowerPoint 后，可在这里选择真实来源图形。</p>}
        {governedAssets.datasetProfiles.length ? (
          <ul aria-label="受控数据集">
            {governedAssets.datasetProfiles.map((dataset) => (
              <li key={dataset.datasetVersionId}>
                <span>{dataset.rowCount} 行 · {dataset.columns.length} 个可用字段</span>
                <button type="button" className="secondary-button" disabled={!available || busy || dataset.columns.length < 2} onClick={() => void buildAndAttachChart(dataset.datasetVersionId)}>
                  生成真实图表
                </button>
              </li>
            ))}
          </ul>
        ) : governedAssets.datasetVersionIds.length ? <p>已识别 {governedAssets.datasetVersionIds.length} 个受控数据集，但缺少可验证字段摘要。</p> : null}
      </div>

      <div className="network-visual-search">
        <label htmlFor="network-visual-query">网络真实图形</label>
        <div>
          <input id="network-visual-query" value={query} onChange={(event) => setQuery(event.target.value)} />
          <button type="button" className="secondary-button" disabled={!available || busy || query.trim().length < 2} onClick={() => void search()}>
            <MagnifyingGlass aria-hidden="true" size={18} />检索
          </button>
        </div>
        {candidates.length ? (
          <ul>
            {candidates.map((candidate) => (
              <li key={candidate.candidateId}>
                <div><strong>{candidate.fileTitle}</strong><span>{candidate.licenseId} · {candidate.width}×{candidate.height}</span></div>
                <button type="button" className="secondary-button" disabled={busy} onClick={() => void acquireAndAttach(candidate.candidateId)}>
                  <GlobeHemisphereWest aria-hidden="true" size={18} />获取并绑定
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="panel-actions">
        <button type="button" className="secondary-button" disabled={!available || busy} onClick={() => void validate()}>
          <ShieldCheck aria-hidden="true" size={18} />验证受控课程
        </button>
        <button type="button" className="primary-button" disabled={!available || busy || projection.publicationStatus !== "validated"} onClick={() => void publish()}>
          发布课程
        </button>
      </div>
    </section>
  );
}

import { useEffect, useLayoutEffect, useMemo, useState, type JSX } from "react";
import { House, Plus, Sparkle } from "@phosphor-icons/react";

import { CourseEditor } from "../components/CourseEditor";
import { GenerateStep } from "../components/GenerateStep";
import { HelperRequiredScreen } from "../components/HelperRequiredScreen";
import { ImportStep } from "../components/ImportStep";
import { PresenterView } from "../components/PresenterView";
import { PersonalCourseAttention } from "../components/PersonalCourseAttention";
import { PersonalCourseCreate } from "../components/PersonalCourseCreate";
import { PersonalCourseHome } from "../components/PersonalCourseHome";
import { PersonalCourseProgress } from "../components/PersonalCourseProgress";
import { StageView } from "../components/StageView";
import {
  TeachingSetup,
  type TeachingRuntime,
} from "../components/TeachingSetup";
import { WorkflowHeader } from "../components/WorkflowHeader";
import {
  WorkspaceProvider,
  canTeach,
  useWorkspace,
  type WorkspaceProviderProps,
} from "../state/workspace";
import {
  bootstrapHelperSession,
  prepareHelperSessionLaunch,
  restoreHelperSessionForProjection,
} from "../services/helper-session";
import { KnowledgeClient } from "../services/knowledge-client";
import { ArtifactClient } from "../services/artifact-client";
import { HelperProjectionClient } from "../services/projection-client";
import {
  createNativeProjectionArtifactReader,
  detectNativeProjectionAdapter,
  type NativeProjectionAdapter,
  type ProjectionBootstrap,
  type ProjectionFrame,
} from "../services/native-projection";
import { requestTeachingProjection, type TeachingProjectionSnapshot } from "../domain/projection-bus";
import { projectReopenedCourse } from "../domain/course-agent";
import type { PersonalCourseResponse, PersonalCourseView } from "../domain/personal-course-schema";
import type { PersonalCourseResolveJob } from "../domain/helper-contracts-schema";
import {
  createImportStartJob,
  createPersonalCourseJob,
  createPersonalCourseResolveJob,
  createPersonalCourseStatusJob,
} from "../domain/governed-job-factory";
import "./tokens.css";
import "./app.css";

export interface AppProps
  extends Pick<WorkspaceProviderProps, "initialState" | "storage" | "agent"> {
  teachingRuntime?: TeachingRuntime;
}

function NativeTeachingProjection({
  adapter,
}: {
  adapter: NativeProjectionAdapter;
}): JSX.Element {
  const [bootstrap, setBootstrap] = useState<ProjectionBootstrap>();
  const [frame, setFrame] = useState<ProjectionFrame>();

  useEffect(() => {
    let current = true;
    const unsubscribe = adapter.subscribeFrame((nextFrame) => {
      if (!current) return;
      adapter.reportMessageAccepted(nextFrame);
      setFrame(nextFrame);
    });
    void adapter.waitForBootstrap().then((nextBootstrap) => {
      if (!current) return;
      adapter.reportMessageAccepted(nextBootstrap.frame);
      setBootstrap(nextBootstrap);
      setFrame(nextBootstrap.frame);
    });
    return () => {
      current = false;
      unsubscribe();
    };
  }, [adapter]);

  const artifactClient = useMemo(
    () =>
      bootstrap === undefined
        ? undefined
        : createNativeProjectionArtifactReader(bootstrap),
    [bootstrap],
  );

  if (bootstrap === undefined || frame === undefined) {
    return (
      <main className="teaching-error-page">
        <section className="teaching-error-card" role="status">
          <p className="eyebrow">Physical projection</p>
          <h1>Preparing the trusted teaching view</h1>
        </section>
      </main>
    );
  }

  const nativeProjection = { adapter, frame };
  return adapter.role === "stage" ? (
    <StageView
      course={bootstrap.course}
      sessionId={bootstrap.sessionId}
      slideDeck={bootstrap.slideDeck}
      artifactClient={artifactClient}
      nativeProjection={nativeProjection}
    />
  ) : (
    <PresenterView
      course={bootstrap.course}
      sessionId={bootstrap.sessionId}
      slideDeck={bootstrap.slideDeck}
      artifactClient={artifactClient}
      nativeProjection={nativeProjection}
    />
  );
}

function WorkspaceProjection({
  teachingRuntime,
  hasExplicitAgent,
}: Pick<AppProps, "teachingRuntime"> & { hasExplicitAgent: boolean }): JSX.Element {
  const { state } = useWorkspace();
  useLayoutEffect(() => {
    prepareHelperSessionLaunch();
  }, []);
  const query = new URLSearchParams(globalThis.window.location.search);
  const view = query.get("view");
  const sessionId = query.get("session") ?? "";

  if (view === "stage") {
    return <RemoteTeachingProjection role="stage" local={{ course: state.course, slideDeck: state.governedProjection?.slideDeck }} canRequestRemote={state.governed.courseVersionId !== undefined} sessionId={sessionId} runtime={teachingRuntime} />;
  }

  if (view === "presenter") {
    return <RemoteTeachingProjection role="presenter" local={{ course: state.course, slideDeck: state.governedProjection?.slideDeck }} canRequestRemote={state.governed.courseVersionId !== undefined} sessionId={sessionId} runtime={teachingRuntime} />;
  }

  return (
    <StudioWorkflow
      teachingRuntime={teachingRuntime}
      hasExplicitAgent={hasExplicitAgent}
    />
  );
}

function RemoteTeachingProjection({
  role,
  local,
  canRequestRemote,
  sessionId,
  runtime,
}: {
  role: "stage" | "presenter";
  local: TeachingProjectionSnapshot;
  canRequestRemote: boolean;
  sessionId: string;
  runtime?: TeachingRuntime;
}) {
  const [remote, setRemote] = useState<TeachingProjectionSnapshot>();
  const artifactClient = useMemo(() => {
    const session = restoreHelperSessionForProjection();
    return session === undefined ? undefined : new ArtifactClient(session);
  }, []);
  const validSession = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(sessionId);
  const snapshot = local.course.chapters.length > 0 ? local : remote;
  useEffect(() => {
    if (local.course.chapters.length > 0 || !validSession || !canRequestRemote) return;
    return requestTeachingProjection(sessionId, setRemote);
  }, [canRequestRemote, local.course.chapters.length, sessionId, validSession]);
  if (!validSession || !canRequestRemote) {
    return role === "stage" ? (
      <StageView course={local.course} sessionId={sessionId} runtime={runtime} slideDeck={local.slideDeck} artifactClient={artifactClient} />
    ) : (
      <PresenterView course={local.course} sessionId={sessionId} runtime={runtime} slideDeck={local.slideDeck} artifactClient={artifactClient} />
    );
  }
  if (!snapshot) {
    return <main className="teaching-error-page"><section className="teaching-error-card" role="status"><p className="eyebrow">课程投影</p><h1>正在接收受控课程</h1><p>等待主工作台通过会话总线发送同一份课程与 Slide AST。</p></section></main>;
  }
  return role === "stage" ? (
    <StageView course={snapshot.course} sessionId={sessionId} runtime={runtime} slideDeck={snapshot.slideDeck} artifactClient={artifactClient} />
  ) : (
    <PresenterView course={snapshot.course} sessionId={sessionId} runtime={runtime} slideDeck={snapshot.slideDeck} artifactClient={artifactClient} />
  );
}

function StudioWorkflow({
  teachingRuntime,
  hasExplicitAgent,
}: Pick<AppProps, "teachingRuntime"> & { hasExplicitAgent: boolean }): JSX.Element {
  const { state, dispatch } = useWorkspace();
  const restoredSession = useMemo(
    () => hasExplicitAgent ? undefined : restoreHelperSessionForProjection(),
    [hasExplicitAgent],
  );
  const [helperState, setHelperState] = useState<"checking" | "ready" | "required">(
    hasExplicitAgent || restoredSession !== undefined ? "ready" : "checking",
  );
  const [knowledgeClient, setKnowledgeClient] = useState<KnowledgeClient | undefined>(
    restoredSession === undefined ? undefined : new KnowledgeClient(restoredSession),
  );
  const [artifactClient, setArtifactClient] = useState<ArtifactClient | undefined>(
    restoredSession === undefined ? undefined : new ArtifactClient(restoredSession),
  );
  const [projectionClient, setProjectionClient] = useState<HelperProjectionClient | undefined>(
    restoredSession === undefined ? undefined : new HelperProjectionClient(restoredSession),
  );

  useEffect(() => {
    if (hasExplicitAgent || helperState === "ready") {
      return;
    }
    let current = true;
    void bootstrapHelperSession().then((session) => {
      if (current && session !== undefined) {
        setKnowledgeClient(new KnowledgeClient(session));
        setArtifactClient(new ArtifactClient(session));
        setProjectionClient(new HelperProjectionClient(session));
        setHelperState("ready");
      } else if (current) {
        setHelperState("required");
      }
    });
    return () => {
      current = false;
    };
  }, [hasExplicitAgent, helperState]);

  useEffect(() => {
    const courseVersionId = state.governed.courseVersionId;
    const slideDeckId = state.governed.slideDeckId;
    const runtimeManifestId = state.governed.runtimeManifestId;
    if (
      knowledgeClient === undefined ||
      state.governedProjection !== undefined ||
      courseVersionId === undefined ||
      slideDeckId === undefined ||
      runtimeManifestId === undefined
    ) return;
    let current = true;
    void knowledgeClient.getCourseProjection({ courseVersionId, slideDeckId, runtimeManifestId }).then(
      (reopened) => {
        if (!current) return;
        const course = projectReopenedCourse(reopened);
        dispatch({
          type: "GOVERNED_COURSE_RESTORED",
          course,
          governed: state.governed,
          projection: {
            courseDigest: reopened.courseDigest,
            usageScope: reopened.usageScope,
            courseUpdatedAt: course.updatedAt,
            slideDeck: reopened.slideDeck,
            runtimeManifest: reopened.runtimeManifest,
            warnings: [],
            publicationStatus: "published",
          },
          receipt: {
            id: `reopen-${reopened.runtimeManifest.versionId}`,
            courseId: reopened.courseVersionId,
            kind: "validation",
            createdAt: reopened.runtimeManifest.createdAt,
            inputDigest: reopened.runtimeManifest.contentDigest,
            summary: "已从本地 Helper 恢复并核验固定的发布课程投影。",
            checks: [{ id: "published-projection", level: "pass", message: "课程、Slide AST 与运行清单绑定一致。" }],
          },
        });
      },
      () => undefined,
    );
    return () => { current = false; };
  }, [dispatch, knowledgeClient, state.governed, state.governedProjection]);

  if (helperState === "checking") {
    return (
      <main className="helper-required-page">
        <section className="helper-required-card" role="status">
          <p>正在连接课程工作台…</p>
        </section>
      </main>
    );
  }

  if (helperState === "required") {
    return <HelperRequiredScreen />;
  }

  if (!hasExplicitAgent && knowledgeClient !== undefined) {
    return (
      <PersonalStudioWorkflow
        knowledgeClient={knowledgeClient}
        artifactClient={artifactClient}
        projectionClient={projectionClient}
        teachingRuntime={teachingRuntime}
      />
    );
  }

  const projectionIdentity =
    state.governed.courseVersionId !== undefined &&
    state.governed.slideDeckId !== undefined &&
    state.governed.runtimeManifestId !== undefined &&
    state.governedProjection?.runtimeManifest !== undefined
      ? {
          courseVersionId: state.governed.courseVersionId,
          slideDeckId: state.governed.slideDeckId,
          runtimeManifestId: state.governed.runtimeManifestId,
          runtimeManifestDigest: state.governedProjection.runtimeManifest.contentDigest,
        }
      : undefined;

  let content: JSX.Element;
  switch (state.step) {
    case "import":
      content = <ImportStep knowledgeClient={knowledgeClient} />;
      break;
    case "generate":
      content = <GenerateStep knowledgeClient={knowledgeClient} />;
      break;
    case "edit":
      content = <CourseEditor knowledgeClient={knowledgeClient} artifactClient={artifactClient} />;
      break;
    case "teach":
      content = (
        <TeachingSetup
          course={state.course}
          selectedLessonId={state.selectedLessonId}
          runtime={teachingRuntime}
          slideDeck={state.governedProjection?.slideDeck}
          projectionClient={projectionClient}
          projectionIdentity={projectionIdentity}
          onReturnToEdit={() => dispatch({ type: "GO_TO_STEP", step: "edit" })}
        />
      );
      break;
  }

  return (
    <div className="app-shell">
      <div className="desktop-workflow">
        <WorkflowHeader
          step={state.step}
          hasChapters={state.course.chapters.length > 0}
          canTeach={canTeach(state)}
          onNavigate={(step) => dispatch({ type: "GO_TO_STEP", step })}
          onStartNew={() => dispatch({ type: "START_NEW" })}
        />
        {content}
      </div>
      <main className="mobile-prompt">
        <p>请在桌面尺寸编辑课程</p>
      </main>
    </div>
  );
}

function PersonalStudioWorkflow({
  knowledgeClient,
  artifactClient,
  projectionClient,
  teachingRuntime,
}: {
  knowledgeClient: KnowledgeClient;
  artifactClient?: ArtifactClient;
  projectionClient?: HelperProjectionClient;
  teachingRuntime?: TeachingRuntime;
}): JSX.Element {
  const { state, dispatch } = useWorkspace();
  const [response, setResponse] = useState<PersonalCourseResponse | undefined>(
    state.personalRunId !== undefined && state.personalView !== undefined
      ? { runId: state.personalRunId, view: state.personalView }
      : undefined,
  );
  type PersonalAttentionAction = PersonalCourseResolveJob["action"];
  const attentionActions: PersonalAttentionAction[] = [
    "retry",
    "exclude-source",
    "approve",
    "reject",
    "use-source-visual",
    "use-network-visual",
    "continue-without-visual",
  ];
  const [resolution, setResolution] = useState<{
    digest: string;
    action: PersonalAttentionAction;
  }>();

  const remember = (
    next: PersonalCourseResponse,
    outputSummary?: Record<string, unknown>,
  ) => {
    setResponse(next);
    dispatch({ type: "PERSONAL_COURSE_TRACKED", runId: next.runId, view: next.view });
    const digest = outputSummary?.attentionDigest;
    const action = outputSummary?.recommendedAction;
    if (
      typeof digest === "string" &&
      typeof action === "string" &&
      attentionActions.includes(action as PersonalAttentionAction)
    ) {
      setResolution({ digest, action: action as PersonalAttentionAction });
    } else {
      setResolution(undefined);
    }
  };

  useEffect(() => {
    if (response !== undefined || state.personalRunId === undefined) return;
    let current = true;
    void knowledgeClient.getPersonalCourse(
      createPersonalCourseStatusJob(state.personalRunId),
    ).then((value) => {
      if (current) remember(value.result, value.evidence.outputSummary);
    }, () => undefined);
    return () => { current = false; };
  }, [knowledgeClient, response, state.personalRunId]);

  useEffect(() => {
    if (response?.view.status !== "creating") return;
    let current = true;
    const timer = globalThis.setTimeout(() => {
      void knowledgeClient.getPersonalCourse(
        createPersonalCourseStatusJob(response.runId),
      ).then((value) => {
        if (current) remember(value.result, value.evidence.outputSummary);
      }, () => undefined);
    }, 900);
    return () => { current = false; globalThis.clearTimeout(timer); };
  }, [knowledgeClient, response]);

  const openCourse = (target: "edit" | "teach") => {
    const course = response?.view.course;
    if (course === null || course === undefined) return;
    dispatch({
      type: "PERSONAL_COURSE_OPENED",
      course,
      target,
      receipt: {
        id: "personal-course-validation",
        courseId: course.id,
        kind: "validation",
        createdAt: course.updatedAt,
        inputDigest: "local-helper-verified",
        summary: "本地 Helper 已完成课程、来源与运行清单验证。",
        checks: [{ id: "personal-course-ready", level: "pass", message: "课程可以预览、编辑或授课。" }],
      },
    });
  };

  const startNew = () => {
    setResponse(undefined);
    setResolution(undefined);
    dispatch({ type: "START_NEW" });
  };

  const courseBar = (
    <header className="personal-product-bar">
      <div className="personal-product-brand">
        <span aria-hidden="true"><Sparkle size={19} weight="fill" /></span>
        <strong>个人课程工作台</strong>
      </div>
      <nav aria-label="个人课程">
        <button type="button" className="secondary-button" onClick={() => dispatch({ type: "GO_TO_STEP", step: "import" })}>
          <House size={18} weight="bold" aria-hidden="true" />课程首页
        </button>
        <button type="button" className="secondary-button" onClick={startNew}>
          <Plus size={18} weight="bold" aria-hidden="true" />新建课程
        </button>
      </nav>
    </header>
  );

  if (state.step === "edit" && state.course.chapters.length > 0) {
    return <div className="app-shell personal-editor-shell">{courseBar}<CourseEditor knowledgeClient={knowledgeClient} artifactClient={artifactClient} /></div>;
  }
  if (state.step === "teach" && state.course.chapters.length > 0) {
    return (
      <TeachingSetup
        course={state.course}
        selectedLessonId={state.selectedLessonId}
        runtime={teachingRuntime}
        slideDeck={state.governedProjection?.slideDeck}
        projectionClient={projectionClient}
        onReturnToEdit={() => dispatch({ type: "GO_TO_STEP", step: "edit" })}
      />
    );
  }

  const start = async (files: File[], prompt: string) => {
    const imported = await Promise.all(files.map(async (file) => {
      const extension = file.name.split(".").pop()?.toLowerCase();
      const mediaType = file.type.trim() || ({
        md: "text/markdown",
        markdown: "text/markdown",
        pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        csv: "text/csv",
        parquet: "application/vnd.apache.parquet",
        xls: "application/vnd.ms-excel",
        xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }[extension ?? ""] ?? "application/octet-stream");
      const typed = file.type.trim() ? file : file.slice(0, file.size, mediaType);
      const upload = await knowledgeClient.uploadSource(typed, file.name);
      return knowledgeClient.startImport(await createImportStartJob(upload));
    }));
    const created = await knowledgeClient.createPersonalCourse(
      await createPersonalCourseJob({
        requestId: `personal-request-${crypto.randomUUID().replaceAll("-", "").slice(0, 32)}`,
        prompt,
        sourceVersionIds: imported.map((item) => item.result.sourceVersionId),
      }),
    );
    remember(created.result, created.evidence.outputSummary);
  };

  const accept = async () => {
    if (response === undefined) return;
    let currentResolution = resolution;
    if (currentResolution === undefined) {
      const refreshed = await knowledgeClient.getPersonalCourse(
        createPersonalCourseStatusJob(response.runId),
      );
      remember(refreshed.result, refreshed.evidence.outputSummary);
      const digest = refreshed.evidence.outputSummary.attentionDigest;
      const action = refreshed.evidence.outputSummary.recommendedAction;
      if (
        typeof digest !== "string" ||
        typeof action !== "string" ||
        !attentionActions.includes(action as PersonalAttentionAction)
      ) return;
      currentResolution = { digest, action: action as PersonalAttentionAction };
    }
    const resolved = await knowledgeClient.resolvePersonalCourseAttention(
      await createPersonalCourseResolveJob({
        runId: response.runId,
        expectedAttentionDigest: currentResolution.digest,
        action: currentResolution.action,
      }),
    );
    remember(resolved.result, resolved.evidence.outputSummary);
  };

  const view: PersonalCourseView | undefined = response?.view;
  if (view === undefined) return <PersonalCourseCreate onStart={start} />;
  if (view.status === "creating") return <PersonalCourseProgress view={view} />;
  if (view.status === "needs-attention") return <PersonalCourseAttention count={view.attentionCount} onAccept={accept} />;
  if (view.status === "ready") return <PersonalCourseHome view={view} onEdit={() => openCourse("edit")} onTeach={() => openCourse("teach")} />;
  return (
    <main className="personal-page"><section className="personal-status-card"><p className="eyebrow">创建未完成</p><h1>{view.phaseLabel}</h1><p>资料仍保留在本地，可以重新开始并调整课程要求。</p><button className="primary-button" onClick={startNew}>重新开始</button></section></main>
  );
}

export function App(props: AppProps): JSX.Element {
  const { teachingRuntime, ...workspaceProps } = props;
  const nativeAdapter = useMemo(
    () => detectNativeProjectionAdapter(globalThis.window),
    [],
  );
  if (nativeAdapter !== undefined) {
    return <NativeTeachingProjection adapter={nativeAdapter} />;
  }
  return (
    <WorkspaceProvider {...workspaceProps}>
      <WorkspaceProjection
        teachingRuntime={teachingRuntime}
        hasExplicitAgent={props.agent !== undefined}
      />
    </WorkspaceProvider>
  );
}

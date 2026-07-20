import { useEffect, useLayoutEffect, useMemo, useState, type JSX } from "react";

import { CourseEditor } from "../components/CourseEditor";
import { GenerateStep } from "../components/GenerateStep";
import { ImportStep } from "../components/ImportStep";
import { PresenterView } from "../components/PresenterView";
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
}: Pick<AppProps, "teachingRuntime">): JSX.Element {
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

  return <StudioWorkflow teachingRuntime={teachingRuntime} />;
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
}: Pick<AppProps, "teachingRuntime">): JSX.Element {
  const { state, dispatch } = useWorkspace();
  const [knowledgeClient, setKnowledgeClient] = useState<KnowledgeClient>();
  const [artifactClient, setArtifactClient] = useState<ArtifactClient>();
  const [projectionClient, setProjectionClient] = useState<HelperProjectionClient>();

  useEffect(() => {
    if (knowledgeClient !== undefined) {
      return;
    }
    let current = true;
    void bootstrapHelperSession().then((session) => {
      if (current && session !== undefined) {
        setKnowledgeClient(new KnowledgeClient(session));
        setArtifactClient(new ArtifactClient(session));
        setProjectionClient(new HelperProjectionClient(session));
      }
    });
    return () => {
      current = false;
    };
  }, [knowledgeClient]);

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
      <WorkspaceProjection teachingRuntime={teachingRuntime} />
    </WorkspaceProvider>
  );
}

# Personal-First Course Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a real Win11 personal course workflow that starts from one local entry, turns selected sources and one human request into a governed course, surfaces at most one attention bundle, and leaves dual-screen teaching optional.

**Architecture:** Keep the existing Helper, catalog, evidence, knowledge-card, Slide AST, RuntimeManifest, and projection layers. Add a persisted `PersonalCourseRun` state machine plus a bounded single-worker `PersonalCourseSupervisor`; `personal_course_create` returns the queued run immediately and the Web polls real persisted stages. Replace the browser-only template generator with a Helper-required three-state UI. Product mode serves the built Web application from the Helper, while development mode may retain the separate Vite origin.

**Tech Stack:** Python 3.12, FastAPI 0.115.6, Pydantic 2.5.3, SQLite/FTS5, DuckDB 1.5.4, React 19.2, TypeScript 5.9, Zod 4.1, Vite 6.4, Vitest 3.2, Playwright 1.61, PowerShell on Win11.

## Global Constraints

- Work only in `D:/cursor/AI培训`; do not read, copy, hash, or modify `Course_AIProduct/` or `references/`.
- Do not use an isolated worktree. Preserve every pre-existing dirty dual-screen change and checkpoint it separately before overlapping edits.
- Direct Web access without a verified Helper session must fail closed; never generate a fixed or non-publishable course as a success path.
- The browser may submit only typed job specifications and may not execute arbitrary shell commands.
- Default UI must hide opaque IDs and digests; evidence retains them and exposes them only through a collapsed evidence view.
- Visuals are source-first, deterministic-dataset second, licensed network-real third; no fake evidence graphics.
- Physical dual-screen and release-signature certification stay `false` until their separate attended gates pass.
- Every task follows RED -> GREEN -> focused regression -> commit. Each milestone ends with cache cleanup, a reflection receipt, and `git gc --prune=now`.
- Use model route P2 (`gpt-5.6-sol/high`) for implementation and review; Ultra is not required unless a new security or hardware-timing decision appears.

---

### Task 1: Preserve the Non-Certifying Dual-Screen Checkpoint

**Files:**
- Verify only: current dirty paths reported by `git status --short`
- Verify: `.superpowers/sdd/dual-screen-task9-attended-checkpoint.json`
- Verify: `platform/windows/evidence/projection-integration.json`

**Interfaces:**
- Consumes: existing Task 9 Helper/Host/WebView2 changes and automated integration receipt.
- Produces: one dedicated commit whose evidence keeps `physicalDualScreenCertified=false` and `releaseSignatureCertified=false`; later tasks may safely edit `jobs.py`, `server.py`, and QA files.

- [ ] **Step 1: Run focused Task 9 regressions without the attended hardware gate**

```powershell
.tools/dotnet/dotnet.exe restore platform/windows/CourseStudio.ProjectionHost.slnx --packages .tools/nuget/packages --locked-mode
python -m pytest platform/helper/tests/test_projection_bundle.py platform/helper/tests/test_projection_jobs.py -q
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx --no-restore --filter "TestCategory!=projection_integration"
npm --prefix platform/web test -- --run src/services/native-projection.test.ts src/components/TeachingSetup.test.tsx
```

Expected: all automated tests pass; no visible hardware witness is launched.

- [ ] **Step 2: Run the explicit non-visible integration gate**

```powershell
$env:COURSE_PROJECTION_INTEGRATION_TEST='1'
try { python platform/qa/run.py projection-integration } finally { Remove-Item Env:COURSE_PROJECTION_INTEGRATION_TEST -ErrorAction SilentlyContinue }
```

Expected: Helper, Host, WebView2, Python, .NET, and Web integration checks pass and cleanup removes the temporary runtime.

- [ ] **Step 3: Verify the receipt remains explicitly non-certifying**

```powershell
python -c "import json,pathlib; p=pathlib.Path('platform/windows/evidence/projection-integration.json'); x=json.loads(p.read_text(encoding='utf-8')); assert x['physicalDualScreenCertified'] is False; assert x['releaseSignatureCertified'] is False"
```

Expected: exit code `0`.

- [ ] **Step 4: Commit only the current Task 9 implementation and checkpoint**

```powershell
git add -- platform/helper/course_helper/jobs.py platform/helper/course_helper/projection_bundle.py platform/helper/course_helper/projection_host.py platform/helper/course_helper/server.py platform/helper/pyproject.toml platform/helper/tests/test_projection_bundle.py platform/helper/tests/test_projection_jobs.py platform/helper/tests/test_projection_hardware.py platform/helper/tests/test_projection_integration.py platform/qa/run.py platform/qa/test_run.py platform/web/src/components/PresenterView.tsx platform/web/src/components/TeachingSetup.test.tsx platform/web/src/domain/projection.projection-integration.test.ts platform/web/src/services/native-projection.test.ts platform/web/src/services/native-projection.ts platform/windows .superpowers/sdd/dual-screen-task9-attended-checkpoint.json .superpowers/sdd/dual-screen-task9-phase-request.json .superpowers/sdd/dual-screen-task9-phase-start.json .superpowers/sdd/dual-screen-task9-route-receipt.json .superpowers/sdd/dual-screen-task9-route-request.json
git commit -m "feat: checkpoint non-certifying native projection pipeline"
```

Expected: the commit succeeds; its message and receipts make no hardware certification claim.

---

### Task 2: Add a Real Product Entry and Fail-Closed Web Startup

**Files:**
- Create: `platform/helper/course_helper/static_web.py`
- Create: `platform/helper/tests/test_static_web.py`
- Create: `platform/start-course-studio.ps1`
- Create: `platform/qa/test_start_course_studio.py`
- Modify: `platform/helper/course_helper/api.py`
- Modify: `platform/helper/course_helper/server.py`
- Modify: `platform/helper/tests/test_server.py`
- Create: `platform/web/src/components/HelperRequiredScreen.tsx`
- Create: `platform/web/src/components/HelperRequiredScreen.test.tsx`
- Modify: `platform/web/src/app/App.tsx`
- Modify: `platform/web/src/app/App.test.tsx`
- Modify: `platform/web/src/state/workspace.tsx`
- Modify: `platform/web/src/domain/course-agent.ts`
- Modify: `platform/web/src/domain/course-agent-helper.test.ts`

**Interfaces:**
- Consumes: `LaunchSession`, `HelperRuntime`, built `platform/web/dist`, and existing session fragment exchange.
- Produces: `mount_static_web(app: FastAPI, web_root: Path) -> None`; `HelperRuntime.web_root: Path | None`; a PowerShell entry that builds Web when needed and launches Helper; no default `LocalCourseAgent`.

- [ ] **Step 1: Write failing Helper static-serving and server tests**

```python
def test_product_mode_serves_index_and_spa_fallback(tmp_path: Path) -> None:
    web_root = tmp_path / "dist"
    web_root.mkdir()
    (web_root / "index.html").write_text("<main>Course Studio</main>", encoding="utf-8")
    app = create_test_app(web_root=web_root)
    client = TestClient(app)
    assert client.get("/").text == "<main>Course Studio</main>"
    assert client.get("/courses/current").text == "<main>Course Studio</main>"
    assert client.get("/v1/unknown").status_code == 404

def test_static_web_rejects_symlink_or_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="web root is invalid"):
        validate_web_root(tmp_path / "missing")
```

- [ ] **Step 2: Run the new Helper tests and verify RED**

```powershell
python -m pytest platform/helper/tests/test_static_web.py platform/helper/tests/test_server.py -q
```

Expected: FAIL because `static_web`, `web_root`, and product-mode launch do not exist.

- [ ] **Step 3: Implement strict static Web validation and mount it after API routes**

```python
class CourseStudioStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code != 404 or path.startswith(("v1/", "health")):
            return response
        return await super().get_response("index.html", scope)

def validate_web_root(path: Path) -> Path:
    root = path.resolve(strict=True)
    index = root / "index.html"
    manifest = root / ".vite" / "manifest.json"
    if root.is_symlink() or not root.is_dir() or not index.is_file() or not manifest.is_file():
        raise ValueError("web root is invalid")
    return root

def mount_static_web(app: FastAPI, web_root: Path) -> None:
    app.mount("/", CourseStudioStaticFiles(directory=validate_web_root(web_root), html=True), name="course-studio-web")
```

Add `web_root: Path | None = None` to `HelperRuntime`; mount only after all `/v1` and `/health` routes are registered. Add `--web-root` to the restricted CLI; when present, require `--web-origin` to equal `http://127.0.0.1:<port>` and launch that same origin.

- [ ] **Step 4: Write failing Web tests for Helper-required behavior**

```tsx
it("does not expose template generation without a verified Helper", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "请从课程工作台启动" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "生成课程结构" })).not.toBeInTheDocument();
});
```

- [ ] **Step 5: Run the focused Web test and verify RED**

```powershell
npm --prefix platform/web test -- --run src/app/App.test.tsx src/components/HelperRequiredScreen.test.tsx
```

Expected: FAIL because the disconnected UI still opens the legacy workflow.

- [ ] **Step 6: Remove the default `LocalCourseAgent` and render the recovery screen**

```tsx
export function HelperRequiredScreen(): JSX.Element {
  return (
    <main className="helper-required-page">
      <section className="helper-required-card" role="alert">
        <h1>请从课程工作台启动</h1>
        <p>关闭此页面，然后双击“启动课程平台”重新打开。</p>
      </section>
    </main>
  );
}
```

Delete `LocalCourseAgent` and its fixed three-chapter template only after the new RED test passes. Keep domain projection helpers used by governed courses.

- [ ] **Step 7: Add the Win11 launcher and its structural QA test**

```powershell
$platformRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$workspace = [IO.Path]::GetFullPath((Split-Path -Parent $platformRoot))
$webRoot = Join-Path $platformRoot 'web'
$helperRoot = Join-Path $platformRoot 'helper'
$dist = Join-Path $webRoot 'dist'
$appData = Join-Path $env:LOCALAPPDATA 'CourseStudio'
$sourceRoot = Join-Path $appData 'sources'
$database = Join-Path $appData 'knowledge.db'
New-Item -ItemType Directory -Force -Path $appData, $sourceRoot | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $dist '.vite\manifest.json'))) {
  & npm.cmd --prefix $webRoot run build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Push-Location -LiteralPath $helperRoot
try {
  & python -m course_helper --database $database --app-data $appData --reference-root $sourceRoot --web-origin 'http://127.0.0.1:8765' --web-root $dist --port 8765
} finally {
  Pop-Location
}
exit $LASTEXITCODE
```

The QA test parses the script and asserts fixed loopback, literal workspace-relative paths, `-LiteralPath`, hidden secrets, and no arbitrary command parameter.

- [ ] **Step 8: Run focused tests and commit**

```powershell
python -m pytest platform/helper/tests/test_static_web.py platform/helper/tests/test_server.py platform/qa/test_start_course_studio.py -q
npm --prefix platform/web test -- --run src/app/App.test.tsx src/components/HelperRequiredScreen.test.tsx src/domain/course-agent-helper.test.ts
npm --prefix platform/web run typecheck
git add -- platform/helper/course_helper/static_web.py platform/helper/course_helper/api.py platform/helper/course_helper/server.py platform/helper/tests/test_static_web.py platform/helper/tests/test_server.py platform/start-course-studio.ps1 platform/qa/test_start_course_studio.py platform/web/src/app/App.tsx platform/web/src/app/App.test.tsx platform/web/src/components/HelperRequiredScreen.tsx platform/web/src/components/HelperRequiredScreen.test.tsx platform/web/src/state/workspace.tsx platform/web/src/domain/course-agent.ts platform/web/src/domain/course-agent-helper.test.ts
git commit -m "feat: add fail-closed personal product entry"
```

Expected: focused Python/Web tests and typecheck pass.

---

### Task 3: Define Personal Course Contracts and Persistence

**Files:**
- Create: `platform/helper/course_helper/domain/personal_course.py`
- Create: `platform/helper/course_helper/personal_runs.py`
- Create: `platform/helper/tests/test_personal_course_contracts.py`
- Create: `platform/helper/tests/test_personal_runs.py`
- Create: `platform/helper/course_helper/migrations/0010_personal_course_runs.sql`
- Modify: `platform/helper/course_helper/catalog.py`
- Modify: `platform/helper/course_helper/upgrades.py`
- Create: `platform/web/src/domain/personal-course-schema.ts`
- Create: `platform/web/src/domain/personal-course-schema.test.ts`

**Interfaces:**
- Produces: `PersonalCourseRequest`, `PersonalCourseRun`, `AttentionItem`, `AttentionBundle`, `PersonalCourseResult`; `create_personal_run`, `get_personal_run`, `advance_personal_run`, and `resolve_personal_attention`.
- Consumes: canonical JSON/digest helpers, `ActorRef`, source/card/course version IDs, and catalog transactions.

- [ ] **Step 1: Write failing Python contract tests**

```python
def test_personal_run_allows_only_declared_transitions() -> None:
    run = personal_run_fixture(status="queued")
    importing = run.advance("importing", evidence_id="evidence-import")
    assert importing.status == "importing"
    with pytest.raises(ValueError, match="transition"):
        importing.advance("ready", evidence_id="evidence-skip")

def test_attention_bundle_contains_no_safe_auto_action() -> None:
    with pytest.raises(ValidationError):
        AttentionItem(kind="visual-license", recommended_action="ignore")
```

- [ ] **Step 2: Run the contract tests and verify RED**

```powershell
python -m pytest platform/helper/tests/test_personal_course_contracts.py platform/helper/tests/test_personal_runs.py -q
```

Expected: FAIL because the models, migration, and repository do not exist.

- [ ] **Step 3: Implement strict Pydantic models and canonical status transitions**

```python
PersonalCourseStatus = Literal[
    "queued", "importing", "organizing_knowledge", "composing",
    "assigning_visuals", "validating", "needs_attention", "ready", "failed",
]

_TRANSITIONS = {
    "queued": {"importing", "failed"},
    "importing": {"organizing_knowledge", "needs_attention", "failed"},
    "organizing_knowledge": {"composing", "needs_attention", "failed"},
    "composing": {"assigning_visuals", "needs_attention", "failed"},
    "assigning_visuals": {"validating", "needs_attention", "failed"},
    "validating": {"ready", "needs_attention", "failed"},
    "needs_attention": {"importing", "organizing_knowledge", "composing", "assigning_visuals", "validating", "failed"},
    "ready": set(),
    "failed": set(),
}
```

Use `extra="forbid"`, bounded human text, unique source IDs, lowercase SHA-256 digests, timezone-aware timestamps, and opaque ID patterns already used by `jobs.py`.

- [ ] **Step 4: Implement the migration and compare-and-swap repository**

```sql
CREATE TABLE personal_course_runs (
  run_id TEXT PRIMARY KEY,
  request_digest TEXT NOT NULL,
  source_snapshot_digest TEXT NOT NULL,
  status TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX personal_course_runs_request_snapshot
ON personal_course_runs(request_digest, source_snapshot_digest);
```

`advance_personal_run` must update with `WHERE run_id=? AND revision=?`; zero changed rows raise `PersonalRunConflict`.

- [ ] **Step 5: Mirror the exact public projection in Zod and test hidden internals**

```ts
export const personalCourseViewSchema = z.object({
  status: z.enum(["creating", "needs-attention", "ready", "failed"]),
  phaseLabel: z.string().min(1).max(80),
  title: z.string().min(1).max(200).nullable(),
  chapterCount: z.number().int().nonnegative(),
  attentionCount: z.number().int().nonnegative(),
  canResume: z.boolean(),
  course: courseDocumentSchema.nullable(),
}).strict();

export const personalCourseResponseSchema = z.object({
  runId: z.string().regex(/^personal-run-[0-9a-f]{32}$/),
  view: personalCourseViewSchema,
}).strict();
```

Map every internal non-terminal phase to `status: "creating"` and its exact Chinese `phaseLabel`. `runId` exists only in the client/service response wrapper so the browser can poll and resume; React components receive `view` rather than the wrapper. The default view omits run IDs, version IDs, and digests; the structured course may retain opaque node keys for React identity, but components must never render them as text.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest platform/helper/tests/test_personal_course_contracts.py platform/helper/tests/test_personal_runs.py platform/helper/tests/test_upgrades.py -q
npm --prefix platform/web test -- --run src/domain/personal-course-schema.test.ts
git add -- platform/helper/course_helper/domain/personal_course.py platform/helper/course_helper/personal_runs.py platform/helper/course_helper/migrations/0010_personal_course_runs.sql platform/helper/course_helper/catalog.py platform/helper/course_helper/upgrades.py platform/helper/tests/test_personal_course_contracts.py platform/helper/tests/test_personal_runs.py platform/web/src/domain/personal-course-schema.ts platform/web/src/domain/personal-course-schema.test.ts
git commit -m "feat: persist personal course runs"
```

---

### Task 4: Implement the Safe Auto-Knowledge Policy

**Files:**
- Create: `platform/helper/course_helper/personal_knowledge.py`
- Create: `platform/helper/tests/test_personal_knowledge.py`
- Modify: `platform/helper/course_helper/cards.py`
- Modify: `platform/helper/course_helper/near_duplicates.py`
- Modify: `platform/helper/course_helper/lifecycle.py`

**Interfaces:**
- Consumes: imported candidate card versions, review tasks, near-duplicate results, tag vocabulary, and lineage.
- Produces: `KnowledgeOrganizationResult(published_card_version_ids, attention_items, evidence)` and `organize_personal_knowledge(catalog, source_version_ids, actor) -> KnowledgeOrganizationResult`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_source_bound_nonconflicting_card_is_named_tagged_and_published() -> None:
    result = organize_personal_knowledge(catalog, (source_version_id,), actor)
    card = catalog.get_card_version(result.published_card_version_ids[0])
    assert card.title == "用来源约束生成"
    assert {tag.dimension_id for tag in card.tags} >= {"topic", "skill", "source-type"}
    assert result.attention_items == ()

def test_conflict_and_unknown_visual_license_share_one_attention_bundle() -> None:
    result = organize_personal_knowledge(conflicting_catalog, source_ids, actor)
    assert {item.kind for item in result.attention_items} == {"knowledge-conflict", "visual-license"}
```

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest platform/helper/tests/test_personal_knowledge.py -q
```

Expected: FAIL because the policy service does not exist.

- [ ] **Step 3: Implement deterministic naming, multi-tagging, and safe publication**

```python
def auto_publishable(candidate: KnowledgeCardVersion, open_tasks: tuple[ReviewTask, ...]) -> bool:
    return (
        bool(candidate.citations)
        and all(citation.source_version_id for citation in candidate.citations)
        and not any(task.subject_version_id == candidate.version_id for task in open_tasks)
        and candidate.lifecycle_status == "candidate"
    )
```

Title selection uses the nearest non-empty heading for the card's cited chunks, normalizes whitespace, and truncates at 40 Chinese characters without appending internal IDs. Existing semantic units are upgraded through lineage instead of duplicated.

- [ ] **Step 4: Run focused knowledge regressions and commit**

```powershell
python -m pytest platform/helper/tests/test_personal_knowledge.py platform/helper/tests/test_cards.py platform/helper/tests/test_near_duplicates.py platform/helper/tests/test_lifecycle.py -q
git add -- platform/helper/course_helper/personal_knowledge.py platform/helper/course_helper/cards.py platform/helper/course_helper/near_duplicates.py platform/helper/course_helper/lifecycle.py platform/helper/tests/test_personal_knowledge.py
git commit -m "feat: automate safe personal knowledge organization"
```

---

### Task 5: Build the Resumable Personal Course Orchestrator

**Files:**
- Create: `platform/helper/course_helper/personal_orchestrator.py`
- Create: `platform/helper/course_helper/personal_supervisor.py`
- Create: `platform/helper/tests/test_personal_orchestrator.py`
- Create: `platform/helper/tests/test_personal_supervisor.py`
- Modify: `platform/helper/course_helper/composer.py`
- Modify: `platform/helper/course_helper/slide_builder.py`
- Modify: `platform/helper/course_helper/source_visuals.py`
- Modify: `platform/helper/course_helper/network_visuals.py`

**Interfaces:**
- Consumes: `PersonalCourseRequest`, `PersonalCourseRun`, upload/import results, `organize_personal_knowledge`, course composer, Slide builder, visual services, validate/publish services.
- Produces: `create_personal_course_run(config, request, actor) -> PersonalCourseRun`; `resume_personal_course(config, run_id, actor) -> PersonalCourseRun`; `PersonalCourseSupervisor.start(run_id)`, `.resume_pending()`, and `.shutdown()`; one `AttentionBundle` at most.

- [ ] **Step 1: Write failing orchestrator tests**

```python
def test_one_request_reaches_ready_without_manual_card_or_visual_steps(tmp_path: Path) -> None:
    queued = create_personal_course_run(config(tmp_path), request_for("fixture.md"), actor)
    run = resume_personal_course(config(tmp_path), queued.run_id, actor)
    assert run.status == "ready"
    assert run.result is not None
    assert run.result.title == "个人 AI 工作流实战"
    assert run.result.chapter_count > 0

def test_restart_resumes_after_last_committed_evidence_without_duplicates(tmp_path: Path) -> None:
    interrupted = run_until(config(tmp_path), request, stop_after="organizing_knowledge")
    resumed = resume_personal_course(config(tmp_path), interrupted.run_id, actor)
    assert resumed.status == "ready"
    assert catalog.count_course_versions(resumed.result.course_logical_id) == 1
```

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest platform/helper/tests/test_personal_orchestrator.py -q
```

Expected: FAIL because the orchestrator does not exist.

- [ ] **Step 3: Implement one bounded state-machine loop**

```python
def resume_personal_course(config: WorkerRuntimeConfig, run_id: str, actor: ActorRef) -> PersonalCourseRun:
    run = get_personal_run(config.database_path, run_id)
    while run.status not in {"ready", "needs_attention", "failed"}:
        handler = _PHASE_HANDLERS[run.status]
        outcome = handler(config=config, run=run, actor=actor)
        run = advance_personal_run(config.database_path, run, outcome)
    return run
```

Each handler writes evidence before the compare-and-swap transition. Re-entry first reopens bound results by digest. An existing matching result is reused; a mismatch fails closed.

- [ ] **Step 4: Write and implement the bounded asynchronous supervisor**

```python
class PersonalCourseSupervisor:
    def __init__(self, config: WorkerRuntimeConfig, *, max_workers: int = 1) -> None:
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="personal-course")
        self._active: dict[str, Future[PersonalCourseRun]] = {}

    def start(self, run_id: str, actor: ActorRef) -> None:
        if run_id not in self._active:
            self._active[run_id] = self._executor.submit(resume_personal_course, self._config, run_id, actor)

    def resume_pending(self) -> None:
        for run in list_resumable_personal_runs(self._config.database_path):
            self.start(run.run_id, ActorRef(actor_type="system", actor_id="personal-resume"))
```

Tests use a blocking phase handler to prove `start` returns without waiting, duplicate `start` calls create one future, `resume_pending` restarts persisted work, and `shutdown` joins or cancels without orphan threads.

- [ ] **Step 5: Enforce source-first authentic visuals and one attention bundle**

```python
def choose_visual(candidates: Sequence[VisualCandidate]) -> VisualCandidate | None:
    ordered = sorted(candidates, key=lambda item: (item.origin_rank, -item.quality_score, item.version_id))
    return next((item for item in ordered if item.provenance_verified and item.license_exportable), None)
```

Ranks are source document, deterministic dataset chart, licensed network asset. Missing reliable visuals append an attention item; they do not create an unproven placeholder asset.

- [ ] **Step 6: Run composition/visual regressions and commit**

```powershell
python -m pytest platform/helper/tests/test_personal_orchestrator.py platform/helper/tests/test_personal_supervisor.py platform/helper/tests/test_composer.py platform/helper/tests/test_slide_builder.py platform/helper/tests/test_source_visuals.py platform/helper/tests/test_network_visuals.py -q
git add -- platform/helper/course_helper/personal_orchestrator.py platform/helper/course_helper/personal_supervisor.py platform/helper/course_helper/composer.py platform/helper/course_helper/slide_builder.py platform/helper/course_helper/source_visuals.py platform/helper/course_helper/network_visuals.py platform/helper/tests/test_personal_orchestrator.py platform/helper/tests/test_personal_supervisor.py
git commit -m "feat: orchestrate resumable personal courses"
```

---

### Task 6: Expose Typed Personal Course Jobs and a Minimal Web Client

**Files:**
- Modify: `platform/helper/course_helper/jobs.py`
- Modify: `platform/helper/course_helper/api.py`
- Modify: `platform/helper/tests/test_api.py`
- Create: `platform/helper/tests/test_personal_jobs.py`
- Modify: `platform/web/src/domain/helper-contracts-schema.ts`
- Modify: `platform/web/src/domain/helper-contracts-schema.test.ts`
- Modify: `platform/web/src/domain/governed-job-factory.ts`
- Modify: `platform/web/src/services/knowledge-client.ts`
- Modify: `platform/web/src/services/knowledge-client.test.ts`

**Interfaces:**
- Produces typed jobs `personal_course_create`, `personal_course_status`, and `personal_course_resolve`; Web methods `createPersonalCourse`, `getPersonalCourse`, and `resolvePersonalCourseAttention`.
- Consumes the Task 3 schemas plus Task 5 orchestrator and supervisor. Create and resolve return the persisted run projection immediately after scheduling; status is read-only polling.

- [ ] **Step 1: Write failing exact-schema and authentication tests**

```python
def test_personal_course_create_rejects_extra_fields_and_bad_digest() -> None:
    response = authenticated_post("/v1/jobs", {**valid_create_job(), "internalId": "leak"})
    assert response.status_code == 422

def test_personal_course_status_projects_no_internal_ids() -> None:
    result = run_job(valid_status_job()).result
    assert set(result) == {"runId", "view"}
    assert set(result["view"]) == {"status", "phaseLabel", "title", "chapterCount", "attentionCount", "canResume", "course"}
```

- [ ] **Step 2: Run Python and Web contract tests and verify RED**

```powershell
python -m pytest platform/helper/tests/test_personal_jobs.py platform/helper/tests/test_api.py -q
npm --prefix platform/web test -- --run src/domain/helper-contracts-schema.test.ts src/services/knowledge-client.test.ts
```

Expected: FAIL because the new discriminated-union members and clients do not exist.

- [ ] **Step 3: Add exact typed jobs, ceilings, handlers, and public projection**

```python
class PersonalCourseCreateJob(_KnowledgeMutationJob):
    type: Literal["personal_course_create"]
    request: HttpPersonalCourseRequest

class PersonalCourseStatusJob(HttpRequestModel):
    type: Literal["personal_course_status"]
    run_id: str = Field(pattern=r"^personal-run-[0-9a-f]{32}$")
```

Create and resolve have a 5-second request ceiling because they only persist/schedule work. The supervisor enforces per-phase ceilings, 50 sources, 500 cards, 200 slides, and 64 MiB returned artifacts. Status is read-only with a 5-second ceiling. Resolve requires expected attention digest and an allowlisted resolution enum. Add `personal_course_supervisor: PersonalCourseSupervisor | None` to `HelperRuntime`, dispatch these three typed jobs in `api.py`, call `resume_pending()` at product startup, and call `shutdown()` on every server exit.

- [ ] **Step 4: Add matching Zod contracts and client methods**

```ts
createPersonalCourse(job: PersonalCourseCreateJob) {
  return this.#runJob(job, personalCourseCreateJobSchema, personalCourseResponseSchema);
}
```

Parse Helper results before returning them; store `runId` in workspace state for polling and pass only `response.view` to React.

- [ ] **Step 5: Run focused tests and commit**

```powershell
python -m pytest platform/helper/tests/test_personal_jobs.py platform/helper/tests/test_api.py platform/helper/tests/test_projection_jobs.py -q
npm --prefix platform/web test -- --run src/domain/helper-contracts-schema.test.ts src/services/knowledge-client.test.ts
git add -- platform/helper/course_helper/jobs.py platform/helper/course_helper/api.py platform/helper/tests/test_personal_jobs.py platform/helper/tests/test_api.py platform/web/src/domain/helper-contracts-schema.ts platform/web/src/domain/helper-contracts-schema.test.ts platform/web/src/domain/governed-job-factory.ts platform/web/src/services/knowledge-client.ts platform/web/src/services/knowledge-client.test.ts
git commit -m "feat: expose governed personal course jobs"
```

---

### Task 7: Replace the Four-Step Default UI with the Personal Flow

**Files:**
- Create: `platform/web/src/components/PersonalCourseCreate.tsx`
- Create: `platform/web/src/components/PersonalCourseProgress.tsx`
- Create: `platform/web/src/components/PersonalCourseAttention.tsx`
- Create: `platform/web/src/components/PersonalCourseHome.tsx`
- Create corresponding `*.test.tsx` files for all four components
- Modify: `platform/web/src/app/App.tsx`
- Modify: `platform/web/src/app/app.css`
- Modify: `platform/web/src/app/tokens.css`
- Modify: `platform/web/src/components/CourseEditor.tsx`
- Modify: `platform/web/src/components/WorkflowHeader.tsx`
- Modify: `platform/web/src/state/workspace.tsx`
- Modify: `platform/web/src/state/storage.ts`

**Interfaces:**
- Consumes `KnowledgeClient` personal-course methods and `PersonalCourseView`.
- Produces three user states: create, creating/attention, and course home; the existing editor and teaching views remain downstream projections.

- [ ] **Step 1: Write failing primary-flow component tests**

```tsx
it("starts a course from sources and one human request", async () => {
  renderPersonalApp({ helper: readyHelper });
  await user.upload(screen.getByLabelText("选择课程资料"), fixtureFiles);
  await user.type(screen.getByLabelText("你想做一门什么课？"), "为个人讲师制作 60 分钟 AI 工作流实战课");
  await user.click(screen.getByRole("button", { name: "开始组课" }));
  expect(await screen.findByRole("heading", { name: "正在整理知识" })).toBeVisible();
});

it("accepts a directory as one source selection", async () => {
  renderPersonalApp({ helper: readyHelper });
  await user.upload(screen.getByLabelText("选择资料目录"), fixtureDirectoryFiles);
  expect(screen.getByText("已选择 3 个文件")).toBeVisible();
});

it("never renders opaque identifiers in the default course view", () => {
  render(<PersonalCourseHome view={readyViewWithInternalEvidence} />);
  expect(screen.queryByText(/personal-run-|course-version-|[0-9a-f]{64}/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run focused tests and verify RED**

```powershell
npm --prefix platform/web test -- --run src/components/PersonalCourseCreate.test.tsx src/components/PersonalCourseProgress.test.tsx src/components/PersonalCourseAttention.test.tsx src/components/PersonalCourseHome.test.tsx
```

Expected: FAIL because the personal-flow components do not exist.

- [ ] **Step 3: Implement the create and real-progress states using existing light tokens**

```tsx
<form aria-labelledby="personal-course-heading" onSubmit={startCourse}>
  <input type="file" multiple aria-label="选择课程资料" />
  <input type="file" multiple aria-label="选择资料目录" {...({ webkitdirectory: "" } as Record<string, string>)} />
  <textarea aria-label="你想做一门什么课？" maxLength={2_000} />
  <button className="primary-button" disabled={!canStart}>开始组课</button>
</form>
```

Progress labels map only from persisted Helper phases. Do not use timers to fabricate progress.

- [ ] **Step 4: Implement one attention bundle and a course home**

```tsx
<section aria-labelledby="attention-heading">
  <h1 id="attention-heading">有几项内容需要你确认</h1>
  <p>{view.attentionCount} 项问题已集中整理。</p>
  <button className="primary-button" onClick={acceptRecommended}>接受建议并继续</button>
  <button className="secondary-button" onClick={toggleDetails}>查看详情</button>
</section>
```

Course home exposes Preview/Edit/Teach actions. “运行证据” remains collapsed. Dual-screen appears under Teach and never gates course creation.

- [ ] **Step 5: Remove redundant default expert navigation after replacements pass**

Delete the default use of `ImportStep`, `GenerateStep`, `WorkflowHeader` four-step navigation, `KnowledgeReviewDrawer`, and `GovernedCoursePanel` from `App.tsx`. Keep reusable editor, evidence, visual, and teaching components; delete source files only if `rg` proves they have no remaining consumer outside obsolete tests.

- [ ] **Step 6: Run component, type, and build checks and commit**

```powershell
npm --prefix platform/web test -- --run src/components/PersonalCourseCreate.test.tsx src/components/PersonalCourseProgress.test.tsx src/components/PersonalCourseAttention.test.tsx src/components/PersonalCourseHome.test.tsx src/app/App.test.tsx src/components/CourseEditor.test.tsx src/components/TeachingSetup.test.tsx
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
git add -- platform/web/src
git commit -m "feat: make personal course creation the default flow"
```

---

### Task 8: Prove the One-Click Browser Flow and Remove Obsolete Evidence

**Files:**
- Replace: `platform/web/e2e/knowledge-course.spec.ts`
- Modify: `platform/web/e2e/global-setup.mjs`
- Modify: `platform/web/e2e/global-teardown.mjs`
- Replace: `platform/web/design-qa.md`
- Replace: `platform/web/evidence/course-composition-browser-e2e.json`
- Create: `platform/web/evidence/personal-course-browser-e2e.json`
- Modify: `platform/qa/run.py`
- Modify: `platform/qa/test_run.py`
- Delete after replacement verification: `platform/web/evidence/design-qa-comparison.png`
- Delete after replacement verification: `platform/web/evidence/design-qa-edit.png`
- Delete after replacement verification: `platform/web/evidence/teaching-stage.png`
- Delete after replacement verification: `platform/web/evidence/teaching-presenter.png`

**Interfaces:**
- Consumes the product-mode Helper, personal job API, current Markdown/PPTX/dataset test fixtures, editor, and optional teaching projection.
- Produces a hash-bound E2E receipt proving one primary action, at most one attention bundle, hidden IDs, real source linkage, restart recovery, and optional non-certifying teaching.

- [ ] **Step 1: Write the new failing E2E acceptance before changing old evidence gates**

```ts
test("one personal action creates and reopens a governed course", async ({ page }) => {
  await page.goto(lifecycle.launchUrl);
  await page.getByLabel("选择课程资料").setInputFiles([
    lifecycle.fixtures.markdown,
    lifecycle.fixtures.pptx,
    lifecycle.fixtures.dataset,
  ]);
  await page.getByLabel("你想做一门什么课？").fill("为个人讲师制作 60 分钟 AI 工作流实战课");
  await page.getByRole("button", { name: "开始组课" }).click();
  await expect(page.getByRole("heading", { name: "个人 AI 工作流实战" })).toBeVisible({ timeout: 180_000 });
  await expect(page.locator("body")).not.toContainText(/personal-run-|course-version-|[0-9a-f]{64}/);
});
```

- [ ] **Step 2: Run the E2E and verify RED**

```powershell
npm --prefix platform/web run test:e2e -- --grep "one personal action"
```

Expected: FAIL until the personal flow is wired end to end.

- [ ] **Step 3: Complete the E2E with authoritative checks**

Assert exactly one click on the primary compose action, zero manual knowledge-card publication clicks, zero manual visual-binding clicks, at most one attention screen, persisted course reopen, source/dataset/visual provenance, `physicalDualScreenCertified=false`, no page errors, no console errors, and no failed same-origin requests.

- [ ] **Step 4: Replace stale visual QA and old absolute worktree paths**

Capture current product-entry, progress/attention when applicable, and ready-course screenshots at a fixed desktop viewport. Update `design-qa.md` and QA hashes to the current main-workspace paths. Only then delete old images and assertions whose sole purpose was the obsolete four-step default flow.

- [ ] **Step 5: Run E2E, QA meta-tests, and commit**

```powershell
npm --prefix platform/web run test:e2e -- --grep "one personal action"
python -m pytest platform/qa/test_run.py -q
git add -- platform/web/e2e platform/web/design-qa.md platform/web/evidence platform/qa/run.py platform/qa/test_run.py
git commit -m "test: certify the personal one-click course flow"
```

---

### Task 9: Full Verification, Deep Cleanup, and Product Reflection

**Files:**
- Create: `.superpowers/sdd/personal-first-final-verification.json`
- Create: `.superpowers/sdd/personal-first-final-cleanup.json`
- Create: `docs/superpowers/reviews/2026-07-21-personal-first-product-reflection.md`
- Modify only if a verified issue is found: files owned by Tasks 2-8

**Interfaces:**
- Consumes all prior task commits and acceptance receipts.
- Produces the final green verification, cleanup inventory, product-direction reflection, and the single next action; it does not certify physical dual-screen hardware.

- [ ] **Step 1: Run focused tests for every changed subsystem**

```powershell
python -m pytest platform/helper/tests/test_static_web.py platform/helper/tests/test_personal_course_contracts.py platform/helper/tests/test_personal_runs.py platform/helper/tests/test_personal_knowledge.py platform/helper/tests/test_personal_orchestrator.py platform/helper/tests/test_personal_jobs.py -q
npm --prefix platform/web test -- --run src/domain/personal-course-schema.test.ts src/components/PersonalCourseCreate.test.tsx src/components/PersonalCourseProgress.test.tsx src/components/PersonalCourseAttention.test.tsx src/components/PersonalCourseHome.test.tsx src/app/App.test.tsx
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the broad release gates**

```powershell
python platform/qa/run.py all
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
npm --prefix platform/web run test:e2e -- --grep "one personal action"
.tools/dotnet/dotnet.exe test platform/windows/CourseStudio.ProjectionHost.slnx --no-restore --filter "TestCategory!=projection_integration"
```

Expected: all automated gates pass; hardware certification remains false unless a separate attended run has genuinely passed.

- [ ] **Step 3: Delete only reproducible caches and verify every target remains inside the workspace**

```powershell
$names = @('dist','.vite','bin','obj','__pycache__','.pytest_cache','test-results','playwright-report')
Get-ChildItem -LiteralPath 'D:\cursor\AI培训\platform' -Directory -Recurse -Force |
  Where-Object { $_.Name -in $names -and $_.FullName -notlike '*\node_modules\*' -and $_.FullName -notlike '*\.embedding-model*\*' } |
  ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
git gc --prune=now
git count-objects -v
```

Expected: one pack preferred, zero loose and garbage objects; dependencies, model caches, source assets, current evidence, and runtime user data remain.

- [ ] **Step 4: Write verification, cleanup, and reflection receipts**

The reflection answers with evidence: whether a personal user can start the product simply, whether one-click composition is real rather than templated, whether attention is compressed, whether IDs are hidden, whether visuals remain authentic, whether dual-screen is optional, what remains blocked, and the single next product action.

- [ ] **Step 5: Verify receipts and commit**

```powershell
python -m json.tool .superpowers/sdd/personal-first-final-verification.json > $null
python -m json.tool .superpowers/sdd/personal-first-final-cleanup.json > $null
git add -- .superpowers/sdd/personal-first-final-verification.json .superpowers/sdd/personal-first-final-cleanup.json docs/superpowers/reviews/2026-07-21-personal-first-product-reflection.md
git commit -m "docs: verify personal course studio release"
```

Expected: receipts parse, reference fresh command output and hashes, and make no unsupported hardware claim.

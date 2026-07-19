# Personal AI Course Platform Reboot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans`, `superpowers:test-driven-development`, and
> `superpowers:verification-before-completion`. Use Product Design plus Image2
> for the final visual reference, and Playwright for browser verification.

**Goal:** Rebuild this repository into a concise, local-first personal AI
course studio that turns source material into a traceable knowledge base, a
complete HTML course, verified runtime evidence, and a Windows dual-screen
presentation session.

**Architecture:** One Next.js studio talks to one local FastAPI engine over
HTTP and WebSocket. The engine owns SQLite, schema validation, ingestion,
retrieval, course generation, typed jobs, evidence, and the persisted Ralph
state machine. A small Electron desktop process owns physical displays and
presentation windows. All three surfaces consume the same versioned JSON
contracts. `Course_AIProduct/` is a protected course workspace and acceptance
fixture, never platform source code.

**Tech Stack:** Next.js 15, React 19, TypeScript, Electron, Python, FastAPI,
SQLite with FTS5 and JSON functions, JSON Schema, Pydantic, pytest, Vitest or
Node test, Playwright, pnpm workspaces.

## 1. Non-Negotiable Product Contract

The product is a single-user, local-first, Codex-oriented course engineering
studio. It has exactly three jobs:

1. Build a personal, source-traceable knowledge base.
2. Generate and edit complete HTML courses from requirements and knowledge.
3. Rehearse and control presenter/stage windows on a Windows dual-screen setup.

The primary interaction is a goal, not a dashboard. A natural-language goal
starts a visible flow:

```text
Goal -> Sources -> Knowledge -> Course -> Verify -> Present or Export
```

The durable product spine is:

```text
SourceAsset -> ExtractedChunk -> KnowledgeCard -> CourseRequirement
-> CourseOutline -> SlideNode -> RuntimeManifest -> Evidence -> PublishGate
```

### In Scope

- Personal projects and course workspaces.
- Markdown, PDF, PPTX, dataset, and codebase source ingestion.
- AST-first Markdown and notes-first PPTX parsing.
- Local retrieval with SQLite FTS5 and inspectable provenance.
- Requirement, outline, Slide AST, presenter notes, stage, print, and export.
- Typed jobs including `python_snippet`, `dataset_sql`, `chart_build`,
  `rag_query`, `agent_run`, and `doc_export`.
- A provider-neutral agent gateway with a deterministic test adapter and one
  verified local/live adapter when credentials and runtime authority exist.
- Skills and MCP only as adapters behind typed jobs; they are not new product
  sections and never bypass the engine allowlist.
- Electron-owned Windows display detection, role assignment, fullscreen,
  reconnection, and evidence receipts.
- Offline course distribution.

### Explicit Non-Goals

- Accounts, teams, roles, billing, cloud-first storage, or collaboration.
- A generic SaaS dashboard, landing page, or slideshow toy.
- Direct browser shell execution or direct browser model calls.
- Raw HTML as the first durable course artifact.
- A new PPTX-to-HTML converter.
- Microservices, Nx, Turborepo, a vector database, or a second operational
  database.
- Hard-coding `Course_AIProduct/`, FDE, Token, or any other course into platform
  routes, schemas, or default UI copy.
- Claiming physical dual-screen certification from browser simulation.

## 2. Factual Diagnosis And Reset Rationale

The 2026-07-14 repository audit found these structural failures:

- 227 commits accumulated in ten days, with the largest file-touch volume in
  `evidence/`, not in the user workflow.
- The root `package.json` exposes 157 scripts.
- The current web app has 10 page routes, 30 API routes, and 63 CSS modules.
- The Python platform has 186 modules and 48 QA gates.
- `content/` is about 12.3 GB, `dataset/` about 9.6 GB, and several generated
  or evidence directories overlap in responsibility.
- The UI is a light-blue card dashboard whose modules mirror implementation
  concepts instead of the user's goal.
- The course surface leaks one-off Token course content into the platform.
- The graph view has visibly disconnected relationships and weak provenance.
- `platform/web/app/screen/ScreenConsoleModel.ts` contains mojibake in user-facing
  strings.
- `platform/tools/evolve_phases.py` marks every Ralph phase `done: True`
  statically. This records ritual, not a running state machine.
- The saved browser audit at
  `output/playwright/platform-audit-2026-07-14/01-home-desktop.png` rendered a
  giant icon and six failed static resources. It is failure evidence, not a
  visual reference.

The useful assets are narrower:

- The schema-first product spine and evidence object idea.
- The existing Electron helper split in `platform/playback/electron-main.mjs`,
  `helper-core.mjs`, `helper-web.mjs`, and `helper-windows.mjs`.
- BroadcastChannel plus localStorage recovery for same-origin presentation
  sync.
- Selected layout lessons from stage and presenter views.
- `Course_AIProduct/` as a real, protected acceptance course.

## 3. Adversarial Reflection And Autonomous Decisions

The reboot is based on the following cross-role review. These are product
requirements, not optional polish:

| Perspective | Real failure | Decision |
|---|---|---|
| Personal user | The platform exposes its internal modules before the user's current goal. | Resume one goal and one next action on launch. |
| Product | Knowledge, course, evidence, runtime, and screen control behave like separate products. | Make them steps and contextual views of one artifact flow. |
| Course author | Course-specific prose and fixtures leak into platform code. | Keep all course truth in workspace assets and manifests. |
| Lecturer | More content and more slides have been mistaken for teaching quality. | Require source-backed presenter notes, a visible narrative spine, and verifiable demonstrations. |
| Learner | Repetition increases length without adding a new decision, example, or proof. | Add semantic duplication findings at outline and slide review. |
| AI engineer | The UI has no truthful model/tool orchestration boundary. | Route every agent, skill, and MCP action through typed engine jobs and evidence. |
| Architect | Routes, scripts, modules, and gates encode historical work rather than stable concepts. | Rebuild around the twelve contracts and delete only after replacement proof. |
| Safety reviewer | Browser APIs and execution responsibilities are mixed. | Browser submits typed jobs; engine and Electron own privileged work. |
| Data engineer | Large datasets and generated copies are mixed with canonical assets. | Reference large data by path, schema, bounded sample, and hash. |
| Operator | Simulated readiness can look like physical dual-screen certification. | Keep readiness and onsite certification as separate states. |
| Designer | Card grids and blue status pills communicate bureaucracy, not technical capability. | Use one precise work surface, progressive disclosure, and restrained status color. |
| Maintainer | Evidence churn and broad QA after tiny changes make every loop slower. | One receipt per run, focused verification per change, broad QA only at milestones. |

### Autonomous Course Requirements

The platform remains generic, but its contracts must support the already
confirmed acceptance profile without special-case code:

```text
deliveryFormat: single_continuous_session
speakerMode: instructor_manuscript
learnerParticipation: none
distributionMode: offline
presenterNotes: required
```

For this profile, "hands-on" means a lecturer-operated demonstration, dataset
query, code run, retrieval result, chart, or other evidence-producing action.
It does not mean a learner form, exercise, quiz, or collaborative activity.

Course review must report:

- unsupported claims and missing provenance;
- repeated claims that add no new example, decision, or evidence;
- conceptual sections with no lecturer-operable demonstration;
- demonstrations with no typed job or verifiable fallback artifact;
- slides with no presenter notes or notes that merely repeat slide text;
- broken narrative transitions, excessive density, and invalid timing;
- offline exports that depend on network-only assets.

The critic reports these findings. It never rewrites the course automatically
or changes acceptance thresholds. The authoring loop chooses one root cause per
cycle.

### Speed Budget

- Use one primary deliverable and no more than two independent workers.
- Do not run a full build when a focused unit test answers the changed question.
- Cache input hashes for unchanged ingestion, screenshots, and exports.
- Do not create a new lesson, matrix, backlog, or evidence file for each cycle.
- A milestone handoff contains only current state, decisions, changed paths,
  verification, blockers, and one next action.
- Stop after acceptance is green or two reviews produce no new evidence.

## 4. Simplified Product Experience

### Global Information Architecture

There are only four global entries:

1. **Studio** - current goal, active run, and resumable project flow.
2. **Knowledge** - sources, extracted chunks, cards, retrieval, and provenance.
3. **Courses** - requirement, outline, slide editing, preview, and export.
4. **Present** - rehearsal, presenter/stage launch, and physical screen status.

The corresponding public routes are limited to:

```text
/
/knowledge
/course/[courseId]
/present/[courseId]
```

Cards, slides, runtime, publish, screen setup, QA, evidence, and graph are
contextual modes or inspector tabs. They are not top-level destinations.

### Shell Contract

Every authoring route uses the same shell:

- One compact left rail with four real icons and concise labels.
- One narrow top bar for project/course context and current run state.
- One uninterrupted main work surface.
- One right inspector, collapsed by default, for provenance, evidence, or
  properties.
- One bottom AI command bar for goals, commands, attachments, and run control.

The first screen resumes the current goal. It does not show metric cards,
marketing copy, feature explanations, or an empty analytics dashboard.

### Visual Contract

Use the three earlier Image2 explorations only as input. Before frontend code,
generate exactly one final simplified Image2 reference with this direction:

- Precision technical studio, not cyberpunk and not generic blue SaaS.
- Warm-neutral canvas, white work surface, graphite rail, crisp 1 px dividers.
- Cyan only for active AI/run state, green for verified, amber for review,
  coral for errors and destructive actions.
- Radius 4-6 px, almost no shadow, no gradients, no decorative blobs, no nested
  cards, and no pill overload.
- System Chinese typography plus an Inter-compatible Latin stack; letter
  spacing is zero.
- Dark canvas is allowed only for a graph or presentation stage when it improves
  legibility. The entire app is not dark.
- Use Lucide icons. Icon-only buttons have `aria-label`, visible keyboard focus,
  hover/focus tooltip, an approximately 44 px hit target, and Escape behavior
  for opened popups.

The final Image2 reference must show one desktop state at 1440x900 with the
inspector collapsed. A second reference is not generated unless the first
violates this contract. The image is a design reference, not a screenshot to
embed in the app.

### Interaction Budget

- A new goal starts or resumes within two user actions.
- The active step, current artifact, and verification state are visible without
  opening a second page.
- The inspector is the only secondary panel and never nests cards.
- At most one modal or popover can be open.
- Destructive actions require an explicit confirmation with exact scope.
- Long visible instructions are replaced by labels, state, and contextual
  tooltips.

## 5. Target Repository Shape

```text
D:/cursor/AI培训/
|-- apps/
|   |-- studio/                 # Next.js UI; four product routes
|   `-- desktop/                # Electron dual-screen controller
|-- engine/
|   |-- pyproject.toml
|   |-- src/aicourse/
|   |   |-- api/
|   |   |-- domain/
|   |   |-- ingest/
|   |   |-- knowledge/
|   |   |-- course/
|   |   |-- runtime/
|   |   |-- evidence/
|   |   |-- integrations/
|   |   `-- ralph/
|   `-- tests/
|-- contracts/
|   |-- catalog.json
|   `-- v1/*.schema.json
|-- tests/
|   `-- architecture/
|-- docs/
|   |-- decisions/
|   |-- migrations/
|   `-- superpowers/plans/
|-- Course_AIProduct/           # protected course workspace; not moved
|-- .local/                     # gitignored SQLite and transient runs
|-- package.json                # no more than 12 scripts
`-- pnpm-workspace.yaml
```

Do not create `packages/ui`, `services`, or a monorepo task-runner layer unless
a second real consumer proves the need. Colocate route-specific components in
the route or feature that owns them.

This shape follows the official guidance for Next.js route groups, private
folders, and the optional `src` directory, plus the Python Packaging Authority
guidance for a Python `src` layout:

- <https://nextjs.org/docs/app/getting-started/project-structure>
- <https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/>

### Root Script Budget

The final root package exposes no more than these 12 commands:

```text
dev
dev:studio
dev:desktop
test
test:engine
test:studio
test:desktop
typecheck
build
qa
ralph
clean:generated
```

`clean:generated` may remove only declared generated paths under `.local/` or
build outputs. It must reject arbitrary paths and never touch course sources.

## 6. Persistence And Contract Design

### SQLite Boundary

Use the standard Python `sqlite3` module. Do not add an ORM in the first
release. The database path is `${AI_TRAINING_HOME}/studio.db`, defaulting to
`.local/studio.db`.

Use these tables:

```text
projects
source_assets
extracted_chunks
knowledge_cards
course_requirements
course_outlines
slide_nodes
runtime_manifests
runtime_jobs
evidence
runs
run_events
agent_configs
migrations
knowledge_fts
```

Rules:

- SQLite is the working state and searchable index, not the only durable truth.
- Every publishable artifact can export to versioned JSON beside its course.
- JSON text columns use `json_valid` checks and are validated against the
  matching JSON Schema at write boundaries.
- FTS5 indexes normalized title, body, tags, and source labels. Search results
  always return source and chunk provenance.
- WAL mode is enabled by the single local daemon. The UI and Electron process
  never open the database directly.
- Migrations are ordered SQL files with a recorded checksum. No startup code
  silently rewrites schema history.
- Large datasets stay as referenced source assets. Only an explicitly selected
  table or bounded sample is imported into a controlled SQLite table.
- The existing 9.6 GB `dataset/` tree is never copied wholesale into SQLite or
  into `Course_AIProduct/`.

Primary database references:

- <https://www.sqlite.org/fts5.html>
- <https://www.sqlite.org/json1.html>
- <https://sqlite.org/wal.html>

### Versioned JSON Contracts

Create JSON Schema v1 contracts for:

```text
Project
SourceAsset
ExtractedChunk
KnowledgeCard
CourseRequirement
CourseOutline
SlideNode
RuntimeManifest
RuntimeJob
Evidence
RalphRun
RalphEvent
```

Every durable object includes `schemaVersion`, `id`, `projectId`, timestamps,
and provenance/evidence references where applicable. `RuntimeJob` is a closed
discriminated union. Unknown job types and extra executable fields fail closed.

An `Evidence` object includes job input hash, runner identity, stdout, stderr,
exit code, started/finished timestamps, duration, artifact paths and hashes,
optional screenshot paths, and verification state. Each run produces one
compact receipt plus referenced artifacts, not dozens of status files.

## 7. Runtime And Integration Boundaries

### Local Engine

One FastAPI process owns:

- project and source commands;
- ingestion and retrieval;
- course generation and artifact validation;
- typed job dispatch;
- evidence and run history;
- Ralph state transitions;
- HTTP query endpoints and WebSocket run events.

The browser submits typed JSON only. The engine maps each job type to an
allowlisted runner with argument validation, timeout, output directory, and
evidence capture. No endpoint accepts a raw shell command.

### Agent, Skill, And MCP Boundary

- `AgentGateway` has a deterministic fixture adapter for tests and an explicit
  configured adapter for real generation.
- The current packaged `codex.exe` resolves in PowerShell but returns access
  denied when launched directly. Do not pretend a local Codex CLI adapter is
  available until a fresh executable/authentication probe proves it.
- A live adapter emits schema-constrained artifacts and can never mark its own
  output verified.
- Skills are versioned instruction assets with metadata and declared inputs,
  outputs, tools, and verification. They are loaded through `agent_run`, not
  executed as arbitrary browser content.
- MCP connections are optional local integrations behind the same allowlist.
  MCP discovery does not grant execution authority automatically.
- Add an integration only when a concrete course flow needs it. Do not build an
  MCP marketplace or a separate skills dashboard.

### Presentation Boundary

Preserve the working responsibilities from `platform/playback/` but move them
behind a small state machine:

```text
idle -> detecting -> permission_required -> assigning -> opening
-> fullscreen_pending -> verifying -> ready -> recovering -> closed
```

Use BroadcastChannel for same-origin slide sync, localStorage for last-frame
recovery, and the engine WebSocket for runtime logs, artifacts, progress, and
reconnection. Electron owns display APIs and BrowserWindow placement. The
browser can request a typed presentation transition; it cannot place native
windows itself.

Primary runtime references:

- <https://fastapi.tiangolo.com/tutorial/sql-databases/>
- <https://fastapi.tiangolo.com/advanced/websockets/>
- <https://www.electronjs.org/docs/latest/api/browser-window>

## 8. Real Ralph Loop

Replace static phase declarations with persisted transitions:

```text
Sense -> Select -> Build -> Verify -> Critique -> Learn -> Stop
```

Each atomic loop has one goal, one root-cause hypothesis, one owned file set,
and one acceptance command. The loop writes `RalphRun` and `RalphEvent` rows and
exports one final receipt.

### Cycle Rules

- Maximum 3 cycles per atomic slice.
- Run the focused test after every Build.
- Run broad QA only at a milestone boundary.
- A critic is read-only and reports findings with severity, location, evidence,
  and a proposed root-cause class.
- A new cycle is allowed only when the previous verification or critic produced
  new evidence.
- The loop never weakens schemas, assertions, thresholds, or safety controls to
  get green.
- At most two workers are active, only on independent paths with separate
  acceptance. Shared contracts, lockfiles, migration files, and final receipts
  always have one writer.

### Stop Rules

Stop successfully when all acceptance commands are green and the critic has no
new HIGH finding. Stop and escalate when:

- the same root failure occurs twice;
- a cycle creates a regression outside its owned path;
- two consecutive critiques add no new evidence;
- credentials, physical hardware, or another external authority is required;
- the next proposed change is cleanup without product-value evidence.

No loop may create activity merely to keep the task alive.

## 9. Implementation Tasks

### Task 1: Freeze Boundaries And Write Failing Architecture Tests

**Files:**

- Create: `docs/decisions/0003-personal-ai-course-platform-reboot.md`
- Create: `docs/migrations/platform-reboot-map.json`
- Create: `tests/architecture/test_repo_boundaries.py`
- Modify: `.gitignore`

**Steps:**

- [ ] Record the current dirty tree, branch, remote, top-level counts, sizes,
  and SHA-256 inventory of unique large files. Do not hash build caches or
  `node_modules`.
- [ ] Declare `Course_AIProduct/` protected and assert that no file under
  `apps/`, `engine/`, or `contracts/` imports it or contains course-specific
  fixture IDs.
- [ ] Add failing tests for the target root script budget, allowed product
  routes, forbidden browser shell execution, and protected directory boundary.
- [ ] Add `.local/`, build outputs, browser artifacts, and transient receipts to
  `.gitignore` without ignoring durable course assets.

**Verify:**

```powershell
python -m pytest tests/architecture/test_repo_boundaries.py -q
git diff --check
```

The first test run is expected to fail only on declared target-shape gaps. It
must already pass the `Course_AIProduct/` protection checks.

### Task 2: Create The Minimal Workspace Skeleton

**Files:**

- Create: `pnpm-workspace.yaml`
- Modify: `package.json`
- Create: `apps/studio/package.json`
- Create: `apps/studio/tsconfig.json`
- Create: `apps/studio/next.config.mjs`
- Create: `apps/desktop/package.json`
- Create: `engine/pyproject.toml`

**Steps:**

- [ ] Add only the root scripts listed in the script budget.
- [ ] Scaffold the studio and desktop packages without copying legacy pages.
- [ ] Configure `engine/src/aicourse` as the Python source layout.
- [ ] Keep `platform/` operational during migration; do not delete or redirect
  it yet.
- [ ] Make the architecture test pass for workspace shape and script count.

**Verify:**

```powershell
pnpm install --lockfile-only
python -m pytest tests/architecture/test_repo_boundaries.py -q
pnpm typecheck
```

### Task 3: Establish Contracts V1

**Files:**

- Create: `contracts/catalog.json`
- Create: `contracts/v1/*.schema.json`
- Create: `engine/src/aicourse/domain/contracts.py`
- Create: `engine/tests/domain/test_contracts.py`
- Create: `apps/studio/src/lib/contracts.test.ts`

**Steps:**

- [ ] Write failing contract tests for valid minimum objects, invalid versions,
  missing provenance, unknown runtime job types, and executable extra fields.
- [ ] Implement the twelve schemas and a catalog that maps names to versions.
- [ ] Validate the same fixtures with Python and TypeScript.
- [ ] Add migration adapters only for legacy structures used in the first
  vertical slice; do not mirror every old schema.

**Verify:**

```powershell
python -m pytest engine/tests/domain/test_contracts.py -q
pnpm --dir apps/studio test -- contracts
```

### Task 4: Build The SQLite Core

**Files:**

- Create: `engine/src/aicourse/config.py`
- Create: `engine/src/aicourse/db.py`
- Create: `engine/src/aicourse/migrations/0001_core.sql`
- Create: `engine/src/aicourse/migrations/0002_fts.sql`
- Create: `engine/src/aicourse/repositories/*.py`
- Create: `engine/tests/test_db.py`
- Create: `engine/tests/test_repositories.py`

**Steps:**

- [ ] Write tests against a temporary database for migrations, checksums,
  foreign keys, WAL, JSON validity, FTS search, and transaction rollback.
- [ ] Implement one connection factory and explicit transaction helper.
- [ ] Implement repositories only for the contract objects required by the
  vertical slice.
- [ ] Export versioned JSON snapshots without changing source files.

**Verify:**

```powershell
python -m pytest engine/tests/test_db.py engine/tests/test_repositories.py -q
```

### Task 5: Complete One Source-To-Stage Vertical Slice

**Files:**

- Create: `engine/src/aicourse/ingest/markdown.py`
- Create: `engine/src/aicourse/knowledge/service.py`
- Create: `engine/src/aicourse/course/service.py`
- Create: `engine/src/aicourse/runtime/dispatcher.py`
- Create: `engine/src/aicourse/evidence/service.py`
- Create: `engine/tests/fixtures/minimal-course/source.md`
- Create: `engine/tests/test_vertical_slice.py`

**Steps:**

- [ ] Write one failing end-to-end test for Markdown AST chunks -> indexed
  knowledge card -> requirement -> outline -> one SlideNode -> one typed job ->
  Evidence -> stage payload.
- [ ] Parse Markdown with a real AST parser. Never collapse the document into
  one chunk or split only on blank lines.
- [ ] Use a deterministic agent fixture so the test is offline and repeatable.
- [ ] Ensure every generated object traces back to chunk and source IDs.
- [ ] Reject a runtime job that contains a shell string or path outside its
  declared workspace.

**Verify:**

```powershell
python -m pytest engine/tests/test_vertical_slice.py -q
```

### Task 6: Add The Local API, Run Stream, And Real Ralph State

**Files:**

- Create: `engine/src/aicourse/api/app.py`
- Create: `engine/src/aicourse/api/routes/*.py`
- Create: `engine/src/aicourse/api/events.py`
- Create: `engine/src/aicourse/ralph/loop.py`
- Create: `engine/src/aicourse/ralph/critic.py`
- Create: `engine/tests/api/test_api.py`
- Create: `engine/tests/ralph/test_loop.py`

**Steps:**

- [ ] Write failing tests for typed command submission, WebSocket event order,
  reconnect replay, cancellation, idempotency, three-cycle cap, and every stop
  rule.
- [ ] Persist every phase transition and verification result in SQLite.
- [ ] Replace static success flags with computed state; do not import
  `platform/tools/evolve_phases.py`.
- [ ] Export one compact run receipt with references to artifacts and evidence.

**Verify:**

```powershell
python -m pytest engine/tests/api engine/tests/ralph -q
```

### Task 7: Generate The Final Image2 Reference And Build The Studio Shell

**Files:**

- Create: `docs/design/platform-studio-final.png`
- Create: `docs/design/platform-studio-visual-contract.md`
- Create: `apps/studio/src/app/layout.tsx`
- Create: `apps/studio/src/app/(studio)/page.tsx`
- Create: `apps/studio/src/app/(studio)/knowledge/page.tsx`
- Create: `apps/studio/src/app/(studio)/course/[courseId]/page.tsx`
- Create: `apps/studio/src/app/(present)/present/[courseId]/page.tsx`
- Create: `apps/studio/src/features/shell/*`
- Create: `apps/studio/src/styles/tokens.css`
- Create: `apps/studio/tests/shell.spec.ts`

**Steps:**

- [ ] Generate one simplified Image2 desktop reference from the visual contract.
  Reject it and regenerate once only if it contains extra rails, metric cards,
  nested cards, gradient decoration, or persistent QA panels.
- [ ] Write failing tests for exactly four global entries, inspector collapsed
  by default, one command bar, keyboard focus, Escape behavior, and accessible
  icon buttons.
- [ ] Build the shared shell and the four route frames using Lucide icons.
- [ ] Use realistic data from the vertical-slice API; no lorem ipsum and no
  course-specific hard-coded copy.
- [ ] Keep the main workflow functional at 1440x900 and 390x844 without
  overlap, clipped text, or layout shift.

**Verify:**

```powershell
pnpm --dir apps/studio test
pnpm --dir apps/studio typecheck
pnpm --dir apps/studio build
```

### Task 8: Connect Goal, Knowledge, Course, And Evidence Workflows

**Files:**

- Create: `apps/studio/src/lib/api.ts`
- Create: `apps/studio/src/lib/run-stream.ts`
- Create: `apps/studio/src/features/goal/*`
- Create: `apps/studio/src/features/knowledge/*`
- Create: `apps/studio/src/features/course/*`
- Create: `apps/studio/src/features/inspector/*`
- Create: `apps/studio/tests/course-flow.spec.ts`

**Steps:**

- [ ] Write a failing browser test that enters a goal, attaches Markdown,
  inspects provenance, accepts an outline, edits one slide, runs one typed job,
  and opens stage preview.
- [ ] Connect HTTP commands and WebSocket progress with reconnect recovery.
- [ ] Keep evidence and graph in the inspector. Graph edges must connect their
  exact source and target anchors and labels must not overlap.
- [ ] Expose one clear next action. Do not reproduce legacy status-card grids.

**Verify:**

```powershell
pnpm --dir apps/studio test
pnpm --dir apps/studio exec playwright test tests/course-flow.spec.ts
```

### Task 9: Migrate The Windows Dual-Screen Helper

**Files:**

- Create: `apps/desktop/src/main.mjs`
- Create: `apps/desktop/src/session/state-machine.mjs`
- Create: `apps/desktop/src/session/display-service.mjs`
- Create: `apps/desktop/src/session/window-service.mjs`
- Create: `apps/desktop/src/session/session-bus.mjs`
- Create: `apps/desktop/tests/*.test.mjs`
- Migrate selectively from: `platform/playback/helper-*.mjs`

**Steps:**

- [ ] Characterize existing display list, window placement, WebSocket message,
  shutdown, and receipt behavior with tests before moving code.
- [ ] Port useful behavior behind the new state machine and typed contracts.
- [ ] Connect the Present route to detect, assign, launch, verify, recover, and
  close transitions.
- [ ] Keep stage and presenter projections of the same Slide AST.
- [ ] Label simulated checks as readiness only. Physical certification stays
  blocked until a real two-display receipt exists.

**Verify:**

```powershell
pnpm --dir apps/desktop test
pnpm --dir apps/studio exec playwright test tests/present-readiness.spec.ts
```

### Task 10: Add Remaining Ingestion And Offline Export

**Files:**

- Create: `engine/src/aicourse/ingest/pdf.py`
- Create: `engine/src/aicourse/ingest/pptx.py`
- Create: `engine/src/aicourse/ingest/dataset.py`
- Create: `engine/src/aicourse/ingest/codebase.py`
- Create: `engine/src/aicourse/course/export.py`
- Create: `engine/tests/ingest/*.py`
- Create: `engine/tests/course/test_export.py`

**Steps:**

- [ ] Add focused fixtures and failing tests for each adapter.
- [ ] Parse PPTX notes first with `python-pptx`, while retaining text, pictures,
  tables, and chart metadata. Pandoc is a fallback bridge only.
- [ ] Extract PDF text and page references without treating rendered images as
  textual truth.
- [ ] Register datasets by path, schema, sample, and hash. Query only selected
  bounded imports through `dataset_sql`.
- [ ] Export a self-contained offline HTML course plus RuntimeManifest,
  evidence index, local assets, and checksums.

**Verify:**

```powershell
python -m pytest engine/tests/ingest engine/tests/course/test_export.py -q
```

### Task 11: Prove The Generic Platform With Course_AIProduct Read-Only

**Files:**

- Create: `tests/acceptance/test_course_ai_product_fixture.py`
- Create: `.local/acceptance/course-ai-product/*`
- Read only: `Course_AIProduct/`
- Read only when present: `D:/mCloudDownload/7.10PDF合并.pdf`
- Read only: `dataset/`

**Steps:**

- [ ] Pass the generic minimal fixture first.
- [ ] Open `Course_AIProduct/` as an external course workspace without moving,
  renaming, or rewriting it.
- [ ] Import a bounded source subset, produce cards, requirement, outline,
  Slide AST, one executable case, evidence, stage preview, and offline export.
- [ ] Verify the platform contains no `Course_AIProduct` IDs, titles, paths, or
  business assumptions after the fixture is closed.
- [ ] Store generated acceptance output under `.local/`, not inside the
  protected course.

**Verify:**

```powershell
python -m pytest tests/acceptance/test_course_ai_product_fixture.py -q
python -m pytest tests/architecture/test_repo_boundaries.py -q
```

### Task 12: Visual, Adversarial, And Release Audit

**Files:**

- Create: `apps/studio/tests/visual.spec.ts`
- Create: `.local/evidence/platform-reboot/final-receipt.json`
- Modify: `README.md`

**Steps:**

- [ ] Capture Studio, Knowledge, Course, expanded inspector, Present readiness,
  stage, and mobile states with Playwright.
- [ ] Compare the 1440x900 product screenshot and Image2 reference together.
  Fix hierarchy, spacing, typography, border, radius, clipping, and state
  mismatches; a screenshot alone is not QA.
- [ ] Run independent product, architecture, safety, course-authoring,
  accessibility, and operational critiques. Merge duplicate findings by root
  cause and run Ralph only on evidenced HIGH/MEDIUM gaps.
- [ ] Replace the README with the new product boundary, four entry points, local
  run commands, and honest dual-screen certification boundary.
- [ ] Produce one final receipt containing commands, exit codes, hashes,
  screenshots, unresolved blockers, and the stop decision.

**Verify:**

```powershell
python -m pytest engine/tests tests/architecture tests/acceptance -q
pnpm --dir apps/studio test
pnpm --dir apps/studio typecheck
pnpm --dir apps/studio build
pnpm --dir apps/desktop test
pnpm qa
```

### Task 13: Migrate Or Remove Legacy Paths Only After Green Gates

**Files:**

- Modify: `docs/migrations/platform-reboot-map.json`
- Remove only after approval: classified legacy paths under `platform/`,
  `content/`, `references/`, `deliverables/`, `evidence/`, `output/`, `tmp/`,
  `tools/`, and `work/`
- Never remove automatically: `Course_AIProduct/`, user source assets, or the
  9.6 GB `dataset/` tree

**Steps:**

- [ ] Classify every legacy path as `migrated`, `unique-source`, `generated`,
  `duplicate`, `blocked`, or `protected`, with destination and hash evidence.
- [ ] Confirm the new platform no longer imports legacy code.
- [ ] Present one exact deletion manifest with resolved absolute paths, sizes,
  and restore strategy. Wait for explicit user approval.
- [ ] Delete in small verified batches and rerun focused tests after each.
- [ ] Keep Git history or an immutable tag as the rollback path.
- [ ] Stage only platform-reboot paths, inspect the staged diff, then commit and
  push to the configured `origin` only after all release gates are green.

**Verify:**

```powershell
git diff --check
git status --short
git diff --cached --stat
pnpm qa
git remote -v
```

## 10. Milestone Acceptance

### Milestone A: Truthful Skeleton

- Target directory exists and architecture tests are green.
- Root scripts are at or below 12.
- Contract v1 and SQLite migrations pass in Python and TypeScript.
- No protected course file changed.

### Milestone B: Useful Course Slice

- A Markdown source becomes traceable chunks, cards, a requirement, outline,
  Slide AST, evidence, and stage preview.
- The four-route Studio works from goal entry through evidence inspection.
- The final Image2 comparison has no unresolved HIGH visual finding.

### Milestone C: Complete Personal Platform

- PDF, PPTX, dataset, and codebase adapters have focused evidence.
- Offline export is self-contained and checksum-verifiable.
- Windows helper passes simulated readiness and reports physical certification
  honestly.
- `Course_AIProduct/` passes as a read-only acceptance course without platform
  leakage.

### Milestone D: Cleanup And Publish

- Full QA is green.
- Ralph stopped because acceptance is green, not because the cycle budget ran
  out.
- The deletion manifest received explicit approval and all removals are
  reversible from Git or the recorded backup.
- Only intended platform paths are committed and pushed to
  `http://water.js.cn:3156/greatwallwen/HTML-PPT`.

## 11. Minimal Goal Command

Use this exact command in the clean execution task:

```text
/goal 按 docs/superpowers/plans/2026-07-14-personal-ai-course-platform-reboot.md 重启平台；保护 Course_AIProduct，按 Ralph Loop 执行到验收通过即停止。
```

The command is intentionally short. This plan, repository contracts, focused
tests, and receipts contain the truth; the task must not depend on replaying the
old conversation.

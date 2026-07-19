# Personal AI Course Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully light, evidence-producing course studio in which one instructor imports material, collaborates with a local course agent to generate and edit a course, validates it, and enters synchronized dual-window teaching.

**Architecture:** A self-contained React/TypeScript/Vite application lives in `platform/web`. Pure domain modules own the schema, deterministic course agent, validation, persistence, and teaching state machine; React is only a projection of those contracts. A small Python QA entrypoint composes unit, type, build, theme, and protected-path checks without reading protected content.

**Tech Stack:** Node 24; React 19.2.0; Vite 6.4.2; TypeScript 7.0.2; Zod 4.4.3; Phosphor Icons 2.1.10; Vitest 4.1.10; Testing Library 16.3.2; jsdom 29.1.1; Python 3.12 for the QA entrypoint.

## Global Constraints

- The authoritative design is `docs/superpowers/specs/2026-07-15-personal-ai-course-studio-design.md`.
- The visual source is `docs/product/assets/course-studio-light-reference.png`, SHA-256 `36A5A9E54C863A326B98CA7082ACF16293EA423D442495E0317D85E91121B3B3`.
- Every UI surface is light; no dark presentation surface and no gradient.
- The only top-level journey is `导入资料 → 生成课程 → 编辑验证 → 双屏授课`.
- Sources, evidence, and tools are contextual capabilities, never top-level products.
- Do not read, modify, copy, hash, format, stage, or commit `Course_AIProduct/`.
- Do not modify, stage, or commit `dataset/`, `references/`, or the existing `AGENTS.md` change.
- Stage only explicit `platform/` and current `docs/superpowers/plans/` paths; never use `git add -A`.
- Real physical dual-screen support may be described as supported by the state machine, but it is certified only with real screen hardware and permission evidence.

## File Map

```text
platform/web/
  package.json                 scripts and pinned dependencies
  package-lock.json            reproducible dependency graph
  tsconfig.json                strict browser TypeScript settings
  vite.config.ts               Vite and Vitest configuration
  index.html                   app entry document
  src/main.tsx                 React mount
  src/test/setup.ts            DOM test matchers and cleanup
  src/domain/course.ts         durable domain types and constructors
  src/domain/course-schema.ts  Zod runtime contracts
  src/domain/source-import.ts  browser file classification and reading
  src/domain/course-agent.ts   deterministic structured course generation
  src/domain/validation.ts     course checks and SHA-256 evidence receipts
  src/domain/teaching.ts       dual-window state machine and session frames
  src/state/workspace.tsx      reducer, context, actions, derived state
  src/state/storage.ts         versioned local persistence
  src/app/App.tsx              route/view projection
  src/app/tokens.css           light-only design tokens
  src/app/app.css              measured responsive layout
  src/components/*.tsx         workflow and teaching projections
  src/**/*.test.{ts,tsx}       focused unit and interaction tests
  design-qa.md                 blocking visual comparison record
  evidence/design-qa-*.png     final browser and comparison evidence
platform/qa/run.py             `focused` and `all` repository acceptance
```

---

### Task 1: Bootstrap the app and lock the schema

**Files:**
- Create: `platform/web/` from the Product Design bootstrap
- Replace: `platform/web/package.json`
- Create: `platform/web/tsconfig.json`
- Create: `platform/web/vite.config.ts`
- Create: `platform/web/src/test/setup.ts`
- Create: `platform/web/src/domain/course.ts`
- Create: `platform/web/src/domain/course-schema.ts`
- Test: `platform/web/src/domain/course-schema.test.ts`

**Interfaces:**
- Produces: `CourseDocument`, `SourceAsset`, `ChapterNode`, `LessonNode`, `CourseBrief`, `EvidenceReceipt`, and `courseDocumentSchema`.
- Invariant: `schemaVersion` is exactly `1`; duration is an integer from 5 to 480; IDs and visible strings are non-empty.

- [ ] **Step 1: Bootstrap and pin the toolchain**

Run:

```powershell
node C:\Users\alvin\.codex\plugins\cache\openai-curated-remote\product-design\0.1.50\scripts\bootstrap-prototype.mjs --dest D:\cursor\AI培训\platform\web
```

Expected: JSON with `"status": "created"` and root `D:\cursor\AI培训\platform\web`.

Replace `package.json` with scripts `dev`, `build`, `typecheck`, and `test`; retain React/Vite versions from the starter and add the pinned versions in the plan header. Configure `vite.config.ts` with React, jsdom tests, `server.host = "0.0.0.0"`, and `server.allowedHosts = ["terminal.local"]`.

- [ ] **Step 2: Write a failing runtime-schema test**

```ts
import { describe, expect, it } from "vitest";
import { courseDocumentSchema } from "./course-schema";

describe("courseDocumentSchema", () => {
  it("accepts a structured course and rejects an empty lesson title", () => {
    const course = {
      schemaVersion: 1,
      id: "course-1",
      title: "企业 AI 入门课",
      audience: "业务团队",
      goal: "理解 AI 并形成可执行工作流",
      durationMinutes: 120,
      chapters: [{
        id: "chapter-1",
        title: "为什么现在需要 AI",
        objective: "建立共同认知",
        lessons: [{
          id: "lesson-1",
          title: "AI 的发展与现状",
          summary: "识别关键趋势和能力边界",
          durationMinutes: 30,
          sourceIds: ["source-1"],
          status: "grounded",
        }],
      }],
      sources: [{
        id: "source-1",
        name: "趋势.md",
        kind: "markdown",
        size: 42,
        status: "ready",
        extractedText: "趋势材料",
        addedAt: "2026-07-15T00:00:00.000Z",
      }],
      updatedAt: "2026-07-15T00:00:00.000Z",
    };
    expect(courseDocumentSchema.parse(course).title).toBe("企业 AI 入门课");
    expect(() => courseDocumentSchema.parse({
      ...course,
      chapters: [{ ...course.chapters[0], lessons: [{ ...course.chapters[0].lessons[0], title: "" }] }],
    })).toThrow();
  });
});
```

- [ ] **Step 3: Run the test and confirm the missing-contract failure**

Run: `npm --prefix platform/web test -- --run src/domain/course-schema.test.ts`

Expected: FAIL because `./course-schema` does not exist.

- [ ] **Step 4: Implement the types and Zod schemas**

Use literal unions from the design spec and export constructors with these signatures:

```ts
export const createId = (prefix: string): string =>
  `${prefix}-${crypto.randomUUID()}`;

export const createEmptyCourse = (now = new Date().toISOString()): CourseDocument => ({
  schemaVersion: 1,
  id: createId("course"),
  title: "未命名课程",
  audience: "",
  goal: "",
  durationMinutes: 90,
  chapters: [],
  sources: [],
  updatedAt: now,
});
```

`courseDocumentSchema` must mirror every field and require at least one chapter and one lesson only when `validateForTeaching` is called; the durable draft schema accepts empty chapter arrays.

- [ ] **Step 5: Install and verify**

Run:

```powershell
npm --prefix platform/web install
npm --prefix platform/web test -- --run src/domain/course-schema.test.ts
npm --prefix platform/web run typecheck
```

Expected: one passing test file and no TypeScript diagnostics.

- [ ] **Step 6: Commit the schema slice**

```powershell
git add -- platform/web
git commit -m "feat(studio): establish course schema"
```

Before commit, require `git diff --cached --name-only` to contain only `platform/web/**`.

### Task 2: Implement import, generation, validation, and persistence

**Files:**
- Create: `platform/web/src/domain/source-import.ts`
- Create: `platform/web/src/domain/source-import.test.ts`
- Create: `platform/web/src/domain/course-agent.ts`
- Create: `platform/web/src/domain/course-agent.test.ts`
- Create: `platform/web/src/domain/validation.ts`
- Create: `platform/web/src/domain/validation.test.ts`
- Create: `platform/web/src/state/storage.ts`
- Create: `platform/web/src/state/storage.test.ts`

**Interfaces:**
- Consumes: Task 1 domain contracts.
- Produces: `readSourceFiles(files)`, `LocalCourseAgent.generate(brief, sources)`, `validateCourse(course)`, `loadWorkspace(storage)`, and `saveWorkspace(storage, snapshot)`.

- [ ] **Step 1: Write focused failing tests**

The tests must prove these exact cases:

```ts
it("reads markdown but keeps binary documents as metadata", async () => {
  const markdown = new File(["# AI 趋势"], "trend.md", { type: "text/markdown" });
  const pptx = new File([new Uint8Array([80, 75])], "case.pptx");
  const [textSource, binarySource] = await readSourceFiles([markdown, pptx]);
  expect(textSource).toMatchObject({ kind: "markdown", status: "ready", extractedText: "# AI 趋势" });
  expect(binarySource).toMatchObject({ kind: "pptx", status: "ready" });
  expect(binarySource.extractedText).toBeUndefined();
});

it("rejects one oversized file without discarding valid siblings", async () => {
  const small = new File(["ok"], "ok.txt");
  Object.defineProperty(small, "size", { value: 2 });
  const large = new File(["x"], "large.pdf");
  Object.defineProperty(large, "size", { value: 20 * 1024 * 1024 + 1 });
  const result = await readSourceFiles([small, large]);
  expect(result.map((item) => item.status)).toEqual(["ready", "failed"]);
});

it("generates course structure from the brief and source names", async () => {
  const result = await new LocalCourseAgent().generate(
    { title: "企业 AI 入门课", audience: "业务团队", goal: "建立 AI 工作流", durationMinutes: 120 },
    [readySource("趋势.md"), readySource("案例.pptx")],
  );
  expect(result.course.chapters.length).toBe(3);
  expect(result.course.chapters.flatMap((chapter) => chapter.lessons)).toHaveLength(8);
  expect(result.receipt.kind).toBe("generation");
});

it("blocks teaching on errors and records coverage warnings", async () => {
  const receipt = await validateCourse(courseWithOneUngroundedLesson());
  expect(receipt.checks.some((check) => check.level === "warning")).toBe(true);
  expect(receipt.checks.some((check) => check.level === "error")).toBe(false);
  expect(receipt.inputDigest).toMatch(/^[a-f0-9]{64}$/);
});
```

Storage tests use a Map-backed `Storage` fake and prove versioned save/load plus a visible `corrupt` result for malformed JSON.

- [ ] **Step 2: Run the four test files and confirm failures**

Run: `npm --prefix platform/web test -- --run src/domain/source-import.test.ts src/domain/course-agent.test.ts src/domain/validation.test.ts src/state/storage.test.ts`

Expected: FAIL with missing modules.

- [ ] **Step 3: Implement deterministic domain behavior**

`readSourceFiles` classifies extensions, reads `.md`, `.markdown`, and `.txt`, rejects files over `20 * 1024 * 1024`, and never throws the whole batch for one file.

`CourseAgent` is:

```ts
export interface CourseAgent {
  generate(brief: CourseBrief, sources: SourceAsset[]): Promise<{
    course: CourseDocument;
    receipt: EvidenceReceipt;
  }>;
  applyIntent(course: CourseDocument, intent: string, chapterId?: string): Promise<{
    course: CourseDocument;
    receipt: EvidenceReceipt;
    message: string;
  }>;
}
```

The local implementation creates three chapters and eight lessons, distributes the requested duration in 5-minute increments, rotates ready source IDs, and derives titles from the brief and source names. `applyIntent` supports duration extraction with `/缩短课程到\s*(\d+)\s*分钟/`, chapter case insertion, and source-coverage validation.

`validateCourse` normalizes JSON by recursively sorting object keys, hashes UTF-8 bytes with `crypto.subtle.digest("SHA-256", ...)`, and emits every check from section 8 of the design spec.

Storage uses key `personal-ai-course-studio:v1` and returns `{ status: "empty" | "ready" | "corrupt"; snapshot?: WorkspaceSnapshot; message?: string }`.

- [ ] **Step 4: Run focused and broad domain tests**

Run:

```powershell
npm --prefix platform/web test -- --run src/domain src/state/storage.test.ts
npm --prefix platform/web run typecheck
```

Expected: all domain test files pass and typecheck is clean.

- [ ] **Step 5: Commit the domain engine**

```powershell
git add -- platform/web/src/domain platform/web/src/state/storage.ts platform/web/src/state/storage.test.ts platform/web/package-lock.json
git commit -m "feat(studio): add course generation and evidence"
```

### Task 3: Build the reducer and workflow shell

**Files:**
- Create: `platform/web/src/state/workspace.tsx`
- Create: `platform/web/src/state/workspace.test.tsx`
- Replace: `platform/web/src/main.tsx`
- Create: `platform/web/src/app/App.tsx`
- Create: `platform/web/src/app/tokens.css`
- Create: `platform/web/src/app/app.css`
- Create: `platform/web/src/components/WorkflowHeader.tsx`
- Create: `platform/web/src/components/ImportStep.tsx`
- Create: `platform/web/src/components/GenerateStep.tsx`
- Test: `platform/web/src/app/App.test.tsx`

**Interfaces:**
- Consumes: domain engine and versioned storage.
- Produces: `WorkspaceProvider`, `useWorkspace()`, workflow navigation, import UI, and generation UI.

- [ ] **Step 1: Write failing reducer and journey tests**

```tsx
it("moves from import to generated editable course", async () => {
  const user = userEvent.setup();
  render(<App />);
  await user.click(screen.getByRole("button", { name: "新建课程" }));
  const input = screen.getByLabelText("导入资料");
  await user.upload(input, new File(["# 趋势"], "趋势.md", { type: "text/markdown" }));
  expect(await screen.findByText("趋势.md")).toHaveTextContent("趋势.md");
  await user.click(screen.getByRole("button", { name: "下一步：生成课程" }));
  await user.type(screen.getByLabelText("课程受众"), "业务团队");
  await user.type(screen.getByLabelText("课程目标"), "建立 AI 工作流");
  await user.click(screen.getByRole("button", { name: "生成课程结构" }));
  expect(await screen.findByRole("heading", { name: "为什么现在需要 AI" })).toBeVisible();
});
```

Reducer tests prove `START_NEW`, `ADD_SOURCES`, `SET_BRIEF`, `GENERATION_STARTED`, `GENERATION_COMPLETED`, and `PERSISTENCE_FAILED` transitions without mutating the previous state.

- [ ] **Step 2: Confirm the missing-UI failure**

Run: `npm --prefix platform/web test -- --run src/state/workspace.test.tsx src/app/App.test.tsx`

Expected: FAIL because provider and app modules do not exist.

- [ ] **Step 3: Implement the shell and first two steps**

The reducer state is:

```ts
export interface WorkspaceState {
  step: WorkflowStep;
  course: CourseDocument;
  brief: CourseBrief;
  receipts: EvidenceReceipt[];
  selectedChapterId?: string;
  selectedLessonId?: string;
  generation: "idle" | "running" | "success" | "error";
  assistant: "idle" | "running" | "success" | "error";
  persistenceWarning?: string;
}
```

`WorkflowHeader` renders exactly four numbered steps and a “新建课程” icon button. `ImportStep` exposes a real file input, drag target, batch statuses, remove/retry controls, and next CTA. `GenerateStep` exposes title, audience, goal, duration, source summary, loading state, and error focus.

Use only the token values from the design spec. The app shell is height `100dvh`, background `var(--color-page)`, white surfaces, one-pixel dividers, and no gradient declarations.

- [ ] **Step 4: Verify the journey slice**

Run:

```powershell
npm --prefix platform/web test -- --run src/state/workspace.test.tsx src/app/App.test.tsx
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

Expected: journey test passes, typecheck has no diagnostics, and Vite writes `dist/`.

- [ ] **Step 5: Commit the workflow shell**

```powershell
git add -- platform/web/src platform/web/index.html platform/web/vite.config.ts
git commit -m "feat(studio): add import and generation journey"
```

### Task 4: Recreate the light editing and validation workspace

**Files:**
- Create: `platform/web/src/components/CourseEditor.tsx`
- Create: `platform/web/src/components/ChapterTree.tsx`
- Create: `platform/web/src/components/LessonList.tsx`
- Create: `platform/web/src/components/SourcePanel.tsx`
- Create: `platform/web/src/components/AssistantDock.tsx`
- Create: `platform/web/src/components/ValidationPanel.tsx`
- Test: `platform/web/src/components/CourseEditor.test.tsx`
- Modify: `platform/web/src/state/workspace.tsx`
- Modify: `platform/web/src/app/app.css`

**Interfaces:**
- Consumes: `WorkspaceState`, `CourseAgent.applyIntent`, and `validateCourse`.
- Produces: the exact three-column edit state from the visual source plus functional editing, reordering, source linking, assistant commands, and validation receipts.

- [ ] **Step 1: Write failing interaction tests**

```tsx
it("edits, reorders, grounds, and validates a lesson", async () => {
  const user = userEvent.setup();
  render(<TestWorkspace initialState={editingFixture}><CourseEditor /></TestWorkspace>);
  await user.click(screen.getByRole("button", { name: "编辑 AI 的发展与现状" }));
  const title = screen.getByLabelText("小节标题");
  await user.clear(title);
  await user.type(title, "AI 演进与能力边界");
  await user.click(screen.getByRole("button", { name: "保存小节" }));
  expect(screen.getByText("AI 演进与能力边界")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "下移 AI 演进与能力边界" }));
  await user.click(screen.getByRole("button", { name: "验证课程" }));
  expect(await screen.findByText(/校验收据/)).toBeVisible();
});

it("closes the lesson editor with Escape and restores focus", async () => {
  const user = userEvent.setup();
  render(<TestWorkspace initialState={editingFixture}><CourseEditor /></TestWorkspace>);
  const trigger = screen.getByRole("button", { name: "编辑 AI 的发展与现状" });
  await user.click(trigger);
  await user.keyboard("{Escape}");
  expect(trigger).toHaveFocus();
});
```

- [ ] **Step 2: Confirm the missing-editor failure**

Run: `npm --prefix platform/web test -- --run src/components/CourseEditor.test.tsx`

Expected: FAIL because editor components do not exist.

- [ ] **Step 3: Implement the measured edit state**

At 1569px, CSS grid tracks are `364px minmax(560px, 1fr) 394px`; header is 76px; assistant dock is 165px; main panels scroll independently. At 1180–1568px tracks become `300px minmax(520px, 1fr) 330px`. Below 1180px the source panel is an accessible drawer.

Required controls and reducer actions:

```ts
type WorkspaceAction =
  | { type: "SELECT_CHAPTER"; chapterId: string }
  | { type: "SELECT_LESSON"; lessonId: string }
  | { type: "UPDATE_LESSON"; chapterId: string; lessonId: string; patch: Partial<LessonNode> }
  | { type: "MOVE_LESSON"; chapterId: string; lessonId: string; direction: -1 | 1 }
  | { type: "TOGGLE_LESSON_SOURCE"; chapterId: string; lessonId: string; sourceId: string }
  | { type: "ADD_CHAPTER" }
  | { type: "ADD_LESSON"; chapterId: string }
  | { type: "VALIDATION_COMPLETED"; receipt: EvidenceReceipt }
  | { type: "ASSISTANT_COMPLETED"; course: CourseDocument; receipt: EvidenceReceipt };
```

Every visible icon comes from `@phosphor-icons/react`. Icon-only buttons have a Chinese `aria-label`, `title`, visible focus ring, and a minimum 44px target. The central cards display title, summary, grounding status, move/edit menus, and source count. `SourcePanel` filters all/document/web/image/note as visual categories but only filters existing contextual sources. `AssistantDock` preserves input on errors and exposes the three supported intents.

- [ ] **Step 4: Run interaction, accessibility, type, and build checks**

Run:

```powershell
npm --prefix platform/web test -- --run src/components/CourseEditor.test.tsx src/app/App.test.tsx
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

Expected: all named tests pass, no type diagnostics, production build succeeds.

- [ ] **Step 5: Commit the editing workspace**

```powershell
git add -- platform/web/src
git commit -m "feat(studio): build evidence-aware course editor"
```

### Task 5: Implement the dual-window teaching state machine

**Files:**
- Create: `platform/web/src/domain/teaching.ts`
- Create: `platform/web/src/domain/teaching.test.ts`
- Create: `platform/web/src/components/TeachingSetup.tsx`
- Create: `platform/web/src/components/StageView.tsx`
- Create: `platform/web/src/components/PresenterView.tsx`
- Test: `platform/web/src/components/TeachingSetup.test.tsx`
- Modify: `platform/web/src/app/App.tsx`
- Modify: `platform/web/src/app/app.css`

**Interfaces:**
- Consumes: validated `CourseDocument` and current lesson selection.
- Produces: `TeachingSetupState`, `TeachingFrame`, `reduceTeachingSetup`, and `createTeachingBus`.

- [ ] **Step 1: Write failing state-machine and synchronization tests**

```ts
it("does not become ready until both windows and sync are confirmed", () => {
  let state = initialTeachingSetup();
  state = reduceTeachingSetup(state, { type: "CHECK_STARTED" });
  state = reduceTeachingSetup(state, { type: "CAPABILITY_RESOLVED", screenDetails: false });
  state = reduceTeachingSetup(state, { type: "REHEARSAL_ACCEPTED" });
  state = reduceTeachingSetup(state, { type: "WINDOWS_OPENED", stage: true, presenter: true });
  expect(state.status).toBe("syncing");
  state = reduceTeachingSetup(state, { type: "SYNC_CONFIRMED" });
  expect(state.status).toBe("ready");
  expect(state.physicalDualScreenCertified).toBe(false);
});

it("replays the last frame to a reconnecting subscriber", () => {
  const harness = createBusHarness();
  const sender = createTeachingBus("session-1", harness);
  sender.publish(teachingFrame({ lessonId: "lesson-2" }));
  const receiver = createTeachingBus("session-1", harness);
  expect(receiver.readLastFrame()?.lessonId).toBe("lesson-2");
});
```

- [ ] **Step 2: Confirm state-machine tests fail**

Run: `npm --prefix platform/web test -- --run src/domain/teaching.test.ts src/components/TeachingSetup.test.tsx`

Expected: FAIL because teaching modules do not exist.

- [ ] **Step 3: Implement capability, window, and bus behavior**

Use statuses `idle`, `checking`, `permission-required`, `opening`, `syncing`, `ready`, `presenting`, and `error`. The capability adapter checks `window.getScreenDetails` without asserting certification. The opener uses named windows `course-stage` and `course-presenter`; a null return becomes a popup-blocked error while retaining the session ID.

`createTeachingBus` uses channel name `course-teaching:<sessionId>`, writes frames to `localStorage` key `course-teaching:last-frame:<sessionId>`, emits heartbeat timestamps, and exposes `publish`, `subscribe`, `readLastFrame`, and `close`.

`StageView` and `PresenterView` are selected by `?view=stage|presenter&session=<id>`. Both use white backgrounds. Stage shows course title, lesson title, summary, progress, and connection state. Presenter shows next lesson, speaker notes derived from summary, elapsed timer, previous/next, play/pause, and reset.

- [ ] **Step 4: Verify teaching behavior**

Run:

```powershell
npm --prefix platform/web test -- --run src/domain/teaching.test.ts src/components/TeachingSetup.test.tsx
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

Expected: state and component tests pass; build succeeds. Test output must not claim physical dual-screen certification.

- [ ] **Step 5: Commit the teaching slice**

```powershell
git add -- platform/web/src
git commit -m "feat(studio): add synchronized teaching rehearsal"
```

### Task 6: Add repository QA and protection gates

**Files:**
- Create: `platform/qa/run.py`
- Create: `platform/qa/test_run.py`
- Create: `platform/web/src/app/theme.test.ts`
- Modify: `platform/web/package.json`

**Interfaces:**
- Consumes: platform files and Git path metadata only.
- Produces: `python platform/qa/run.py focused` and `python platform/qa/run.py all`.

- [ ] **Step 1: Write failing QA-runner tests**

```py
def test_forbidden_theme_scan_rejects_dark_surface(tmp_path):
    css = tmp_path / "tokens.css"
    css.write_text(":root { --color-page: #111827; }", encoding="utf-8")
    result = scan_light_theme(css)
    assert result.ok is False
    assert "#111827" in result.details


def test_protected_path_guard_accepts_platform_only_changes():
    changed = ["platform/web/src/app/App.tsx", "docs/superpowers/plans/current.md"]
    assert protected_path_violations(changed) == []
```

- [ ] **Step 2: Confirm the QA tests fail**

Run: `python -m pytest platform/qa/test_run.py -q`

Expected: FAIL because `platform.qa.run` does not exist.

- [ ] **Step 3: Implement the QA runner**

`focused` runs Python QA tests and scans `tokens.css` plus `app.css` for `linear-gradient`, `radial-gradient`, `conic-gradient`, `#000`, `#111827`, `#0f172a`, `#020617`, and dark `rgb()` surfaces. It verifies the four workflow labels, source image hash, schema file, and `design-qa.md` presence once visual QA begins.

`all` additionally runs:

```text
npm --prefix platform/web test -- --run
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
```

The protected-path guard compares committed changes since `e6cd08d59` and rejects `Course_AIProduct/`, `dataset/`, `references/`, and `AGENTS.md`. It reads Git path names only, not protected file contents.

- [ ] **Step 4: Run focused and all checks**

Run:

```powershell
python -m pytest platform/qa/test_run.py -q
python platform/qa/run.py focused
python platform/qa/run.py all
```

Expected: every command exits 0 and prints an individual PASS line per gate.

- [ ] **Step 5: Commit QA gates**

```powershell
git add -- platform/qa platform/web/src/app/theme.test.ts platform/web/package.json platform/web/package-lock.json
git commit -m "test(studio): enforce product acceptance gates"
```

### Task 7: Run browser flow and blocking design QA

**Files:**
- Create: `platform/web/evidence/design-qa-edit.png`
- Create: `platform/web/evidence/design-qa-comparison.png`
- Create: `platform/web/evidence/acceptance-receipt.json`
- Create: `platform/web/design-qa.md`
- Modify: any `platform/web/src/**` file needed to fix P0/P1/P2 findings

**Interfaces:**
- Consumes: source visual, running application, and exact 1569 × 1002 edit state.
- Produces: browser evidence, a passed design QA report, and the final acceptance receipt.

- [ ] **Step 1: Start the verified local app**

Run `npm --prefix platform/web run dev -- --host 0.0.0.0 --port 4173 --strictPort` in a yielded background terminal. Wait for Vite ready output before browser capture.

- [ ] **Step 2: Exercise the complete primary journey**

Using the Codex in-app browser, at 1569 × 1002:

1. Click “新建课程”.
2. Import a Markdown fixture created inside `platform/web/evidence/`.
3. Fill audience, goal, and duration; generate.
4. Edit and reorder one lesson; link a source.
5. Validate and record the visible receipt digest.
6. Enter rehearsal; verify popup-blocked recovery or two-window sync, whichever the environment exposes.
7. Reload and verify persistence.
8. Check focus restoration, Escape, warning/error state, and browser console errors.

- [ ] **Step 3: Capture and compare the exact edit state**

Capture `platform/web/evidence/design-qa-edit.png`. Create a side-by-side 3138 × 1002 comparison with the reference on the left and implementation on the right, preserving aspect ratio and using no visual alteration. Open the combined comparison and inspect typography, layout rhythm, colors, icons, copy, and asset fidelity.

- [ ] **Step 4: Iterate until the blocking report passes**

Write `design-qa.md` with source path, screenshot path, viewport, state, full-view evidence, focused evidence, primary interactions, console result, and comparison history. For every P0/P1/P2: record it, fix it, recapture the same state, and compare again. Stop visual iteration once no actionable P0/P1/P2 remains; retain P3 only as follow-up polish.

The final line is exactly:

```text
final result: passed
```

- [ ] **Step 5: Create the acceptance receipt and run the release gate**

The JSON receipt records the source hash, implementation screenshot hash, commit, test commands and exit codes, viewport, workflow steps exercised, physical dual-screen certification boolean, protected-status digest, and design-QA result.

Run:

```powershell
python platform/qa/run.py all
git diff --check
git status --short
```

Expected: QA exits 0; diff check is clean; status contains only pre-existing protected/user changes plus intentional platform evidence before staging.

- [ ] **Step 6: Commit final evidence without protected paths**

```powershell
git add -- platform/web/design-qa.md platform/web/evidence platform/web/src
git commit -m "feat(studio): complete verified course journey"
```

Require the staged path guard, rerun `python platform/qa/run.py all`, and compare the protected-path status digest to the implementation baseline before declaring completion.

# Course Composition Task 18 Report

## Outcome

Task 18 is complete in the main workspace. The product UI now closes the
governed loop from upload through review, card publication, exact index wait,
course composition, authentic visual placement, validation, publication,
response-loss recovery, immutable reopen, and shared editor/Stage/Presenter
rendering.

## Product And Safety Contracts

- Markdown, PPTX, CSV, Parquet, XLS, and XLSX can use the authenticated Helper
  import path. Candidate cards and blocking review details remain bounded and
  are resolved against exact review/card digests before publication.
- Card publication waits for its exact index outbox item and reports explicit
  FTS-only degradation instead of claiming semantic retrieval.
- PPTX source visuals retain source lineage. Data charts are built only from a
  fixed dataset digest, schema digest, and per-column digests. Network visuals
  require current acquisition, revalidation, trusted provenance links, and
  card lineage before attachment.
- The same immutable Slide AST asset bindings render in the editor, Stage, and
  Presenter. Artifact bytes are fetched through the authenticated Helper,
  bounded by media type and size, and their Blob URLs are deterministically
  revoked. Missing authentication produces a visible fallback with attribution.
- Public, internal, and private-training scope is visible in the publication
  panel and remains enforced by Helper validation and licensing policy.
- Publication uses a recoverable operation ledger. Both a direct response and
  a recovered committed response are followed by an authenticated projection
  reopen. The final published course, deck, and runtime IDs replace the earlier
  validation IDs only after the reopened bytes and cross-bindings validate.
- Workspace v2 persists only governed IDs and bounded view preferences. On
  reload, the app retrieves the exact published course projection from Helper,
  rebuilds the concise editor projection, and unlocks teaching only after the
  published course/deck/runtime binding passes validation.
- Browser projection windows receive the same in-memory CourseDocument and
  Slide AST over an unguessable BroadcastChannel session. No course bodies,
  artifact URLs, or Helper tokens are written to browser persistence.

## Verification

- Complete Web suite: `279 passed` across 25 files.
- Complete Helper suite: `782 passed, 13 skipped`.
- Authenticated byte-identical course reopen regression: passed.
- TypeScript strict typecheck: passed.
- Vite production build: passed; 4,676 modules transformed.
- Targeted trailing-whitespace scan: no findings.
- Git-specific checks remain intentionally inapplicable until final acceptance
  and repository reinitialization.

## Routing

Publication identity changes, authenticated immutable reopen, artifact policy,
and cross-window projection are architecture and security sensitive. Adaptive
routing therefore remains P3 with `gpt-5.6-sol`/`xhigh` recommended through
Task 19 acceptance. Ultra is not needed. After the acceptance and reflection
milestone, routine reporting and brainstorm facilitation can move to a lower
token profile.

## Key Changed Files

- `platform/helper/course_helper/api.py`
- `platform/helper/course_helper/jobs.py`
- `platform/helper/course_helper/import_pipeline.py`
- `platform/web/src/domain/course-agent.ts`
- `platform/web/src/domain/governed-job-factory.ts`
- `platform/web/src/domain/helper-contracts-schema.ts`
- `platform/web/src/domain/projection-bus.ts`
- `platform/web/src/services/artifact-client.ts`
- `platform/web/src/services/knowledge-client.ts`
- `platform/web/src/state/workspace.tsx`
- `platform/web/src/components/ImportStep.tsx`
- `platform/web/src/components/KnowledgeReviewDrawer.tsx`
- `platform/web/src/components/GovernedCoursePanel.tsx`
- `platform/web/src/components/SlideVisualGallery.tsx`
- `platform/web/src/components/CourseEditor.tsx`
- `platform/web/src/components/StageView.tsx`
- `platform/web/src/components/PresenterView.tsx`
- `platform/web/src/components/TeachingSetup.tsx`
- `platform/web/src/app/App.tsx`

Task 18 does not certify real browser E2E, current live network provenance,
reference allowlist execution, signed packaging, OS network isolation, or a
witnessed physical dual-screen assignment. These remain Task 19 or explicit
hardware/signing gates and must not be inferred from simulated tests.

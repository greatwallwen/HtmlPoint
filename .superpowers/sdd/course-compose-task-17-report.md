# Course Composition Task 17 Report

## Outcome

Task 17 is complete in the main workspace. The Generate step now captures the
complete governed requirement, composes against an exact ready/degraded index
snapshot, exposes bounded recall and coverage evidence, invalidates stale
previews, and creates a course projection only after explicit digest-bound
confirmation.

## Product and Safety Contracts

- Requirement input covers title, audience, learning goals, duration,
  required/excluded tags, usage scope, visual needs, and dataset needs.
- The outline shows recalled card IDs, chapter placement, minutes, tag matches,
  retrieval mode, evidence IDs, prerequisites, goal coverage, and blocking gaps.
- Required and excluded tags and included and excluded card versions remain
  disjoint. Any draft or selection change makes the prior preview stale.
- Helper composition requires an exact index snapshot ID and digest. Semantic
  unavailability is presented as explicit FTS-only degraded retrieval.
- Card publication now returns its deterministic `indexOutboxId`, so the next UI
  phase can wait for the exact index result before composition.
- Offline generation remains a visibly `legacy-unlinked`, non-publishable
  rehearsal path. Only Helper confirmation stores governed IDs in workspace v2.
- Browser contracts reject a ready/degraded summary that omits its snapshot
  binding. Helper summary reports actual index state and bounded tag options.

## Verification

- Helper API tests: `52 passed`.
- Helper governed review/import/card publication tests: `21 passed`.
- Focused Web gate: `119 passed`.
- Complete Web suite: `270 passed` across 21 files.
- TypeScript strict typecheck: passed.
- Vite production build: passed; 4,670 modules transformed.
- Whole-file whitespace check: passed.

## Routing

The snapshot binding, confirmation digest, persistence boundary, and index
outbox closure are security and architecture sensitive, so adaptive routing
remains P3 with `gpt-5.6-sol`/`xhigh` recommended. The receipt is
`recommended_only`; Ultra is not needed.

## Key Changed Files

- `platform/helper/course_helper/api.py`
- `platform/helper/course_helper/jobs.py`
- `platform/web/src/domain/course-agent.ts`
- `platform/web/src/domain/helper-contracts-schema.ts`
- `platform/web/src/domain/knowledge-schema.ts`
- `platform/web/src/state/workspace.tsx`
- `platform/web/src/components/CourseRequirementPanel.tsx`
- `platform/web/src/components/CourseOutlinePanel.tsx`
- `platform/web/src/components/GenerateStep.tsx`
- `platform/web/src/app/App.tsx`
- `platform/web/src/app/app.css`

Task 17 does not certify browser E2E, current network provenance, signing,
physical dual-screen hardware, OS isolation, or Git state. Those claims remain
outside this checkpoint.

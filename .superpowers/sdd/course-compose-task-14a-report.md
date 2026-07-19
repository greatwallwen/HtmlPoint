# Course Composition Task 14A Report

## Outcome

The first Task 14 Helper slice is complete in the main workspace. Strict,
authenticated `knowledge_index`, `course_compose`, and
`course_outline_confirm` jobs now expose the existing index, composer,
confirmation, course-version, and operation-ledger authorities without adding
browser filesystem, URL, SQL, token, or card-body inputs.

## Architecture

- `knowledge_index` accepts one expected opaque outbox ID, consumes only the
  next pending item, seals an immutable snapshot, and reports either hybrid
  readiness or the explicit `fts-degraded` state. The current offline job uses
  the honest degraded mode because no verified semantic provider is injected.
- `course_compose` performs controlled retrieval on an idle connection, binds
  the predicted canonical requirement storage digest, then enters the durable
  operation transaction. The transaction persists the exact requirement,
  revalidates the prepared retrieval/snapshot/card lifecycle evidence, and
  atomically stores retrieval evidence, composition evidence, the outline, and
  the operation outcome.
- A committed replay is resolved before retrieval, so it returns the exact
  stored bounded result without searching again.
- `course_outline_confirm` revalidates the exact outline and confirmation
  summary digests, persists the immutable confirmation, and creates the
  confirmed `CourseVersion` in the same operation transaction.
- Parent timeout/disconnect recovery recognizes all three new mutation jobs and
  returns a committed outcome instead of a false cancellation.

## Browser Boundary

Results contain opaque IDs, digests, lifecycle state, the bounded outline and
confirmation summary, evidence IDs, retrieval mode, and placement IDs. They do
not contain card AST/body, normalized chunks, source locators, local paths,
arbitrary URLs, SQL, or sessions. The existing authenticated `/v1/jobs`
endpoint remains the only HTTP seam needed by this slice.

## Verification

- Focused course jobs including real spawned workers: `3 passed`.
- Composer, composition storage, operations, and course jobs: `46 passed`.
- API: `52 passed`.
- Server: `4 passed`.
- Complete Helper: `774 passed, 13 skipped, 70 warnings` in 106.16 seconds.
- `py_compile` passed for the changed Helper modules and tests.
- Ten dedicated Task 14A pytest roots were verified under
  `D:\AppData\Temp` and removed: 1,694 files, 11,826,034,592 bytes.

The skips and warnings are pre-existing environment/adversarial-fixture facts:
Windows permission-dependent cases, OpenPyXL `utcnow` deprecations, and
deliberate duplicate ZIP members.

## Routing

Adaptive routing classified the combined architecture/security seam as P3 and
recommended `gpt-5.6-sol` with `xhigh` reasoning. The switch receipt is
`recommended_only`; no Ultra escalation was recommended or required.

## Changed Files

- `platform/helper/course_helper/jobs.py`
  - SHA-256 `6C00CD6C461449F177205A9DC5254C3AF34418B69E2AF62599D16473C81258E5`
- `platform/helper/course_helper/composer.py`
  - SHA-256 `B413DA523999B95669D8AD74F8C3F8BEFD6B46441BC7507D7400D918A89B9ADA`
- `platform/helper/tests/test_course_jobs.py`
  - SHA-256 `7B482A07D2BE045FD4974D308FCFB45591AD2896003C5C815978359BEFEB3F10`
- `platform/helper/tests/test_api.py`
  - SHA-256 `ABC1E598F8A578464EBE4FB59E4A3C3598352250DC1E2D315EEB54903A14E82F`

## Remaining Task 14 Work

Authenticated artifact delivery and visual jobs are next, followed by visual
attach/detach and validation/publication job exposure. No live network,
browser-publication, signing, physical dual-screen, hardware, OS-isolation, or
Git certification is claimed.

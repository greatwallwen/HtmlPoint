# Course Composition Task 19 Report

## Outcome

Task 19 is complete for every locally executable and authorized gate. A clean
system-Chrome loopback run now uploads Markdown, CSV, and PPTX fixtures;
resolves governed reviews; publishes and indexes cards; composes and confirms
a two-goal course; binds source, data, and network-fixture visuals; validates,
publishes, replays, reopens, and renders the same evidence in editor, Stage,
and Presenter.

## Product Defects Closed By The Browser Loop

- Governed dataset identity and profile evidence now use the same deterministic
  locator/version formula as the chart verifier.
- Hash-addressed governed `.blob` data is accepted only after digest, schema,
  row, and parser verification.
- Explicitly reviewed `needs-review` datasets become publishable only when the
  current review projection has no blocking task.
- PPTX images are materialized with exact source artifact provenance.
- Source/data visual placement attribution matches publication validation.
- Independent imported datasets can produce explicit data visuals without a
  fabricated knowledge-card dataset reference.
- Each visual placement receives a unique immutable slot, so source, chart,
  and network visuals can coexist on one slide.
- Visual search now receives its runtime fixture configuration; the fixture is
  denied unless the exact E2E process authority is present.
- Persisted course requirements use a versioned response schema distinct from
  the unversioned create request.
- Stage and Presenter restore only an existing same-origin Helper session and
  load real artifact bytes; projection windows never exchange launch nonces.
- Browser idempotence replay runs inside the page origin, preserving Helper
  origin authentication instead of copying forbidden request headers.
- Vitest excludes Playwright specs, keeping unit and browser runners isolated.

## Verification

- Browser E2E: `1 passed (3.2m)`, including three decoded images in each
  projection window, exact publish replay, byte-bound reopen, and shared AST.
- Web: `279 passed` across 25 files; strict typecheck passed; production build
  passed with 4,676 transformed modules.
- Helper offline suite: `785 passed, 6 skipped, 7 deselected`.
- QA validator suite: `167 passed`.
- `course-composition`: passed.
- `authentic-visuals`: historical fixture-backed receipt verified; current
  network authorization not certified.

## Boundaries

`Course_AIProduct/`, `references/`, `.worktrees/`, and live network/reference
producers were not accessed. Physical dual-screen hardware, current live
network provenance, signed packaging, and OS-level network isolation remain
explicitly NOT CERTIFIED.

## Routing

Adaptive routing remained P3/xhigh for the browser/security corrections. No
Ultra escalation was needed. With implementation and local acceptance green,
reflection and Supergrill brainstorm facilitation should move to a lower-token
profile.

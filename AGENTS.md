# AI Training Platform Engineering Guide

This repository is a product platform for HTML-based course playback and course
generation. FDE is only a test fixture. Do not turn the platform into a
one-off FDE deck, a new slideshow toy, or a simple PPTX-to-HTML converter.

## Core Principles

- Codex-first engineering: use Codex to evolve code, schemas, QA, and migration
  plans. Content truth must live in structured assets, schemas, manifests, and
  evidence files, not in chat memory.
- Schema-first product: every durable artifact should map to a stable schema
  before it reaches UI rendering. Prefer ASTs and JSON contracts over raw HTML.
- Evidence-first runtime: demos, code execution, dataset queries, retrieval
  results, and exports must produce verifiable evidence objects.
- Occam loop: make the smallest reversible change that improves the platform,
  then verify it with a concrete command, screenshot, schema check, or fixture.

## Stable Layers

1. Content layer
   - Keep `SourceAsset`, `ExtractedChunk`, `KnowledgeCard`,
     `CourseRequirement`, `CourseOutline`, `SlideNode`, and
     `RuntimeManifest` as first-class product concepts.
   - PPTX ingestion is notes-first. Use deterministic parsing with
     `python-pptx` for notes, slide text, pictures, tables, and charts. Use
     Pandoc only as a bridge or fallback.
   - Markdown ingestion is AST-first. Do not collapse a whole document into one
     chunk.
   - Dataset and codebase ingestion are separate workflows. Use DuckDB for
     CSV/Parquet analytical fixtures where practical.

2. Retrieval layer
   - Start local-first: SQLite, FTS5, JSON metadata, and embeddings.
   - Use DuckDB for analytical datasets.
   - Treat cloud search or hosted file search as optional later integrations,
     not as the platform source of truth.

3. Playback layer
   - Stage, presenter, overview, editor, print, and runtime views are different
     projections of the same Slide AST.
   - Do not generate final HTML directly as the first durable artifact.
   - Keep UI concise, icon-forward, accessible, and low-noise. Avoid long
     visible instructions on the stage.
   - Icon-only controls must be real buttons with `aria-label`, keyboard focus,
     hover/focus tooltips, an approximately 44px target, and Escape behavior
     where a popup is opened.

4. Execution layer
   - Browser pages must not run arbitrary shell commands.
   - The browser submits typed job specs such as `python_snippet`,
     `dataset_sql`, `chart_build`, `rag_query`, and `doc_export`.
   - A local helper maps job specs to allowlisted runners with argument
     validation, time limits, artifact folders, and evidence objects.
   - Evidence should include stdout, stderr, exit code, timing, artifact paths,
     hashes, screenshots when useful, and verification status.

## Dual-Screen Direction

Do not rebuild dual-screen playback from scratch. Upgrade it into a state
machine plus session bus:

- Detect available screens.
- Request and verify permission.
- Open or reuse stage and presenter windows.
- Assign screen targets.
- Enter fullscreen per window.
- Confirm projection state before marking the session ready.

Use BroadcastChannel for same-origin low-latency sync, localStorage events for
last-frame recovery, and a local WebSocket/helper bus for runtime logs,
artifacts, execution progress, and reconnection.

## Course Generation Flow

Use this product flow for future course generation:

1. Parse requirement.
2. Recall matching knowledge cards.
3. Detect gaps.
4. Draft adjustable outline.
5. Generate Slide AST.
6. Render stage and presenter views.
7. Attach RuntimeManifest and typed execution jobs.
8. Run QA gates before publish.

## Verification Expectations

- For platform code changes, prefer `python platform/qa/run.py all`,
  `npm --prefix platform/web run typecheck`, and
  `npm --prefix platform/web run build`.
- Use focused verification first, then broaden only when the changed surface
  justifies it.
- Never mark real dual-screen support as certified from a simulated or
  single-screen environment.
- Keep generated content, analysis outputs, and evidence under versioned,
  inspectable files when they affect product behavior.

## Efficient Task And Session Rules

- Start ambiguous work with the current `grill-me` entry and `grilling` core.
  Ask one decision question at a time, recommend an answer, inspect the
  repository for facts, and do not implement until shared understanding is
  explicit.
- Treat repository files, manifests, receipts, and focused command output as
  truth. Do not replay an entire chat, PDF, browser transcript, or generated
  log when a small source card or targeted read can answer the question.
- Keep one primary deliverable on the critical path. Use at most two concurrent
  workers by default, only for independent paths with explicit ownership and
  acceptance commands. Shared manifests, master Markdown, and final PowerPoint
  always have one writer.
- Use the minimum adequate reasoning depth. Reserve deep research and
  adversarial review for decisions that can change architecture, safety,
  acceptance, or release status.
- Run focused tests after each small change. Run broad builds, full suites, and
  visual audits once at milestone or release boundaries, using hashes and
  caches when the inputs are unchanged.
- Reuse verified source cards and local evidence. Refresh online sources only
  for time-sensitive claims, conflicting evidence, or expired provenance.
- End each major milestone with a compact handoff containing current state,
  decisions, changed paths, verification, blockers, and the single next
  action. Start a clean task when the work changes milestone, accumulates three
  unrelated workstreams, or requires repeatedly loading old history.
- Stop loops when acceptance is green, the next pass has no new evidence, or a
  missing authority blocks progress. Do not create activity merely to keep a
  long-running task alive.
- Before moving environments, preserve the dirty working tree, inventory large
  files, transfer with resume and hashes, and verify the destination. Never
  delete the local source solely because a copy command returned success.

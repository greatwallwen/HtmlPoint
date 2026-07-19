# Knowledge-Grounded Course Composition and Authentic Visuals Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`, and use `superpowers:test-driven-development` for every production change. Stop at every task boundary if its focused checks are not green. Checkboxes are the execution record.

**Goal:** Complete the product path from a structured course requirement to reviewed knowledge, an adjustable evidence-backed outline, a confirmed immutable course, traceable Slide AST and RuntimeManifest, and authentic source/data/network visuals rendered by the existing bright course studio.

**Architecture:** The Python Helper remains the fact, review, composition, artifact, and publication authority. SQLite owns immutable versions, append-only lifecycle/review events, current projections, FTS5, embedding metadata, course composition, visual provenance, and evidence. DuckDB executes only typed analytical specs. React persistently stores identifiers and bounded non-content view models only. It may transiently render an authenticated, bounded `KnowledgeCardReviewView` in memory, but never writes card bodies/review content, source files, model files, arbitrary URLs, visual bytes, or database truth to localStorage/IndexedDB/cache. Existing published versions remain immutable; new source or course feedback creates suggestions, never silent upgrades.

**Primary references:** [approved product specification](../specs/2026-07-16-knowledge-cards-and-win11-teaching-design.md), [knowledge foundation plan](2026-07-16-knowledge-library-foundation-demo.md), [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/), [MediaWiki Imageinfo API](https://www.mediawiki.org/wiki/API:Imageinfo), and [Wikimedia Commons reuse guidance](https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia).

## Non-negotiable boundaries

- Work only in `D:/cursor/AI培训/.worktrees/course-studio` on `codex/course-studio-light`.
- Never read, copy, or modify `Course_AIProduct/`.
- Keep `D:/cursor/AI培训/references` read-only. Tasks 0-18 and every default/offline gate perform zero access. Task 19 may perform one explicitly bounded acceptance run over the approved allowlist manifest only; prefer the already verified foundation catalog when it proves the same condition without reopening the source.
- Do not perform broad reference scans or full-tree hashes. Reuse the canonical foundation receipt and stream-hash only an exact allowlisted object consumed by the final acceptance.
- Never execute imported code, notebooks, installers, untrusted/imported model weights, macros, arbitrary SQL, or shell text. The sole approved embedding model may load only after immutable revision and file-hash verification from quarantine.
- Published versions are immutable. Lifecycle, review, and suspension state are append-only events plus rebuildable current projections.
- A newer source, card, dataset, or visual version creates an upgrade suggestion only. It does not invalidate a still-present, digest-valid pinned version and never changes an existing course.
- New publication fails only for an invalid pinned dependency: dangling version, digest/schema mismatch, explicit suspension/revocation, unresolved required gap/review, unknown tag, or an expired/invalid public visual authorization.
- `suspended` is a lifecycle-projection flag on an immutable version, not a `KnowledgeCardVersion.status`. It excludes the version from new retrieval/placement/publication; an old course may reopen it with a warning but cannot republish until the suspension is cleared by another event.
- Embedding/model and network access are explicit live gates. `all` never downloads a model, reaches the network, or reopens references.
- All UI remains light, compact, keyboard-operable, and within the existing four top-level steps. Icon-only controls are real approximately 44 px buttons with `aria-label`, visible focus, hover/focus tooltip, Escape handling, and focus return.
- Stage explicit file paths for every commit. Never use `git add -A` or broad directory staging.

## Stable policy contracts

### Immutable version and review rules

- `register_or_reuse(versionId, contentDigest, payload)` returns the first stored object when all immutable bytes match, including its original `createdAt`; the same ID with different bytes is a conflict.
- An injectable clock is required in tests. Two independent constructions of identical inputs must produce identical IDs, ordering, serialized bytes, and output digests.
- `ReviewTask.category` is one of `candidate-card`, `exact-duplicate`, `near-duplicate`, `tag`, `source-changed`, `course-feedback`, and `visual-rights`; `reasonCode` preserves the specific machine reason. The schema-v1 `kind` mapping is exact: `source-changed -> source-changed/source-changed`, `near-duplicate -> near-duplicate/near-duplicate`, `unknown-tag|deprecated-tag|tag-conflict -> tag/<legacy-kind>`, `visual-rights|visual-unverified -> visual-rights/<legacy-kind>`, and `citation-missing|dataset-reference|sensitive-sample|grain-needs-review|provenance|manual-review -> candidate-card/<legacy-kind>`. New `exact-duplicate` and `course-feedback` tasks use the same value for category and reason. Legacy immutable payload bytes are never rewritten; readers and a rebuildable projection expose category/reason.
- `KnowledgeCardReviewView` is a session-authenticated, `Cache-Control: no-store` projection containing candidate ID/version/digest, bounded title/objective/content AST (maximum 64 nodes and 12,000 UTF-8 bytes), tag suggestions, duplicate comparisons, up to 12 citations with ≤500-character normalized excerpts and typed locators/evidence IDs, and no full source body/path. It exists in component memory only and is cleared on close/session change.
- A review resolution records actor, decision, expected review digest, evidence IDs, and time. Accepting an upgrade creates or selects a candidate card version; it never publishes it. Publication is a separate, common `knowledge_card_publish` action.
- Exact same-version replay returns the stored object and creates no self-edge. A different logical/version candidate with the same normalized digest is stored as an archived candidate and receives an automatically resolved exact-duplicate audit plus `candidateVersionId → reusedPublishedVersionId` `deduplicates` edge. Near duplicates are proposed by deterministic shingle/FTS and embedding lanes, remain human-blocking, and are never automatically merged.
- Every mutation carries caller-generated `operationId + requestDigest`. The domain transaction atomically writes both immutable changes and a durable outcome-ledger row. Response loss may yield `committed` or `unknown`, never a false rollback claim; authenticated status lookup and same-digest retry recover the result.

### Retrieval policy

- Filter status, lifecycle projection, required/excluded tags, audience, and difficulty before ranking.
- `course-studio-rrf-v1` uses equal FTS/semantic lane weights and `score = sum(1 / (60 + rank))`. Ties are resolved by `cardVersionId` ascending.
- Evidence records lane ranks/scores, filtered candidate digest, query digest, index snapshot digest, provider/package version, model ID, immutable model revision, dimension, every model-file SHA-256, query/document encoding policy, RRF parameters, and policy digest.
- Default tests deny sockets. A missing or corrupt verified model yields explicit FTS-only `degraded` evidence; it must never substitute fake vectors in a live result.

### Authentic visual policy

- Selection order is `official-primary` → `source-provided` → `data-derived` → `licensed-secondary` → `generated`.
- Wikimedia Commons is always at most `licensed-secondary`; hosting and license metadata do not establish first-party factual authenticity.
- `AuthenticityPolicy course-studio-authenticity-v1` and `LicensePolicy course-studio-license-v1` are versioned data. A public network visual requires authoritative metadata revalidation no older than 24 hours. Offline or expired metadata fails closed for public publish; private/internal use may show a warning.
- A committed receipt is historical audit evidence only. It never proves that a mutable URL or license remains currently valid. Offline QA prints `HISTORICAL RECEIPT VERIFIED — CURRENT NETWORK AUTHORIZATION NOT CERTIFIED`.
- `TrustedExternalLink` is the sole browser/API URL-bearing projection. It may be returned only after server-side provider validation and contains an HTTPS landing/license link plus provenance kind. A server-only `VisualProvenanceRecord` may retain canonical landing, license, and acquisition URLs for revalidation; schema-v1 `VisualAssetVersion.landing_page_url/asset_url` bytes remain immutable and are read only through the compatibility reader/provenance projection. Arbitrary user-entered URLs, final media URLs, local paths, redirects, request headers, and legacy `asset_url` never become browser commands or API output.
- `VisualPlacement` is immutable and binds `visualVersionId`, slide/slot, fit/crop, alt text, authenticity/license evidence, attribution block, transformation manifest, and originating card/source/dataset version. Its transformation manifest records crop/scale/color edits, change notice, derivative-license decision, export license, and ShareAlike/GFDL/no-derivatives compatibility. Attach/detach creates a new course/deck version and never rewrites a card or old deck.
- The browser fetches visual bytes through authenticated opaque artifact IDs, validates MIME/length, uses revocable Blob URLs, and never renders untrusted SVG. Internally generated chart SVG must pass a no-script/no-external-reference validator.

### Publication scope

| Scope | Source-provided rights unknown | Network metadata expired/unreachable | Data-derived | Generated asset |
|---|---|---|---|---|
| `private-training` | allowed with persistent warning | cached acquired bytes allowed with warning; no current authorization claim | pinned dataset/query integrity required | existing asset only, visibly labeled and rights statement required |
| `internal` | explicit rights disposition required | blocked until authoritative license/authenticity revalidation succeeds | pinned dataset/query integrity required | existing asset only, labeled, rights/export decision required |
| `public` | verified authorization required | blocked until revalidation within 24-hour TTL succeeds | pinned dataset/query integrity and attribution required | existing asset only, labeled, verified rights/export decision required |

- This plan recognizes and validates existing `generated` assets but does not generate new imagery.
- Every scope still requires valid lineage, pinned dependencies, dedup/tag review, transformation-license compatibility, and resolved required goals.
- A validation or transaction rollback leaves the last course/deck/runtime version unchanged. If the transaction committed but its response was lost, the operation ledger reports `committed`; the caller must not label that case as a failure.

### Explicit live-gate receipt contract

- Live producers are separate opt-in commands: `embedding-model-live`, Task 11's provider-only `network-visual-acquisition-live`, and Task 19's product-path `reference-knowledge-live` plus `network-visual-publication-live`. Each first proves that only its own opt-in is set, verifies its immutable manifest/allowlist and output destination, denies all unrelated roots/capabilities, writes an ignored temporary receipt, strictly validates receipt schema plus every declared source/object digest, and atomically promotes the sealed receipt to its named evidence path. A failed run never overwrites the last sealed receipt. Acquisition evidence alone never satisfies a course-publication gate.
- Producer exit codes are stable: `2` missing/conflicting opt-in or environment preflight, `3` manifest/allowlist/policy mismatch, `4` external acquisition/runtime failure, `5` receipt schema/digest/self-replay failure, and `6` protected-boundary or zero-write audit failure. The command prints the symbolic code without secrets, URLs, local paths, or content.
- A sealed receipt records producer/schema/policy versions, input and allowlist/manifest digests, exact object IDs/digests, started/finished times, environment capability claims, verification outcome, and zero-write proof. Final reference/network publication receipts additionally require real UI upload/inventory/job/operation IDs and immutable card/course/deck/runtime/visual-placement/publication IDs/digests, byte-identical reopen, and idempotent replay counts. Offline `course-composition`, `authentic-visuals`, and `all` only read and validate the applicable sealed digests; they never invoke a producer, reach the network, download a model, or reopen references.

## Planned durable surfaces

```text
platform/helper/course_helper/
  domain/composition.py
  domain/slide_ast.py
  domain/visual_policy.py
  domain/knowledge.py              # extend canonical ReviewTask
  domain/sources.py                # extend canonical VisualAssetVersion
  migrations/0002_card_lifecycle.sql
  migrations/0003_course_composition.sql
  migrations/0004_embeddings.sql
  migrations/0005_artifact_metadata.sql
  migrations/0006_visual_provenance.sql
  migrations/0007_import_sources.sql
  lifecycle.py
  reviews.py
  upgrades.py
  embeddings.py
  model_cache.py
  near_duplicates.py
  composer.py
  slide_builder.py
  artifacts.py
  source_visuals.py
  chart_builder.py
  network_visuals.py
  uploads.py
  source_inventory.py
  jobs.py
  api.py
platform/helper/model-manifests/bge-small-zh-v1.5.json
platform/web/src/domain/{composition,review,slide-ast,visual}.{ts,-schema.ts}
platform/web/src/services/knowledge-client.ts
platform/web/src/components/
platform/web/e2e/knowledge-course.spec.ts
platform/qa/{run.py,test_run.py}
```

---

### Task 0: Re-establish and verify the isolated Git boundary

**Files:** none.

- [ ] Run `git rev-parse --is-inside-work-tree`, `git rev-parse --show-toplevel`, `git branch --show-current`, `git worktree list --porcelain`, and `git status --short --branch` from `D:/cursor/AI培训/.worktrees/course-studio`.
- [ ] Require the exact worktree path and branch `codex/course-studio-light`. If `.git` metadata is absent or points elsewhere, stop before downloads or edits. Restore only from upstream-authorized Git metadata; never run `git init`.
- [ ] Verify `docs/superpowers/plans/2026-07-14-personal-ai-course-platform-reboot.md` SHA-256 is exactly `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80` and compare this plan byte-for-byte with its committed Git blob.
- [ ] Inventory the dirty tree without changing it. Confirm `COURSE_REFERENCE_ROOT`, `COURSE_NETWORK_VISUAL_TEST`, and `COURSE_EMBEDDING_MODEL_DOWNLOAD` are unset. Confirm Tasks 0-18/default gates never traverse `Course_AIProduct/` or `references/`; Task 19 is statically limited to its exact allowlist manifest.
- [ ] Record Python, Node, npm, SQLite, and FTS5 versions. Do not commit or proceed until all checks pass.

---

### Task 1: Extend canonical contracts for composition, review, visual placement, and policy

**Files:** create `domain/composition.py`, `domain/slide_ast.py`, `domain/visual_policy.py`; extend canonical `domain/knowledge.py` (`ReviewTask`) and `domain/sources.py` (`VisualAssetVersion`); modify `domain/__init__.py` with compatibility re-exports; create `tests/test_composition_contracts.py` and a schema-v1 review/visual fixture; modify canonical domain regression tests.

- [ ] Write RED tests for `CourseRequirement`, `CardPlacement`, `CourseOutline`, `CourseVersion`, `SlideNode`, `SlideDeckAst`, `RuntimeManifest`, `ReviewTask`, `VisualAssetVersion`, `VisualPlacement`, `TrustedExternalLink`, `AuthenticityPolicy`, and `LicensePolicy`.
- [ ] Cover strict extra-field/bool-as-int rejection, bounded learning goals and duration, disjoint tags, usage scope, unique placements/assets, frozen payloads, canonical digests, valid version/evidence IDs, no raw HTML/command/path or arbitrary/untyped browser URL, `TrustedExternalLink` as the sole URL-bearing browser/API projection, transformation-license decisions, and publication-scope policy decisions.
- [ ] Load a real schema-v1 fixture containing all 13 persisted `ReviewTask.kind` values and legacy `VisualAssetVersion.landing_page_url/asset_url`. Require the exact category/reason mapping above, byte-identical legacy payload/digest round trips, server-only provenance access, and browser serialization containing neither legacy field nor any final media URL.
- [ ] Run `python -m pytest platform/helper/tests/test_composition_contracts.py -q` and observe RED.
- [ ] Implement minimal frozen models, canonical digest helpers, and versioned compatibility readers. Extend/migrate the existing canonical classes rather than creating duplicate schema truths; keep compatibility imports until all callers move. Never rewrite a schema-v1 immutable payload to perform migration. A `SlideNode` carries placement IDs and immutable asset bindings, never mutable cards.
- [ ] Run the focused suite plus `test_domain_contracts.py`; run `git diff --check`.
- [ ] Stage only the exact contract modules, canonical-module changes, compatibility export, and focused tests listed above; commit `feat(helper): define composition and visual contracts`.

---

### Task 2: Migrate card lifecycle truth and rebuild every status projection

**Files:** create `migrations/0002_card_lifecycle.sql`, `lifecycle.py`, `tests/test_lifecycle.py`; modify `catalog.py`, `retrieval.py`, `cards.py`, `tests/test_catalog.py`, `tests/test_retrieval.py`, `tests/test_cards.py`.

- [ ] Write RED migration/backfill tests from a schema-v1 database. Prove all existing `publish_card`, status reads, FTS membership, retrieval filters, and card queries use lifecycle projections rather than in-place `cards.status`/`payload_json` updates.
- [ ] Test publish, supersede, archive, suspend, reinstate, concurrent events, idempotent replay, projection rebuild, migration failure read-only recovery, and old-course reopening warnings.
- [ ] Implement append-only events and a rebuildable current projection. Do not change immutable card payload bytes. Backfill existing rows transactionally and verify pre/post card digests.
- [ ] Run `python -m pytest platform/helper/tests/test_lifecycle.py platform/helper/tests/test_catalog.py platform/helper/tests/test_cards.py platform/helper/tests/test_retrieval.py -q` and `git diff --check`.
- [ ] Stage only the files listed for this task; commit `refactor(helper): make card lifecycle append only`.

---

### Task 3: Add immutable course/review storage, operation outcomes, and index outbox

**Files:** create `migrations/0003_course_composition.sql`, `reviews.py`, `operations.py`, `tests/test_reviews.py`, `tests/test_composition_storage.py`, `tests/test_operations.py`; modify `catalog.py`.

- [ ] Write RED tests for requirements, outlines, confirmations, course/deck/runtime versions, placements, review tasks/resolutions, upgrade suggestions, feedback suggestions, lineage/evidence joins, mutation outcome ledger, and knowledge-index outbox. Migrate a real `0001` database containing every legacy review kind and both legacy visual URL fields.
- [ ] With an injected clock, construct the same objects twice and require reuse of the first stored bytes/time. Require conflicts for same version ID with different bytes and one deterministic winner for concurrent confirmations.
- [ ] Test per-item outcome bundles: one invalid asset/review item cannot roll back successful siblings. Simulate child commit followed by queue/HTTP loss; the same transaction must contain a durable `committed` outcome, while a killed pre-commit transaction leaves no domain rows and a truthful `unknown|rolled-back` resolution.
- [ ] Implement transactional `register_or_reuse`, append-only review resolution, immutable composition storage, `operationId + requestDigest` ledger, authenticated lookup primitive, transactional index-outbox writes, and rebuildable review category/reason projections. Backfill the exact legacy mapping without changing `review_tasks.payload_json`, visual payload bytes, or their digests; prove projection rebuild and v1 reopening. Do not add embedding or new visual-provenance tables yet.
- [ ] Run `python -m pytest platform/helper/tests/test_reviews.py platform/helper/tests/test_composition_storage.py platform/helper/tests/test_operations.py platform/helper/tests/test_catalog.py -q` and `git diff --check`.
- [ ] Stage exact migration/storage/review/test files; commit `feat(helper): store immutable course and review versions`.

---

### Task 4: Pin the real embedding model and implement deterministic hybrid retrieval

**Files:** modify `pyproject.toml`, create `migrations/0004_embeddings.sql`, `embeddings.py`, `model_cache.py`, `model-manifests/bge-small-zh-v1.5.json`, `tests/test_embeddings.py`, `tests/test_model_cache.py`; modify `retrieval.py`, `tests/test_retrieval.py`, `platform/qa/run.py`, `platform/qa/test_run.py`; create the sealed model receipt only through the live producer.

- [ ] Write RED provider, cache, vector-validation, filter-before-rank, FTS/semantic lane, RRF formula, tie-break, snapshot, degraded, and socket-denial tests.
- [ ] Before invoking it, write RED QA CLI tests for `embedding-model-live`: only its opt-in is accepted; common exit codes are exact; manifest/cache/output paths are fixed and contained; temp receipt validation precedes atomic promotion; a failed run preserves the prior sealed receipt; the zero-network replay digest is required. Implement this producer and the common receipt helpers in `run.py` first.
- [ ] Pin `fastembed==0.8.0` and `BAAI/bge-small-zh-v1.5`. The committed model manifest must name the immutable repository revision, dimension 512, provider/package versions, encoding policy, expected file list, every file SHA-256, and aggregate manifest digest. A floating branch or model ID alone is invalid.
- [ ] `COURSE_EMBEDDING_MODEL_DOWNLOAD=1` invokes a revision-aware downloader into an ignored quarantine directory, forbids floating HEAD and alternate-source fallback, verifies every expected hash/size, then atomically promotes to a `specific_model_path`. Runtime is `local_files_only` and loads only that verified path; failure never substitutes a fake provider.
- [ ] Implement `course-studio-rrf-v1` exactly as specified above and persist the policy/index/model identity in retrieval evidence.
- [ ] Process the transactional index outbox after card publication; only mark a hybrid index snapshot ready when FTS and verified semantic rows share the published-version digest. Compose either waits for the requested ready snapshot or returns explicitly degraded evidence.
- [ ] Run focused tests with sockets denied. Then execute the exact wrapper below. The producer applies the common preflight/temp/self-validate/atomic-seal/failure-code contract and verifies quarantine → promotion → index/query; the parent shell—not the child Python process—always removes the opt-in. Afterward deny sockets and prove the sealed model cache repeats successfully with zero network.

```powershell
$code = 0
$env:COURSE_EMBEDDING_MODEL_DOWNLOAD = '1'
try {
  python platform/qa/run.py embedding-model-live --receipt platform/helper/evidence/embedding-model-live.json
  $code = $LASTEXITCODE
} finally {
  Remove-Item Env:COURSE_EMBEDDING_MODEL_DOWNLOAD -ErrorAction SilentlyContinue
}
if ($code -ne 0) { exit $code }
```
- [ ] Stage exact dependency, migration, manifest, implementation, QA runner/tests, producer-created sealed receipt, and focused test files; commit `feat(helper): add pinned hybrid retrieval`.

---

### Task 5: Implement blocking exact and near-duplicate review

**Files:** create `near_duplicates.py`, `tests/test_near_duplicates.py`; modify `cards.py`, `reviews.py`, `retrieval.py`, and focused tests.

- [ ] Write RED tests for same-version replay with no self-edge; different logical candidate with the same normalized digest stored archived plus auto-resolved audit and candidate→published edge; deterministic token shingles; FTS candidate scores; embedding cosine candidates; unioned lane evidence; thresholds/version policy; candidate ordering; model-degraded review; dismissal/duplicate-link decisions; and no automatic merge.
- [ ] A candidate card cannot publish while its exact/near-duplicate review is unresolved. If semantic comparison is unavailable, create an explicit degraded review that a lecturer must resolve; do not silently pass.
- [ ] Implement versioned `course-studio-near-dedup-v1` with candidate and index digests. Store only IDs/scores/evidence, not copied card payloads.
- [ ] Run `python -m pytest platform/helper/tests/test_near_duplicates.py platform/helper/tests/test_cards.py platform/helper/tests/test_retrieval.py -q` and `git diff --check`.
- [ ] Stage exact files; commit `feat(helper): review semantic card duplicates`.

---

### Task 6: Generate source-change and course-feedback upgrade suggestions

**Files:** create `upgrades.py`, `tests/test_upgrades.py`; modify `cards.py`, `reviews.py`, and their tests.

- [ ] Write RED tests for changed/unchanged/removed chunks, dataset/schema/visual changes, multiple affected cards/courses, repeated detection, dismissal, acceptance into candidate version, and typed course-feedback suggestions.
- [ ] Prove accepted suggestions enter the common candidate → dedup/tag/provenance review → `knowledge_card_publish` path and never change an old card/course.
- [ ] Implement field-level digest diffs, affected immutable IDs, actor/evidence audit, and `register_or_reuse` semantics.
- [ ] Run focused upgrade/card/review tests and `git diff --check`; stage exact files; commit `feat(helper): propose governed knowledge upgrades`.

---

### Task 7: Compose adjustable outlines with coverage and gap evidence

**Files:** create `composer.py`, `tests/test_composer.py`.

- [ ] Write RED tests for all `CourseRequirement` fields, tag/audience/difficulty/prerequisite coverage, deterministic chapters, five-minute allocations, include/exclude overrides, required visual/data prerequisites, duplicate placements, lifecycle-invalid cards, stale retrieval snapshots, uncovered goals, and optimistic confirmation races.
- [ ] Implement deterministic composition. The Helper may summarize governed metadata but cannot invent sourced facts; uncovered required goals remain visible and block confirmation/publication.
- [ ] Prove private/internal/public confirmation summaries are digest-bound and a stale response cannot win.
- [ ] Run focused composer/retrieval tests and `git diff --check`; stage exact files; commit `feat(helper): compose adjustable grounded outlines`.

---

### Task 8: Build the content-only draft Slide AST and RuntimeManifest

**Files:** create `slide_builder.py`, `tests/test_slide_builder.py`; modify `catalog.py` and composition contracts/tests.

- [ ] Write RED tests tracing every content node to a placement, card version, chunk/source version, and evidence. Reject dangling, digest-invalid, suspended/revoked, unresolved review/gap, and unsafe runtime jobs.
- [ ] Implement concise stage nodes, separate presenter notes, deterministic content ordering, and an immutable draft `SlideDeckAst`/`RuntimeManifest`. This task has no visual attachment and no publication claim; those depend on Tasks 9-11.
- [ ] Prove a merely newer valid dependency creates an upgrade suggestion but does not change/reject the pinned draft unless the pinned dependency itself is invalid.
- [ ] Run slide/composer/catalog tests and `git diff --check`; stage exact files; commit `feat(helper): build traceable draft projections`.

---

### Task 9: Build the content-addressed artifact store and source visuals

**Files:** create `migrations/0005_artifact_metadata.sql`, `artifacts.py`, `source_visuals.py`, `tests/test_artifacts.py`, `tests/test_source_visuals.py`; modify `catalog.py`, `parsers/pptx_parser.py` and `tests/test_pptx_parser.py`.

- [ ] Write RED tests with synthetic PPTX and media fixtures for exact slide/media relationship, source-version digest match, bounded streaming, containment/reparse protection, atomic write, MIME sniff, dimensions, duplicate reuse, corrupt/oversize media, unsupported SVG, missing relation, and zero source writes.
- [ ] Implement immutable artifact metadata/foreign keys in SQLite and opaque artifact bytes under caller-supplied ignored app data. No response or evidence exposes a local path.
- [ ] Each asset returns an independent success/failure outcome so a malformed image does not roll back valid siblings.
- [ ] Run focused tests and `git diff --check`; stage only named files; commit `feat(helper): materialize traced source visuals`.

---

### Task 10: Build evidence-backed dataset charts

**Files:** create `chart_builder.py`, `tests/test_chart_builder.py`; modify `parsers/dataset_profiler.py`, `tests/test_dataset_profiler.py`, `artifacts.py`, and focused tests.

- [ ] Write RED tests using tiny CSV/XLSX fixtures for allowlisted aggregates, pinned dataset/schema/column digests, row/time/result ceilings, sensitive-field refusal, deterministic SVG bytes, title/description, query/spec digest, lineage, and per-item mixed success.
- [ ] Reject arbitrary SQL, expressions, HTML/script/external references, screenshot-derived fake charts, and schema drift.
- [ ] Implement typed `bar|line|scatter` specs over verified DuckDB relations and validate generated SVG before storage.
- [ ] Run focused chart/dataset/artifact tests and `git diff --check`; stage exact files; commit `feat(helper): build verifiable dataset charts`.

---

### Task 11: Add provenance-safe network discovery, acquisition, and freshness revalidation

**Files:** create `migrations/0006_visual_provenance.sql`, `network_visuals.py`, `tests/test_network_visuals.py`, versioned offline fixtures under `tests/fixtures/visual-providers/wikimedia`; modify `pyproject.toml`, `artifacts.py`, visual policy tests, `platform/qa/run.py`, and `platform/qa/test_run.py`; create the sealed network receipt only through the live producer.

- [ ] Create hashed fixtures for search, `imageinfo`, redirects, headers, license metadata, and tiny images. Default tests deny all network.
- [ ] Before invoking it, write RED QA CLI tests for provider-only `network-visual-acquisition-live`: only its opt-in is accepted; bounded provider policy and fixed output containment are enforced; common exit codes are exact; temp schema/object/freshness validation precedes atomic promotion; failure preserves the prior sealed receipt. The receipt explicitly states `coursePublicationVerified=false` and cannot satisfy `authentic-visuals`. Extend the already tested common receipt helpers and implement this producer first.
- [ ] Write RED SSRF/rebinding/redirect/downgrade/size/MIME/hash/HTML/unknown-license/expired-candidate tests and policy tests for all authenticity classes. Commons must never be labeled official-primary.
- [ ] Discovery accepts a bounded query and creates a short-lived opaque candidate. Acquisition accepts only that ID, revalidates every redirect and authoritative metadata, stores verified bytes, and emits typed `TrustedExternalLink` provenance.
- [ ] Implement a mutable verification projection with 24-hour public TTL. `revalidate_visual` refreshes authoritative metadata; offline/expired/removed/license-changed evidence fails public publish while cached historical evidence remains intact.
- [ ] Run offline focused tests, then execute the exact wrapper below. The provider-only producer applies the common preflight/temp/self-validate/atomic-seal/failure-code contract and performs one bounded acquire/revalidate. The parent shell always removes the opt-in. A failed live call remains truthful and cannot be replaced by generated content or overwrite the last sealed receipt.

```powershell
$code = 0
$env:COURSE_NETWORK_VISUAL_TEST = '1'
try {
  python platform/qa/run.py network-visual-acquisition-live --receipt platform/helper/evidence/network-visual-acquisition-live.json
  $code = $LASTEXITCODE
} finally {
  Remove-Item Env:COURSE_NETWORK_VISUAL_TEST -ErrorAction SilentlyContinue
}
if ($code -ne 0) { exit $code }
```
- [ ] Stage exact migration/provider/fixture/test/dependency, QA runner/tests, and producer-created sealed receipt files; commit `feat(helper): acquire policy-verified network visuals`.

---

### Task 12: Bind verified visuals and finalize atomic course publication

**Files:** modify `slide_builder.py`, `catalog.py`, `domain/slide_ast.py`, `tests/test_slide_builder.py`; create `tests/test_course_publication.py`.

- [ ] Write RED tests requiring source-provided, data-derived, and network `VisualPlacement`s to bind exact visual/artifact/evidence/attribution/transformation IDs into a new immutable course/deck/runtime version. Attach/detach never rewrites an old deck/card.
- [ ] Validate artifact metadata foreign keys, MIME/hash, dataset schema/query, current network verification projection, scope × origin × rights × freshness table, CC BY/CC BY-SA/public-domain/GFDL/no-derivatives transformation obligations, and attribution/change notices.
- [ ] Test new-version suggestions versus invalid pinned dependencies: a newer valid source/card/asset only suggests upgrade; dangling, digest/schema-invalid, suspended/revoked, unresolved review/gap, or expired scope-required network authorization blocks publication.
- [ ] Implement atomic `validate_course_version` and `publish_course_version`. The committed outcome returns Helper-issued `courseVersionId`, `slideDeckId`, `runtimeManifestId`, `runtimeManifestDigest`, and `courseProjectionId`; reopening resolves byte-identical AST/manifest. Domain rows and durable mutation outcome commit in one transaction; post-commit response loss is recovered by `operationId`, never reported as a rollback.
- [ ] Run `python -m pytest platform/helper/tests/test_slide_builder.py platform/helper/tests/test_course_publication.py platform/helper/tests/test_artifacts.py platform/helper/tests/test_network_visuals.py -q` and `git diff --check`.
- [ ] Stage exact slide/catalog/domain/publication-test files; commit `feat(helper): publish courses with versioned visual evidence`.

---

### Task 13: Expose durable bounded upload, source inventory, import, review, and card APIs

**Files:** create `migrations/0007_import_sources.sql`, `uploads.py`, `source_inventory.py`, `tests/test_uploads.py`, `tests/test_source_inventory.py`; modify `jobs.py`, `api.py`; create `tests/test_knowledge_review_jobs.py`; modify `tests/test_api.py`, `tests/test_server.py`.

- [ ] Write RED tests for authenticated streaming `POST /v1/uploads`, bounded `knowledge_source_inventory_list`, `knowledge_import_start`, `knowledge_import_status`, `knowledge_import_cancel`, `knowledge_review_list`, bounded `knowledge_review_detail`, `knowledge_review_resolve`, `knowledge_card_publish`, `knowledge_upgrade_list`, `knowledge_upgrade_resolve`, and `operation_status`.
- [ ] Bound uploads to 20 MiB, stream atomically into ignored app data, validate name/type/size/hash, and return a short-lived opaque `uploadId`. Starting import transactionally acquires an active-import lease before acknowledging the job. After deterministic validation, atomically promote the exact bytes into an immutable content-addressed source blob plus durable source/version metadata before releasing the lease; only then may temporary upload expiry run. Cancel/expiry coordinates with the lease and worker so it cannot delete a live input or a promoted source. Crash/retry/concurrent-expiry tests prove no lost source, orphan temp, double promotion, or partial catalog row.
- [ ] `knowledge_source_inventory_list` is session-authenticated, paginated, capped, `no-store`, and returns only Helper-issued opaque source/version IDs plus bounded safe name/type/size/digest/status metadata. It makes Helper-registered sources discoverable without exposing paths, bytes, content, URLs, protected roots, or arbitrary filesystem browsing. Import accepts only an active `uploadId` or one of these durable opaque source/version IDs—never a browser path.
- [ ] Require strict schemas, bounded pagination/filters/detail AST/citation excerpts, `Cache-Control: no-store`, `operationId + requestDigest`, actor and both expected review/card digests, per-item progress/failure outcomes, origin/session auth, worker-side revalidation, stable safe errors, and no unbounded card/source body, raw path, arbitrary URL, or token leakage.
- [ ] Implement the real pipeline: upload/lease/promote/register → deterministic ingest → candidate construction → exact/near dedup → tag/provenance review creation. Reparse and later source-visual extraction resolve only the promoted content-addressed source version. Upgrade acceptance creates a candidate and common review task; only `knowledge_card_publish` may publish after all gates, atomically enqueue affected index work, and return `indexState/indexSnapshotId`.
- [ ] On parent timeout/disconnect, query the durable outcome ledger before responding. Expose `committed|rolled-back|unknown|in-progress` and idempotent retry; never claim failure after an unobserved commit.
- [ ] First run upload/lease/promotion/inventory tests and commit only migration, upload, inventory, API slice, and tests as `feat(helper): persist governed import sources`.
- [ ] Then run review/job/API/server tests plus non-reference Helper regression; stage only the remaining review/job/API files and tests; commit `feat(helper): expose governed knowledge review jobs`.

---

### Task 14: Expose composition, visual binding, publication, and artifact APIs

**Files:** modify `jobs.py`, `api.py`; create `tests/test_course_jobs.py`, `tests/test_artifact_api.py`; modify API/server tests.

- [ ] Write RED tests for `knowledge_index`, `course_compose`, `course_outline_confirm`, `course_visual_attach`, `course_visual_detach`, `course_validate`, `course_publish`, `chart_build`, `visual_search`, `visual_acquire`, `visual_revalidate`, shared `operation_status`, and authenticated `GET /v1/artifacts/{artifactId}`.
- [ ] Browser requests use IDs/specs only. Responses may include only server-validated `TrustedExternalLink` landing/license links required for attribution; never final media redirects, local paths, arbitrary input URLs, SQL, or unbounded card content.
- [ ] Artifact GET requires the established session, sends exact MIME/length, `nosniff`, content-address containment, and a safe 404. Private/internal artifacts use `Cache-Control: private, no-store` and cannot be reused across session/origin; only explicitly public, published, content-addressed assets may use long-lived immutable cache. Network/source SVG is not served renderable.
- [ ] Every mutation uses the Task 3 outcome ledger. Publish revalidates the complete pinned snapshot atomically; validation failure leaves the prior course untouched, while response loss returns status-recoverable `committed|unknown` rather than a false failure.
- [ ] First implement and run the `knowledge_index`, compose, outline-confirm, and operation-recovery slice; stage only its job/API/tests and commit `feat(helper): expose governed course composition jobs`.
- [ ] Next implement and run authenticated artifact GET, `chart_build`, `visual_search`, `visual_acquire`, and `visual_revalidate`; stage only that API/job/test slice and commit `feat(helper): expose governed visual artifacts`.
- [ ] Finally implement and run visual attach/detach plus course validate/publish, operation-loss recovery, and non-reference Helper regression; stage only that API/job/test slice and commit `feat(helper): expose governed course publication jobs`.

---

### Task 15: Add strict web contracts, complete clients, and safe artifact loading

**Files:** create web composition/review/slide/visual domain schemas and tests; modify `services/knowledge-client.ts`; create/modify its tests; create `services/artifact-client.ts` and tests.

- [ ] Write RED tests for every Task 13/14 job method, upload/import/cancel/status and operation recovery, strict response parsing, bounded review-detail/list/resolve/publish closure, in-memory detail disposal, index readiness/degraded state, stale schema/digest rejection, duplicate placement rejection, and mismatched job/result types.
- [ ] Implement job-specific methods only; never expose `runArbitraryJob`. Maintain one verified Helper session.
- [ ] Implement `fetchArtifact(artifactId)`: authenticated fetch, status/MIME/content-length/body ceiling validation, Blob URL creation, abort support, and deterministic `URL.revokeObjectURL` cleanup on replacement/unmount.
- [ ] Parse `TrustedExternalLink` as exact HTTPS provenance types and render external links with safe `rel`; scrub tokens, paths, arbitrary URLs, and response fragments from user-visible errors.
- [ ] Run web domain/service tests, typecheck, and `git diff --check`; stage exact files; commit `feat(web): add governed knowledge and artifact clients`.

---

### Task 16: Migrate to a whitelist-only persisted workspace v2

**Files:** modify `domain/course.ts`, `course-schema.ts`, `state/storage.ts`, `storage.test.ts`, `state/workspace.tsx`, `workspace.test.tsx`.

- [ ] Write RED migration tests for a whitelist-only `PersistedWorkspaceV2` containing requirement/outline/course/deck/runtime/card/visual IDs and bounded view preferences only.
- [ ] Serialize representative v1 data and require the v2 serialized string contains no `extractedText`, chunk/card body, Helper payload, URL, binary/base64, token, nonce, artifact bytes, or local path.
- [ ] Migrate by first writing and validating v2, then deleting the v1 key. Keep a bounded `legacy-unlinked` summary only; do not persist or promote old `extractedText` as a card/chunk in the browser.
- [ ] Test reopen pins identical IDs, same-input reuse, async generation race protection, Helper failure preserving the prior course, and one-time migration rollback safety.
- [ ] Run focused state/storage tests, typecheck, and `git diff --check`; stage exact files; commit `refactor(web): persist identifiers only in workspace v2`.

---

### Task 17: Replace fixed generation with the complete requirement and outline flow

**Files:** modify `domain/course-agent.ts` and tests, `components/GenerateStep.tsx` and its focused test; create `CourseRequirementPanel.tsx`, `CourseOutlinePanel.tsx` and tests; modify `workspace.tsx` tests.

- [ ] Write RED interaction tests for title, audience, learning goals, duration, required/excluded tags, and `private-training|internal|public` usage scope. Submitting creates a requirement and compose request, not the old fixed three-chapter/eight-lesson course.
- [ ] Show recalled card IDs/bounded summaries, tag matches, retrieval mode/evidence, goal coverage, gaps, minutes, prerequisites, and include/exclude controls. A changed draft invalidates prior confirmation.
- [ ] Only an explicit digest-bound confirmation creates the course projection. Offline local generation remains a clearly `legacy-unlinked`, non-publishable rehearsal fallback.
- [ ] Compose requests name a ready `indexSnapshotId`; after card publish the UI waits for the index outbox result. If semantic indexing is unavailable, it presents and records explicit FTS-only degraded evidence rather than silently skipping the new card.
- [ ] Keep the existing four top-level steps and bright visual system; add no knowledge-home or fifth step.
- [ ] Run Generate/outline/workspace tests, full typecheck/build, and `git diff --check`; stage exact files; commit `feat(studio): confirm grounded course outlines`.

---

### Task 18: Close import, review, visual placement, editor, presenter, and publish UI loops

**Files:** create/modify `KnowledgeReviewDrawer`, `KnowledgeEvidencePanel`, `KnowledgePreparationPanel`, `ImportStep`, `CourseEditor`, `SourcePanel`, `ValidationPanel`, `StageView`, `PresenterView`, their focused tests, and `app.css`.

- [ ] Write RED tests for bounded upload and opaque `uploadId`, import start/progress/cancel/per-item failures, candidate creation, all review types, authenticated bounded card AST/citation review detail, digest-bound accept/dismiss and card publish/index wait, review detail removal on close/session change and absence from persisted storage, blocking counts, source/data/network visual selection, attach/detach, attribution/transformation notice, invalid artifact fallback, operation outcome recovery, and scope-aware course validate/publish.
- [ ] Prove `GenerateStep`, editor, validation, Stage, and Presenter use the real client and pinned Slide AST. An attached source visual, chart, and network visual must render from the same AST visual ID/artifact/attribution tuple in editor, stage, and presenter.
- [ ] Cover public/private/internal behavior, validation/pre-commit failure preserving the old course, post-commit response loss recovered through operation status, reopen preserving pinned IDs, and newer knowledge showing an upgrade suggestion only.
- [ ] Cover Escape/focus trap/return, keyboard access, approximately 44 px controls, screen-reader labels, reduced motion, Blob URL cleanup, Helper offline/degraded states, and concise stage content.
- [ ] Implement contextual drawers inside Import/Generate/Edit/Validation only, real Phosphor icons, light tokens, and no raw paths/internal delivery language.
- [ ] Complete the Import + knowledge review/publish/index-wait loop first; run its focused tests and commit only those components/tests/styles as `feat(studio): govern imported knowledge`.
- [ ] Complete composition editor + visual placement + validation/publication next; run its focused tests and commit only that slice as `feat(studio): compose courses with authentic visuals`.
- [ ] Complete shared Slide AST rendering in editor/Stage/Presenter plus accessibility, operation recovery, and reopen regression last; run all focused component tests, then full web tests, typecheck, build, and `git diff --check`; stage exact files only and commit `feat(studio): publish evidence-backed courses`.

---

### Task 19: Exercise the real loopback/browser path and certify bounded evidence

**Files:** create `platform/web/e2e/knowledge-course.spec.ts`, `platform/web/e2e/browser-policy.json`, `platform/web/playwright.config.ts`, Helper/app lifecycle fixtures, Helper evidence receipts, `course-composition-design-qa.md`; modify `platform/web/package.json`, its lockfile, `platform/qa/run.py`, `platform/qa/test_run.py`, `.superpowers/sdd/progress.md`. Task 4 already owns the model producer/common receipt helpers and Task 11 owns provider-only network acquisition; this task adds the full reference-course and network-publication producers plus offline receipt consumers without moving their earlier implementation forward in time.

- [ ] Write RED QA validators for canonical hashes, immutable IDs, exact model identity, RRF evidence, dedup review, tags, pinned placements, visual bindings/attribution, dataset schema, URL freshness status, scope-aware publish, idempotent second pass, forbidden leakage, and zero protected-source writes.
- [ ] Before invoking either final producer, write RED CLI/receipt tests for `reference-knowledge-live` and `network-visual-publication-live`. Reference tests require exact allowlist/root containment and before/after zero-write proof. Network tests require a real current acquire/revalidate plus public-scope authorization inside the 24-hour TTL. Both require mutually exclusive opt-ins, common exit codes, verified system Chrome, real loopback UI job/operation IDs, immutable card/course/deck/runtime/visual-placement/publication IDs/digests, byte-identical reopen, idempotent second pass, temp self-validation, atomic seal, and no overwrite on failure. Also test that `course-composition`, `authentic-visuals`, and `all` reject acquisition-only evidence, require the applicable final sealed receipt digests, verify every live opt-in is absent, and cannot dispatch any producer.
- [ ] Pin `@playwright/test` in the package lock with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`, add a `test:e2e` script/config, and never run `playwright install` from a default gate. Commit `browser-policy.json` with the verified system Chrome channel, exact product/file version, executable SHA-256, pinned Authenticode publisher, and allowed basename. A socket-denied preflight resolves the installation server-side, verifies every field, passes only that `executablePath` to Playwright, and fails `E2E_BROWSER_MISSING|E2E_BROWSER_POLICY_MISMATCH` instead of downloading or silently selecting another browser. Implement deterministic Helper/app start/readiness/teardown with ignored isolated app data. The default fixture-backed E2E uses a clean catalog and is part of the release gate.
- [ ] Add the real loopback/browser E2E: upload → `knowledge_import_start` → inspect per-item status → candidate review/dedup/tag → publish card → wait for index snapshot (or assert honest degraded mode) → enter complete requirement → compose → adjust → confirm → attach source visual + chart + network fixture visual → validate → publish → recover the operation outcome → reopen → Stage → Presenter. It may not begin from the existing 12-card catalog. The final receipt references actual upload/operation/job/evidence IDs from this path, not a private orchestration shortcut.
- [ ] Offline `all` validates committed historical receipts and denies reference/network/model access. `authentic-visuals` must print the exact status `HISTORICAL RECEIPT VERIFIED — CURRENT NETWORK AUTHORIZATION NOT CERTIFIED`; neither gate may claim current license freshness or a new live run.
- [ ] At final acceptance only, run the exact reference wrapper below from a parent shell with all live opt-ins initially absent. `reference-knowledge-live` opens only the committed allowlist through the Helper, drives the real loopback/browser UI to build/review/publish the verified 12-card catalog, composes GPT/prompt/bike/RFM goals, attaches at least one allowlisted PPTX visual and one data-derived chart, validates/publishes/reopens, and repeats independently with zero new source/card/review/course/deck/visual versions and identical bytes/digests. Only after verifying the exact allowlist, approved source digests, before/after zero writes, UI operation lineage, publication, reopen, and idempotence may it self-validate and atomically seal `reference-knowledge-live.json`. It permits exactly: `AI.pptx` slides 3-18; `AIGC实操 -数据分析.md` heading `自行车共享需求`; `AIGC实操-Prompt工程.md` headings `Prompt概论` and `正确提问`; `dataset/1-train.csv`; `AIExcelData/ex-17-RFM.xlsx`; and metadata-only inventory roots `dataset`, `AIExcelData`. Never inspect another object.

```powershell
$code = 0
$env:COURSE_REFERENCE_ROOT = (Resolve-Path -LiteralPath 'D:\cursor\AI培训\references').Path
try {
  python platform/qa/run.py reference-knowledge-live --manifest platform/helper/course_helper/demo/reference-demo.json --receipt platform/helper/evidence/reference-knowledge-live.json
  $code = $LASTEXITCODE
} finally {
  Remove-Item Env:COURSE_REFERENCE_ROOT -ErrorAction SilentlyContinue
}
if ($code -ne 0) { exit $code }
```

- [ ] Run the exact network-publication wrapper below from a parent shell with all live opt-ins initially absent. Unlike Task 11's acquisition smoke, `network-visual-publication-live` drives the real loopback/browser path from a clean governed upload/catalog, performs a current bounded acquire/revalidate for one eligible AI-related visual, binds its opaque artifact/provenance IDs to a public course, validates/publishes within the 24-hour TTL, recovers the operation result, reopens identical bytes, and performs an idempotent second run. Only then may it self-validate and atomically seal `network-visual-publication-live.json` with trusted landing/license links and complete provenance—never paths or final media URLs. A network failure remains a truthful explicit-gate blocker and preserves the previous sealed receipt.

```powershell
$code = 0
$env:COURSE_NETWORK_VISUAL_TEST = '1'
try {
  python platform/qa/run.py network-visual-publication-live --receipt platform/helper/evidence/network-visual-publication-live.json
  $code = $LASTEXITCODE
} finally {
  Remove-Item Env:COURSE_NETWORK_VISUAL_TEST -ErrorAction SilentlyContinue
}
if ($code -ne 0) { exit $code }
```
- [ ] Perform desktop and narrow visual/interaction QA of Import, Generate, Edit/Validation, Stage, and Presenter. Record bright styling, real icons, no overlap/clipping/leakage, focus/Escape, attribution, and stage brevity.
- [ ] Run:

```powershell
foreach ($name in 'COURSE_EMBEDDING_MODEL_DOWNLOAD','COURSE_NETWORK_VISUAL_TEST','COURSE_REFERENCE_ROOT') {
  if (Test-Path "Env:$name") { throw "offline gate requires $name to be unset" }
}
python -m pytest platform/helper/tests -m "not reference_demo and not network_visual and not model_download" -q
python -m pytest platform/qa/test_run.py -q
python platform/qa/run.py course-composition
python platform/qa/run.py authentic-visuals
npm --prefix platform/web test -- --run
npm --prefix platform/web run typecheck
npm --prefix platform/web run build
npm --prefix platform/web run test:e2e
python platform/qa/run.py all
```

- [ ] When the model, reference-course, and network acquisition/publication live prerequisites are authorized, run their exact wrappers separately before the offline block. Immediately before the offline commands, assert `COURSE_EMBEDDING_MODEL_DOWNLOAD`, `COURSE_NETWORK_VISUAL_TEST`, and `COURSE_REFERENCE_ROOT` are all absent. Verify `course-composition`, `authentic-visuals`, and `all` consume the same sealed receipt digests, reject the provider-only network receipt as publication proof, and make zero network/reference/model calls. A skipped or failed producer remains an explicit uncertified/blocking status; never hand-author or copy a receipt.

- [ ] Stage only the named package/lock/config, QA files, receipts, E2E/lifecycle fixtures, design QA, and progress file; commit `test(platform): certify grounded course composition`.

## Completion boundary

This plan is complete only when the product UI—not an internal shortcut—can review and publish governed cards, compose and confirm a complete requirement, bind authentic source/data/network visual versions into immutable Slide AST, publish according to scope, reopen identical pinned versions, and render the same evidence in editor/stage/presenter. All locally executable gates must be green; offline receipts remain historical; the model, reference-course, network-acquisition, and network-publication live gates must be truthful; and no command may write to protected sources.

Physical dual-screen certification is explicitly outside this plan and remains `NOT CERTIFIED` until the dependent Win11 plan's separate real-hardware witness succeeds.

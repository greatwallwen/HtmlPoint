# Subagent-Driven Development Progress

Worktree: `D:\cursor\AI培训\.worktrees\course-studio`
Branch: `codex/course-studio-light`
Plan: `docs/superpowers/plans/2026-07-15-personal-ai-course-studio.md`
Baseline: `5e7c3944d97a9c3557e6b9cefbc332bb38d31b7f`

Task 0: complete (visual baseline, design spec, implementation plan, and sparse isolated worktree verified)
Task 1: complete (commits 5e7c3944d..a17a0a2bc, spec and quality review approved; Minor deferred to Task 3: set index.html lang to zh-CN)
Task 2: complete (commits a17a0a2bc..942d6d3d4, 52 tests and typecheck reported green; spec and quality re-review approved with no open findings)
Task 3: complete (commits 942d6d3d4..5c82d9e7d, 81 tests/typecheck/build/audit reported green; spec and quality re-review approved; scratch report metadata Minor corrected)
Task 4A: complete (commit bd9ebc3b8, RED 25 expected failures then focused 40/40 and regression 93/93 green; typecheck passed; independent review approved with no findings)
Task 4B: complete (commit 55a88226a, 62 interaction/regression tests plus typecheck/build green; two Important and two Minor accessibility findings fixed; re-review found no open Critical/Important issues)
Task 4C: complete (commit 18b7ebe97, 138 regression tests/typecheck/build/audit green; stale async, operation ownership, no-op revision, persisted/direct teach bypass, and same-batch races fixed; final independent review Pass)
Task 5A: complete (commit e41d659eb, 13 focused and 57 domain tests plus typecheck green; persistence-before-broadcast and native adapter cleanup coverage fixed; re-review Ready; no hardware certification claimed)
Task 5B: complete (commit 426ae971b, 40 focused and 165 full-web tests plus typecheck/build green; reconnect labels, pre-authority presenter race, StrictMode cleanup, and persistent certification truth fixed; final review PASS)
Task 6: complete (commit 48b1a9714, Python 34/34, theme 15/15, focused 8/8, full web 180/180, typecheck/build green; final re-review Ready: Yes; design QA PENDING before Task 7)

---

Plan: `docs/superpowers/plans/2026-07-16-knowledge-library-foundation-demo.md`
Baseline: `205db34ea`
Authoritative reboot plan SHA-256: `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80`
Pre-flight: complete (QA 37/37, web 194/194, typecheck/build green; plan review approved)

Foundation Task 1: complete (commits 205db34ea..e74b4f07a, 21 contract tests and schema/config checks green; spec and quality re-review approved with no findings)
Foundation Task 2: complete (commits e74b4f07a..0488b7b80, Task 2 26 passed/1 permitted symlink skip and helper 47 passed/1 skip; concurrency stress 20/20; spec and quality re-review approved)
Foundation Task 3: complete (commits 0488b7b80..55ce9963c, parser 5 passed/1 deselected, reference Demo 1 passed/5 deselected, helper 52 passed/2 skips; spec and quality re-review approved)
Foundation Task 3 Minor for final review: extraction summary says `len(chunks)` selected slides on image-only input; prefer `slide chunks` wording.
Foundation Task 4: complete (commits 55ce9963c..659e8b80c, local 12 passed/2 skips, reference integration 2 passed/12 deselected, helper 64 passed/4 skips; security/identity re-review approved; report wording Minor corrected)
Foundation Task 5: complete (commits 659e8b80c..10b26776e, Task 5 local 20 passed/3 deselected, exact reference 3 passed/20 deselected, source-root/catalog 27 passed/2 permitted symlink skips, helper 85 passed/8 skips; three security/integrity findings fixed; independent re-review approved)
Foundation Task 5 Minor for final review: `dataset_profiler.py` remains about 996 lines; defer format-adapter split until the public profiling interface is stable.
Foundation Task 6: complete (commits 10b26776e..8ae0aba4f, Task 6 focused 55 passed, 20x dual-connection stress green, helper non-reference 140 passed/2 permitted symlink skips/6 deselected; four Important findings fixed; independent re-review approved)
Foundation Task 6 Minor for final review: `cards.py` is about 997 lines; split vocabulary, candidate, governance/versioning, evidence, and persistence only after the public interface stabilizes.
Foundation Task 7: complete (commits 8ae0aba4f..8074bdff3, focused 177 passed, helper non-reference 243 passed/2 permitted symlink skips/6 deselected, 20x retrieval snapshot and direct lifecycle concurrency green; retrieval snapshot, latest vocabulary, direct catalog, and archive history findings fixed; final independent re-review approved with no findings)
Foundation Task 8: complete (commits 8074bdff3..3f7d21d76, API/server 32 passed before review, final helper non-reference 288 passed/2 permitted symlink skips/6 deselected, Python QA 37 and web 194/typecheck/build green; nonce, atomic bundles, publish receipt, and spawn startup findings fixed; final independent re-review approved with no findings)
Foundation Task 9: complete (commits 3f7d21d76..5ed8c248c, reference Demo 16 PPTX chunks/16 notes, 3 Markdown selectors, 2 datasets, 12 published cards, 12 review decisions, 3 degraded retrieval topics; CLI two-pass idempotence second pass zero new/duplicates; focused non-reference 19 passed/2 link-permission skips/1 deselected and helper non-reference 307 passed/4 skips/7 deselected; output-boundary Critical fixed; final independent re-review approved; current exact-five hashes and 358-item metadata inventory independently match the receipt)
Foundation Task 10: complete (commits 5ed8c248c..f564f97c3, focused 67 passed and typecheck green; early launch-fragment scrubbing, import-only session exchange, bounded knowledge summary client, bright import-panel UI, and four-step workflow independently approved with no remaining findings)
Foundation Task 11: complete (commits f564f97c3..9ea9a715d, canonical 5-source/358-item/12-published-card receipt matches Task 9 byte-for-byte, explicit read-only gate passed with two authorized reference executions, QA 99/helper 307/web 244/typecheck/build/root-unset all green, canonical digest and malformed-type findings fixed, independent re-review approved with no remaining findings; physical dual-screen remains NOT CERTIFIED)

---

Execution amendment (2026-07-17): operator explicitly discarded the old Git
history. Continue from the preserved isolated snapshot without Git, record
RED/GREEN commands plus exact changed-file SHA-256 inventories in bounded task
reports, finish both reviewed downstream plans, and initialize a new repository
only after fresh locally executable acceptance is green. Authority and reviewed
plan files remain byte-identical; see
`docs/superpowers/plans/2026-07-17-gitless-execution-amendment.md`.

Course composition Task 1: complete under Git-less protocol (initial RED 1
failed/15 skipped; review RED 11 failed/15 passed; final focused+domain 47
passed and Helper non-reference 333 passed/4 skipped/7 deselected; two
independent review rounds closed at 0 Critical/0 Important/0 Minor). Report:
`.superpowers/sdd/course-compose-task-1-report.md`, SHA-256
`2613624E7D7C98ED424340B07B6CDE8AABBA2C06362A480ADCA06323C97E0F02`.

Course composition Task 2: complete under Git-less protocol (focused 196
passed; Helper non-reference 351 passed/4 skipped/7 deselected; compileall
passed; independent joint regression 254 passed/3 skipped and final review
closed at 0 Critical/0 Important/0 Minor). Immutable card bytes, append-only
lifecycle events, rebuildable lifecycle/FTS projections, suspension semantics,
and API/Demo projected counts are verified. Report:
`.superpowers/sdd/course-compose-task-2-report.md`, SHA-256
`34141C31A249951410F44E89C9C0F912FF6042AF687A7D817BB5055BF68324FD`.

Course composition Task 3: complete under Git-less protocol (fresh focused 128
passed; fresh Helper non-reference/network/model 403 passed/4 skipped/7
deselected; compileall passed; bounded adversarial envelope/projection review
closed at 0 Critical/0 Important/0 Minor). Immutable composition, exact
course/deck/runtime/placement binding, append-only review truth, authenticated
operation recovery, and transactional publication-gated index outbox are
verified. Report: `.superpowers/sdd/course-compose-task-3-report.md`, SHA-256
`7E56069A4D8242DCD5646CC7A773E5C9B441B4F8FF55B628946BF2E4E7391B2B`.

Course composition Task 4 bootstrap amendment: active for the impossible
pre-download SHA-256 prerequisite only. Phase A is identity-anchored,
non-promoting, non-sealing, and must exit 3; Phase B independently re-downloads
and verifies the complete model/runtime lock. Original authority plans remain
byte-identical. Amendment:
`docs/superpowers/plans/2026-07-17-task4-model-manifest-bootstrap-amendment.md`,
SHA-256 `3753CA317C3BB422B5BD070D2338C1CABEB0A90581F3021C6CBB2ADCE69788F5`.

Main workspace promotion (2026-07-18): complete for the verified source
snapshot. The platform now lives at `D:\cursor\AI培训\platform`; 118 selected
files (3,961,345 bytes) matched the prior snapshot by SHA-256 with zero
mismatches. Six missing authority plans were restored, generated caches and
historical SDD noise were excluded, and Web dependencies were restored with an
offline lockfile install. Fresh root baseline: Helper non-reference excluding
the in-progress embedding-live slice 554 passed/4 skipped/7 deselected,
embeddings 41 passed, QA 146 passed, Web 244 passed, typecheck passed, and build
passed. Receipt: `.superpowers/sdd/main-workspace-migration-2026-07-18.md`.
Amendment: `docs/superpowers/plans/2026-07-18-main-workspace-execution-amendment.md`,
SHA-256 `28AF72AD995A71DD64BBD122FF983F753C8E4A2A8F6E0C2132B573B47711D13A`.

Course composition Task 4 local Phase B integration (2026-07-18): complete in
the main workspace. Independent QA is 150 passed; model-cache/embedding/live
receipt security is 146 passed with one deliberate duplicate-wheel fixture
warning. Exact authority binding, locked first fresh pipeline, independent
socket-denied replay, strict canonical receipt, and prior-preserving deferred
seal are implemented. Task 4 remains pending the real model gate: two exact
Phase A wrapper attempts failed closed at
`EMBEDDING_MODEL_METADATA_CONNECT_FAILED`; direct comparison found Hugging Face
timed out while PyPI returned HTTP 200. No candidate/cache/receipt was created
or replaced. See `.superpowers/sdd/course-compose-task-4-retrieval-report.md`.

Task 4 route diagnosis update (2026-07-18): a third exact outside-sandbox run
reproduced `EMBEDDING_MODEL_METADATA_CONNECT_FAILED`. The existing loopback
Clash proxy reaches the official origin, but the authority producer correctly
ignores ambient proxies. Temporary TUN changed the failure to
`EMBEDDING_MODEL_METADATA_DNS_FAILED` because Python received Mihomo fake IP
`198.18.0.8`; dual-DoH real-address plus official-hostname TLS probing passed.
Every TUN probe restored the original disabled state. A temporary hosts edit
was denied before mutation, and the system hosts SHA-256 remained
`95DD901E096E239F2E06B777F086D7B563D1BECAB47568FC0133E8FCCDE2E268`.
The remaining external prerequisite is Mihomo `redir-host` (or an equivalent
narrow real-IP DNS rule) before rerunning the unchanged exact wrapper. No
fake-IP acceptance, proxy fallback, candidate, cache, runtime, or sealed
receipt was added.

Task 4 local security closeout (2026-07-18): a P3 audit after green tests found
one HIGH, two MEDIUM, and then two additional Important receipt/recovery
issues. All are fixed. The final transaction uses Windows share-deny held
handles, completes all fallible checks before recovery deletion, preserves
exact prior bytes through a contained flushed recovery copy, validates
canonical UTC ordering, and truthfully scopes write evidence to the verified
generation tree with native/global coverage explicitly not certified. Root
verification is 184 receipt/QA passed, 153 related model/cache/embedding
passed, complete Helper 602 passed/11 skipped, and compileall passed. Final
independent re-review: 0 Critical / 0 Important, Ready: Yes.

Course composition Task 4 final live acceptance (2026-07-18): complete. The
Codex bundled Python 3.12.13 producer exited 0 and emitted
`EMBEDDING_MODEL_LIVE_VERIFIED: CPYTHON SOCKET-DENIED VERIFIED`; it also
truthfully emitted `OS NETWORK ISOLATION NOT CERTIFIED`. The official receipt
is verified, uses the exact 20-key contract with eight checks and 30 wheels,
has digest `560f908c20a5cf29f59f7f8f26b41e694b899d7dbfd6abfe6ed1926ed3ad1138`,
and binds active generation
`45acc0ce15d056f5faee9ad133f0716bd0c89bc51cf8304c65b00aaaac873c0c`.
Offline strict validation passed; final focused verification is 322 passed
with one deliberate duplicate-wheel-name warning; complete Helper verification
is 643 passed/11 skipped and compileall exits 0. Repository QA also passed
Python QA, Helper, Web 244, typecheck, build, and design/evidence gates; only
the intentionally deferred no-Git protected-path query remains unavailable.
Network settings were restored to `mode=rule`/`tun=false`, and temporary
configuration plus live opt-in are absent. See
`.superpowers/sdd/course-compose-task-4-retrieval-report.md`.

Course composition Task 5: complete under the main-workspace Gitless protocol.
Deterministic shingle/FTS/pinned-semantic lanes, degraded blocking reviews,
human dismissal/duplicate-link resolution, transactional exact-duplicate
audit, and payload-free digest-bound evidence are implemented. Independent
root regression: 595 passed/4 skipped/7 deselected. Task 13 retains the explicit
responsibility to require the scan seam in the real import-to-publish pipeline.
Report: `.superpowers/sdd/course-compose-task-5-report.md`.
The old `.worktrees/course-studio` snapshot has now been deleted; only the
ACL-locked, pytest-only `.worktrees/platform-reboot` cleanup tail remains.

Course composition Task 6: complete under the main-workspace Gitless protocol.
Source, dataset/schema, visual, and typed course-feedback upgrades use immutable
affected snapshots, field-level digest diffs, atomic proposal bundles, real Task
5 near-duplicate receipts, replay-safe register-or-reuse semantics, and a
fail-closed common publication seam. Rejected/dismissed or orphaned upgrade
candidates cannot publish; first resolutions cannot predate their review tasks.
Root verification: 109 related passed; complete Helper 615 passed/11 skipped;
compileall passed. Final independent adversarial re-review: 0 Critical / 0
Important, Ready: Yes. Report:
`.superpowers/sdd/course-compose-task-6-report.md`, SHA-256
`F668798ECC3B19BACE55D8DFFE21DFC6B2ED5FAD0E296444BB2EA38CE13951EC`.

Course composition Task 7: complete under the main-workspace Gitless protocol.
Authoritative composition performs controlled per-goal retrieval under one
sealed snapshot, persists raw retrieval receipts plus the composer-v2 binding
atomically, binds complete outline semantics and confirmation digests, rejects
forged/stale/lifecycle-invalid evidence, and rechecks lifecycle at course
registration. Current focused verification is 85 passed; the later complete
Helper milestone gate is 643 passed/11 skipped. No browser, network, physical
dual-screen, signing, or Git certification is claimed. Report:
`.superpowers/sdd/course-compose-task-7-report.md`.

Course composition Task 8: complete under the main-workspace Gitless protocol.
Confirmed courses now produce deterministic immutable content-only Slide AST
and RuntimeManifest drafts. Every emitted node pins placement, published card,
chunk, source version, and official publication evidence; lifecycle, digest,
lineage, blocking-review, gap, and unsafe-job failures close the seam. Newer
valid sources create governed suggestions without implicit draft mutation.
Focused verification is 65 passed; complete Helper verification is 653
passed/11 skipped. Repository QA also passed Python QA, Helper, Web 244,
typecheck, build, and design/evidence gates; its sole aggregate failure remains
the intentionally unavailable no-Git protected-path query. No visual,
publication, browser, network, signing, physical dual-screen, hardware, or Git
certification is claimed. Report:
`.superpowers/sdd/course-compose-task-8-report.md`.

Course composition Task 9: complete under the main-workspace Gitless protocol.
Exact PPTX relationships now materialize into bounded, atomically installed,
content-addressed artifacts with immutable catalog metadata, exact source and
parser lineage, path-free evidence, duplicate reuse, and independent per-asset
outcomes. Corrupt, oversized, unsupported, missing, forged, changed-source, and
containment/reparse cases fail closed. Focused verification is 212 passed/2
skipped; complete Helper verification is 670 passed/12 skipped. Repository QA
also passed Python QA, Helper, Web 244, typecheck, build, and design/evidence
gates; its sole aggregate failure remains the intentionally unavailable no-Git
protected-path query. No chart, network visual, course publication, live
browser, signing, physical dual-screen, hardware, or Git certification is
claimed. Report: `.superpowers/sdd/course-compose-task-9-report.md`.

Course composition Task 10: complete under the main-workspace Gitless protocol.
Verified CSV/XLSX relations now produce deterministic accessible bar, line,
and scatter SVG artifacts from typed allowlisted aggregates. Dataset, worksheet,
schema, column, query, result, visual, artifact, evidence, and lineage identities
are digest-bound; SQL, expressions, sensitive data, screenshots, active SVG,
external references, drift, and configured ceilings fail closed with independent
per-item outcomes. Focused verification is 38 passed/4 skipped; complete Helper
verification is 680 passed/12 skipped. Repository QA also passed Python QA,
Helper, Web 244, typecheck, build, and design/evidence gates; its sole aggregate
failure remains the intentionally unavailable no-Git protected-path query. No
network visual, course publication, live browser, signing, physical dual-screen,
hardware, or Git certification is claimed. Report:
`.superpowers/sdd/course-compose-task-10-report.md`.

Course composition Task 11: offline implementation complete under the
main-workspace Gitless protocol; the live provider gate remains NOT CERTIFIED.
Migration 0006, opaque short-lived discovery, policy-bound Wikimedia
acquisition, immutable history, mutable 24-hour revalidation, canonical
transactional receipt sealing, and fail-closed network/metadata/license tests
are implemented. Focused verification is 224 passed/1 skipped. Repository QA
passed Helper 708 selected tests, Web 244, typecheck, build, and all
design/evidence gates; its aggregate Git query remains intentionally unavailable.
The exact live wrapper reached only `NETWORK_VISUAL_ACQUISITION_FAILED` because
the sole public DNS target timed out during TLS handshake, so no sealed receipt
was created or replaced and course publication is not certified. Report:
`.superpowers/sdd/course-compose-task-11-report.md`.

Course composition Task 12: complete under the main-workspace Gitless protocol.
Published course revisions now pin exact source, data, and network visual
placements into immutable Slide AST and RuntimeManifest snapshots, enforce
artifact/evidence/attribution/transformation/scope/rights/freshness contracts,
and commit course/deck/manifest plus durable operation outcome atomically.
Attach/detach creates a later immutable revision; response loss recovers by
operation ID; a late failure rolls back all new rows. Focused verification is
49 passed/1 skipped, composition regression is 95 passed/1 skipped, and full
Helper is 711 passed/12 skipped. Repository QA passed Python 166, Web 244,
typecheck, build, design, and evidence gates; its sole aggregate failure remains
the intentionally unavailable root-Git protected-path query. Live network,
browser publication, signing, physical dual-screen, hardware, OS isolation,
and Git remain NOT CERTIFIED. Report:
`.superpowers/sdd/course-compose-task-12-report.md`.

Course composition Task 13A (current execution-plan Task 4): complete under the
main-workspace Gitless protocol. Migration 0007, authenticated 20 MiB streaming
uploads, durable import leases, content-addressed promotion, cancellation and
expiry fencing, response-loss replay, duplicate-byte reuse, and bounded opaque
source inventory are implemented. Focused upload/inventory verification is 19
passed; migration regression is 21 passed; complete Helper is 731 passed/12
skipped. Two stale `1..6` migration assertions found by the first broad run were
updated to exact `1..7`, then the complete suite passed. No protected reference
root was read, and no API/job/review, browser, network, signing, physical
dual-screen, hardware, OS isolation, or Git certification is claimed. Report:
`.superpowers/sdd/course-compose-task-13a-report.md`.

Course composition Task 13B3: complete under the main-workspace Gitless
protocol. Authenticated bounded review list/detail and upgrade list projections,
strict lower-camel jobs, opaque pagination, canonical envelope validation, and
`no-store` API responses are implemented. Focused verification is 91 passed;
py_compile passed. P1/medium was sufficient and Ultra was not needed. Report:
`.superpowers/sdd/course-compose-task-13b3-report.md`.

Execution-plan Task 4 verification: complete. Recovery, idempotency,
authentication, integrity, and bounded-projection gates passed 123 tests with
one Windows permission-dependent reparse-fixture skip. The 2.76 GB reproducible
external pytest temp root was verified and removed. P1/medium was sufficient;
no Ultra escalation was warranted. Report:
`.superpowers/sdd/course-compose-task-13b4-verification-report.md`.

Course composition Task 13B4: complete. Authenticated digest-bound review
resolution, card publication, and upgrade resolution now commit through the
durable operation ledger; card publication atomically enqueues deterministic
index work and response loss recovers the committed result. Final related gate
is 181 passed; py_compile passed. Eleven reproducible Task13B4 temp roots
(about 14 GB) were removed. Report:
`.superpowers/sdd/course-compose-task-13b4-report.md`.

Course composition Task 13B5: complete. Governed Markdown/PPTX imports now
reserve the browser operation for the final parsed/candidate/review outcome;
lease and promotion are deterministic internal operations. Promoted blobs are
parsed through a contained temporary root, rebound to governed source/chunk/
visual identities, and produce tagged review candidates plus provenance and
duplicate governance. Related gate is 199 passed/4 skipped; six reproducible
temp roots (about 2.84 GB) were removed. Report:
`.superpowers/sdd/course-compose-task-13b5-report.md`.

Course composition Task 13B6: complete. Governed CSV/Parquet/XLS/XLSX blobs
now produce bounded source-bound dataset profiles, evidence, and review tasks
under the final client operation; replay does not reprofile. Related gate is
131 passed/4 skipped; three reproducible temp roots (about 2.76 GB) were
removed. P1/medium was sufficient. Report:
`.superpowers/sdd/course-compose-task-13b6-report.md`.

Course composition Task 13 milestone: Helper scope complete. Complete Helper
verification is 771 passed/13 skipped; the 2,999,707,553-byte dedicated temp
root was removed. Task 14 course/publication jobs and Web loops remain. Report:
`.superpowers/sdd/course-compose-task-13-report.md`.

Course composition Task 14A: complete. Authenticated strict `knowledge_index`,
`course_compose`, and `course_outline_confirm` jobs now consume expected index
work, expose explicit FTS-only degradation, perform idle-connection retrieval
followed by transaction-time revalidation, atomically persist requirement/
evidence/outline/outcome, create the confirmed course projection, and recover
committed results after response loss. Focused spawned-worker verification is
3 passed; complete Helper is 774 passed/13 skipped. Ten dedicated pytest roots
(11,826,034,592 bytes) were removed. Router classification was P3/xhigh with no
Ultra requirement. Task 14 visual/artifact and publication slices remain.
Report: `.superpowers/sdd/course-compose-task-14a-report.md`.

Course composition Task 14 milestone: complete. The Helper now exposes strict
authenticated index, composition, outline-confirmation, chart, visual search/
acquire/revalidate, visual attach/detach, validation, publication, operation
recovery, and artifact delivery seams. Browser inputs remain opaque-ID/typed-
spec only; visual attribution is server-derived; final media URLs, paths, SQL,
tokens, and unbounded card bodies are excluded. Complete Helper verification is
781 passed/13 skipped. Twelve dedicated Task 14 pytest roots
(13,964,081,132 bytes) were removed. P3/xhigh was recommended for the sensitive
slices; Ultra was not needed. Task 15 Web contracts and clients are next.
Report: `.superpowers/sdd/course-compose-task-14-report.md`.

Course composition Task 15: complete. The Web now exposes only job-specific
Task 13/14 clients, parses strict cross-bound result contracts, keeps one
verified Helper session, validates safe HTTPS provenance links, and loads
bounded authenticated artifacts with deterministic Blob URL cleanup. Focused
verification is 53 passed; complete Web verification is 263 passed, with
typecheck and production build green. P3/xhigh remains recommended for this
security-sensitive slice; Ultra is not needed. Task 16 persisted workspace v2
is next. Report: `.superpowers/sdd/course-compose-task-15-report.md`.

Course composition Task 16: complete. Browser persistence is now a strict v2
whitelist of governed requirement/outline/course/deck/runtime/card/visual IDs,
bounded view preferences, and an optional count-only legacy-unlinked summary.
Migration writes and validates v2 before deleting v1 and preserves v1 on any
failed write or readback. Overlapping generation runs now accept only the
newest result. Focused verification is 107 passed; complete Web verification
is 259 passed, with typecheck, build, and whitespace checks green. P3/xhigh was
recommended; Ultra was not needed. Task 17 Helper-backed requirement and
outline flow is next. Report:
`.superpowers/sdd/course-compose-task-16-report.md`.

Course composition Task 17: complete. The Generate step now captures the full
governed requirement, composes only against an exact current index snapshot,
shows bounded recall/coverage/gap evidence, invalidates stale previews, and
creates a governed projection only after explicit digest-bound confirmation.
Offline generation remains visibly legacy-unlinked and non-publishable. Card
publication now returns its exact index outbox ID for the Task 18 wait loop.
Helper gates are 52 and 21 passed; focused Web is 119 passed; complete Web is
270 passed with typecheck, production build, and whitespace checks green.
P3/xhigh remains recommended; Ultra is not needed. Task 18 UI closure is next.
Report: `.superpowers/sdd/course-compose-task-17-report.md`.

Course composition Task 18: complete. The Studio now closes authenticated
import/review/card/index, source/data/network visual attachment, validation,
publication, response-loss recovery, immutable Helper reopen, and shared
editor/Stage/Presenter Slide AST rendering. Published IDs replace validation
IDs only after byte-bound reopen validation. Complete Web is 279 passed;
complete Helper is 782 passed/13 skipped; typecheck, production build, and
targeted whitespace checks are green. P3/xhigh remains recommended through
Task 19; Ultra is not needed. Report:
`.superpowers/sdd/course-compose-task-18-report.md`.

Course composition Task 19: locally complete. System Chrome now proves a clean
fixture-backed upload/review/index/compose/confirm/source-chart-network visual/
validate/publish/replay/reopen/Stage/Presenter path, including three decoded
artifact images in both teaching windows. Web is 279 passed/25 files; Helper is
785 passed/6 skipped/7 deselected; QA validators are 167 passed; typecheck and
production build are green. Protected/reference roots and live producers were
not accessed. Physical dual-screen hardware and current live network authority
remain NOT CERTIFIED. P3/xhigh was sufficient; Ultra was not needed. Report:
`.superpowers/sdd/course-compose-task-19-report.md`.

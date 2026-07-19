# Course Composition Task 4 Retrieval/Index Report

Date: 2026-07-17  
Scope: `docs/superpowers/plans/2026-07-16-course-composition-and-authentic-visuals.md`, Task 4 retrieval/index slice  
Execution protocol: Gitless and offline; no Git command, Git metadata initialization, network access, protected-reference access, or model download was used.

## Result

PASS for the retrieval/index slice. The SQLite index outbox, immutable index
candidate and snapshot records, strict model identity binding, deterministic
hybrid retrieval, bounded evidence, compatibility migration, and adversarial
tests are locally green. This subreport does not approve the separate model
bootstrap/live-producer slice and does not claim all of Task 4 complete.

All work stayed inside
`D:\cursor\AI培训\.worktrees\course-studio`. Neither
`Course_AIProduct/` nor `references/` was accessed. No fake embedding vector is
used in a live result: only a truly missing provider creates explicit FTS-only
degraded evidence; inference or identity failures fail closed.

## TDD and adversarial evidence

RED tests were written before implementation and reproduced the intended
failure modes for:

- missing migration tables, append-only triggers, outbox claim APIs, candidate
  rows, and snapshot sealing;
- incomplete raw snapshot sealing, late child insertion after a seal, and
  candidate/snapshot/outbox payload or digest tampering;
- lease expiry and reclaim, wrong-owner completion, concurrent workers,
  response-loss retry, provider crash, and SQL failure rollback;
- wrong provider/model identity, semantic inference failure, missing-provider
  degradation, vector count/dimension/normalization/digest divergence, and
  document batches larger than 1,000;
- filter-before-rank across lifecycle, required/excluded tags, audience, and
  difficulty; exact FTS and semantic ordering; equal-weight RRF with `k=60`,
  ID tie-break, and limit only after fusion;
- raw `card_tags` projection tampering, raw `card_fts` projection tampering,
  more than 1,000 candidate IDs with SQLite's variable limit forced to 999,
  and bounded query/token/tag/ID inputs;
- evidence lane/count/digest completeness, returned-lane bounding, strict
  model/artifact revision identity, path/URL exclusion, and sockets denied;
- legacy schema-v1 FTS projection upgrade without rewriting immutable card
  payload bytes or content digests;
- review-found attacks in which a query provider reused the expected SHA tuple
  under wrong model filenames/sizes, or a missing published-card FTS row was
  silently omitted from the eligible set.

The last regression cycle first produced `196 passed, 1 failed`: a schema-v1
fixture retained a historical truncated `projected_text`. Root-cause tracing
showed that retrieval's immutable projection was correct but the v1-to-v4
migration had not rebuilt the derived FTS row. The migration now replays the
append-only lifecycle projection and regenerates FTS inside the same migration
transaction; the strengthened migration assertion and the 197-test related
gate then passed.

## Implementation

- `0004_embeddings.sql` creates immutable candidate FTS/semantic row sets,
  sealed index snapshots, append-only outbox claims/results/consumptions, and
  trigger-level row-set, lifecycle, digest, provider, and late-child checks.
- `index_outbox.py` captures one deterministic eligible-card/outbox/lifecycle
  state from the complete lifecycle-eligible card set, requires every eligible
  card to have an exact immutable FTS projection, processes documents in
  batches no larger than 1,000, validates the exact approved provider identity,
  seals ready or truthful FTS-degraded snapshots, and reopens every persisted
  fact by recomputing its digest. Claim completion uses a savepoint so a failed
  seal removes partial candidate rows while preserving a sanitized append-only
  failed attempt.
- `retrieval.py` validates bounded requests, resolves a requested sealed
  snapshot, proves exact `card_tags` and FTS projections from immutable card
  bytes, reuses the same strict provider inventory validator used at index
  build time, applies all eligibility filters before both lanes, ranks FTS by
  `bm25 ASC, cardVersionId ASC` and semantic results by
  `score DESC, cardVersionId ASC`, applies exact `course-studio-rrf-v1`, and
  limits only after fusion. Candidate membership is a single JSON-bound SQL
  parameter, so the query remains valid beyond SQLite's host-parameter limit.
- Retrieval evidence records deterministic policy/query/candidate/snapshot,
  lane, fusion, return-order, and strict model identity digests. It expands
  only returned lanes (maximum 50) while retaining complete lane counts and
  digests, and contains no local path, model filename, or URL.
- `catalog.py` rebuilds lifecycle-derived FTS when migration 4 is first applied,
  preserving immutable schema-v1 card bytes while making upgraded catalogs
  satisfy the new index invariant.
- Compatibility tests now expect schema version 4 and the seven Task 4 index
  tables. No later composition, near-duplicate, visual, or UI feature was
  pulled into this slice.

## Verification

Focused retrieval/index gate:

```text
python -m pytest platform/helper/tests/test_embedding_index.py platform/helper/tests/test_retrieval.py -q
80 passed in 4.82s
```

Related Helper regression:

```text
python -m pytest platform/helper/tests/test_embeddings.py platform/helper/tests/test_catalog.py platform/helper/tests/test_lifecycle.py platform/helper/tests/test_operations.py platform/helper/tests/test_embedding_index.py platform/helper/tests/test_retrieval.py -q
199 passed in 13.65s
```

Helper regression excluding the separate in-progress Phase B model-cache REDs:

```text
python -m pytest platform/helper/tests -q -k "not phase_b"
490 passed, 11 skipped, 5 deselected, 56 warnings in 73.63s
```

The 56 warnings are existing `openpyxl` `datetime.utcnow()` deprecations. The
five deselected tests belong to the parallel model-cache final-phase slice and
were still RED because `validate_offline_wheel_closure` and `run_final_phase`
were not yet implemented when this retrieval slice froze.

Additional verification:

```text
python -m compileall -q platform/helper/course_helper platform/helper/tests
exit 0
```

Independent review round 1: `0 Critical / 2 Important / 0 Minor`. It reproduced
both findings independently: query-time identity comparison ignored filenames
and sizes, and an inner FTS join could silently omit an eligible published card.
Both now have RED-to-GREEN regression tests and the focused/Helper gates above
were rerun. Independent focused re-review then closed at
`0 Critical / 0 Important / 0 Minor`: it verified the exact five-file
path/size/SHA provider inventory at both index and query time, the complete
published-card `LEFT JOIN` eligible set, and fail-closed missing-FTS behavior.
The coordinating agent independently reran the focused gate with `80 passed`
and `compileall` with exit 0 before sealing this retrieval/index slice.

## Final SHA-256 inventory

```text
A434E72B3E266C4E40269EC8520F62F1D86EB54BBB2FBA2D91E719E45E25B791  platform/helper/course_helper/migrations/0004_embeddings.sql
75A8EAEF2DCF97F963485BF83C7DF22097D8FEE74D7FBDCAFD7902811E0EB0BA  platform/helper/course_helper/index_outbox.py
F4B5E16ABE57CC6FC0AAD87B8EDBC56B0027EAD713D3C4B2E23FFD12AF88E6C8  platform/helper/course_helper/retrieval.py
13436A29331DB56F2B2DA217D2625A890F4DCC96D422EF188B609502BEFCA950  platform/helper/course_helper/catalog.py
BBC3BE5D265A6C3D27BE4903FE9FB13EEBEE2C33E1C42E58A3F802F5E1CF64DD  platform/helper/tests/test_embedding_index.py
E56A0D4984A3E46568DF03F5E940FE43B8FFC202CF0E705737864DC336B77332  platform/helper/tests/test_retrieval.py
FFD9CEC92E085FFD9AC9E01B1FF36E0D53E795B77C9ECD7003B61AF7EADBE11E  platform/helper/tests/test_catalog.py
3B1389964991BC1FDDE9EF117B6DA96B448DFB8BD9C10033338765550C38B336  platform/helper/tests/test_lifecycle.py
1F7C0161DFB2CA8FFFBDF284E2717F7ACA9ED1204DBB8BFD7B77F1B05CF81216  platform/helper/tests/test_api.py
C196E90006FA58834CCCA6B203E414FD3BA73F554D7236C2605DC535AD739935  platform/helper/tests/test_composition_storage.py
```

This report is excluded from its own embedded inventory because embedding its
final digest would change that digest. Its sealed SHA-256 is reported separately
to the coordinating agent.

## Main-workspace Phase B integration addendum (2026-07-18)

The active platform root is now `D:\cursor\AI培训\platform`; this addendum
supersedes the historical isolated-worktree location above. The local Phase B
implementation is PASS, but Task 4 as a whole remains pending the real network
bootstrap/final live gate and no hardware or OS network-isolation certification
is claimed.

The producer now uses one `LiveEmbeddingAuthority` for exact manifest types,
runs the first fresh publish/index/query pipeline inside the final-generation
handle, write-denial, and socket-denial scope, constructs `FinalExpectation`
only from the returned real `FinalPhaseResult`, and performs an independent
socket-denied replay against a different database and temporary identity. Five
stable digests must match while process/challenge/temp evidence must differ.
The canonical 21-key receipt is written and flushed in quarantine, then sealed
through a deferred authority transaction. A post-return validation failure
rolls back the exact prior sealed receipt; `platform/qa/run.py` contains no
pathname-level `os.replace` call.

Independent main-workspace verification:

```text
python -m pytest platform/qa/test_run.py -q -p no:cacheprovider --basetemp D:\AppData\Temp\course-studio-phaseb-root-qa-v3
150 passed in 1.26s

python -m pytest platform/helper/tests/test_model_cache.py platform/helper/tests/test_embeddings.py platform/helper/tests/test_embedding_live.py -q -p no:cacheprovider --basetemp D:\AppData\Temp\course-studio-phaseb-root-helper-v3
146 passed, 1 fixture warning in 8.80s
```

Current changed-file SHA-256 values:

```text
47C48AAFB29EEFAF5A343A4DD01349633470BDE00F434876FF270CA07A71C0D7  platform/qa/run.py
2DC9458FAE9A4B4F0F8A8DFAA1D77A754496967C6624839852A4FDCEC16C97B1  platform/qa/test_run.py
5D8D1116D2847BBB014072A9DFED5A6D1138FFE0460005C1FABBA38E634C1813  platform/helper/course_helper/embedding_live.py
4DC20407FF31F584EB6A4B19FDAF2F795776A92F90366509B63F002628650DEE  platform/helper/tests/test_embedding_live.py
```

The exact Phase A wrapper was attempted twice, including once outside the
sandbox. Both attempts failed closed with
`EMBEDDING_MODEL_METADATA_CONNECT_FAILED`. A bounded connectivity comparison
then showed `huggingface.co` timing out while `pypi.org` returned HTTP 200.
No bootstrap candidate, model cache, runtime, or sealed model receipt was
created or replaced, and the live opt-in was removed by the wrapper. Further
retries stop until the external Hugging Face route changes.

### Network route diagnosis addendum (2026-07-18)

The exact wrapper was run again outside the sandbox and reproduced
`EMBEDDING_MODEL_METADATA_CONNECT_FAILED`. The existing user-managed Clash
HTTP proxy at `127.0.0.1:17890` reached the official `https://huggingface.co`
origin with HTTP 200, but the live producer intentionally does not trust
ambient proxies, so that result was not used as model evidence.

A temporary Clash TUN run changed the stable failure to
`EMBEDDING_MODEL_METADATA_DNS_FAILED`. A bounded Python probe showed why:
`socket.getaddrinfo("huggingface.co", 443)` received only `198.18.0.8`, the
Mihomo fake-IP range, which the producer correctly rejects as non-public.
Independent Google and Cloudflare DoH queries through the existing tunnel
agreed on the current CloudFront A records; using one of those records only as
a `curl --resolve` transport test retained official hostname TLS verification
and returned HTTP 200. No downloaded bytes from that probe entered Phase A.

A proposed temporary Windows hosts override could not start because the
current process lacks system-file write permission. The hosts file remained
byte-identical at SHA-256
`95DD901E096E239F2E06B777F086D7B563D1BECAB47568FC0133E8FCCDE2E268`,
and TUN was restored to its original disabled state after every probe.

The remaining prerequisite is external network configuration: run the exact
wrapper with Mihomo DNS returning real public addresses (`redir-host`, or an
equivalent narrowly scoped real-IP rule for the required official hosts).
Accepting `198.18.0.0/15` in platform code would weaken the plan's DNS/IP
fail-closed contract and was therefore rejected. A separate read-only design
review found that adding explicit loopback HTTP CONNECT support is possible
only with a new strict transport/receipt contract and additional offline,
TLS, redirect, subprocess-environment, and malicious-proxy tests; it is not a
small Task 4 execution fix and was not implemented.

### Receipt-state-machine audit and final local verification (2026-07-18)

A bounded P3 audit ran after the initially green suites. The first independent
pass found one HIGH and two MEDIUM issues: a same-inode/same-size mutation
window after receipt validation, non-canonical timestamps in the independent
QA validator, and a zero-write object that overstated its evidence. The fixes
now hold a Windows share-deny receipt handle across commit/finalize, validate
canonical UTC ordering, and bind the receipt only to actual generation-tree
ACL apply/probe/identity/restore evidence. The nested proof explicitly says
`nativeGlobalCoverage: not-certified`; it no longer claims global filesystem
zero-write coverage.

The subsequent `gpt-5.6-sol` ultra audit found two additional Important
recovery-order issues. First, `finalize()` could delete the prior backup before
a final held-handle check that could still fail. Second, prior/backup recovery
trusted identity and size without preserving exact prior bytes. The final
implementation performs every fallible held identity/path/content check before
removing recovery material; after removal only no-throw handle cleanup and
state return remain. It also writes a contained, flushed, exclusive recovery
copy of the original prior bytes and binds restoration to current recovery
identity plus exact content, rebuilding from the detached original bytes when
necessary. Fault tests cover final verification failure, compare mismatch,
parent swap, same-size prior/backup/recovery changes, and native close failure.

The ultra follow-up was stopped by the model service safety classifier, so no
false ultra PASS is claimed. A separate independent transaction re-review of
the final files closed at `0 Critical / 0 Important`, `Ready: Yes`.

Root verification after all fixes:

```text
python -m pytest platform/helper/tests/test_embedding_live.py platform/qa/test_run.py -q -p no:cacheprovider --basetemp <external-unique>
184 passed in 4.82s

python -m pytest platform/helper/tests/test_model_cache.py platform/helper/tests/test_embeddings.py platform/helper/tests/test_embedding_live.py -q -p no:cacheprovider --basetemp <external-unique>
153 passed, 1 deliberate duplicate-wheel warning in 8.85s

python -m pytest platform/helper/tests -q -p no:cacheprovider --basetemp <external-unique>
602 passed, 11 skipped, 57 warnings in 52.98s

python -m compileall -q platform/helper/course_helper platform/helper/tests platform/qa
exit 0
```

The complete offline `python platform/qa/run.py all` attempt passed Web tests
`244/244`, typecheck, build, design QA, and the existing knowledge receipt. Its
aggregate result remains non-green for two environment/milestone reasons: the
root Git repository is intentionally not yet reinitialized, so the
Git-metadata protected-path gate fails by design; and that run's default pytest
temporary root hit the known Win11 file-monitor setup race. The same Python QA
and Helper suites pass with unique external `--basetemp` as recorded above.

Final Task 4 local changed-file SHA-256 values:

```text
5D8614CC2755853EA85C2ED942932FCDE7AE685E5BC4CBA711652C49A213BBA7  platform/helper/course_helper/embedding_live.py
FCA5156BCE1410307D45134E8783D2111585ED9F0CBFE86343F49F3412C713E3  platform/helper/tests/test_embedding_live.py
2F4AA4EE26AF7076516318727264BE372754232FAA5BF96E308BC60D3F61462A  platform/helper/course_helper/model_cache.py
FFE1C75AEF96AFFA16B007ACFF7235619E8AEB30DF444B398F3B1E0F16D88B0F  platform/helper/tests/test_model_cache.py
2F06BA869E83CD71873C4974D74C13F5A6627335928BF5D7C04B1974A79C2F91  platform/qa/run.py
BC3540681E928016761FD6556F84D09E9C3E094C968699C017BCE5A1480A882F  platform/qa/test_run.py
```

The three authority plan hashes were rechecked and remain byte-identical. The
manifest phase is still `bootstrap-required`; the fixed candidate, model
cache, quarantine, and sealed live receipt paths are all absent. Therefore the
local implementation/review slice is accepted, while Task 4 overall remains
pending the real Phase A and Phase B producer gates.

### Final live-producer acceptance (2026-07-18)

Task 4 is complete. The authoritative live producer was run with the Codex
bundled Python 3.12.13:

This final section supersedes every earlier historical `pending`,
`bootstrap-required`, no-network, and absent-receipt statement in this report.

```text
platform/qa/run.py embedding-model-live --receipt platform/helper/evidence/embedding-model-live.json
exit 0
EMBEDDING_MODEL_LIVE_VERIFIED: CPYTHON SOCKET-DENIED VERIFIED
OS NETWORK ISOLATION NOT CERTIFIED
```

The sealed receipt is `status: verified`, has the exact 20-key contract, eight
checks and 30 wheels, and has SHA-256
`560f908c20a5cf29f59f7f8f26b41e694b899d7dbfd6abfe6ed1926ed3ad1138`.
Its single active model generation is
`45acc0ce15d056f5faee9ad133f0716bd0c89bc51cf8304c65b00aaaac873c0c`.
Offline strict receipt validation passed. The final focused suite passed
`322 passed` with one deliberate duplicate-wheel-name warning.

Phase A identity inputs remain bound to candidate
`e68798b46998d306e59174782452ce1eb24c6b0151dd612ecdc8c1dc605ac01e`,
model metadata
`7d8598057a8a9af6828d2ab2e028f96f6397f0efb80b8dd48b9fc4736ae23a52`,
dependency graph
`0a799a3dcf247c4d565b1f3af32ee2ef48dd0b117f5cf049271b9f108f8c95d3`,
and aggregate manifest
`4b57e139b5605e27f22daaa946d5f041717f58c3ad05ca40adb8e2f7cbf529dd`.

Final hardening covered `asyncio.windows_utils.Popen` in the CPython guard,
the exact 20-key receipt contract, non-inheriting per-directory Windows ACL
denial, UTF-8 worker stdin, empty FastEmbed cache enforcement, and strict
`RECORD` traversal handling. The temporary network route was restored to
`mode=rule` and `tun=false`; its temporary configuration and live opt-in are
absent. A historical ACL-impaired generation remains isolated under
`.embedding-model-legacy-acl/`; it is not active and was not force-deleted.

The evidence certifies the producer's CPython socket denial. It deliberately
does not certify OS-level network isolation.

Final milestone verification:

```text
Task 4 focused: 322 passed, 1 deliberate duplicate-wheel warning
Complete Helper: 643 passed, 11 skipped, 57 warnings
compileall: exit 0
```

`python platform/qa/run.py all` passed Python QA (`154`), Helper (`643` in
that gate, with environment-based skips/deselections), Web (`244`), Web
typecheck, Web build, design QA, and the existing knowledge receipt. Its sole
aggregate failure is the protected-path Git query: the repository is
intentionally not reinitialized yet, so `git diff` exits 129. This is an
environment/milestone gate, not a Task 4 product failure.

Post-acceptance cleanup leaves exactly one active model generation and an empty
active quarantine root. The failed historical ACL generation is retained only
in the explicitly named legacy isolation root because deleting it would require
an unnecessary privilege escalation. The canonical receipt digest is
`560f908c20a5cf29f59f7f8f26b41e694b899d7dbfd6abfe6ed1926ed3ad1138`;
the receipt file SHA-256 is
`B34F4249B81257BE163733587AACF199605AD78B6C226D0B9DEA476EE8CCCEF8`.

Final implementation SHA-256 inventory:

```text
2F3579C093A14BBA8812FB2B0F3D1DCEB66B5E1A72BC2C35DD24F6CD2927E2EA  platform/helper/course_helper/model_cache.py
EE0D004A4F6ECCC6E8934D76B70FFDB980C84C77C2F10CC56EFF5B5F6BFC0DEC  platform/helper/course_helper/embeddings.py
55EDD421AE28BEBD8655D77ECA652CB3702391CD8555D62FA8977E28613F9835  platform/helper/course_helper/embedding_live.py
19E1C83345830370D4C2AFAFFE319E0A55C96519F6B254ECA5D531737B22C0C8  platform/helper/tests/test_model_cache.py
AC316E1C3FA98541E6FE4595BFBDE4AC43D268BE6AC480CDC54D253ADB522073  platform/helper/tests/test_embeddings.py
546A9DD0FF73A1B5D3DC96BEC8F8589B5C89CFF347D509B151B60CA56CCB3DC8  platform/helper/tests/test_embedding_live.py
28104AB9FC1BF07708B11D52EA1295753F3CE885AEB4C67AB2B468DAA8472481  platform/qa/run.py
C4939AC144C15088B7C98C5948F6E019B2F82D7013C60AB25B54E582C51BD17A  platform/qa/test_run.py
E491C705FC7980E91B840387AC50F173EDFA0CB2424E298DBD671560A6237DC3  platform/helper/model-manifests/bge-small-zh-v1.5.json
B34F4249B81257BE163733587AACF199605AD78B6C226D0B9DEA476EE8CCCEF8  platform/helper/evidence/embedding-model-live.json
```

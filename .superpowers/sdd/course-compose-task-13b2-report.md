# Course composition Task 13B2 report

Date: 2026-07-19  
Scope: governed import start/status/cancel and durable operation recovery.

## Outcome

The typed job boundary now accepts four strict lower-camel operations:
`knowledge_import_start`, `knowledge_import_status`,
`knowledge_import_cancel`, and `operation_status`. Browser requests contain
only opaque IDs, exact digests, and actor metadata. The authenticated API
derives the internal session owner itself; browser-provided session IDs and
paths are forbidden by schema.

Import start commits its lease through the client operation before performing
a deterministic internal promotion operation. Normal retry replays both
operations without duplicate source/blob rows. Status authenticates the
original actor/session and revalidates the upload/lease/blob envelopes and
actual bytes. Cancel commits through the operation ledger, recovers response
loss, and completes short-lived input cleanup on replay.

The bounded parent runner now checks the durable client operation after child
disconnect, timeout, non-zero exit, empty response, and parent-side failure.
Only an authenticated exact-digest `committed` outcome replaces the apparent
failure. Tests delay the worker response after the real allowlisted handler has
committed and prove both disconnect and timeout return recovered success. An
unknown or corrupted outcome never becomes success.

## P3 review corrections

- The router correctly raised this isolated phase from the initial P2 estimate
  to P3/xhigh because process termination crosses commit visibility.
- Promotion replay now verifies the committed outcome and promoted blob, then
  removes a short-lived upload left behind by response loss.
- Ledgered cancel recovery first observes an existing commit and cannot create
  a cancellation while merely checking recovery.
- Content-address shard directories explicitly reject symlink/reparse
  boundaries; blob verification now requires a regular single-link file.
- All new nested evidence keys are lower camel and session/token material is
  never serialized.

No Ultra review was needed: deterministic delayed-response processes, SQLite
authority checks, corrupt-envelope probes, and real spawned-worker tests make
the relevant races observable.

## Verification

- RED upload-store contract: missing cancel digest/status APIs failed test collection.
- RED job contract: missing import job types failed test collection.
- Focused import/upload gate: 27 passed, 1 environment-permission symlink skip.
- Final phase gate: `python -m pytest platform/helper/tests/test_knowledge_review_jobs.py platform/helper/tests/test_uploads.py platform/helper/tests/test_operations.py platform/helper/tests/test_api.py platform/helper/tests/test_server.py -q` passed 97 tests with one permitted directory-symlink creation skip.
- `python -m py_compile platform/helper/course_helper/jobs.py platform/helper/course_helper/api.py platform/helper/course_helper/uploads.py`: passed.

## Exact changed-file evidence

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/uploads.py` | 39896 | `5580B3BFD8AACD788AA6067D9B66EC28EA4946E851EB3425E234E7E0C1218DA2` |
| `platform/helper/course_helper/jobs.py` | 50611 | `8A374237DBDB007C8F3CA5EDE608FE86AEC219A9B39742F91B150E79E92C6C25` |
| `platform/helper/course_helper/api.py` | 17654 | `58EE0BFAF6235C88CAF177BEA58952CABD83F4BA8C9BD55E36C7EA96804CAE66` |
| `platform/helper/tests/test_uploads.py` | 23745 | `34C29F011A3F2C18877810AE868A0E27B54BE43BFCF0A7531EBC6D741467A32B` |
| `platform/helper/tests/test_knowledge_review_jobs.py` | 15635 | `FDD1A3BDBF95494D3279E7B050567BE753C882CA8496987210949EAF7CC09016` |
| `platform/helper/tests/test_api.py` | 75213 | `EF1EFAA1BBCD2ADBF95C87238E5328291CA214B8939A6E2205C8F0E469386174` |

Pytest created only reproducible managed temporary files; none were retained in
the project. Source, tests, and this acceptance report are durable. The
superseded initial P2 Supergrill checkpoint is retained as route-correction
audit evidence; the active checkpoint is P3. No protected reference root was
read. No card-review pipeline, browser publication, network, signing, physical
dual-screen, hardware, OS isolation, or Git certification is claimed.


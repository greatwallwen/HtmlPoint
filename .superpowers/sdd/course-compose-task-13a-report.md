# Course composition Task 13A report

Date: 2026-07-19  
Scope: current execution-plan Task 4, governed upload persistence and opaque
source inventory only. The later job/API/review slice is deliberately not
included.

## Outcome

Task 13A is complete in the main workspace. Migration 0007 adds durable upload,
import-lease, and immutable governed-source-blob tables. `UploadStore` streams
allowlisted files through an fsynced temporary file, enforces the exact 20 MiB
ceiling, stores only a session digest, acquires a durable lease before a start
outcome commits, promotes verified bytes into a content-addressed blob, and
recovers committed promotion replay after the short-lived upload was removed.

Expiry and cancellation select only unleased uploads. Two SQLite connections
prove that concurrent import starts produce one lease and that an expiry/start
race either preserves a live leased input or expires an unleased input. A late
catalog failure rolls back source, lease, upload-state, and operation rows while
leaving no temporary file. Separate uploads with identical bytes reuse one
source version and one immutable blob.

`list_source_inventory` provides capped stable pagination through opaque
cursors and returns only Helper-issued source IDs plus safe name, kind, MIME,
size, digest, and status. It rejects malformed cursors and fails closed on
non-canonical or digest-invalid stored envelopes. Tests assert that projections
contain no local path, locator, source body, URL, protected-root name, raw
session identifier, or source bytes.

## Verification

- `python -m py_compile platform/helper/course_helper/uploads.py platform/helper/course_helper/source_inventory.py`: passed.
- `python -m pytest platform/helper/tests/test_uploads.py platform/helper/tests/test_source_inventory.py -q`: 19 passed.
- `python -m pytest platform/helper/tests/test_uploads.py platform/helper/tests/test_source_inventory.py platform/helper/tests/test_catalog.py platform/helper/tests/test_composition_storage.py -q`: 115 passed before the final two concurrency cases were added; the final focused suite is covered by the 19-test run above.
- `python -m pytest platform/helper/tests/test_embeddings.py::test_migration_four_creates_append_only_embedding_snapshot_tables platform/helper/tests/test_lifecycle.py::test_v1_migration_backfills_projection_without_rewriting_immutable_card platform/helper/tests/test_uploads.py platform/helper/tests/test_source_inventory.py -q`: 21 passed.
- `python -m compileall -q platform/helper/course_helper`: passed.
- `python -m pytest platform/helper/tests -q`: 731 passed, 12 skipped. The 12 skips are existing environment/permission gates; warnings are existing OpenPyXL deprecations and deliberate duplicate-archive fixtures.

The first complete Helper run found two stale migration-version assertions
(`1..6`). Both were updated to the current exact `1..7` sequence; the complete
suite was rerun and passed.

## Exact file inventory

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/catalog.py` | 109889 | `9EF9A8CCC599ABB7A2B8329C837806D0119710BE7482FEC171B0E760C377165A` |
| `platform/helper/course_helper/migrations/0007_import_sources.sql` | 2901 | `F88D7610DD464A840E9706CBD8391C1494796984431051562E32D69A4CB76848` |
| `platform/helper/course_helper/uploads.py` | 32488 | `68D01F0E552B7CF5ABB4188DDBA4EB1A77F43EC5CF0573889414A0746020BE2A` |
| `platform/helper/course_helper/source_inventory.py` | 5748 | `B0EF9F2F77C8CEF2B06E7021E8951C3CC94EBB44B5AC742363CC2030F88135F0` |
| `platform/helper/tests/test_uploads.py` | 16155 | `64AD2CEE7472B7B8FA844C9A3FEFCFA1CB940CB7366A1C0378F785A4AC495778` |
| `platform/helper/tests/test_source_inventory.py` | 5417 | `1C741FF91C994687F65B6599A751B3972C8371C398E1B12A2CEA40FF53AFF7EF` |
| `platform/helper/tests/test_catalog.py` | 61526 | `2EAD399BD8A0AEB799B71A1F117056348475966720E1AFE624DC18B2312626DE` |
| `platform/helper/tests/test_composition_storage.py` | 43545 | `2BED73D57C5DAEDEE3AC7931344DEFA876C6018E20BFD9B6189C66BA8FC38BF6` |
| `platform/helper/tests/test_api.py` | 62401 | `1217B1266DF3A05540EE98AFBDA0C34754021729899D4CBE06E0B5A02440C6B1` |
| `platform/helper/tests/test_embeddings.py` | 38196 | `ED92EBC10AFEC9A239E77E0A18973F1C3DA1419DB268A9E1FF30751610B3D90A` |
| `platform/helper/tests/test_lifecycle.py` | 23026 | `61F0AD88D450D44080FC7A0DAA5C1F8E15AB0FB394EEB81631EA87F7B1835815` |

No protected reference root was read. No browser, network, signing, physical
dual-screen, hardware, OS isolation, or Git certification is claimed.

## Adaptive routing receipt

- Unit: governed upload/inventory persistence.
- Recommended route: P2, `gpt-5.6-sol` at `high`.
- Execution status: `recommended_only`; the root task has no callable in-place
  model switch.
- First full-gate result: two stale expected-version assertions, attributed to
  shared migration input drift rather than reasoning failure.
- Rework: one bounded assertion update; focused and complete gates then passed.
- Next unit recommendation: P1, `gpt-5.6-terra` at `medium`, for ordinary API
  schema and CRUD wiring. Promote only the bounded disconnect/operation-recovery
  review to P2/high.


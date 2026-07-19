# Course composition Task 13B6 report

Date: 2026-07-19  
Scope: governed CSV, Parquet, XLS, and XLSX import profiling.

## Outcome

PASS. Promoted tabular blobs now use the same final client-operation boundary
as content imports. The verified content-addressed blob is copied into a
contained temporary root, profiled with the existing bounded DatasetProfiler,
and rebound to the governed source locator and identity before persistence.

The immutable dataset profile contains at most 20 redacted sample rows. Its
profile evidence is rebound to the governed dataset version; no absolute path
or temporary `governed-import` locator enters browser results. Every dataset
creates a blocking `dataset-reference` review. Non-ready grain and detected
sensitive columns add their existing blocking review kinds.

The final browser operation commits the dataset, evidence, and reviews
atomically. Replay returns the exact stored dataset and review IDs without
profiling again. The generic `datasetVersionIds` result is empty for Markdown/
PPTX imports and populated for tabular imports; `candidateCardVersionIds` is
empty for dataset-only imports.

## Verification

- RED: governed CSV import failed at the content-only parser boundary.
- Governed CSV integration and no-reprofile replay: passed.
- Dataset profiler, import, upload, operation, API, and server gate:
  `131 passed, 4 skipped in 34.70s`.
- `python -m py_compile` for pipeline, jobs, and tests: exit 0.

## Exact changed-file evidence

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/import_pipeline.py` | 18438 | `E1D35B7958F0262E4E34ED7D317D70D6700ABE0DB05AE3505A04EF2E3D6D403E` |
| `platform/helper/course_helper/jobs.py` | 74540 | `FB5FB76094F149C6A5393E4D0B73E0696D35A157E1C15CBD9D64D404073D0D4B` |
| `platform/helper/tests/test_knowledge_review_jobs.py` | 47400 | `453D2699DFDFF4ED071F34FFA7D9F58F2EBF510ECD240AC40934E106820CD032` |

Three resolved Task13B6 pytest roots totaling about 2.76 GB were removed; none
remain. No protected reference root was read. The router's P1/medium choice was
sufficient and produced no rework-worthy architecture or safety finding.

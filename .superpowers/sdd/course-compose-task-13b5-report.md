# Course composition Task 13B5 report

Date: 2026-07-19  
Scope: governed Markdown/PPTX import pipeline from promoted source blob to review candidates.

## Outcome

PASS. `knowledge_import_start` now commits the browser's operation only after
the promoted source has been deterministically parsed, rebound to the governed
source version, persisted as chunks/visual metadata, converted to review-state
knowledge-card candidates, and assigned blocking provenance and duplicate
governance tasks.

Lease acquisition and content-addressed promotion use deterministic internal
operation IDs. The caller's `operationId` is reserved for the final import
outcome, so disconnect/timeout recovery cannot mistake a merely promoted source
for a completed import. A committed replay returns the exact stored outcome and
does not reparse the source.

Markdown and PPTX bytes are read only from the verified single-link governed
blob. Parsing uses a contained short-lived copy with the safe original suffix;
the directory is removed on success and failure. Chunk IDs, visual IDs, visual
parents, citations, extraction evidence, and card parents are rebound to the
promoted source version. Temporary roots and absolute paths are excluded from
durable and browser projections.

Candidates receive generic controlled-vocabulary tags, source citations, a
blocking provenance review, exact-duplicate review when applicable, and the
existing near-duplicate scan. A missing semantic provider remains an explicit
blocking degraded review, never a silent pass.

## Verification

- RED: four import tests failed because final operation refs had no chunks, candidates, or reviews and invalid Markdown was never parsed.
- Focused import/job gate: `20 passed`.
- Markdown, PPTX, cards, near-duplicates, uploads, operations, API and server regression: `199 passed, 4 skipped in 38.36s`.
- `python -m py_compile` for the pipeline, jobs, and tests: exit 0.

## Exact changed-file evidence

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/import_pipeline.py` | 12018 | `89DD298B9618F3C91E81A666D92D6AAD8EC4DD6F6639D0A270184623B9F4BECD` |
| `platform/helper/course_helper/jobs.py` | 73048 | `9AD10E1225429ED50F521081DDC336CC2697F1EA22A1C324C5481F811E8525FE` |
| `platform/helper/tests/test_knowledge_review_jobs.py` | 45494 | `B2F296B912FE81C88DDFFF019C576478DF9F290C413D0DA08499B9EF7D3228E6` |

## Artifact lifecycle and boundaries

Six resolved `D:\AppData\Temp\course-studio-task13b5-*` roots contained about
2.84 GB of reproducible pytest files. All six were removed and none remain.
No protected reference root was read. Dataset imports remain a separate typed
workflow and are the next Task 13 phase. No network, signing, physical
dual-screen, hardware, OS-wide isolation, or Git certification is claimed.

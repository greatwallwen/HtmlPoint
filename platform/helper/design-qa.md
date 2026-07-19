# Helper Knowledge Demo Design QA

## Scope

This review covers the Helper knowledge-demo panel, its four-step workflow,
the read-only reference contract, the evidence summary, and the repository QA
entry points. The review does not claim certification for hardware that was not
available in this environment.

## Interface review

- Light panel and hierarchy: passed. The panel keeps the course workspace
  visually quiet and separates source registration, knowledge processing,
  retrieval, and evidence without adding stage-level instructions.
- Four-step workflow: passed. Register sources, process knowledge, retrieve
  known phrases, and inspect evidence remain distinct and understandable.
- Read-only whitelist: passed. The demo is limited to the five registered source
  locators, while the two inventory roots use metadata-only integrity checks.
- Evidence summary: passed. The receipt exposes source hashes, inventory
  digests, parser versions, object digests, retrieval evidence, idempotence, and
  forbidden-write results without exposing the absolute reference root.
- Gate integration: passed. `python platform/qa/run.py focused` validates the
  tracked receipt and this report; `python platform/qa/run.py knowledge-demo`
  runs the explicit reference-backed gate; `python platform/qa/run.py all`
  remains truthful when `COURSE_REFERENCE_ROOT` is unset.

## Receipt reviewed

- Five allowlisted sources were hash-verified before and after processing.
- Two metadata-only inventory roots contained 358 items with unchanged
  before/after digests.
- Twelve knowledge cards were published. The first pass created five source
  versions, 24 card versions, and 32 evidence objects; the second pass created
  none.
- Duplicate cards and forbidden source writes were both zero.
- Retrieval evidence is degraded because the deterministic lexical fallback is
  active; this is surfaced rather than presented as verified embedding output.

physical dual-screen: NOT CERTIFIED

final result: passed

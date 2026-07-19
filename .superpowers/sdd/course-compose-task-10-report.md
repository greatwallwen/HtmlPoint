# Course composition Task 10 — evidence-backed dataset charts

Date: 2026-07-18  
Workspace: `D:\cursor\AI培训`  
Protocol: main-workspace Gitless execution

## Result

Task 10 is complete. Exact registered CSV and XLSX dataset relations now
produce deterministic, accessible `bar`, `line`, and `scatter` SVG artifacts
through typed allowlisted query plans. Every chart pins dataset, schema,
columns, spec, query, result, visual, artifact, and evidence digests. No API or
evidence contains raw SQL, raw result rows, local paths, or external URLs.

## Implemented

- Added `chart_builder.py` with strict `ChartSpec`, path-free materialization
  and independent per-item outcomes. Arbitrary SQL, expressions, filters,
  screenshot URLs, unknown chart types/aggregates, markup, scripts, and
  external references are outside or rejected by the schema.
- Added exact dataset-profile authority validation: canonical catalog envelope,
  deterministic dataset/evidence identities, official producer, source locator,
  row/column counts, content digest, worksheet identity, schema digest, and
  both column digests must agree.
- Added verified DuckDB relations for CSV and XLSX. XLSX worksheets are now
  first-class relation identities; their package members, encryption,
  duplicates, paths, per-member size, and total expansion are bounded before
  openpyxl reads them. Actual worksheet types and sensitive values are checked
  before a typed temporary relation is created.
- Enforced source-byte, source-row, query-time, result-row, finite-number, and
  result-value ceilings. The exact source digest is checked before relation
  creation and again after query execution.
- Added a generated-SVG-only artifact seam. It accepts canonical UTF-8 XML with
  a small inert element/attribute allowlist, required accessible title and
  description, fixed dimensions, no events, style, foreign elements, links,
  data URLs, scripts, or external references. Ordinary source/network artifact
  writes still reject SVG.
- Registered the verified artifact, data-derived visual, evidence, and two
  lineage edges atomically. Replays with a different operation/request ID reuse
  identical SVG bytes, artifact metadata, visual identity, and evidence.
- Extended migration 0005's artifact media constraint only for validated
  generated SVG; PPTX source SVG remains rejected by its separate store seam.

## Adversarial coverage

- CSV bar, XLSX line, and numeric CSV scatter fixtures.
- Sensitive dataset refusal, dataset/schema/column drift, changed source bytes,
  forged XLSX type metadata, unpinned worksheet relation, and non-numeric axes.
- Source row/byte, query time, result row/value, and non-finite ceilings.
- Mixed valid/invalid chart outcomes without sibling rollback.
- SQL, unknown fields, screenshot URLs, markup/script/external references, and
  invalid chart/aggregate combinations.
- Active/foreign/external SVG content and ordinary source SVG rejection.
- Same semantic chart under a new request ID reuses its exact visual/evidence.

## Adaptive model router evaluation

The broad work unit routed to P3/xhigh because typed query construction and SVG
storage cross a security boundary. Splitting the deterministic implementation
to P2/high and reserving P3 depth for a bounded review was effective. The review
found XLSX type-to-SQL risk, incomplete whole-operation timeout accounting,
request-ID pollution of semantic identity, worksheet identity ambiguity, and
package expansion gaps before the full suite. All were corrected without an
Ultra pass. This reinforces that the router's boolean security signal is useful
for review selection but too coarse for an entire implementation phase. The
actual in-place model switch remains unavailable, so receipts truthfully say
`recommended_only`.

## Verification

```text
python -m pytest platform/helper/tests/test_chart_builder.py platform/helper/tests/test_dataset_profiler.py platform/helper/tests/test_artifacts.py -q
38 passed, 4 skipped, 12 warnings in 2.73s

python -m pytest platform/helper/tests -q
680 passed, 12 skipped, 70 warnings in 56.25s

python -m compileall -q platform/helper/course_helper/chart_builder.py platform/helper/course_helper/artifacts.py platform/helper/course_helper/parsers/dataset_profiler.py
exit 0

python platform/qa/run.py all
Python QA 154 passed; Helper 680 passed/5 skipped/7 deselected; Web 244 passed;
typecheck passed; build passed; design/evidence gates passed.
Aggregate exit 1 only because the protected-path query requires root Git
metadata, which is intentionally absent under the current Gitless protocol.

python -m ruff check ...
not executed: the active Python environment has no Ruff module.
```

## SHA-256

```text
90341CBCAB09F0A94AF4C155497AF6B2DF21D69BF320B3A70974C9F2623C192A  platform/helper/course_helper/chart_builder.py
7F98EEB4A70CD90A42CB84645CCD4D3A7B027D6E1B156DCAF04F87847FBA0F4D  platform/helper/course_helper/artifacts.py
0FA900D99C1B20E8C694DFE20576582F1CD26C42B5BD319BD70C3B0349A97694  platform/helper/course_helper/parsers/dataset_profiler.py
C488F93EB5FD5E4A7C3E57BA1FE2942369A9B4487550CF2F34B7A87B60DF62ED  platform/helper/course_helper/migrations/0005_artifact_metadata.sql
7AC5A53264584E4CABB136CF43603B97F8FB040189D9C5A88F9CF68B32905313  platform/helper/tests/test_chart_builder.py
CE738F48226AE669928A91B602E1D3A774384309F77EE8ADE601531546DA3E6C  platform/helper/tests/test_artifacts.py
4D35D466856832358BA46694169FE9213048682927D9A1220A1D69D97A26926E  platform/helper/tests/test_dataset_profiler.py
```

## Explicit limits

- Query timeout is enforced for bounded relation preparation/query acceptance
  and DuckDB execution interruption; filesystem hashing and library teardown
  cannot be certified as OS-hard real-time cancellation.
- Generated SVG is stored but not yet exposed to a browser or attached to a
  published course; those remain Tasks 12 and 14.
- No Task 11 live network visual, course publication, browser, signing, network
  isolation, physical dual-screen, hardware, or Git certification is claimed.

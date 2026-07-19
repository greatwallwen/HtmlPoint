# Course composition Task 9 — traced source visuals

Date: 2026-07-18  
Workspace: `D:\cursor\AI培训`  
Protocol: main-workspace Gitless execution

## Result

Task 9 is complete. Exact PPTX image relationships can now be materialized as
immutable, content-addressed artifacts with path-free metadata, exact source
and parser lineage, independent per-asset outcomes, and fail-closed evidence.
The source file remains read-only. This task does not attach visuals to a
course or make a publication claim.

## Implemented

- Added migration 0005 with immutable artifact metadata and source-visual
  bindings. Foreign keys bind artifact, source, visual, relationship, and
  evidence identities; update/delete triggers prevent silent history rewrite.
- Added a caller-rooted artifact store with bounded streaming, content-addressed
  object names, atomic no-overwrite installation, containment and reparse-point
  checks, exact duplicate reuse, MIME sniffing, Pillow verification, dimension
  limits, and digest verification when reopening an object.
- Added path-free `ArtifactMetadata`, `SourceVisualMaterialization`, and
  independent success/failure outcomes. Responses and evidence never contain a
  local artifact or source path.
- Added direct bounded PPTX ZIP/XML relationship resolution. It rejects archive
  traversal, external targets, duplicate or ambiguous members, encrypted
  members, missing relations, oversized XML/media, unsupported SVG, corrupt
  images, MIME/dimension/digest mismatch, and source changes during the read.
- Recomputed and verified the official parser's visual identity, exact slide
  relationship, source/chunk parents, and media digest before registering
  catalog authority. Evidence binds the exact source, visual, artifact, slide,
  and relationship IDs.
- Kept retrieval index contract version 4 independent from catalog storage
  migration 5. The health endpoint correctly reports catalog schema version 5.
- Concurrent/replayed materialization reuses the same verified artifact while
  retaining a distinct immutable visual binding for each exact relationship.

## Adversarial coverage

- Bounded writes, mid-stream oversize/failure, no temporary partial object.
- Corrupt image, unsupported SVG, MIME mismatch, invalid timezone/size hint.
- Symlink/reparse containment plus a deterministic Windows reparse simulation.
- Changed source digest, forged visual identity, missing relationship, duplicate
  archive member, and exact parser-parent mismatch.
- Two visual relationships with identical image bytes reuse one artifact and
  preserve two source-visual bindings, including cross-store/cross-clock replay.
- A malformed sibling fails independently and does not roll back a valid one.
- Catalog foreign keys, immutable rows, exact evidence, and lineage edges.

## Adaptive model router evaluation

The router initially classified media parsing and content-addressed storage as
P3/xhigh because they cross a security boundary. Splitting the work kept the
deterministic construction at P2/high and used only a bounded P3 review. That
review found the real catalog-migration/retrieval-schema coupling, which is now
fixed. Ultra was not needed. The router still over-escalates when a broad
security flag covers routine deterministic code, so phase splitting is useful.
The root task has no callable in-place model switch; receipts therefore remain
`recommended_only` and do not claim that a recommended model actually ran.

## Verification

```text
python -m py_compile platform/helper/course_helper/artifacts.py platform/helper/course_helper/source_visuals.py platform/helper/course_helper/catalog.py platform/helper/course_helper/parsers/pptx_parser.py platform/helper/course_helper/retrieval.py
exit 0

python -m pytest platform/helper/tests/test_retrieval.py platform/helper/tests/test_api.py platform/helper/tests/test_artifacts.py platform/helper/tests/test_source_visuals.py platform/helper/tests/test_pptx_parser.py platform/helper/tests/test_catalog.py platform/helper/tests/test_composition_storage.py -q
212 passed, 2 skipped, 1 deliberate duplicate-member warning in 27.58s

python -m pytest platform/helper/tests -q
670 passed, 12 skipped, 58 warnings in 54.51s

python platform/qa/run.py all
Python QA 154 passed; Helper 670 passed/5 skipped/7 deselected; Web 244 passed;
typecheck passed; build passed; design/evidence gates passed.
Aggregate exit 1 only because the protected-path query requires root Git
metadata, which is intentionally absent under the current Gitless protocol.
```

## SHA-256

```text
9AF2809B8B171FFD705E63886E122A0A4F8AC62FA0E91288B0C5CDDB9C38992D  platform/helper/course_helper/migrations/0005_artifact_metadata.sql
4EEFB876D3343FD501D8456B92A0C7ECCFC9B4B71A722335A98454C48E47ABEE  platform/helper/course_helper/artifacts.py
A1B673B4DF8A5FE91274B25CB4C34B5A74D46282A04A484BA6ECCB62F52D4512  platform/helper/course_helper/source_visuals.py
02876249CCBC608DA33E0A8239F13252CE9CBF6B491BE0466B73E21C68F467F8  platform/helper/course_helper/catalog.py
84886E59D4D27C8719421F89C76948FB331D114662A6499668AA5EB0CFF97EC1  platform/helper/course_helper/parsers/pptx_parser.py
C74D25329E63A031043FDCEF6CCCC6EDBCD481C5C8E6471327467B38C773DCF7  platform/helper/course_helper/retrieval.py
2F6EECE5910D80FCF61D6B6CB3BB9110671879491EAA4C6B1DF564603252DFB2  platform/helper/tests/test_artifacts.py
B0A238CBC1260DB458F63944B38CA1A47CDF3E865FAACB117066EA560B9622EA  platform/helper/tests/test_source_visuals.py
6A4AEEB9555C537C0A6619D3617A9E6B0EB08EEB90375243BDEA67E706782636  platform/helper/tests/test_pptx_parser.py
```

## Explicit limits

- Static containment and reparse protection are verified; Windows-native
  adversarial race isolation is not certified.
- A higher-level identity rejection after valid byte storage may leave a safe,
  unregistered content-addressed object for later lifecycle cleanup/reuse; it
  creates no catalog row or success evidence.
- No Task 10 generated chart, Task 11 network acquisition, Task 12 visual
  attachment/course publication, live browser, signing, network, physical
  dual-screen, hardware, or Git certification is claimed.

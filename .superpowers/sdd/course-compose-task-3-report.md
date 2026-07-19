# Course Composition Task 3 Report

Date: 2026-07-17  
Scope: `docs/superpowers/plans/2026-07-16-course-composition-and-authentic-visuals.md`, Task 3  
Execution protocol: Gitless execution; no Git command or Git metadata initialization was used.

## Result

PASS — immutable course/review storage, durable operation outcomes, and the
transactional knowledge-index outbox are implemented and locally accepted.
Task 4 was not started.

All work stayed inside
`D:\cursor\AI培训\.worktrees\course-studio`. Neither `Course_AIProduct/` nor
`references/` was accessed. No embedding or visual-provenance table was added.
The plan's Git commands were intentionally omitted under the Gitless execution
amendment; verification is sealed below with focused/full tests and SHA-256
inventory instead.

## TDD and adversarial evidence

RED tests were written before each implementation slice and proved the intended
failure modes for:

- immutable requirements, outlines, confirmations, course/deck/runtime versions,
  ordered card and visual placements, replayed bytes/time, identity conflicts,
  and concurrent confirmation winners;
- exact migration of every schema-v1 review kind plus preservation of legacy
  review and visual URL payload bytes/digests;
- append-only resolution, upgrade, feedback, evidence joins, and rebuildable
  review projections;
- per-item SAVEPOINT isolation, response-loss recovery, authenticated operation
  lookup, truthful pre-commit unknown state, and transactional index-outbox rows;
- denormalized-envelope attacks against resolutions, suggestions, upgrade source
  versions, composition getters, confirmations, deck placements, and operation
  outbox IDs.

Review-driven RED cycles additionally exposed and closed scope escalation,
published/archived course-registration bypass, raw missing/naive timestamps,
duplicate outbox IDs, resolution/projection divergence, suggestion column
swaps, forged upgrade lineage columns, confirmation digest aliases, and raw
placements outside an immutable course version.

## Implementation

- `0003_course_composition.sql` adds immutable composition versions and placements,
  outline confirmation, review resolution/suggestion/evidence storage,
  rebuildable review projection, durable operation/item outcomes, and the
  transactional knowledge-index outbox. Append-only and envelope triggers fail
  closed on raw-column/payload divergence.
- `catalog.py` registers or reuses exact immutable bytes with injected storage
  clocks, rejects identity/envelope divergence, validates confirmed requirement
  scope and ordered placement lineage, preserves one concurrent confirmation
  winner, and requires exact course/deck/runtime/visual bindings.
- `reviews.py` provides append-only digest-bound resolution, typed upgrade and
  course-feedback suggestions, exact evidence-link replay, canonical polymorphic
  upgrade lineage, and transactional fail-closed projection rebuild.
- `operations.py` binds `operationId + requestDigest` to actor/session ownership,
  commits domain/result/outbox rows atomically, isolates expected item failures
  with SAVEPOINTs, returns truthful recovery states, and rejects corrupted parent,
  child, outbox, timestamp, ordinal, or duplicate-ID facts.
- Compatibility assertions were minimally updated for schema version 3 and the
  new requirement that review subjects already exist. No later-task production
  feature was pulled forward.

## Verification

Task 3 focused gate:

```text
python -m pytest platform/helper/tests/test_reviews.py platform/helper/tests/test_composition_storage.py platform/helper/tests/test_operations.py platform/helper/tests/test_catalog.py -q
128 passed in 8.11s
```

Helper non-reference regression:

```text
python -m pytest platform/helper/tests -m "not reference_demo" -q
403 passed, 4 skipped, 7 deselected, 56 warnings in 56.12s
```

The warnings are existing `openpyxl` deprecations for `datetime.utcnow()`.

Additional verification:

```text
python -m compileall -q platform/helper/course_helper platform/helper/tests
exit 0
```

Independent final review: `0 Critical / 0 Important / 0 Minor`. The reviewer
re-ran the 128-test focused gate, a 10-test adversarial set, the 403-test Helper
gate, and compileall.

## Final SHA-256 inventory

```text
BE0EB7449EFC8F2742DC6202EFEE9887D57CFB738F2653635199A70AF36588D9  platform/helper/course_helper/migrations/0003_course_composition.sql
3D59D280DDA3D5A4D2F57E2FF880039612F70984207B4724A491E6456AB4B671  platform/helper/course_helper/operations.py
263EFB7742BF919B05A8269A7A94C02B95BF91E3062C00E24E7F325934697A8E  platform/helper/course_helper/reviews.py
FA2D17E983230974B5E81F6065E6F01E88C8FBF9D509385B3B0FA7A7DC93643E  platform/helper/course_helper/catalog.py
FF22E32EF6A9186FF116A8A67ACD98DC6D8B3237CE417C91A3173CE2EFD2FB5D  platform/helper/tests/test_operations.py
9672A695280CACDA0B2991FAED15C6EF5619BF8847935B8B3377A4125EEE8AD4  platform/helper/tests/test_composition_storage.py
556B1C586D914C0A331A2B5E00D8F7D6E35692B7F3FD59F852002156C4DF568C  platform/helper/tests/test_reviews.py
CB37345AAEFCB5DCE8A569594304EBC3C7D7C92CB41591878AA3B3751A1ABC35  platform/helper/tests/test_catalog.py
0D713FA944349377E261E223F9AE02512AC1623098C1CA92960B8D708BE3060E  platform/helper/tests/test_api.py
479E40A1CFA0D6713582CA0DC600611247C3E058F85D99DE93F4BC19454A242C  platform/helper/tests/test_lifecycle.py
89079D7EF73760FD0455A449B07975F96918E5CA71327683E4EDA8F1E98B4DA7  platform/helper/tests/test_retrieval.py
567933E4BCDC5DBAEB78FF011127BC9AC88469A85F7C55CC4071DAD4B506E374  platform/helper/tests/test_cards.py
```

This report is excluded from its own embedded inventory because embedding its
final digest would change that digest. Its sealed SHA-256 is reported separately
to the coordinating agent.

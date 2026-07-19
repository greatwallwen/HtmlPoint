# Course Composition Task 2 Report

Date: 2026-07-17  
Scope: `docs/superpowers/plans/2026-07-16-course-composition-and-authentic-visuals.md`, Task 2  
Execution protocol: Gitless execution; no Git command or Git metadata initialization was used.

## Result

PASS — card lifecycle truth is append-only, immutable card bytes remain unchanged,
current status/suspension and FTS are rebuildable projections, and all locally
authorized Task 2 and Helper non-reference checks are green.

The work remained inside
`D:\cursor\AI培训\.worktrees\course-studio`. Neither `Course_AIProduct/` nor
`references/` was accessed. Reference, network-visual, and model-download live
gates were not run.

## TDD evidence

The initial lifecycle test was written before the migration/module existed.

```text
python -m pytest platform/helper/tests/test_lifecycle.py -q
1 failed, 3 skipped
```

The first four-file focused regression after the initial implementation exposed
49 legacy assertions that still expected in-place `cards.status` and
`payload_json.status` rewrites, migration version 1, or the retired direct FTS
write hook.

```text
138 passed, 49 failed
```

Those tests were updated to assert immutable raw bytes plus
`card_lifecycle_current` truth. The first focused GREEN was 187 passed.

Review-driven RED cycles then proved the following previously uncovered gaps:

- a suspended published revision could be skipped during supersede and later
  reinstated beside a newer published revision (`2 failed` before the query fix);
- suspended review and published replay paths did not fail closed;
- lifecycle events could be updated/deleted and card identity columns could be
  rewritten;
- rebuild trusted declared after-state and silently omitted cards without an
  initial event;
- registration accepted invalid requests or silently treated a different event
  as replay;
- a malformed partial v1 migration was misdetected as an available lifecycle
  schema;
- API and Demo counts read legacy raw status instead of lifecycle projections.

The consolidated review regression command first reported 9 failures and 7
passes. Two of the failures were invalid test fixtures missing the existing
published-card citation requirement; the fixtures were corrected to use legal
source-backed or review-to-publish setup. The completed fix set then passed all
16 targeted review regressions.

## Implementation

- `0002_card_lifecycle.sql` creates append-only lifecycle events, a rebuildable
  current projection, transactional v1 backfill, supporting indexes, event
  update/delete guards, and a complete card-row update guard.
- `lifecycle.py` provides validated registration, idempotent event append,
  transition enforcement, per-card contiguous sequencing, projection/FTS
  rebuild, suspension-aware reopen warnings, and migration-version plus column
  contract detection for read-only recovery.
- Rebuild validates every initial event against immutable raw card status,
  recalculates every later after-state from event type, rejects later
  register/backfill events, rejects missing cards and missing initial chains,
  and only then replaces projections and FTS.
- `catalog.py` and `cards.py` project effective status in memory, never rewrite
  raw card payload/status, block suspended publication/replay, and supersede a
  suspended published predecessor when a legitimate new revision is published.
- `retrieval.py` filters and returns lifecycle-projected, unsuspended published
  cards.
- The authorized compatibility extension updates `api.py` and `demo.py` status
  counts to lifecycle truth. Their focused tests were added to `test_api.py` and
  `test_demo.py`; no reference fixture or live reference path was opened.
- Migration execution uses explicit complete-statement execution inside one
  `BEGIN IMMEDIATE` transaction, so a failed migration rolls back and the v1
  catalog remains available through explicit read-only recovery.

## Verification

Task 2 focused gate:

```text
python -m pytest platform/helper/tests/test_lifecycle.py platform/helper/tests/test_catalog.py platform/helper/tests/test_cards.py platform/helper/tests/test_retrieval.py -q
196 passed in 10.19s
```

Helper non-reference regression:

```text
python -m pytest platform/helper/tests -m "not reference_demo and not network_visual and not model_download" -q
351 passed, 4 skipped, 7 deselected, 56 warnings in 47.95s
```

The warnings are existing `openpyxl` deprecations for `datetime.utcnow()`.

Additional verification:

```text
python -m compileall -q platform/helper/course_helper platform/helper/tests
exit 0
```

Independent final re-review: `0 Critical / 0 Important / 0 Minor`. Its joint
lifecycle/catalog/cards/retrieval/API/Demo regression reported `254 passed, 3
skipped` in 42.24 seconds.

## Final SHA-256 inventory

```text
F80A793E1E13E0D272D9A6DF884B9BC5180523C5469FE4B46B406F0D8539E18C  platform/helper/course_helper/migrations/0002_card_lifecycle.sql
5F18B801EEEDA6D70D8C1BB3B6B9926D972715DB710CA2E035231FD1AAFED920  platform/helper/course_helper/lifecycle.py
28E28C62910C4EFB70F77EF27F293CE590C4D43BC953F1C95A7368486CFA9E66  platform/helper/course_helper/catalog.py
101E3D3171A8483EDFB07556FB92CBBB3061F9BB39AF768443A691A4088142B0  platform/helper/course_helper/cards.py
8D6E2CEF74D8154A44B3260D75E48103AB6953996CA98575C51C3C81B7431692  platform/helper/course_helper/retrieval.py
89B3B3A1522EE789344381F092EF36E33CD63C0B0DCC0C8C858E409558280225  platform/helper/course_helper/api.py
AD05911F04D260BAB3B2B345D65C4EA531AA34DF846B5C47DC62246F971001AA  platform/helper/course_helper/demo.py
A1AF0B864A35BAA37004F7BEBA8888AA69C0935E8D9D3FA10655EFF642DF6CED  platform/helper/tests/test_lifecycle.py
00E92F120AB7A93613B821DF12962656243112FD19F78F6FC7DD57174E15E15C  platform/helper/tests/test_catalog.py
0879FF3AFA487106453BCEB36B2229CAF240D7FBF7139D6C1E754A4C3FA877C1  platform/helper/tests/test_cards.py
4605B2C265A3D66B43F618021E35A3789B0CEBEABED59E4DB3A8922499939727  platform/helper/tests/test_retrieval.py
2E3B559BC28A9202DF6C15DB7B61EB9A83A1BD2B73242638FD050DAF373CDB27  platform/helper/tests/test_api.py
BA02890B26CCC3F2073CDC65B30993FEB57FFE46C61AB840CC3969362FB75AC6  platform/helper/tests/test_demo.py
```

This report is excluded from its own embedded inventory because embedding its
final digest would change that digest. Its sealed SHA-256 is reported separately
to the coordinating agent.

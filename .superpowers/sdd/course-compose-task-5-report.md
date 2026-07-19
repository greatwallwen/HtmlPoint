# Course Composition Task 5 Near-Duplicate Review Report

Date: 2026-07-18  
Scope: `docs/superpowers/plans/2026-07-16-course-composition-and-authentic-visuals.md`, Task 5  
Execution: main workspace, Gitless, offline, no protected-root access

## Result

PASS for the Task 5 implementation boundary. The Helper now provides the
explicit pre-publication scan seam required by later Task 13 orchestration.
Exact duplicates receive a transactional archived candidate, automatically
resolved exact-duplicate audit, and candidate-to-published `deduplicates`
edge. Near duplicates remain human-blocking and are never automatically
merged.

The `course-studio-near-dedup-v1` policy combines deterministic NFKC/casefold
token shingles, payload-free FTS scores, and cosine scores from only the exact
pinned FastEmbed identity. It records policy/candidate/index/provider digests,
IDs, ranks, scores, and checks, but no card payload, vector, local path, or URL.
Missing/invalid semantic authority and inference failures create explicit
degraded blocking reviews.

Task 13 still owns the product-level wiring that makes this explicit seam
mandatory in the real import -> candidate -> publish orchestration. Task 5
does not make `publish_card` invoke a model implicitly.

## TDD and review evidence

The initial RED was the missing `course_helper.near_duplicates` module. The
GREEN implementation covered deterministic shingles, FTS and semantic lanes,
union ordering, thresholds, stable insertion-order-independent digests,
degraded review, dismissal, lecturer-directed duplicate link, no automatic
merge, exact auto-resolution, idempotent replay, and no self-edge.

A coordinating review then found three integrity gaps and required new
regressions before sealing:

- lifecycle-eligible published cards must be selected independently of FTS;
  a missing or immutable-projection-mismatched FTS row now fails closed rather
  than disappearing through an inner join;
- duplicate-link resolution fully revalidates the canonical scan evidence,
  subject, producer/policy, candidate schema, identity digest, and task binding
  before accepting a target;
- semantic comparison requires the exact pinned model identity, 512 finite
  normalized vectors, exact counts, bounded policy-digested batches, and
  clamped cosine values. Invalid authority/output becomes degraded blocking
  evidence rather than a semantic pass.

## Verification

```text
python -m pytest platform/helper/tests/test_near_duplicates.py platform/helper/tests/test_cards.py platform/helper/tests/test_retrieval.py -q -p no:cacheprovider
130 passed in 7.74s

python -m pytest platform/helper/tests/test_reviews.py platform/helper/tests/test_catalog.py -q -p no:cacheprovider
98 passed in 6.19s

python -m pytest platform/helper/tests -m "not reference_demo" -q -p no:cacheprovider --basetemp D:\AppData\Temp\course-studio-task5-root-full-v1
595 passed, 4 skipped, 7 deselected, 57 warnings in 61.69s
```

Warnings are the existing OpenPyXL UTC deprecations plus the deliberate
duplicate-wheel security fixture.

## Adaptive model routing evidence

This work unit was classified P2: multi-file deterministic algorithm and
storage integration with focused integrity risk, but no hardware control or
new security boundary. A strong coding model at high effort reached GREEN and
closed two evidence-driven review rounds. No independent failure or hidden
coupling justified ultra. This supports downgrading routine follow-up after
the P3 Task 4 receipt boundary.

## SHA-256 inventory

```text
9B3248FDD07F7738190F59831E7D099E91366F5F0233FCD1CC1981F69298C1B7  platform/helper/course_helper/near_duplicates.py
8ABF17B641EFA11E0815C56DDD85666FC5F2F138F6B3AEAF41887725D313C530  platform/helper/course_helper/cards.py
9CC744129D7B9DAF64FCD92313EAF5909F4CD6BEBD70B788EC203A5DE738A01A  platform/helper/course_helper/retrieval.py
1D4A8E3623D5116725E7291BBBC38113038BCDE6D37455EF20B92D070CEA60B7  platform/helper/tests/test_near_duplicates.py
```

This report is excluded from its own inventory because embedding its digest
would change the digest.

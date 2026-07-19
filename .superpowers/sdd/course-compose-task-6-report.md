# Course Composition Task 6 receipt

## Delivered

- Added `course_helper.upgrades` for deterministic source chunk comparison and
  governed source, dataset/schema, visual, and course-feedback suggestions.
- Chunk receipts classify `changed`, `unchanged`, `removed`, and `added` typed
  locations.  Classification compares field-level SHA-256 digests, not only a
  supplied content digest; removed/added fields are also represented by digests.
- Source changes create a direct immutable asset suggestion plus review-status
  card candidates only where a changed cited chunk has a typed replacement.
  Removed citations are auditable unresolved findings; no replacement content is
  fabricated.
- Before any proposal write, the helper verifies both source versions are
  persisted members of one newer source lineage and verifies every supplied
  chunk is bound to that source and byte-identical to its persisted chunk row.
  A logical-source mismatch or a misbound/tampered chunk therefore leaves no
  evidence, candidate, task, or suggestion behind.
- Dataset and visual proposals apply the same pre-write newer-descendant
  validation.  Asset field receipts include `content_digest`, so a byte-only
  immutable asset revision is visible without exposing the underlying content.
- A source proposal commits its evidence, candidates, real Task 5 scan, review
  tasks, and suggestions as one catalog transaction. Task 5 retains its default
  active-transaction refusal; only this internal proposal bundle opts into its
  nested savepoint. Asset and feedback proposals are also atomic write bundles.
- Each card candidate calls Task 5's real `scan_near_duplicates` seam with
  `embedding_provider=None`.  Its real receipt reports a degraded semantic lane
  and opens the blocking near-duplicate review; this task does not fabricate a
  substitute task.  Exact-dedup, tag, and provenance checks remain independently
  evidenced.
- A source-detector replay never reruns that live Task 5 scan.  It first
  revalidates the existing upgrade suggestion and its source-change task
  envelope, then requires the candidate's persisted Task 5 near-duplicate
  task/receipt and the provenance task/candidate-gate receipt to bind the
  candidate.  Missing, malformed, or mismatched replay gates fail closed with
  no new rows, preventing a changed index from producing replay-only evidence.
- Acceptance appends a digest-bound resolution and returns an explicit outcome:
  card candidates alone can name `knowledge_card_publish`; source/dataset/visual
  suggestions return `review_affected_knowledge`, feedback returns
  `compose_candidate_from_feedback`, and reject/dismiss returns `no_action`.
  A card publication additionally rejects any associated source-change candidate
  whose terminal decision is not `accept`, and rejects source-change tasks with
  no complete `UpgradeSuggestion` binding. Acceptance itself does not publish,
  mutate an existing card, or rebind an old course placement.  Immutable card
  publication remains the existing `publish_card` path and produces a later
  published version which supersedes the review candidate.
- Course feedback remains a typed `CourseFeedbackSuggestion`; its task identity
  is bound to summary and actor audit digests, preventing same-course feedback
  from sharing a terminal review.  Affected cards/courses are read from one
  SQLite snapshot and its canonical payload digest is retained in evidence.

## Verification

```text
python -m pytest platform/helper/tests/test_upgrades.py platform/helper/tests/test_near_duplicates.py platform/helper/tests/test_cards.py platform/helper/tests/test_reviews.py -q
109 passed

python -m pytest platform/helper/tests/test_cards.py platform/helper/tests/test_reviews.py -q
80 passed

python -m compileall -q platform/helper/course_helper
passed

python -m pytest platform/helper/tests -q -p no:cacheprovider --basetemp D:\\AppData\\Temp\\course-studio-task6-full-helper-final2-0718
615 passed, 11 skipped, 57 warnings

independent adversarial re-review
0 Critical, 0 Important, Ready: Yes
```

Focused tests cover deterministic repeat detection across different actors/clocks,
direct source/dataset-schema/visual version suggestions, two affected cards and
two independently confirmed courses, changed/unchanged/removed/added chunks,
canonical old card/course byte preservation, real Task 5 degraded-scan evidence,
review-gated publication, reject/dismiss publish-exploit refusal, typed feedback
identity, actor/evidence audit, invalid-evidence rollback, source-lineage and
persisted-chunk preflight rollback, and terminal review non-revival under a
reverse clock.  Dataset/visual non-descendant candidates are refused before
evidence or review rows are written, and digest-only asset changes are covered.
Late suggestion failures roll back the complete source bundle; orphaned
source-change tasks fail closed; first resolution timestamps before task creation
are rejected without a resolution row; and semantic evidence-ID conflicts do not
silently reuse the first receipt.  Replay tests additionally prove that a later
actor/clock adds no rows, while a suggestion ID preoccupied by a different
reason/task or a missing candidate-gate receipt fails closed without writes.

## SHA-256

```text
EF46146A135CBC41E21839C66C7C4DC2950B22417622AFB43D957090450A4B5A  platform/helper/course_helper/upgrades.py
E62F0E1FB26040DEC1D0F7B2C33A4645ED9859F5E10401DEBA52EFAD801AECA5  platform/helper/course_helper/cards.py
01482A201DAAA6F9DA6C5A7DAC189105D4DF42CFC1AF64CFFE661F0FA7BF524E  platform/helper/course_helper/reviews.py
12852A3BDFA7DEE7CBD73E21DC97107E6225FE5CBCD8BC64602AEE5656DD5F6D  platform/helper/course_helper/near_duplicates.py
35C3086BF60FCB2DCACA70895F33DAEEB8CE06B544D230A9A9F508F4F0E6452A  platform/helper/tests/test_upgrades.py
```

## Uncertified / next required action

- The Task 5 scan runs with no embedding provider in this offline Task 6 path.
  Its semantic lane is therefore explicitly degraded and stays human-blocking;
  no semantic clean-match or automatic merge is claimed.
- No browser/API job, index publication, real dual-screen, reference-root, model,
  or network-live certification was run.  Those are outside Task 6 and remain
  uncertified.

# Task 7 — grounded course composition integrity receipt

## Delivered

- Split caller-supplied `RetrievalResult` composition into a non-authoritative
  preview and made `compose_and_register` the only authoritative persistence
  seam. It now calls a controlled retriever once per learning goal under one
  explicit snapshot and rejects handcrafted retrieval results at that seam.
- Added composer v2 binding receipts with canonical identities. Each receipt
  binds the persisted requirement digest, exact normalized query contracts and
  query digests, every raw retrieval evidence ID/digest, snapshot ID/digest,
  returned and selected card IDs/content digests, goal selections, filters,
  options, complete placement payloads/digests, allocation policy, exact goal
  partition, and total duration.
- Persist all raw retrieval receipts, the composer binding, card placements,
  and the outline in one transaction. A late outline failure rolls back the
  whole bundle. Multi-goal composition no longer manufactures a partial bundle
  receipt or drops per-goal evidence.
- Made outline content digests canonical over requirement, complete ordered
  chapters/placements, uncovered goals, composer evidence, and snapshot. The
  confirmation-summary path recomputes this digest, so changed allocations,
  purposes, lessons, or chapter semantics cannot reuse an old digest.
- Hardened catalog registration/confirmation to require the exact composer v2
  producer/version/kind and canonical receipt ID; reopen and verify the sealed
  snapshot; verify requirement, filters, queries, raw receipts, returned-hit
  subsets, selected card bytes, placements, duration, and covered/uncovered
  partition; and reject non-retrieval or nonmatching evidence.
- Recompute the scope-specific confirmation-summary digest during confirmation.
  Recheck selected-card lifecycle during confirmation and course registration,
  including suspension after confirmation.
- Reject include/exclude overlap and option/retrieval snapshot mismatch.
- Reuse the first immutable outline and evidence bytes for semantically
  identical cross-actor/cross-clock authoritative replay without duplicate
  rows. Conflicting semantics remain fail-closed.
- Converted unrelated upgrade/review test fixtures into explicit raw legacy
  persisted rows. This preserves byte-identical reopening of already-persisted
  legacy fixtures without allowing public APIs to create new ungrounded
  confirmations.

## Adversarial coverage

- Handcrafted result rejected at the authoritative seam.
- Real multi-goal query propagation through a recording retriever over a real
  catalog snapshot; all raw receipts are persisted.
- Include/exclude overlap and mismatched snapshot options.
- Non-retrieval evidence bypass and unrelated handcrafted outline registration.
- Allocation edit with unchanged claimed digest.
- Stale/tampered snapshot digest at confirmation.
- Late transactional rollback.
- Cross-actor/cross-clock deterministic replay with no duplicate evidence.
- Card suspension after confirmation before course registration.

## Verification

```text
python -m pytest platform/helper/tests/test_composer.py platform/helper/tests/test_retrieval.py platform/helper/tests/test_composition_storage.py -q
85 passed in 5.06s

python -m compileall -q platform/helper/course_helper/composer.py platform/helper/course_helper/catalog.py platform/helper/course_helper/retrieval.py platform/helper/course_helper/domain/composition.py
pass

python -m pytest platform/helper/tests/test_composition_contracts.py platform/helper/tests/test_catalog.py platform/helper/tests/test_upgrades.py platform/helper/tests/test_reviews.py -q
137 passed in 6.60s
```

## SHA-256

```text
9257A17A973FAA7A834A04450D4CDDAA5643810395FCE85CE627EC5D66EDB4AB  platform/helper/course_helper/composer.py
6AD940F48D71F070B6CB6D88B44B6F803CDFA401C1235537877315699A65829E  platform/helper/course_helper/catalog.py
D0167661D980D275201DE67EF0539127637D429BCE7C825A97143A3273D6E8E2  platform/helper/course_helper/retrieval.py
0290CE53F11B70A51BB181C7A93C10C66A406B9B855EE29D8529E5C33266767A  platform/helper/course_helper/domain/composition.py
DC5EA9BFABCFAAF80E75BA43D7B264297AC725CC56060C0B7086822C691404EB  platform/helper/tests/test_composer.py
4DEF7D26EF8137CB44B146247883AF3CA248EE22E9832AA9C905E0844BDEBBFB  platform/helper/tests/test_composition_storage.py
0F0B0CCC026784CD3BD471E6E6EB7B71AD9BD4E2FBC9C713C9CB0228735C12EF  platform/helper/tests/test_upgrades.py
88DBEDD0EE3AAC20D4A30FE4770A9B6893FDD08041665DC1C7155AA9FADC3C77  platform/helper/tests/test_reviews.py
```

## Not certified here

- The root worker owns the full Helper suite; it was intentionally not run in
  this bounded Task 7 receipt.
- No browser/runtime, network, reference, or hardware gate was run.
- No Git command was run.

## Main-workspace closeout (2026-07-18)

The current implementation bytes still match every SHA-256 value above. The
exact focused gate was rerun from the main workspace and passed `85 passed in
4.57s`. The later milestone-wide Helper gate passed `643 passed, 11 skipped`,
so the previously deferred full-suite ownership is now satisfied. Task 7 is
complete. Browser/runtime, network, physical dual-screen, signing, and Git
claims remain outside this Task 7 receipt.

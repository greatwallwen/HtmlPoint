# Course composition Task 8 — traceable content-only draft

Date: 2026-07-18  
Workspace: `D:\cursor\AI培训`  
Protocol: main-workspace Gitless execution

## Result

Task 8 is complete. A confirmed course now projects into an immutable,
deterministic, content-only `SlideDeckAst` plus `RuntimeManifest`. Every emitted
content node pins its exact course placement, published card version, cited
chunk, owning source version, and official verified card-publication evidence.
The draft contains no visual binding, artifact, publication, browser, network,
signing, or hardware claim.

## Implemented

- Added `course_helper/slide_builder.py` with one atomic
  `build_and_register_draft` seam. It preserves confirmed outline placement
  order, emits concise stage nodes, keeps presenter notes separate, and reuses
  the first immutable bytes across actor/clock replay.
- Added chunk and source lineage plus presenter notes to `SlideNode` without
  putting local paths, URLs, raw HTML, or commands into the AST.
- Added canonical semantic digest functions for `SlideDeckAst` and
  `RuntimeManifest`; catalog registration rejects a new payload whose claimed
  semantic digest is not exact.
- Catalog storage validates ordered placement/card ownership, chunk/source
  envelopes, source extraction state, verified evidence, separate presenter
  notes, deck/runtime cross-binding, and required manifest evidence.
- The builder rejects missing or tampered cards, chunks, sources, lineage, or
  evidence; suspended/archived cards; revoked/unparsed sources; uncovered
  outline goals; open blocking reviews; and runtime jobs that are not backed by
  a selected card's allowlisted dataset activity specification.
- Runtime jobs are content-only bindings. Until a typed card-pinned dataset
  specification exists, only `dataset_sql` and `chart_build` references are
  accepted; shell-like or unbound `python_snippet` requests fail closed.
- A newer source revision and its governed upgrade suggestion do not mutate or
  reject the already pinned draft. Reopening still revalidates the pinned
  dependency, so invalidating that exact old dependency rejects the draft.
- Existing composition storage fixtures now use the same canonical deck and
  runtime digests and explicit chunk/source/evidence lineage.

## Adversarial coverage

- Card suspension and archive after course confirmation.
- Card digest tampering, chunk envelope divergence, revoked source state, and
  deleted citation lineage.
- Substitution of generic verified evidence for the exact official card
  publication receipt.
- Open blocking review and a post-confirmation uncovered outline gap.
- Unsafe or unbound runtime job reference.
- Cross-actor/cross-clock immutable replay.
- New source revision plus upgrade suggestion while the draft stays pinned to
  the original valid source.
- Review subject queries are bounded in 400-ID batches to avoid SQLite
  parameter-limit failure on large valid courses.

## Adaptive model router evaluation

The deterministic router usefully split the work into P2 implementation
(`gpt-5.6-sol`, high) and a bounded P3 adversarial review
(`gpt-5.6-sol`, xhigh), while correctly keeping `boundedUltraAudit=false`.
This avoided an unnecessary Ultra pass. Its current limitation is operational:
the receipt remained `switchStatus=recommended_only` because this root task has
no callable in-place model switch. The routing recommendation influenced review
depth, but it is not proof that the recommended model actually executed.
After closeout the suitable route returns to P0/Terra-low for the next bounded
inspection; Task 9 should be rerouted before implementation because artifact
storage and media parsing raise the risk profile again.

## Verification

```text
python -m pytest platform/helper/tests/test_slide_builder.py platform/helper/tests/test_composer.py platform/helper/tests/test_composition_contracts.py platform/helper/tests/test_composition_storage.py -q
65 passed in 3.16s

python -m pytest platform/helper/tests -q
653 passed, 11 skipped in 53.33s

python -m compileall -q platform/helper/course_helper/slide_builder.py platform/helper/course_helper/catalog.py platform/helper/course_helper/cards.py platform/helper/course_helper/domain/slide_ast.py
exit 0

python platform/qa/run.py all
Python QA 154 passed; Helper 653 passed/4 skipped/7 deselected; Web 244 passed;
typecheck passed; build passed; design/evidence gates passed.
Aggregate exit 1 only because the protected-path query requires root Git
metadata, which is intentionally absent under the current Gitless protocol.
```

## SHA-256

```text
5F3FC526C2133D8FA3FBB6645D9758A8EE64A4F9445D4A53F43AED9012052D3F  platform/helper/course_helper/slide_builder.py
7E848E10C185FE98832A9A25E105BF0A1253184A54552A534AF97E3946D9CD6D  platform/helper/course_helper/catalog.py
557F113AECFBAA7012F3F0F59B058FE932CA54381BAC97788BF5A41155889795  platform/helper/course_helper/cards.py
77981FB67DAB8550E3B018251784D0C3F358F656EF535C640E5052185BFE2C55  platform/helper/course_helper/domain/slide_ast.py
79247A007B773645A7B6A2DD7CEF5232E97216F2F0359032F66B1A987566127E  platform/helper/tests/test_slide_builder.py
B593E839C54A78FFC93AB130284774CB54AEEF11D085CE22CF064F31A136AF98  platform/helper/tests/test_composition_contracts.py
3AD31E473EC5DE6A2C4B2ECB037FCA09373473F10F9872E5B042660068D5E079  platform/helper/tests/test_composition_storage.py
```

## Not certified here

- No Task 9 visual/artifact attachment or Task 10-11 publication claim.
- No live browser or execution-runner certification.
- No network, signing, Win11 physical dual-screen, or hardware certification.
- No Git mutation or repository reinitialization.


# Course composition Task 13B4 report

Date: 2026-07-19  
Scope: governed review resolution, card publication, and upgrade resolution typed mutations.

## Outcome

PASS. Three authenticated lower-camel jobs are now available:
`knowledge_review_resolve`, `knowledge_card_publish`, and
`knowledge_upgrade_resolve`. Each request binds the exact actor-independent
domain intent to `operationId + requestDigest`; the API injects the session
owner rather than accepting it from the browser.

Review resolution requires the exact raw review digest and bounded evidence
IDs. Upgrade resolution additionally requires immutable suggestion, review,
and candidate-card digests. Card publication reopens and validates canonical
card bytes, lifecycle, vocabulary, references, blocking reviews, and the exact
expected card digest inside the operation transaction.

Each mutation commits domain effects and its durable operation outcome
atomically. Card publication also commits one deterministic index-outbox item
and returns `indexState: queued`. Operation replay is byte-identical; parent
disconnect/response loss recovers the authenticated committed outcome rather
than reporting a false failure. Pre-replay domain reads were moved inside the
authenticated mutation so a wrong session cannot use integrity errors as an
existence side channel.

## Defects found and corrected

- Frozen operation result arrays were passed directly into the final strict
  JSON outcome model. The operation authority now thaws immutable structures
  before validating and freezing the durable outcome, enabling bounded array
  result refs without weakening the schema.
- The API inventory test fixture used the random browser token as an internal
  storage owner. Tokens beginning with `-` were correctly rejected and made
  the test flaky. The fixture now mirrors production by using the SHA-256-based
  `session-...` owner ID.
- Review/upgrade handlers originally read task/suggestion state before durable
  operation authentication. Those reads now occur only inside the mutation
  after replay ownership has been checked.

## Verification

- RED collection failed because the three strict job types and digest helpers did not exist.
- Focused mutation jobs: `18 passed`.
- Cards/upgrades/reviews/operations domain regression: `107 passed`.
- API/server regression: `56 passed`.
- Final combined gate: `181 passed in 42.98s`.
- `python -m py_compile` for reviews, cards, upgrades, operations, jobs, and API: exit 0.

## Exact changed-file evidence

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/reviews.py` | 42609 | `0FC88DA050C985A3F37ED50BECEC8E5A2047C726AE0FB8D234A9894192B7E5A6` |
| `platform/helper/course_helper/cards.py` | 39769 | `0410BE840B5D3EAD5723A261F1EC0035F761661C6102B1C587DDF55B5DE6C24C` |
| `platform/helper/course_helper/upgrades.py` | 51870 | `21F31F49D44AACC91B89B5898A1B65DDAD5E4184D6B1B45B683F284F455FD962` |
| `platform/helper/course_helper/operations.py` | 19951 | `15DF70B330D0672117F38F7F54580C95389965BAD57E50FB7E20473A1C712482` |
| `platform/helper/course_helper/jobs.py` | 70420 | `8940EE73A91E20C278EE391FF5769C85A52CE81C311BD36F6F26EF40A1C1CE74` |
| `platform/helper/tests/test_knowledge_review_jobs.py` | 41279 | `BE993AED12C42563D3302747046886E2E32D52EA24B1A88462EFD8512BB17757` |
| `platform/helper/tests/test_api.py` | 75558 | `2AFCF5CBE6A395BD1F5D269391883A7CDAE9432ADF6D301169DA69FBD3CD3E03` |

## Artifact lifecycle and routing

Eleven resolved `D:\AppData\Temp\course-studio-task13b4-*` roots contained
reproducible pytest databases and caches totaling about 14 GB. All eleven were
removed after verification; none remain. Durable evidence is limited to source,
tests, this report, and Supergrill receipts.

The deterministic router recommended P2 (`gpt-5.6-sol`, high) because this unit
crossed several coupled transaction authorities. Root switching was unavailable,
so the receipt remains `recommended_only`. Deterministic production-path tests
closed the observed defects; Ultra was not warranted. Real import parsing and
candidate construction remain the next separate phase. No network, physical
dual-screen, hardware, signing, OS-wide isolation, or Git certification is
claimed.

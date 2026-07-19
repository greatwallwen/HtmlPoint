# Course composition Task 13B3 report

Date: 2026-07-19  
Scope: authenticated, bounded review-list, review-detail, and upgrade-list read projections.

## Outcome

The helper now exposes three strict lower-camel typed jobs:
`knowledge_review_list`, `knowledge_review_detail`, and
`knowledge_upgrade_list`. The API derives the authenticated session owner,
never accepts a browser-supplied owner, and marks job responses `no-store`.

Review list and upgrade list use bounded opaque cursors and limits of 1..100.
Review detail validates canonical review/task/card/citation envelopes, rejects
dangling subjects, and projects only bounded excerpts: at most 50 AST nodes,
50 citations, 32 path entries, 5x5 table cells, and explicit total/truncation
metadata. Upgrade reads validate evidence links, version lineage, and the
associated review projection before returning data.

## Verification

- RED: collection initially failed because the three read jobs and projection APIs did not exist.
- Focused projection tests covered pagination, filters, malformed cursors and limits, tampered envelopes, bounded excerpts, strict schemas, direct handlers, and a real spawned worker.
- Final gate: `python -m pytest platform/helper/tests/test_knowledge_review_jobs.py platform/helper/tests/test_reviews.py platform/helper/tests/test_api.py platform/helper/tests/test_server.py -q -p no:cacheprovider --basetemp <external-unique>` passed `91` tests.
- `python -m py_compile platform/helper/course_helper/reviews.py platform/helper/course_helper/jobs.py platform/helper/course_helper/api.py`: passed.

## Exact changed-file evidence

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/reviews.py` | 42082 | `7B46B420E48B1D7156E7A27EDA803C6B14AF3AF97F8F56E5FA16EA12D7DD5A66` |
| `platform/helper/course_helper/jobs.py` | 55626 | `8A98E23AC58ABD506C6AB542D0F8DE22F8C1C9B835FD083E96F0C2B738B4C9E5` |
| `platform/helper/course_helper/api.py` | 17705 | `2F76396725BA49BE021CDA974D0AEBD8C69F5C7563302B1DB273A768CEC81518` |
| `platform/helper/tests/test_knowledge_review_jobs.py` | 25772 | `8335F66AB1AF6573A26D4FC33E8D3BB8D7A2D53A790754213477B3C92B64F00E` |
| `platform/helper/tests/test_reviews.py` | 44448 | `88DBEDD0EE3AAC20D4A30FE4770A9B6893FDD08041665DC1C7155AA9FADC3C77` |
| `platform/helper/tests/test_api.py` | 75374 | `B87A931F2CB869AF19A25EA5CFA164810B51396CB47285F9CB0BD2296693BD4B` |
| `platform/helper/tests/test_server.py` | 4511 | `27E99013E6ECCB11B7D7867C598016265D4173D519562A34476309C31E91A29A` |

Pytest temporary artifacts were created only under the external managed temp
root and were not retained as product evidence. Source, tests, and this report
are durable. The adaptive router's P1/medium recommendation was sufficient;
no reasoning failure or hidden cross-system boundary justified Ultra. The
receipt remains `recommended_only` because in-place model switching is not
available. No mutation, live browser, network, signing, physical dual-screen,
hardware, OS-isolation, or Git certification is claimed.

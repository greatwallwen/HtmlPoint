# Course composition Task 13B1 report

Date: 2026-07-19  
Scope: authenticated upload and governed-source inventory HTTP slice only.

## Outcome

The loopback Helper now exposes `POST /v1/uploads` and
`GET /v1/knowledge/sources` behind the existing exact-origin session guard.
Upload requests are raw streamed bytes with one required safe name, MIME type,
and canonical positive content length. The endpoint delegates every size,
digest, allowlist, atomic-file, and database invariant to `UploadStore`, returns
201 plus bounded lower-camel metadata, and sends `Cache-Control: no-store`.

The stored upload owner is derived from the active session token through a
one-way SHA-256 identifier before the existing storage digest is applied. This
keeps raw token material out of SQLite and also avoids relying on a randomly
generated token beginning with an alphanumeric character.

The inventory endpoint accepts only one optional opaque cursor and one
canonical integer limit from 1 through 100. Repeated, unknown, malformed, or
path-like query values fail with a stable redacted 422. Successful pages expose
only opaque IDs, safe display metadata, digest, and status. Both endpoints and
their errors are no-store; CORS adds only `X-Upload-Name` to the existing
allowlist.

## Verification

- RED: `python -m pytest platform/helper/tests/test_api.py -q -k "upload or source_inventory or cors"` produced 13 expected endpoint/CORS failures and one existing CORS pass.
- GREEN: the same focused command passed 14 tests.
- Slice gate: `python -m pytest platform/helper/tests/test_api.py platform/helper/tests/test_server.py platform/helper/tests/test_uploads.py platform/helper/tests/test_source_inventory.py -q` passed 74 tests.
- `python -m py_compile platform/helper/course_helper/api.py`: passed.

## Exact changed-file evidence

| Path | Bytes | SHA-256 |
|---|---:|---|
| `platform/helper/course_helper/api.py` | 17557 | `A2D94DFFD4D7978A2B9E12BB54DB03125C6BC583EAF9DD8775E35144B463B9AB` |
| `platform/helper/tests/test_api.py` | 72103 | `315EF7DDF7E1EA8ED1E38A5BA1D142035AE237A05B3E7057744D6A99B51F44F2` |

Pytest outputs were created only under its managed temporary directory and are
reproducible temporary artifacts; no project-local logs, screenshots, caches,
or databases were retained. Source, tests, and this compact acceptance report
are durable. No protected reference root was read. No import/review job,
browser publication, network, signing, physical dual-screen, hardware, OS
isolation, or Git certification is claimed.

## Router receipt

The phase was recommended as P1 with `gpt-5.6-terra` at `medium`. In-place root
switching remained unavailable, so the receipt is `recommended_only`. The
implementation had one bounded review correction (derive a safe owner ID from
the token); no Critical/Important defect or reasoning failure required an
upgrade. This supports the low-token profile for similarly scoped endpoint
wiring, but not yet for the upcoming durable operation-recovery boundary.


# Course composition Task 13B4 verification report

Date: 2026-07-19  
Scope: execution-plan Task 4 — recovery, idempotency, authentication, and bounded-projection verification.

## Outcome

PASS. No implementation defect was found in this verification phase. The
combined adversarial gate exercised the existing governed upload/import,
durable operation, review/upgrade projection, API, and real spawned-worker
boundaries as one unit.

The verified behavior includes:

- committed import recovery after parent disconnect and timeout;
- exact request-digest replay without duplicate source, blob, operation, or outbox mutation;
- cancel response-loss recovery and idempotent cleanup;
- wrong actor, wrong session, wrong digest, unknown operation, and corrupted durable envelope fail-closed behavior;
- authenticated operation lookup without existence leakage;
- atomic rollback, append-only operation/outbox records, and post-reopen integrity checks;
- review/upgrade opaque-cursor pagination and bounded detail excerpts;
- malformed cursors, invalid limits, repeated/extra fields, dangling subjects, and tampered projections rejected before unsafe projection.

## Verification evidence

```text
python -m pytest platform/helper/tests/test_knowledge_review_jobs.py platform/helper/tests/test_uploads.py platform/helper/tests/test_operations.py platform/helper/tests/test_reviews.py platform/helper/tests/test_api.py platform/helper/tests/test_server.py -q -p no:cacheprovider --basetemp D:\AppData\Temp\course-studio-task4-verification

123 passed, 1 skipped in 32.32s
exit 0
```

The single skip is the existing Windows permission-dependent directory
reparse/symlink attack fixture. The production code still rejects such a
boundary; the fixture could not create the attack primitive under this user
token. This is not physical dual-screen, hardware, signing, live-network, or
OS-wide isolation certification.

## Artifact lifecycle

The external pytest root contained 172 reproducible files totaling
2,763,389,344 bytes. Its resolved absolute path was checked to equal
`D:\AppData\Temp\course-studio-task4-verification`, then the directory was
removed recursively. It no longer exists. No runtime log, test database,
cache, screenshot, or protected-reference-derived artifact was retained.

Durable evidence is limited to source tests, the earlier Task 13B1–B3 reports,
this report, and the Supergrill route/checkpoint/verification receipts. The
router selected P1/medium on the balanced model; the deterministic gate passed
first time, so no Sol/Ultra escalation was justified. Because root in-place
switching is unavailable, the route receipt truthfully remains
`recommended_only`.

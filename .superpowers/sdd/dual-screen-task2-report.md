# Dual-screen Task 2 report

- State: complete; Supergrill checkpoint closed without input drift.
- Reducer: pure and immutable; it has no clock, random, Win32, WebView2, file, process, network, or UI dependency.
- Certification: the exact attended sequence reaches `Certified`, sets only `physicalDualScreenCertified`, and keeps `releaseSignatureCertified=false`.
- Invalidation: topology, DPI, role collision, minimize, cloak, frame rollback, identity, heartbeat, navigation, runtime, Helper restart, and Host restart all fail closed with stable codes.
- Witness boundary: simulated, expired, reused, and identity-mismatched challenges cannot certify.
- Determinism: 100 seeded replays produce byte-identical canonical JSON; event and session identities are derived deterministically from bounded state evidence.
- Verification: 6 Core tests passed with `--no-restore`; `git diff --check` passed.

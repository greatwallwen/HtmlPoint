# Course Composition Task 11 Report

Date: 2026-07-19  
Workspace: `D:\cursor\AI培训` (main-workspace Gitless protocol)

## Status

Offline implementation and QA producer: complete.  
Live Wikimedia acquisition receipt: **NOT CERTIFIED** because the current host
timed out during the certificate-validating TLS handshake to the sole public
DNS answer. No sealed receipt was created or overwritten.

## Delivered

- Migration 0006 adds immutable discovery/acquisition history and a mutable,
  revisioned 24-hour verification projection.
- Wikimedia discovery returns short-lived opaque candidate IDs. Acquisition
  rechecks provider metadata, license, URL policy, public DNS, redirects, MIME,
  byte bounds, provider SHA-1, artifact SHA-256, dimensions, and provenance.
- Revalidation preserves historical evidence while making removed,
  license-changed, content-changed, and expired material fail closed.
- Only raster network media is accepted. SVG, HTML/active metadata, arbitrary
  hosts, ports, downgrade redirects, private/mixed DNS, rebinding, duplicate
  JSON keys, oversized bodies, and unknown licenses are rejected.
- The provider-only live QA command requires its exact opt-in and exact output
  path, validates canonical unlinked receipt bytes before atomic promotion,
  restores the prior receipt on failure, and always records
  `coursePublicationVerified=false`.

## Verification

- Task 11 focused + artifact/domain + QA: `224 passed, 1 skipped`.
- Full Helper after migration correction via repository QA: `708 passed,
  5 skipped, 7 deselected`.
- Web: `244 passed`; typecheck and production build passed.
- Repository `all` gates passed except the expected Git protected-path query;
  root Git intentionally remains absent until platform acceptance.
- Exact live wrapper: `NETWORK_VISUAL_ACQUISITION_FAILED`; diagnosis was a TLS
  handshake timeout, not a policy, license, hash, or receipt-validation error.

## Truthful limitations

- Network acquisition and public course publication are not certified on this
  host. A provider-acquisition receipt alone could not certify publication even
  if the network call succeeded.
- OS-wide network isolation, signing, live browser publication, physical
  dual-screen hardware, and Git status remain NOT CERTIFIED.
- No retry was performed after the same external network state produced no new
  evidence; no fixture or generated asset was substituted for live evidence.


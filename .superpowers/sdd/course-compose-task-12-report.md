# Course Composition Task 12 Report

Date: 2026-07-19  
Workspace: `D:\cursor\AI培训` (main-workspace Gitless protocol)

## Status

Complete for all locally executable Task 12 acceptance gates.

## Delivered

- Published course versions now pin the ordered visual-placement snapshot and
  use a canonical semantic digest. A later attach/detach publishes a new
  revision and supersedes the prior course without rewriting old bytes.
- Slide asset bindings now include exact visual/artifact IDs and digest, both
  evidence IDs, attribution ID and block, and transformation ID and manifest.
- Runtime manifests must exactly match the deck's ordered artifact and evidence
  snapshot; extra or omitted dependencies fail closed.
- Publication resolves source-provided PPTX materializations, typed data-derived
  charts, and current Wikimedia acquisitions through their exact artifact,
  lineage, evidence, attribution, scope, license, and transformation contracts.
- Public freshness and rights policy uses the mutable 24-hour network
  verification projection while historical acquisition evidence remains
  immutable.
- `validate_course_version` builds the complete proposed immutable snapshot.
  `publish_course_version` commits course, deck, runtime manifest, and durable
  operation result in one transaction.
- A simulated post-commit response loss is recovered by `operationId`; a late
  manifest failure rolls back the new course, deck, and outcome together.

## Verification

- Plan-focused publication command: `49 passed, 1 skipped`.
- Composition/storage/publication regression: `95 passed, 1 skipped`.
- Full Helper: `711 passed, 12 skipped`.
- Platform QA: Python QA `166 passed`; Web `244 passed`; typecheck and build
  passed; design and evidence gates passed.
- Aggregate QA's only failure is the expected root-Git protected-path query.
  Git remains intentionally absent until full platform acceptance.

## Truthful limitations

- The local fixture proves the publication state machine for a current governed
  network visual; live Wikimedia connectivity remains NOT CERTIFIED because the
  Task 11 TLS handshake timed out on this host.
- Product API/UI publication, live browser publication, signing, physical
  dual-screen hardware, OS-wide network isolation, and final Git initialization
  remain outside Task 12 and are not certified here.


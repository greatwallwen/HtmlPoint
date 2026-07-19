# Course Composition Task 15 Report

## Outcome

Task 15 is complete in the main workspace. The Web application now has strict
version-one contracts and job-specific clients for every Task 13/14 upload,
inventory, import, review, indexing, composition, chart, visual, validation,
publication, and operation-recovery seam. No arbitrary job runner is public.

## Safety and Product Boundaries

- Every response is parsed through a job-specific strict schema; extra fields,
  stale cross-object digests, mismatched operation IDs, mismatched result
  shapes, duplicate outline/visual placements, and inconsistent index states
  fail closed.
- A client instance is constructed only from one `VerifiedHelperSession`; the
  token stays in `X-Course-Session`, never the URL or user-visible error.
- Review list/detail and upgrade projections remain bounded and are not cached
  by the client, so callers can dispose detail state without hidden retention.
- `TrustedExternalLink` accepts only exact HTTPS provenance without
  credentials, fragments, whitespace, or extra raw URL fields. The browser
  projection uses `noopener noreferrer external`.
- Artifact loading accepts only opaque IDs, authenticated GET, four allowlisted
  image MIME types, exact positive `Content-Length`, and at most 32 MiB. Blob
  URLs are revoked on replacement and disposal, and caller abort is supported.

## Verification

- Focused contract/client/session tests: `53 passed`.
- Complete Web test suite: `263 passed` across 17 files.
- TypeScript strict typecheck: passed.
- Vite production build: passed; 4,668 modules transformed.
- Whole-file whitespace check found no whitespace errors; Git reported only
  the expected LF-to-CRLF advisory because root Git is intentionally absent.

## Routing

The browser authentication, strict contract, and Blob lifecycle surface is a
security-sensitive P3 slice. The router recommendation remains
`gpt-5.6-sol`/`xhigh` and `recommended_only`; Ultra is not needed.

## Changed Files

- `platform/web/src/domain/helper-contracts-schema.ts`
  - SHA-256 `B4F34A5E7CEB64EC531F30585A1D9D3FD2A9BBC5239833F529BEF0558E7764BA`
- `platform/web/src/domain/helper-contracts-schema.test.ts`
  - SHA-256 `DE076A7459F9FC542DAA3D223999BB2315C4ECA13A1F9C681C674411DFD3C090`
- `platform/web/src/services/knowledge-client.ts`
  - SHA-256 `336963B80E0A3FA6AAFD0C69310ADCFF058CE36251C70B823D308ED2714CBAB8`
- `platform/web/src/services/knowledge-client.test.ts`
  - SHA-256 `AC1893F8C2507E5A4B4150C5A9ECE672224E78E052A889D4FAE502DE0E44E409`
- `platform/web/src/services/artifact-client.ts`
  - SHA-256 `8188AD42939F92BCC61C31EC3288231B24821066B812C8C66D6EA23872145042`
- `platform/web/src/services/artifact-client.test.ts`
  - SHA-256 `B728FE64771E900B417B6A6C93E3DC255FDD87ED119657EE7E0137C3328C6B60`

Task 15 does not certify browser E2E, live network publication, signing,
physical dual-screen behavior, hardware control, OS isolation, or Git.

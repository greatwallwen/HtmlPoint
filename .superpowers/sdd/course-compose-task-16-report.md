# Course Composition Task 16 Report

## Outcome

Task 16 is complete in the main workspace. Browser persistence now uses a
strict `PersistedWorkspaceV2` whitelist containing only governed requirement,
outline, course, deck, runtime, card, and visual-placement IDs; bounded view
preferences; an optional count-only `legacy-unlinked` summary; and `savedAt`.

## Migration and Safety

- The v2 key is `personal-ai-course-studio:v2`; the former v1 key is migration
  input only.
- Migration parses bounded v1, constructs a count-only summary, writes v2,
  reads it back, validates the exact serialized bytes, and only then deletes
  v1.
- A write, readback, or schema failure removes the candidate v2 when possible
  and leaves v1 authoritative. A later cleanup failure does not invalidate an
  already verified v2.
- Legacy titles, objectives, lesson summaries, source names, `extractedText`,
  receipts, Helper payloads, URLs, paths, tokens, nonces, base64, and artifact
  bytes never enter v2.
- Reopen restores exact governed IDs but not browser-owned course bodies or
  validation receipts. Local legacy work remains explicitly unlinked and
  non-publishable.
- Identical serialized inputs reuse the prior write. Overlapping generation
  runs are epoch-fenced so only the newest result can replace the current
  course; a failed run preserves the prior course.

## Verification

- Focused storage/workspace/App/editor gate: `107 passed`.
- Complete Web test suite: `259 passed` across 17 files.
- TypeScript strict typecheck: passed.
- Vite production build: passed; 4,668 modules transformed.
- Whole-file whitespace check: passed.

## Routing

Persisted-data minimization and migration rollback were classified P3 because
they guard local secrets and product state. The receipt is
`recommended_only` for `gpt-5.6-sol`/`xhigh`; Ultra is not needed.

## Changed Files

- `platform/web/src/domain/course.ts`
  - SHA-256 `74849FAE767981CE2BA8E4345E969D3DFD47D7F6676199F9207D92523222175F`
- `platform/web/src/domain/course-schema.ts`
  - SHA-256 `374BC099126D4FB5C8E39DF950DB2FEAF31630D4E34E0C41CBA237417176D250`
- `platform/web/src/state/storage.ts`
  - SHA-256 `140D3396065DEAF35AA2E457D6C03AD36187E14924D59A0A52280B89D297B57D`
- `platform/web/src/state/storage.test.ts`
  - SHA-256 `E37307C61B6925CE7C0B19AAB57CFEB8DA9756046BC6B56366FD2C2495C64FBA`
- `platform/web/src/state/workspace.tsx`
  - SHA-256 `E895C4A425E3338BC52E980671EC3C7D4CEB35DA05A910AE85DD3B0FFC5908E6`
- `platform/web/src/state/workspace.test.tsx`
  - SHA-256 `FD51C64D8156CA7A7849C637084D3FD59F15149DCE03CC6FAE5EC23D752DA1DD`
- `platform/web/src/app/App.test.tsx`
  - SHA-256 `A0348F0A5CA33281DAF52629AA9CAC0CA5EF2606A71B161893EE787C5C936867`
- `platform/web/src/components/CourseEditor.test.tsx`
  - SHA-256 `211CC54DC4C27829A7F77314746AB1FB0310FE7AF24355250407FACD416E73F9`

Task 16 does not yet connect the UI generation flow to Helper composition;
that is Task 17. It makes no browser E2E, network, signing, physical
dual-screen, hardware, OS isolation, or Git certification claim.

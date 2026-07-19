# Git-less execution amendment

Date: 2026-07-17

This amendment records the operator's explicit decision to discard the old Git
history, finish the current platform work from the preserved isolated snapshot,
and initialize a new repository only after locally executable acceptance is
green.

## Authority and unchanged product boundaries

- Working directory: `D:\cursor\AI培训\.worktrees\course-studio`.
- Never initialize or modify Git metadata at `D:\cursor\AI培训`.
- Never read, copy, modify, or derive implementation from `Course_AIProduct/`.
- `references/` remains read-only and may be accessed only by the exact final
  allowlist gates already defined in the reviewed plans. Default, offline, and
  pre-final gates must not probe it.
- The authoritative reboot plan remains byte-identical with SHA-256
  `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80`.
- The reviewed course-composition plan remains byte-identical with SHA-256
  `5E46C450C4937C4DC434AB514C0D9897180D60BED3634F9EB24960B7CC55BC9D`.
- The reviewed Win11 projection-host plan remains byte-identical with SHA-256
  `68C0795E6953985506A67E3D64DF5906D1B5B438DD2DCED4BE5D4787277433D5`.

## Temporary execution protocol

Until all locally executable acceptance gates pass:

1. Do not run `git init`, create commits, reconstruct old Git metadata, or
   import review diffs as history.
2. Git-specific Task 0 hard stops, `git diff --check`, staging, commit, branch,
   and committed-range review bullets in the two reviewed plans are superseded
   by this amendment. All product, test, security, evidence, and protection
   requirements remain unchanged.
3. Preserve test-driven development: record a focused failing test before each
   behavior change, then the focused passing test and justified regressions.
4. Use one implementation writer for a task. Reviewers inspect the current
   snapshot read-only from an exact changed-file list and SHA-256 inventory.
5. Write bounded task reports under `.superpowers/sdd/`; do not treat chat
   history, generated logs, caches, build outputs, or old `.diff` files as
   product truth.
6. Large-file inventory is incremental. If a scan yields no new evidence,
   immediately narrow its scope; never put a full-workspace hash scan on the
   critical path.

## Final repository initialization

Only after course, Helper, Web, Windows-host, packaging, and QA gates that are
locally executable have freshly passed:

1. Re-verify the exact working directory and confirm neither the shared root nor
   `Course_AIProduct/` is in scope.
2. Create or verify a conservative `.gitignore` for dependencies, caches,
   temporary evidence, UDFs, publish output, logs, secrets, and large generated
   artifacts.
3. Run `git init -b codex/course-studio-light` only in the isolated working
   directory.
4. Stage reviewed source, schemas, plans, bounded receipts, fixtures, and tests
   by explicit path groups after inspecting `git status`; do not use a blind
   workspace-wide add.
5. Re-run the final acceptance gates against the staged snapshot and create the
   first commit only if the staged content is clean and protection checks stay
   green.

Physical dual-screen certification is still a separate witnessed hardware
outcome. Missing signing authority, a locked runtime, a second physical panel,
or an operator witness must produce an honest `NOT CERTIFIED`; it does not
permit weakening automated acceptance.

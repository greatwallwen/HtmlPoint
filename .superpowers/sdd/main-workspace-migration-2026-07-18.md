# Main workspace migration receipt

Date: 2026-07-18

## Decision

The product root is now `D:\cursor\AI培训`. Platform development continues at
`D:\cursor\AI培训\platform`; no additional project wrapper is used.

`Course_AIProduct/` was not read, enumerated, copied, hashed, or modified.
`references/` was not read, enumerated, copied, hashed, or modified.
No Git command was run.

## Whitelist migration

The source snapshot was
`D:\cursor\AI培训\.worktrees\course-studio\platform`. A whitelist copy created
the root `platform/` tree with:

- 118 files;
- 3,961,345 bytes;
- 60 Helper files, 2 QA files, and 56 Web files;
- zero missing or SHA-256-mismatched files when compared with the source
  snapshot.

The copy excluded `dist/`, `.artifacts/`, `.pytest_cache/`, every
`__pycache__/` and `*.pyc`, `course_studio_helper.egg-info/`, coverage output,
logs, and temporary files. The old 105-file SDD directory was not copied as a
unit; only the current progress record, reboot baseline, and Course Composition
Task 1-4 reports/research were retained. Old review diffs, logs, task briefs,
temporary browser scripts, and stale final-state data were left behind.

The six missing authority plans were copied into the root `docs/` tree. Their
verified SHA-256 values are:

- reboot plan:
  `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80`;
- course-composition plan:
  `5E46C450C4937C4DC434AB514C0D9897180D60BED3634F9EB24960B7CC55BC9D`;
- Win11 projection-host plan:
  `68C0795E6953985506A67E3D64DF5906D1B5B438DD2DCED4BE5D4787277433D5`;
- Git-less execution amendment:
  `83EC6410DB884E5654AD222ECAEB79DFC720C573AC4DFEE9C5CE02C617152AC2`;
- Task 4 bootstrap amendment:
  `3753CA317C3BB422B5BD070D2338C1CABEB0A90581F3021C6CBB2ADCE69788F5`.

The operator's new main-workspace amendment was then added with SHA-256
`28AF72AD995A71DD64BBD122FF983F753C8E4A2A8F6E0C2132B573B47711D13A`.

The pre-existing `2026-07-15-personal-ai-course-studio.md` was preserved rather
than overwritten because its line content matched and only its newline encoding
differed.

## Dependency and cleanup state

`npm --prefix platform/web ci --offline --no-audit --no-fund` restored 164
packages from the local cache without network access. The root `.gitignore` now
excludes the protected source roots, worktrees, caches, build outputs, package
dependencies, projection publish/UDF output, and local tools.

After the root passed its migration baseline, the old `course-studio` snapshot
was removed. Its duplicate 15,210-file, 144,293,275-byte `node_modules` tree and
all identified build/cache/artifact directories were removed first; the final
source-shell deletion then succeeded. The obsolete `platform-reboot` tree
contains only locked pytest temporary directories, but its ACL still denies
deletion; it is a bounded cleanup tail item and was not force-removed without a
successful ownership boundary check.

## Next acceptance

Continue all implementation and acceptance from the root. Windows security
handle tests use a unique system temporary directory because Codex file
monitoring intentionally holds workspace paths open; this environmental choice
does not change product paths or evidence contracts.

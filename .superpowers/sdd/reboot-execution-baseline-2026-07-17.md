# Reboot execution baseline — 2026-07-17

Scope: `D:\cursor\AI培训\.worktrees\course-studio`

## Boundary evidence

- Shared-root `.git`: absent.
- Isolated-workspace `.git`: absent.
- `Course_AIProduct/`: not accessed.
- `references/`: not accessed.
- `COURSE_REFERENCE_ROOT`: unset.
- `COURSE_NETWORK_VISUAL_TEST`: unset.
- `COURSE_EMBEDDING_MODEL_DOWNLOAD`: unset.
- Python: 3.12.4.
- Node: 24.15.0.
- npm: 11.12.1.
- SQLite: 3.45.3 with FTS5 available.
- `dotnet` on PATH: absent.
- `platform/windows/`: absent.

## Plan evidence

- Authoritative reboot plan SHA-256:
  `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80`.
- Reviewed course plan SHA-256:
  `5E46C450C4937C4DC434AB514C0D9897180D60BED3634F9EB24960B7CC55BC9D`.
- Reviewed Win11 plan SHA-256:
  `68C0795E6953985506A67E3D64DF5906D1B5B438DD2DCED4BE5D4787277433D5`.
- Git-less execution amendment SHA-256:
  `83EC6410DB884E5654AD222ECAEB79DFC720C573AC4DFEE9C5CE02C617152AC2`.

## Fresh baseline commands

```text
python -m pytest platform/helper/tests -m "not reference_demo" -q
307 passed, 4 skipped, 7 deselected

python -m pytest platform/qa/test_run.py -q
99 passed

npm --prefix platform/web test -- --run
14 files passed, 244 tests passed

npm --prefix platform/web run typecheck
passed

npm --prefix platform/web run build
passed; 4,667 modules transformed
```

All five commands exited 0. They intentionally excluded the live reference
gate and did not require Git metadata. The existing `all` command is not yet a
valid Git-less baseline because its protected-path check still calls Git; the
reviewed downstream plans must migrate that gate before final acceptance.

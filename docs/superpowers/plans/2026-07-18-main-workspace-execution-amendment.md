# Main workspace execution amendment

Date: 2026-07-18

This amendment records the operator's explicit correction that the platform is
to be completed directly in the main workspace, not in an isolated worktree.
It supersedes only the working-directory, cleanup, and final Git-location
clauses of earlier execution plans.

## Authoritative development root

- Product root: `D:\cursor\AI培训`.
- Platform code: `D:\cursor\AI培训\platform`.
- Plans: `D:\cursor\AI培训\docs\superpowers\plans`.
- Bounded execution reports: `D:\cursor\AI培训\.superpowers\sdd`.
- Do not create another wrapper project directory and do not resume development
  under `.worktrees/`.

The isolated `course-studio` snapshot may be used only as the already verified
migration source. After the root copy has matching file hashes and passes local
acceptance, remove the old worktree snapshot and generated caches.

## Unchanged protection boundaries

- Never read, enumerate, copy, hash, modify, or derive implementation from
  `Course_AIProduct/`.
- `references/` remains read-only and may be opened only by the exact final
  allowlist gates already specified by the reviewed course-composition plan.
- Default, focused, offline, Web, Helper, build, and desktop-development gates
  must not probe either protected root.
- Evidence, model manifests, schemas, tests, and current bounded receipts are
  product truth and are not redundant cleanup targets.
- Hardware, signing, current network authorization, and physical dual-screen
  outcomes remain honestly `NOT CERTIFIED` unless their separate witnessed
  gates actually pass.

## Cleanup policy

Remove generated or superseded material when it is safe and reproducible:
`__pycache__`, `.pytest_cache`, `*.pyc`, `dist`, coverage, stale logs, temporary
browser scripts, old review diffs, egg-info, abandoned pytest trees, and the old
worktree after root acceptance. Keep exactly one locally usable dependency tree
when it is required for offline personal operation; never track it in Git.

Every recursive delete must first verify the resolved absolute target is inside
the intended workspace and outside both protected roots. An ACL-blocked temp
directory is reported as a bounded cleanup tail item rather than bypassed with
an unverified destructive command.

## Product completion and final Git

Continue the reviewed platform, course-composition, authentic-visual, and Win11
projection work from the root paths above. Optimize the finished experience for
one person: a bright, low-noise four-step workflow; concise controls; governed
knowledge cards; adjustable course composition; editor, validation, stage, and
presenter projections; and truthful evidence/degraded states.

Only after every locally executable Helper, Web, QA, packaging, and protection
gate is freshly green:

1. verify the exact root and `.gitignore` protection rules;
2. initialize new Git metadata only at `D:\cursor\AI培训`;
3. stage source, schemas, plans, tests, and bounded evidence by explicit path
   groups, never with a blind workspace-wide add;
4. prove `Course_AIProduct/`, `references/`, `.worktrees/`, dependencies,
   caches, build output, secrets, and large generated artifacts are excluded;
5. rerun acceptance against the staged snapshot and create the first commit.

Acceptance passing is the loop stop condition. Missing external authority does
not justify weakening automated gates or fabricating certification evidence.

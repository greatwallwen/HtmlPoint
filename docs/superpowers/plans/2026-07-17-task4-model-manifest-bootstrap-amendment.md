# Task 4 Model Manifest Bootstrap Execution Amendment

Date: 2026-07-17

This bounded execution amendment preserves the byte-identical authority plans
and resolves one prerequisite deadlock in Course Composition Task 4. It does
not weaken the final model, cache, runtime, receipt, or offline acceptance gate.

## Authority preserved

- `2026-07-14-personal-ai-course-platform-reboot.md` remains byte-identical,
  SHA-256 `49264B9B1A57F241730BF5839DA871C0165A100E92565040BC860132A2E93E80`.
- `2026-07-16-course-composition-and-authentic-visuals.md` remains byte-identical,
  SHA-256 `5E46C450C4937C4DC434AB514C0D9897180D60BED3634F9EB24960B7CC55BC9D`.
- This amendment applies only to Task 4's first immutable model-manifest and
  Windows x64/Python 3.12 runtime-wheel lock bootstrap.

## Why a bootstrap phase is necessary

The exact Qdrant artifact revision exposes the ONNX member as an LFS SHA-256,
but exposes the four small tokenizer/config members only as Git blob SHA-1.
No matching local cache or installed provider exists. Therefore their content
SHA-256 values cannot be known locally before reading the exact immutable
revision bytes. Treating Git blob SHA-1 as content SHA-256 or trusting the
first downloaded bytes without an independent identity check is forbidden.

## Phase A: identity-anchored candidate only

Phase A uses the same `COURSE_EMBEDDING_MODEL_DOWNLOAD=1` opt-in and the same
tested live-producer boundary, but only when the committed manifest carries the
exact `bootstrap-required` sentinel.

- Accept only the fixed Qdrant repository, revision
  `46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`, five exact member paths, declared
  sizes, four exact official Git blob object IDs, and the exact ONNX LFS
  SHA-256.
- Fetch immutable metadata and only the four small members. Verify each size
  and `SHA1("blob <size>\0" + bytes)` against its official object ID before
  calculating SHA-256. Do not fetch or execute the ONNX member in Phase A.
- Resolve the Windows x64/Python 3.12 binary-only dependency wheel set rooted at
  `fastembed==0.8.0`; record exact package versions, filenames, sizes, PyPI
  SHA-256 values, and fixed official artifact URLs. Source distributions and
  build hooks are forbidden.
- Write only an ignored, fixed bootstrap candidate/evidence artifact through a
  contained temporary file and atomic replace. It must include the immutable
  metadata digest, official identities, calculated SHA-256 values, runtime
  wheel lock, and its own canonical digest.
- Exit `3` with `MODEL_MANIFEST_BOOTSTRAP_REQUIRED`. Never import FastEmbed or
  ONNX Runtime, install a wheel, execute model bytes, promote a model/runtime
  cache, write or replace the sealed live receipt, or return success.
- Any identity, path, redirect, host, DNS/IP, size, member, wheel, or digest
  mismatch fails closed and preserves prior cache/receipt bytes.

The candidate is reviewed and copied into the committed manifest only with
`apply_patch`. Phase A bytes are not eligible for Phase B reuse.

## Phase B: final independent verification

After the manifest contains every model-file and runtime-wheel SHA-256, the
producer starts from a new empty quarantine and independently downloads the
five model members plus the exact locked wheels. It verifies path containment,
member exactness, size and SHA-256 before any promotion or installation.

- Install the provider runtime only from the verified local wheel set with
  `--no-index` and hash-required exact requirements into an ignored contained
  runtime directory.
- Atomically promote verified model/runtime directories, then re-open and
  re-hash every promoted member.
- Load FastEmbed only from that runtime and the verified
  `specific_model_path`; runtime network fallback is denied.
- With sockets denied, prove deterministic inference, 512 finite normalized
  values, transactional index processing, and query evidence.
- Strictly self-validate a temporary receipt before atomically sealing it.
  Failure never overwrites a prior sealed receipt.
- A second socket-denied replay must reproduce the declared cache/model/runtime
  identities with zero network.

Offline `course-composition`, `authentic-visuals`, and `all` gates never invoke
either phase and reject any set live opt-in.


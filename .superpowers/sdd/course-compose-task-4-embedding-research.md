# Course composition Task 4 embedding research

Date: 2026-07-17

Read-only research only. No model/package was downloaded or installed and no
runtime inference claim is made.

## Confirmed identities

- Package: `fastembed==0.8.0`, PyPI wheel
  `fastembed-0.8.0-py3-none-any.whl`, 116572 bytes, SHA-256
  `40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0`.
- FastEmbed source tag `v0.8.0` resolves to commit
  `6fa442b9603cd197c4b8cf19f072b3b9bbaac9b0`.
- Logical upstream model:
  `BAAI/bge-small-zh-v1.5@7999e1d3359715c523056ef9478215996d62a620`.
- Actual FastEmbed ONNX artifact repository:
  `Qdrant/bge-small-zh-v1.5@46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`.
- Runtime contract: 512 dimensions, maximum 512 tokens, normalized output,
  ONNX Runtime CPU-first certification target.

Primary sources:

- https://pypi.org/pypi/fastembed/0.8.0/json
- https://github.com/qdrant/fastembed/tree/v0.8.0
- https://github.com/qdrant/fastembed/blob/v0.8.0/fastembed/text/onnx_embedding.py#L83-L98
- https://github.com/qdrant/fastembed/blob/v0.8.0/fastembed/common/model_management.py#L204-L264
- https://huggingface.co/BAAI/bge-small-zh-v1.5
- https://huggingface.co/Qdrant/bge-small-zh-v1.5/tree/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59

## Artifact inventory known from official metadata

| Path | Bytes | Official metadata digest |
|---|---:|---|
| `model_optimized.onnx` | 94781076 | LFS SHA-256 `1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38` |
| `config.json` | 739 | Git blob SHA-1 `60938626ad1097a0c1a14be4f8340e32c714a056` |
| `tokenizer.json` | 439125 | Git blob SHA-1 `cdb3043fc938fc918c06e66cf704c2ba58f88747` |
| `tokenizer_config.json` | 367 | Git blob SHA-1 `3a59388f0fd1bd22dec2ce7902c1be8e1fb84107` |
| `special_tokens_map.json` | 125 | Git blob SHA-1 `a8b3208c2884c4efb86e49300fdd3dc877220cdf` |

The four non-LFS files still require SHA-256 calculation after the exact
revision is downloaded through the explicit live prerequisite. Official Hub
metadata provides their Git blob SHA-1, not content SHA-256.

## Implementation consequences

- FastEmbed 0.8.0 does not bind its normal `snapshot_download` call to the
  inspected repository revision. The platform downloader must fetch the exact
  immutable artifact revision, verify every file, atomically promote it, and
  load only `specific_model_path` with network fallback disabled.
- The manifest must record package wheel identity/hash, logical upstream
  revision, artifact revision, exact member list/hash/size, model dimension,
  encoding/normalization policy, Python/OS/architecture/provider, and the
  runtime fixture fingerprint.
- Reproducibility requires the complete resolved dependency wheel set and
  hashes, not only FastEmbed. In particular record compatible pinned NumPy and
  ONNX Runtime wheels; FastEmbed excludes ONNX Runtime 1.20.0, 1.24.0, and
  1.24.1.
- First support target is Windows x64 CPU. Python 3.12 wheel metadata indicates
  a viable path, but import/session/inference must be proven by the live gate.
  ARM64 and GPU/DirectML remain separate unsupported/unverified matrices.

Official metadata APIs for the live producer:

- Hugging Face: `GET /api/models/{repo}/revision/{sha}?blobs=true` or
  `HfApi.model_info(..., revision=sha, files_metadata=True)`.
- PyPI: `GET /pypi/{package}/{version}/json`.

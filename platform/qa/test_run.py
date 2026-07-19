from __future__ import annotations

import builtins
import importlib.util
import hashlib
import json
import math
import mmap
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


def _load_qa_module() -> ModuleType:
    module_path = Path(__file__).with_name("run.py")
    spec = importlib.util.spec_from_file_location("course_studio_qa_run", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qa = _load_qa_module()

VALID_TOKENS = """
:root {
  --color-page: #f7f8fa;
  --color-surface: #ffffff;
  --color-surface-muted: #f4f6f8;
  --color-text: #172033;
  --color-brand: #1463ff;
}
"""


def _write_course_composition_receipt(tmp_path: Path) -> dict[str, Any]:
    policy = {
        "schemaVersion": 1,
        "channel": "chrome",
        "productName": "Google Chrome",
        "productVersion": "150.0.7871.124",
        "fileVersion": "150.0.7871.124",
        "executableSha256": "a" * 64,
        "publisher": "CN=Google LLC",
        "allowedBasename": "chrome.exe",
    }
    policy_path = tmp_path / qa.COURSE_BROWSER_POLICY
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_bytes = json.dumps(policy).encode("utf-8")
    policy_path.write_bytes(policy_bytes)
    required = [
        "knowledge_import_start",
        "knowledge_review_resolve",
        "knowledge_card_publish",
        "knowledge_index",
        "course_compose",
        "course_outline_confirm",
        "chart_build",
        "visual_search",
        "visual_acquire",
        "visual_revalidate",
        "course_visual_attach",
        "course_validate",
    ]
    publish = {
        "type": "course_publish",
        "operationId": "publish-operation-1",
        "resultIds": {"courseVersionId": "course-v1"},
    }
    receipt = {
        "schemaVersion": 1,
        "status": "verified",
        "mode": "fixture-backed-loopback",
        "browserPolicySha256": hashlib.sha256(policy_bytes).hexdigest(),
        "operations": [
            *(
                {"type": item, "operationId": f"operation-{index}", "resultIds": {}}
                for index, item in enumerate(required)
            ),
            publish,
            dict(publish),
        ],
        "published": {
            "courseVersionId": "course-v1",
            "slideDeckId": "deck-v1",
            "runtimeManifestId": "runtime-v1",
            "runtimeManifestDigest": "b" * 64,
            "courseProjectionId": "projection-v1",
        },
        "checks": {
            "exactOperationReplay": True,
            "byteBoundReopen": True,
            "stagePresenterSharedProjection": True,
            "physicalDualScreenCertified": False,
            "liveNetworkAuthorizationCertified": False,
            "protectedSourceAccessed": False,
        },
    }
    receipt_path = tmp_path / qa.COURSE_COMPOSITION_RECEIPT
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    for screenshot in qa.COURSE_COMPOSITION_SCREENSHOTS:
        path = tmp_path / screenshot
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 1024)
    return receipt


def test_course_composition_and_authentic_visual_offline_receipts(tmp_path: Path) -> None:
    receipt = _write_course_composition_receipt(tmp_path)
    assert qa.run_course_composition_gate(tmp_path).ok
    authentic = qa.run_authentic_visuals_gate(tmp_path)
    assert authentic.ok
    assert authentic.details == "HISTORICAL RECEIPT VERIFIED — CURRENT NETWORK AUTHORIZATION NOT CERTIFIED"

    receipt["checks"]["liveNetworkAuthorizationCertified"] = True
    (tmp_path / qa.COURSE_COMPOSITION_RECEIPT).write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    assert not qa.run_course_composition_gate(tmp_path).ok


class _FakeEmbeddingHttpResponse:
    def __init__(
        self,
        *,
        status: int,
        chunks: list[bytes] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self._chunks = iter(chunks or [])
        self._headers = {key.casefold(): value for key, value in (headers or {}).items()}

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name.casefold(), default)

    def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")

    def close(self) -> None:
        return None

    def __enter__(self) -> "_FakeEmbeddingHttpResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _write_css(tmp_path: Path, extra: str = "") -> Path:
    css_path = tmp_path / "theme.css"
    css_path.write_text(f"{VALID_TOKENS}\n{extra}", encoding="utf-8")
    return css_path


def valid_demo_receipt() -> dict[str, Any]:
    digest = "a" * 64
    source_paths = [
        "AI.pptx",
        "AIGC实操 -数据分析.md",
        "AIGC实操-Prompt工程.md",
        "dataset/1-train.csv",
        "AIExcelData/ex-17-RFM.xlsx",
    ]
    metadata = {"byte_size": 100, "modified_ns": 123}
    return {
        "schema_version": 1,
        "command_version": "course-helper/demo@1",
        "status": "degraded",
        "root_id": "reference-demo",
        "manifest_digest": digest,
        "deep_read_source_count": 5,
        "hash_verified_source_count": 5,
        "inventory_root_count": 2,
        "inventory_integrity_scope": "metadata-only",
        "inventory_item_count": 7,
        "quarantined_extension_counts": {
            ".pth": 1,
            ".pt": 0,
            ".tmp": 0,
            ".whl": 0,
        },
        "source_integrity": [
            {
                "root_id": "reference-demo",
                "relative_path": path,
                "before_metadata": metadata,
                "before_sha256": digest,
                "after_metadata": metadata,
                "after_sha256": digest,
            }
            for path in source_paths
        ],
        "inventory_integrity": [
            {
                "root_id": "reference-demo",
                "relative_path": path,
                "integrity_scope": "metadata-only",
                "before_item_count": count,
                "before_metadata_digest": digest,
                "after_item_count": count,
                "after_metadata_digest": digest,
                "changed_item_count": 0,
            }
            for path, count in (("dataset", 4), ("AIExcelData", 3))
        ],
        "pptx_slide_chunks": 16,
        "pptx_chunks_with_notes": 16,
        "markdown_units": ["Prompt概论", "正确提问", "自行车共享需求"],
        "profiled_datasets": [
            "AIExcelData/ex-17-RFM.xlsx",
            "dataset/1-train.csv",
        ],
        "parser_versions": {
            "AI.pptx": "python-pptx@1.0.2",
            "AIGC实操 -数据分析.md": "markdown-it-py@2.2.0",
            "AIGC实操-Prompt工程.md": "markdown-it-py@2.2.0",
            "dataset/1-train.csv": "course-helper/dataset-profiler@1",
            "AIExcelData/ex-17-RFM.xlsx": "course-helper/dataset-profiler@1",
        },
        "object_digests": {
            key: digest
            for key in ("sources", "chunks", "visuals", "datasets", "cards", "evidence")
        },
        "checks": [
            {"code": code, "status": status, "message": "checked", "details": {}}
            for code, status in (
                ("deep-read-allowlist", "passed"),
                ("inventory-integrity-scope", "passed"),
                ("parser-digest-recomputation", "warning"),
                ("known-phrase-retrieval", "warning"),
                ("forbidden-source-write", "passed"),
            )
        ],
        "published_card_count": 12,
        "review_decision_count": 12,
        "retrievals": [
            {
                "query": query,
                "query_digest": digest,
                "hit_count": 1,
                "hit_version_ids": ["card-version"],
                "evidence_id": "evidence-id",
                "evidence_status": "degraded",
            }
            for query in ("人工智能", "自行车共享需求", "正确提问")
        ],
        "new_source_versions": 0,
        "new_card_count": 0,
        "new_evidence_count": 0,
        "duplicate_card_count": 0,
        "forbidden_source_writes": 0,
        "idempotence": {
            "pass_count": 2,
            "first_pass": {
                "new_source_versions": 5,
                "new_card_count": 12,
                "new_evidence_count": 20,
                "duplicate_card_count": 0,
                "forbidden_source_writes": 0,
            },
            "second_pass": {
                "new_source_versions": 0,
                "new_card_count": 0,
                "new_evidence_count": 0,
                "duplicate_card_count": 0,
                "forbidden_source_writes": 0,
            },
            "verified": True,
        },
    }


def write_demo_receipt(repo_root: Path, receipt: dict[str, Any]) -> Path:
    path = repo_root / "platform/helper/evidence/reference-demo-receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path


def write_helper_design_qa(repo_root: Path, text: str) -> Path:
    path = repo_root / "platform/helper/design-qa.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_check_result_exposes_gate_status() -> None:
    result = qa.CheckResult(name="example", ok=True, details="ready")

    assert result.name == "example"
    assert result.ok is True
    assert result.details == "ready"


def test_scan_light_theme_rejects_dark_surface_and_allows_dark_text(
    tmp_path: Path,
) -> None:
    dark_surface = _write_css(tmp_path, ".card { background: #111827; }")
    result = qa.scan_light_theme(dark_surface)
    assert result.ok is False
    assert "#111827" in result.details

    dark_surface.write_text(
        f"{VALID_TOKENS}\n.copy {{ color: #172033; }}",
        encoding="utf-8",
    )
    assert qa.scan_light_theme(dark_surface).ok is True


@pytest.mark.parametrize(
    "gradient",
    ["LiNeAr-GrAdIeNt", "RADIAL-GRADIENT", "Conic-Gradient"],
)
def test_scan_light_theme_rejects_mixed_case_gradients(
    tmp_path: Path,
    gradient: str,
) -> None:
    css_path = _write_css(
        tmp_path,
        f".card {{ background: {gradient}(white, blue); }}",
    )

    result = qa.scan_light_theme(css_path)

    assert result.ok is False
    assert "gradient" in result.details.lower()


def test_scan_light_theme_ignores_forbidden_values_inside_comments(
    tmp_path: Path,
) -> None:
    css_path = _write_css(
        tmp_path,
        "/* .old { background: LINEAR-GRADIENT(#000, rgb(15, 23, 42)); } */",
    )

    assert qa.scan_light_theme(css_path).ok is True


def test_light_tokens_require_exact_values(tmp_path: Path) -> None:
    assert qa.check_light_tokens(_write_css(tmp_path)).ok is True

    wrong = VALID_TOKENS.replace("--color-brand: #1463ff", "--color-brand: #1463fe")
    css_path = tmp_path / "wrong.css"
    css_path.write_text(wrong, encoding="utf-8")

    result = qa.check_light_tokens(css_path)

    assert result.ok is False
    assert "--color-brand" in result.details
    assert "#1463ff" in result.details


def test_light_tokens_ignore_comments_and_prefixed_property_names(
    tmp_path: Path,
) -> None:
    css_path = tmp_path / "misleading.css"
    commented_tokens = VALID_TOKENS.replace(":root {", "/* :root {").replace(
        "}\n", "} */\n"
    )
    prefixed_tokens = VALID_TOKENS.replace("--color-", "--fallback--color-")
    css_path.write_text(commented_tokens + prefixed_tokens, encoding="utf-8")

    result = qa.check_light_tokens(css_path)

    assert result.ok is False
    assert "--color-page" in result.details


@pytest.mark.parametrize(
    "declaration",
    [
        "background: rgb(15, 23, 42)",
        "background-color: RGB(2 6 23 / 80%)",
        "fill: rgba(17, 24, 39, 0.5)",
    ],
)
def test_scan_light_theme_rejects_very_dark_rgb_surfaces(
    tmp_path: Path,
    declaration: str,
) -> None:
    css_path = _write_css(tmp_path, f".card {{ {declaration}; }}")

    result = qa.scan_light_theme(css_path)

    assert result.ok is False
    assert "rgb" in result.details.lower()


def test_protected_path_guard_normalizes_paths_without_false_siblings() -> None:
    changed = [
        r"Course_AIProduct\lesson.md",
        "course_aiproduct/fixture.json",
        r".\DATASET\rows.csv",
        "references/guide.md",
        r"REFERENCES\nested\note.md",
        "AGENTS.md",
        "platform/web/src/app/App.tsx",
        "dataset-tools/parser.py",
        "references-old/readme.md",
        "docs/AGENTS.md",
    ]

    assert qa.protected_path_violations(changed) == changed[:6]


def test_protected_path_guard_never_opens_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_opened(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("path matching must never read repository content")

    monkeypatch.setattr(Path, "open", fail_if_opened)
    monkeypatch.setattr(Path, "read_text", fail_if_opened)

    assert qa.protected_path_violations(["dataset/item.csv"]) == [
        "dataset/item.csv"
    ]


def test_committed_changed_paths_uses_git_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="platform/web/src/app/App.tsx\0dataset/fixture.csv\0",
            stderr="",
        )

    monkeypatch.setattr(qa.subprocess, "run", fake_run)

    assert qa.committed_changed_paths(tmp_path, "baseline") == [
        "platform/web/src/app/App.tsx",
        "dataset/fixture.csv",
    ]
    assert captured["args"] == [
        "git",
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--diff-filter=ACDMR",
        "baseline...HEAD",
        "--",
    ]
    assert captured["cwd"] == tmp_path
    assert captured["shell"] is False
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


@pytest.mark.parametrize(
    ("git_path_metadata", "expected_paths", "expected_violation"),
    [
        pytest.param(
            "dataset/deleted.csv\0",
            ["dataset/deleted.csv"],
            "dataset/deleted.csv",
            id="deleted-protected-path",
        ),
        pytest.param(
            "platform/web/old.csv\0dataset/imported.csv\0",
            ["platform/web/old.csv", "dataset/imported.csv"],
            "dataset/imported.csv",
            id="rename-into-protected-path",
        ),
        pytest.param(
            "references/guide.md\0platform/web/guide.md\0",
            ["references/guide.md", "platform/web/guide.md"],
            "references/guide.md",
            id="rename-out-of-protected-path",
        ),
    ],
)
def test_committed_changed_paths_checks_deletions_and_both_rename_sides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_path_metadata: str,
    expected_paths: list[str],
    expected_violation: str,
) -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, stdout=git_path_metadata, stderr="")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)

    changed_paths = qa.committed_changed_paths(tmp_path, "baseline")

    assert changed_paths == expected_paths
    assert qa.protected_path_violations(changed_paths) == [expected_violation]


def test_committed_changed_paths_propagates_git_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="bad revision")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="bad revision"):
        qa.committed_changed_paths(tmp_path)


def test_protected_gate_reports_git_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("git executable missing")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)

    result = qa._protected_paths_gate(tmp_path)

    assert result.ok is False
    assert "could not start git" in result.details


def test_source_image_hash_passes_and_fails(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"approved-image")
    expected = "4b7ee2cb26471f9cd024f68f8a5c7ab1d3b22d95937fd7afea4e88a2ebacd998"

    assert qa.check_source_image(image, expected).ok is True

    result = qa.check_source_image(image, "0" * 64)
    assert result.ok is False
    assert expected.upper() in result.details


def test_workflow_labels_are_the_required_utf8_values() -> None:
    assert qa.WORKFLOW_LABELS == (
        "导入资料",
        "生成课程",
        "编辑验证",
        "双屏授课",
    )


def test_workflow_order_passes_and_fails(tmp_path: Path) -> None:
    labels = list(qa.WORKFLOW_LABELS)
    workflow = tmp_path / "WorkflowHeader.tsx"
    workflow.write_text(" -> ".join(labels), encoding="utf-8")
    assert qa.check_workflow_order(workflow).ok is True

    workflow.write_text(" -> ".join(reversed(labels)), encoding="utf-8")
    result = qa.check_workflow_order(workflow)
    assert result.ok is False
    assert "order" in result.details.lower()


def test_design_qa_is_pending_before_milestone(tmp_path: Path) -> None:
    result = qa.check_design_qa(tmp_path)

    assert result.ok is True
    assert "PENDING" in result.details


def test_design_qa_rejects_a_directory_at_the_report_path(tmp_path: Path) -> None:
    (tmp_path / qa.DESIGN_QA_REPORT).mkdir(parents=True)

    result = qa.check_design_qa(tmp_path)

    assert result.ok is False
    assert "not a file" in result.details


def test_design_qa_requires_final_result_and_evidence(tmp_path: Path) -> None:
    report = tmp_path / "platform" / "web" / "design-qa.md"
    report.parent.mkdir(parents=True)
    report.write_text("checked\n\nfinal result: passed\n", encoding="utf-8")

    missing = qa.check_design_qa(tmp_path)
    assert missing.ok is False
    assert "design-qa-edit.png" in missing.details

    evidence_payloads = {
        qa.SOURCE_IMAGE: b"reference",
        Path("platform/web/evidence/design-qa-edit.png"): b"implementation",
        Path("platform/web/evidence/design-qa-comparison.png"): b"comparison",
        Path("platform/web/evidence/teaching-stage.png"): b"stage",
        Path("platform/web/evidence/teaching-presenter.png"): b"presenter",
        Path("platform/web/evidence/browser-flow-fixture.md"): b"fixture",
    }
    for relative, payload in evidence_payloads.items():
        evidence = tmp_path / relative
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_bytes(payload)

    def entry(relative: Path) -> dict[str, str]:
        return {
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(evidence_payloads[relative]).hexdigest().upper(),
        }

    receipt = {
        "schemaVersion": 1,
        "currentCommitBeforeEvidenceCommit": "a" * 40,
        "reference": entry(qa.SOURCE_IMAGE),
        "evidence": {
            "implementation": entry(Path("platform/web/evidence/design-qa-edit.png")),
            "comparison": entry(Path("platform/web/evidence/design-qa-comparison.png")),
            "stage": entry(Path("platform/web/evidence/teaching-stage.png")),
            "presenter": entry(Path("platform/web/evidence/teaching-presenter.png")),
            "fixture": entry(Path("platform/web/evidence/browser-flow-fixture.md")),
        },
        "physicalDualScreenCertified": False,
        "protectedChangedPathGuard": {"forbiddenPathCount": 0},
        "commands": [{"command": "verify", "exitCode": 0}],
        "designQa": "passed",
    }
    receipt_path = tmp_path / "platform/web/evidence/acceptance-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    assert qa.check_design_qa(tmp_path).ok is True

    (tmp_path / "platform/web/evidence/design-qa-edit.png").write_bytes(b"tampered")
    tampered = qa.check_design_qa(tmp_path)
    assert tampered.ok is False
    assert "SHA-256" in tampered.details

    report.write_text("final result: failed\n", encoding="utf-8")
    wrong_final = qa.check_design_qa(tmp_path)
    assert wrong_final.ok is False
    assert "final result: passed" in wrong_final.details


def test_valid_knowledge_demo_receipt_is_strictly_accepted(tmp_path: Path) -> None:
    receipt_path = write_demo_receipt(tmp_path, valid_demo_receipt())

    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is True
    assert result.name == "knowledge demo receipt"
    assert "5" in result.details


@pytest.mark.parametrize(
    "case",
    [
        "top-level-extra",
        "nested-extra",
        "source-write",
        "source-hash",
        "source-whitelist",
        "inventory-digest",
        "inventory-count",
        "inventory-roots",
        "parser-version",
        "parser-key",
        "object-digest",
        "object-key",
        "quarantine-key",
        "retrieval-hit",
        "retrieval-status",
        "failed-check",
        "missing-check",
        "second-pass",
        "first-pass",
        "idempotence-flag",
        "top-level-delta",
        "absolute-path-leak",
    ],
)
def test_knowledge_demo_receipt_rejects_tampering(
    tmp_path: Path,
    case: str,
) -> None:
    receipt = valid_demo_receipt()
    if case == "top-level-extra":
        receipt["extra"] = True
    elif case == "nested-extra":
        receipt["source_integrity"][0]["extra"] = True
    elif case == "source-write":
        receipt["forbidden_source_writes"] = 1
    elif case == "source-hash":
        receipt["source_integrity"][0]["after_sha256"] = "b" * 64
    elif case == "source-whitelist":
        receipt["source_integrity"][0]["relative_path"] = "other.pptx"
    elif case == "inventory-digest":
        receipt["inventory_integrity"][0]["after_metadata_digest"] = "b" * 64
    elif case == "inventory-count":
        receipt["inventory_item_count"] = 8
    elif case == "inventory-roots":
        receipt["inventory_integrity"][0]["relative_path"] = "other"
    elif case == "parser-version":
        receipt["parser_versions"]["AI.pptx"] = "python-pptx@0"
    elif case == "parser-key":
        receipt["parser_versions"]["extra.md"] = "markdown-it-py@2.2.0"
    elif case == "object-digest":
        receipt["object_digests"]["cards"] = "not-a-digest"
    elif case == "object-key":
        del receipt["object_digests"]["evidence"]
    elif case == "quarantine-key":
        receipt["quarantined_extension_counts"][".bin"] = 1
    elif case == "retrieval-hit":
        receipt["retrievals"][0]["hit_count"] = 0
    elif case == "retrieval-status":
        receipt["retrievals"][0]["evidence_status"] = "verified"
    elif case == "failed-check":
        receipt["checks"][0]["status"] = "failed"
    elif case == "missing-check":
        receipt["checks"] = receipt["checks"][1:]
    elif case == "second-pass":
        receipt["idempotence"]["second_pass"]["new_card_count"] = 1
    elif case == "first-pass":
        receipt["idempotence"]["first_pass"]["new_source_versions"] = 4
    elif case == "idempotence-flag":
        receipt["idempotence"]["verified"] = False
    elif case == "top-level-delta":
        receipt["new_evidence_count"] = 1
    elif case == "absolute-path-leak":
        receipt["checks"][0]["message"] = "D:/secret/reference/root"

    write_demo_receipt(tmp_path, receipt)
    result = qa.check_knowledge_demo_receipt(tmp_path, require_canonical=False)

    assert result.ok is False
    assert "D:/secret/reference/root" not in result.details
    assert str(tmp_path) not in result.details


def test_receipt_validation_reads_only_the_given_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_path = write_demo_receipt(tmp_path, valid_demo_receipt()).resolve()
    original_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def guarded_read_bytes(path: Path, *args: object, **kwargs: object) -> bytes:
        resolved = path.resolve()
        reads.append(resolved)
        if resolved != receipt_path:
            raise AssertionError("receipt validation must not read source payloads")
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    assert qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    ).ok is True
    assert reads == [receipt_path]


def test_receipt_rejects_bool_schema_version(tmp_path: Path) -> None:
    receipt = valid_demo_receipt()
    receipt["schema_version"] = True
    receipt_path = write_demo_receipt(tmp_path, receipt)

    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is False


def test_receipt_rejects_unhashable_markdown_unit_without_raising(
    tmp_path: Path,
) -> None:
    receipt = valid_demo_receipt()
    receipt["markdown_units"][0] = {}
    receipt_path = write_demo_receipt(tmp_path, receipt)

    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is False


@pytest.mark.parametrize(
    "case",
    ["source-pair", "inventory-pair", "object-evidence", "query-digest"],
)
def test_canonical_anchor_rejects_structurally_valid_digest_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    receipt = valid_demo_receipt()
    receipt_path = write_demo_receipt(tmp_path, receipt)
    canonical = hashlib.sha256(receipt_path.read_bytes()).hexdigest().upper()
    monkeypatch.setattr(
        qa, "KNOWLEDGE_DEMO_RECEIPT_SHA256", canonical, raising=False
    )
    assert qa.check_knowledge_demo_receipt(tmp_path, receipt_path).ok is True

    replacement = "b" * 64
    if case == "source-pair":
        receipt["source_integrity"][0]["before_sha256"] = replacement
        receipt["source_integrity"][0]["after_sha256"] = replacement
    elif case == "inventory-pair":
        receipt["inventory_integrity"][0]["before_metadata_digest"] = replacement
        receipt["inventory_integrity"][0]["after_metadata_digest"] = replacement
    elif case == "object-evidence":
        receipt["object_digests"]["evidence"] = replacement
    elif case == "query-digest":
        receipt["retrievals"][0]["query_digest"] = replacement
    write_demo_receipt(tmp_path, receipt)

    assert qa.check_knowledge_demo_receipt(tmp_path, receipt_path).ok is False


def test_retrieval_hit_count_must_match_version_ids(tmp_path: Path) -> None:
    receipt = valid_demo_receipt()
    receipt["retrievals"][0]["hit_count"] = 2
    receipt_path = write_demo_receipt(tmp_path, receipt)

    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is False


def test_canonical_anchor_matches_the_committed_receipt() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    receipt_path = repo_root / qa.KNOWLEDGE_DEMO_RECEIPT

    actual = hashlib.sha256(receipt_path.read_bytes()).hexdigest().upper()

    assert actual == qa.KNOWLEDGE_DEMO_RECEIPT_SHA256


def test_retrieval_hit_version_ids_must_be_unique(tmp_path: Path) -> None:
    receipt = valid_demo_receipt()
    receipt["retrievals"][0]["hit_count"] = 2
    receipt["retrievals"][0]["hit_version_ids"] = ["same", "same"]
    receipt_path = write_demo_receipt(tmp_path, receipt)

    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is False


@pytest.mark.parametrize(
    "case",
    [
        "required-count-float",
        "top-level-zero-bool",
        "source-metadata-list",
        "source-count-bool",
        "inventory-changed-bool",
        "inventory-count-dict",
        "dataset-null",
        "parser-list",
        "parser-value-dict",
        "object-digest-null",
        "check-status-list",
        "check-details-null",
        "retrieval-ids-dict",
        "retrieval-count-bool",
        "idempotence-pass-float",
        "idempotence-first-pass-null",
    ],
)
def test_receipt_malformed_types_fail_closed(
    tmp_path: Path,
    case: str,
) -> None:
    receipt = valid_demo_receipt()
    if case == "required-count-float":
        receipt["deep_read_source_count"] = 5.0
    elif case == "top-level-zero-bool":
        receipt["new_card_count"] = False
    elif case == "source-metadata-list":
        receipt["source_integrity"][0]["before_metadata"] = []
    elif case == "source-count-bool":
        receipt["source_integrity"][0]["before_metadata"]["byte_size"] = True
    elif case == "inventory-changed-bool":
        receipt["inventory_integrity"][0]["changed_item_count"] = False
    elif case == "inventory-count-dict":
        receipt["inventory_integrity"][0]["before_item_count"] = {}
    elif case == "dataset-null":
        receipt["profiled_datasets"][0] = None
    elif case == "parser-list":
        receipt["parser_versions"] = []
    elif case == "parser-value-dict":
        receipt["parser_versions"]["AI.pptx"] = {}
    elif case == "object-digest-null":
        receipt["object_digests"]["evidence"] = None
    elif case == "check-status-list":
        receipt["checks"][0]["status"] = []
    elif case == "check-details-null":
        receipt["checks"][0]["details"] = None
    elif case == "retrieval-ids-dict":
        receipt["retrievals"][0]["hit_version_ids"] = [{}]
    elif case == "retrieval-count-bool":
        receipt["retrievals"][0]["hit_count"] = True
    elif case == "idempotence-pass-float":
        receipt["idempotence"]["pass_count"] = 2.0
    elif case == "idempotence-first-pass-null":
        receipt["idempotence"]["first_pass"] = None

    receipt_path = write_demo_receipt(tmp_path, receipt)
    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is False
    assert str(tmp_path) not in result.details


@pytest.mark.parametrize(
    "failure",
    [TypeError, ValueError, OverflowError, RecursionError],
)
def test_receipt_validation_exceptions_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
) -> None:
    receipt_path = write_demo_receipt(tmp_path, valid_demo_receipt())

    def fail_validation(_receipt: object) -> object:
        raise failure("secret D:/reference/value")

    monkeypatch.setattr(qa, "_validate_knowledge_demo_receipt", fail_validation)

    result = qa.check_knowledge_demo_receipt(
        tmp_path, receipt_path, require_canonical=False
    )

    assert result.ok is False
    assert "secret" not in result.details
    assert "D:/reference/value" not in result.details


def test_helper_design_qa_requires_truthful_dual_screen_boundary(
    tmp_path: Path,
) -> None:
    write_helper_design_qa(
        tmp_path,
        "knowledge demo checked\nphysical dual-screen: NOT CERTIFIED\nfinal result: passed\n",
    )
    assert qa.check_helper_design_qa(tmp_path).ok is True

    write_helper_design_qa(
        tmp_path,
        "physical dual-screen: certified by simulation\nfinal result: passed\n",
    )
    uncertified = qa.check_helper_design_qa(tmp_path)
    assert uncertified.ok is False
    assert "NOT CERTIFIED" in uncertified.details

    write_helper_design_qa(
        tmp_path,
        "physical dual-screen: NOT CERTIFIED\nfinal result: failed\n",
    )
    wrong_final = qa.check_helper_design_qa(tmp_path)
    assert wrong_final.ok is False
    assert "final result: passed" in wrong_final.details


def test_run_command_propagates_stdout_stderr_and_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["cwd"] == tmp_path
        assert kwargs["shell"] is False
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return subprocess.CompletedProcess(args, 7, stdout="partial output", stderr="boom")

    monkeypatch.setattr(qa.subprocess, "run", fake_run)

    result = qa.run_command("broken gate", ["tool", "check"], tmp_path)

    assert result.ok is False
    assert "exit 7" in result.details
    assert "partial output" in result.details
    assert "boom" in result.details


def test_compact_output_is_single_line_and_console_safe() -> None:
    compact = qa._compact_output("\x1b[32m✓ passed\x1b[0m\nnext")

    assert "\x1b" not in compact
    assert "\n" not in compact
    assert "\\u2713" in compact
    assert "passed | next" in compact


def test_compact_output_bounds_noise_while_preserving_head_and_tail() -> None:
    compact = qa._compact_output(
        "command-start\n" + ("x" * 2_000) + "\nfinal-summary",
        limit=120,
    )

    assert len(compact) <= 120
    assert compact.startswith("command-start")
    assert compact.endswith("final-summary")
    assert "output truncated" in compact


def test_npm_executable_uses_the_windows_command_shim() -> None:
    assert qa.npm_executable("win32") == "npm.cmd"
    assert qa.npm_executable("linux") == "npm"


def test_run_focused_reports_receipt_and_helper_design_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qa,
        "run_command",
        lambda *_args: qa.CheckResult("python QA tests", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "scan_light_theme",
        lambda *_args: qa.CheckResult("light theme", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "check_light_tokens",
        lambda *_args: qa.CheckResult("light tokens", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "check_workflow_order",
        lambda *_args: qa.CheckResult("workflow order", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "check_required_domain_files",
        lambda *_args: qa.CheckResult("durable domain", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "check_source_image",
        lambda *_args: qa.CheckResult("source image", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "_protected_paths_gate",
        lambda *_args: qa.CheckResult("protected paths", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "check_design_qa",
        lambda *_args: qa.CheckResult("design QA", True, "PENDING"),
    )
    monkeypatch.setattr(
        qa,
        "check_knowledge_demo_receipt",
        lambda *_args: qa.CheckResult("knowledge demo receipt", True, "ok"),
    )
    monkeypatch.setattr(
        qa,
        "check_helper_design_qa",
        lambda *_args: qa.CheckResult("helper design QA", True, "ok"),
    )

    results = qa.run_focused(tmp_path)

    assert [result.name for result in results] == [
        "python QA tests",
        "light theme",
        "light tokens",
        "workflow order",
        "durable domain",
        "source image",
        "protected paths",
        "design QA",
        "knowledge demo receipt",
        "helper design QA",
        "course composition",
        "authentic visuals",
    ]


def test_run_all_aggregates_focused_and_every_npm_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    focused = [qa.CheckResult("focused", False, "failed")]
    commands: list[list[str]] = []

    monkeypatch.setattr(qa, "run_focused", lambda _root: focused)
    monkeypatch.setattr(
        qa,
        "run_knowledge_demo_gate",
        lambda _root, require_source_root: qa.CheckResult(
            "knowledge demo", not require_source_root, "checked"
        ),
    )

    def fake_command(name: str, args: list[str], cwd: Path) -> object:
        assert cwd == tmp_path
        commands.append(args)
        return qa.CheckResult(name, args[-1] != "build", "checked")

    monkeypatch.setattr(qa, "run_command", fake_command)

    results = qa.run_all(tmp_path)

    assert results[:1] == focused
    assert commands == [
        [
            sys.executable,
            "-m",
            "pytest",
            "platform/helper/tests",
            "-m",
            "not reference_demo and not network_visual and not model_download",
            "-q",
        ],
        [qa.npm_executable(), "--prefix", "platform/web", "test", "--", "--run"],
        [qa.npm_executable(), "--prefix", "platform/web", "run", "typecheck"],
        [qa.npm_executable(), "--prefix", "platform/web", "run", "build"],
        [qa.npm_executable(), "--prefix", "platform/web", "run", "test:e2e"],
    ]
    assert [result.ok for result in results] == [False, True, True, True, True, False, True]


def test_all_gate_includes_helper_contracts_and_reference_demo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    monkeypatch.setattr(qa, "run_focused", lambda _root: [])
    monkeypatch.setattr(
        qa,
        "run_command",
        lambda name, _args, _cwd: qa.CheckResult(name=name, ok=True, details="stubbed"),
    )

    names = [check.name for check in qa.run_all(tmp_path)]

    assert "helper tests" in names
    assert "knowledge demo" in names


def test_explicit_knowledge_demo_gate_requires_registered_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)

    result = qa.run_knowledge_demo_gate(tmp_path, require_source_root=True)

    assert result.ok is False
    assert "COURSE_REFERENCE_ROOT" in result.details


def test_optional_knowledge_demo_gate_is_truthfully_not_certified_without_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)

    result = qa.run_knowledge_demo_gate(tmp_path, require_source_root=False)

    assert result.ok is True
    assert result.name == "knowledge demo"
    assert result.details == "NOT CERTIFIED: COURSE_REFERENCE_ROOT unset"


def test_knowledge_demo_gate_uses_fixed_temp_outputs_and_cleans_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_root = "D:/secret/reference-root"
    monkeypatch.setenv("COURSE_REFERENCE_ROOT", secret_root)
    captured: dict[str, Any] = {}
    receipt_bytes = json.dumps(valid_demo_receipt()).encode("utf-8")
    monkeypatch.setattr(
        qa,
        "KNOWLEDGE_DEMO_RECEIPT_SHA256",
        hashlib.sha256(receipt_bytes).hexdigest().upper(),
    )

    def fake_run_command(name: str, args: list[str], cwd: Path) -> object:
        captured.update(name=name, args=args, cwd=cwd)
        database = Path(args[args.index("--database") + 1])
        evidence = Path(args[args.index("--evidence") + 1])
        captured.update(database=database, evidence=evidence, temp_dir=evidence.parent)
        database.write_bytes(b"temporary database")
        evidence.write_bytes(receipt_bytes)
        return qa.CheckResult(name=name, ok=True, details="exit 0")

    monkeypatch.setattr(qa, "run_command", fake_run_command)

    result = qa.run_knowledge_demo_gate(tmp_path, require_source_root=True)

    assert result.ok is True
    assert captured["name"] == "knowledge demo"
    assert captured["cwd"] == tmp_path / "platform/helper"
    assert captured["args"][:3] == [sys.executable, "-m", "course_helper.demo"]
    assert captured["args"][-1] == "--verify-idempotence"
    assert secret_root not in captured["args"]
    assert secret_root not in result.details
    assert not captured["temp_dir"].exists()


def test_knowledge_demo_gate_cleans_temp_outputs_when_command_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COURSE_REFERENCE_ROOT", "D:/secret/reference-root")
    captured: dict[str, Path] = {}

    def fake_run_command(name: str, args: list[str], _cwd: Path) -> object:
        evidence = Path(args[args.index("--evidence") + 1])
        captured["temp_dir"] = evidence.parent
        evidence.write_text("partial", encoding="utf-8")
        return qa.CheckResult(name=name, ok=False, details="exit 7")

    monkeypatch.setattr(qa, "run_command", fake_run_command)

    result = qa.run_knowledge_demo_gate(tmp_path, require_source_root=True)

    assert result.ok is False
    assert result.details == "exit 7"
    assert not captured["temp_dir"].exists()


def test_demo_receipt_must_report_zero_reference_writes(tmp_path: Path) -> None:
    receipt = valid_demo_receipt()
    receipt["forbidden_source_writes"] = 1
    write_demo_receipt(tmp_path, receipt)

    result = qa.check_knowledge_demo_receipt(tmp_path, require_canonical=False)

    assert result.ok is False


@pytest.mark.parametrize(
    ("mode", "ok", "expected_exit"),
    [("focused", True, 0), ("focused", False, 1), ("all", True, 0), ("all", False, 1)],
)
def test_cli_modes_return_aggregate_status(
    mode: str,
    ok: bool,
    expected_exit: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = [qa.CheckResult("stub gate", ok, "stub detail")]
    monkeypatch.setattr(qa, "run_focused", lambda _root: result)
    monkeypatch.setattr(qa, "run_all", lambda _root: result)

    assert qa.main([mode]) == expected_exit
    output = capsys.readouterr().out
    assert ("PASS" if ok else "FAIL") in output
    assert "stub gate" in output


@pytest.mark.parametrize(("ok", "expected_exit"), [(True, 0), (False, 1)])
def test_cli_knowledge_demo_requires_the_explicit_gate(
    ok: bool,
    expected_exit: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[bool] = []

    def fake_gate(_root: Path, require_source_root: bool) -> object:
        calls.append(require_source_root)
        return qa.CheckResult("knowledge demo", ok, "stub detail")

    monkeypatch.setattr(qa, "run_knowledge_demo_gate", fake_gate)

    assert qa.main(["knowledge-demo"]) == expected_exit
    assert calls == [True]
    assert ("PASS" if ok else "FAIL") in capsys.readouterr().out


def test_cli_rejects_invalid_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert qa.main(["invalid"]) == 2
    usage = capsys.readouterr().err
    assert "focused" in usage
    assert "knowledge-demo" in usage


def test_print_results_keeps_each_gate_on_one_physical_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    qa._print_results(
        [qa.CheckResult("stub gate", False, "first detail\nsecond detail")]
    )

    output_lines = capsys.readouterr().out.splitlines()
    assert output_lines == ["FAIL stub gate: first detail | second detail"]


def test_embedding_model_live_requires_only_its_exact_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa, "_repo_root", lambda: tmp_path)
    monkeypatch.delenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", raising=False)
    assert qa.main(["embedding-model-live", "--receipt", str(tmp_path / "receipt.json")]) == 2
    assert "EMBEDDING_MODEL_OPT_IN_REQUIRED" in capsys.readouterr().err

    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", "1")
    monkeypatch.setenv("COURSE_NETWORK_VISUAL_TEST", "1")
    assert qa.main(["embedding-model-live", "--receipt", str(tmp_path / "receipt.json")]) == 2
    assert "EMBEDDING_MODEL_OPT_IN_CONFLICT" in capsys.readouterr().err

    monkeypatch.setenv("COURSE_NETWORK_VISUAL_TEST", "")
    assert qa.main(["embedding-model-live", "--receipt", str(tmp_path / "receipt.json")]) == 2
    assert "EMBEDDING_MODEL_OPT_IN_CONFLICT" in capsys.readouterr().err


def test_network_visual_live_requires_exact_opt_in_path_and_no_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa, "_repo_root", lambda: tmp_path)
    expected = tmp_path / qa.NETWORK_VISUAL_RECEIPT
    called = False

    def forbidden(*_args: object) -> int:
        nonlocal called
        called = True
        raise AssertionError("producer ran before preflight")

    monkeypatch.setattr(qa, "produce_network_visual_live", forbidden)
    monkeypatch.delenv("COURSE_NETWORK_VISUAL_TEST", raising=False)
    assert qa.main(["network-visual-acquisition-live", "--receipt", str(expected)]) == 2
    assert "NETWORK_VISUAL_OPT_IN_REQUIRED" in capsys.readouterr().err

    monkeypatch.setenv("COURSE_NETWORK_VISUAL_TEST", "1")
    assert qa.main(["network-visual-acquisition-live", "--receipt", str(tmp_path / "wrong.json")]) == 3
    assert "NETWORK_VISUAL_PATH_POLICY_MISMATCH" in capsys.readouterr().err

    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", "")
    assert qa.main(["network-visual-acquisition-live", "--receipt", str(expected)]) == 2
    assert "NETWORK_VISUAL_OPT_IN_CONFLICT" in capsys.readouterr().err
    assert called is False


def test_network_visual_live_validates_before_seal_and_never_certifies_course(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = tmp_path / qa.NETWORK_VISUAL_RECEIPT
    events: list[str] = []
    receipt = {"status": "verified", "coursePublicationVerified": False}

    class Transaction:
        def commit(self) -> object:
            events.append("commit")
            return receipt

        def finalize(self) -> object:
            events.append("finalize")
            return receipt

        def rollback(self) -> None:
            events.append("rollback")

    class FakeLive:
        class NetworkVisualLiveError(RuntimeError):
            pass

        @staticmethod
        def build_live_receipt(work_root: Path) -> object:
            events.append("build")
            assert not work_root.exists()
            work_root.mkdir()
            return receipt

        @staticmethod
        def write_temporary_receipt(value: object, directory: Path) -> Path:
            events.append("write-temp")
            assert value is receipt
            path = directory / "receipt.tmp"
            path.write_text("temporary", encoding="utf-8")
            return path

        @staticmethod
        def validate_receipt(path: Path) -> object:
            events.append("validate-temp" if path.name == "receipt.tmp" else "validate-sealed")
            return receipt

        @staticmethod
        def seal_receipt(temporary: Path, sealed: Path, *, defer_commit: bool) -> object:
            events.append("seal")
            assert events[-2] == "validate-temp"
            assert defer_commit is True
            sealed.write_text("sealed", encoding="utf-8")
            temporary.unlink()
            return Transaction()

    monkeypatch.setattr(qa, "_network_visual_live_module", lambda _root: FakeLive)
    assert qa.produce_network_visual_live(tmp_path, expected) == 0
    assert events == [
        "build",
        "write-temp",
        "validate-temp",
        "seal",
        "commit",
        "validate-sealed",
        "finalize",
    ]
    output = capsys.readouterr().out
    assert "NETWORK_VISUAL_LIVE_VERIFIED" in output
    assert "COURSE PUBLICATION NOT CERTIFIED" in output


def test_network_visual_live_failure_restores_prior_sealed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = tmp_path / qa.NETWORK_VISUAL_RECEIPT
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"prior-sealed")

    class LiveError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code

    class Transaction:
        def commit(self) -> object:
            return {"status": "verified"}

        def finalize(self) -> object:
            raise AssertionError("must not finalize")

        def rollback(self) -> None:
            expected.write_bytes(b"prior-sealed")

    class FakeLive:
        NetworkVisualLiveError = LiveError

        @staticmethod
        def build_live_receipt(work_root: Path) -> object:
            work_root.mkdir()
            return {"status": "verified"}

        @staticmethod
        def write_temporary_receipt(_value: object, directory: Path) -> Path:
            path = directory / "receipt.tmp"
            path.write_bytes(b"new")
            return path

        @staticmethod
        def validate_receipt(path: Path) -> object:
            if path == expected:
                raise LiveError("NETWORK_VISUAL_RECEIPT_INVALID")
            return {"status": "verified"}

        @staticmethod
        def seal_receipt(temporary: Path, sealed: Path, *, defer_commit: bool) -> object:
            assert defer_commit is True
            sealed.write_bytes(temporary.read_bytes())
            temporary.unlink()
            return Transaction()

    monkeypatch.setattr(qa, "_network_visual_live_module", lambda _root: FakeLive)
    assert qa.produce_network_visual_live(tmp_path, expected) == 5
    assert expected.read_bytes() == b"prior-sealed"
    assert capsys.readouterr().err.strip() == "NETWORK_VISUAL_RECEIPT_INVALID"


@pytest.mark.parametrize(
    ("symbol", "exit_code"),
    (
        ("NETWORK_VISUAL_ACQUISITION_FAILED", 4),
        ("NETWORK_VISUAL_RECEIPT_INVALID", 5),
        ("NETWORK_VISUAL_PROTECTED_BOUNDARY", 6),
    ),
)
def test_network_visual_live_maps_only_fixed_failure_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    symbol: str,
    exit_code: int,
) -> None:
    expected = tmp_path / qa.NETWORK_VISUAL_RECEIPT

    class LiveError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code

    class FakeLive:
        NetworkVisualLiveError = LiveError

        @staticmethod
        def build_live_receipt(_work_root: Path) -> object:
            raise LiveError(symbol)

    monkeypatch.setattr(qa, "_network_visual_live_module", lambda _root: FakeLive)
    assert qa.produce_network_visual_live(tmp_path, expected) == exit_code
    assert capsys.readouterr().err.strip() == symbol


@pytest.mark.parametrize("mode", ("focused", "all", "knowledge-demo"))
@pytest.mark.parametrize("opt_in", ("1", ""))
def test_offline_modes_reject_network_visual_opt_in_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    opt_in: str,
) -> None:
    monkeypatch.setenv("COURSE_NETWORK_VISUAL_TEST", opt_in)
    monkeypatch.setattr(
        qa,
        "run_focused",
        lambda *_args: (_ for _ in ()).throw(AssertionError("offline dispatch ran")),
    )
    monkeypatch.setattr(
        qa,
        "run_all",
        lambda *_args: (_ for _ in ()).throw(AssertionError("offline dispatch ran")),
    )
    monkeypatch.setattr(
        qa,
        "run_knowledge_demo_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("offline dispatch ran")
        ),
    )

    assert qa.main([mode]) == 2
    assert capsys.readouterr().err.strip() == "OFFLINE_GATE_LIVE_OPT_IN_SET"


def test_embedding_manifest_preflight_rejects_nonfixed_or_reparse_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = tmp_path / qa.EMBEDDING_MODEL_MANIFEST
    expected.parent.mkdir(parents=True)
    expected.write_text("{}", encoding="utf-8")

    with pytest.raises(qa.EmbeddingLiveFailure) as wrong:
        qa._embedding_manifest_path_preflight(tmp_path, tmp_path / "other.json")
    assert wrong.value.symbol == "MODEL_MANIFEST_PATH_POLICY_MISMATCH"

    real_lstat = qa.os.lstat

    def marked(path: object) -> object:
        info = real_lstat(path)
        if Path(path).absolute() == expected.absolute():
            return SimpleNamespace(st_file_attributes=0x400)
        return info

    monkeypatch.setattr(qa.os, "lstat", marked)
    with pytest.raises(qa.EmbeddingLiveFailure) as reparse:
        qa._embedding_manifest_path_preflight(tmp_path, expected)
    assert reparse.value.symbol == "EMBEDDING_MODEL_PROTECTED_BOUNDARY"


@pytest.mark.parametrize(
    "address",
    (
        "127.0.0.1",
        "10.0.0.8",
        "169.254.1.2",
        "::1",
        "fc00::1",
    ),
)
def test_embedding_phase_a_transport_rejects_nonpublic_dns_before_open(
    address: str,
) -> None:
    opened = False

    def resolver(
        _host: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    def forbidden_open(*_args: object, **_kwargs: object) -> object:
        nonlocal opened
        opened = True
        raise AssertionError("private address must not be opened")

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa._embedding_https_fetch(
            "https://pypi.org/pypi/fastembed/json",
            url_policy=lambda url, depth: (
                url == "https://pypi.org/pypi/fastembed/json" and depth == 0
            ),
            max_bytes=1024,
            failure_symbol="EMBEDDING_MODEL_RESOLUTION_FAILED",
            resolver=resolver,
            opener=forbidden_open,
        )

    assert caught.value.symbol == "EMBEDDING_MODEL_RESOLUTION_FAILED"
    assert caught.value.exit_code == 4
    assert opened is False


def test_embedding_phase_a_transport_rejects_redirect_host_drift() -> None:
    calls: list[str] = []

    def resolver(
        _host: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    def opener(url: str, _addresses: object) -> _FakeEmbeddingHttpResponse:
        calls.append(url)
        return _FakeEmbeddingHttpResponse(
            status=307,
            headers={"Location": "https://example.invalid/alternate"},
        )

    fixed = "https://huggingface.co/fixed/member.json"
    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa._embedding_https_fetch(
            fixed,
            url_policy=lambda url, depth: url == fixed and depth == 0,
            max_bytes=1024,
            failure_symbol="EMBEDDING_MODEL_ACQUISITION_FAILED",
            resolver=resolver,
            opener=opener,
        )

    assert caught.value.symbol == "EMBEDDING_MODEL_ACQUISITION_FAILED"
    assert calls == [fixed]


def test_embedding_phase_b_artifact_policy_pins_each_immutable_redirect() -> None:
    revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    small = (
        "https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/"
        f"{revision}/config.json?download=true"
    )
    small_redirect = (
        "https://huggingface.co/api/resolve-cache/models/"
        f"Qdrant/bge-small-zh-v1.5/{revision}/config.json"
        "?download=true&etag=%2260938626ad1097a0c1a14be4f8340e32c714a056%22"
    )
    assert qa._phase_b_artifact_url_policy(small, small, 0) is True
    assert qa._phase_b_artifact_url_policy(small, small_redirect, 1) is True
    assert (
        qa._phase_b_artifact_url_policy(
            small,
            small_redirect.replace("60938626", "00000000"),
            1,
        )
        is False
    )
    assert (
        qa._phase_b_artifact_url_policy(
            small,
            small_redirect.replace("huggingface.co", "example.invalid"),
            1,
        )
        is False
    )
    assert qa._phase_b_artifact_url_policy(small, small_redirect, 2) is False

    onnx = (
        "https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/"
        f"{revision}/model_optimized.onnx?download=true"
    )
    xet = (
        "https://us.aws.cdn.hf.co/xet-bridge-us/676a9a3040be8b8a518ccd4e/"
        "9eedf0673c9aa300264fe51ef8df7c22e09538e5512f8132f3c2b65ef8143076"
        "?response-content-disposition=attachment&Expires=1&Key-Pair-Id=K1"
        "&Policy=P&Signature=S&user_id=U&X-Xet-Cas-Uid=C"
    )
    assert qa._phase_b_artifact_url_policy(onnx, onnx, 0) is True
    assert qa._phase_b_artifact_url_policy(onnx, xet, 1) is True
    assert (
        qa._phase_b_artifact_url_policy(
            onnx,
            xet.replace("us.aws.cdn.hf.co", "cdn.example.invalid"),
            1,
        )
        is False
    )
    assert qa._phase_b_artifact_url_policy(onnx, xet + "&extra=1", 1) is False

    wheel = "https://files.pythonhosted.org/packages/aa/bb/runtime.whl"
    assert qa._phase_b_artifact_url_policy(wheel, wheel, 0) is True
    assert qa._phase_b_artifact_url_policy(wheel, wheel + "?redirect=1", 1) is False


def test_embedding_phase_a_transport_enforces_streamed_byte_bound() -> None:
    response = _FakeEmbeddingHttpResponse(
        status=200,
        chunks=[b"12345678", b"abcdef", b"must-not-be-read"],
        headers={"Content-Type": "application/octet-stream"},
    )
    reads_before = response._chunks

    def resolver(
        _host: str,
        port: int,
        **_kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa._embedding_https_fetch(
            "https://pypi.org/pypi/fastembed/json",
            url_policy=lambda _url, _depth: True,
            max_bytes=10,
            failure_symbol="EMBEDDING_MODEL_RESOLUTION_FAILED",
            resolver=resolver,
            opener=lambda *_args: response,
        )

    assert caught.value.symbol == "EMBEDDING_MODEL_RESOLUTION_FAILED"
    assert response._chunks is reads_before


def test_embedding_metadata_transport_maps_only_typed_safe_reasons() -> None:
    fixed = "https://huggingface.co/fixed/metadata.json"
    public_records = [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", 443),
        )
    ]
    symbols = {
        "dns": "EMBEDDING_MODEL_METADATA_DNS_FAILED",
        "connect": "EMBEDDING_MODEL_METADATA_CONNECT_FAILED",
        "tls": "EMBEDDING_MODEL_METADATA_TLS_FAILED",
        "http-policy": "EMBEDDING_MODEL_METADATA_HTTP_POLICY_FAILED",
    }
    cases = (
        (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                socket.gaierror("sanitized dns fixture")
            ),
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("opener ran after DNS failure")
            ),
            "EMBEDDING_MODEL_METADATA_DNS_FAILED",
        ),
        (
            lambda *_args, **_kwargs: public_records,
            lambda *_args: (_ for _ in ()).throw(TimeoutError("fixture timeout")),
            "EMBEDDING_MODEL_METADATA_CONNECT_FAILED",
        ),
        (
            lambda *_args, **_kwargs: public_records,
            lambda *_args: (_ for _ in ()).throw(qa.ssl.SSLError("fixture tls")),
            "EMBEDDING_MODEL_METADATA_TLS_FAILED",
        ),
        (
            lambda *_args, **_kwargs: public_records,
            lambda *_args: _FakeEmbeddingHttpResponse(status=403),
            "EMBEDDING_MODEL_METADATA_HTTP_POLICY_FAILED",
        ),
        (
            lambda *_args, **_kwargs: public_records,
            lambda *_args: (_ for _ in ()).throw(RuntimeError("fixture unknown")),
            "EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED",
        ),
    )

    for resolver, opener, expected_symbol in cases:
        with pytest.raises(qa.EmbeddingLiveFailure) as caught:
            qa._embedding_https_fetch(
                fixed,
                url_policy=lambda url, depth: url == fixed and depth == 0,
                max_bytes=1024,
                failure_symbol="EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED",
                reason_symbols=symbols,
                resolver=resolver,
                opener=opener,
            )
        assert caught.value.symbol == expected_symbol
        assert caught.value.exit_code == 4
        assert "fixture" not in caught.value.symbol.casefold()
        assert "https" not in caught.value.symbol.casefold()


@pytest.mark.parametrize(
    "transport_symbol",
    (
        "EMBEDDING_MODEL_METADATA_DNS_FAILED",
        "EMBEDDING_MODEL_METADATA_CONNECT_FAILED",
        "EMBEDDING_MODEL_METADATA_TLS_FAILED",
        "EMBEDDING_MODEL_METADATA_HTTP_POLICY_FAILED",
        "EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED",
    ),
)
def test_embedding_phase_a_preserves_sanitized_metadata_transport_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport_symbol: str,
) -> None:
    class InjectedModelError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code
            super().__init__(code)

    class FakeModelCache:
        PINNED_MODEL_METADATA_URL = "https://huggingface.co/fixed/metadata.json"
        ModelCacheError = InjectedModelError

        @staticmethod
        def run_bootstrap_phase(_manifest: object, **kwargs: object) -> None:
            kwargs["fetch_model_metadata"](FakeModelCache.PINNED_MODEL_METADATA_URL)

    monkeypatch.setattr(
        qa,
        "_embedding_https_fetch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            qa.EmbeddingLiveFailure(transport_symbol, 4)
        ),
    )
    context = qa.EmbeddingManifestContext(
        phase="bootstrap-required",
        manifest_digest="a" * 64,
        model_cache=FakeModelCache,
        manifest=SimpleNamespace(package=object()),
    )

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.run_embedding_bootstrap_phase(
            tmp_path,
            context,
            tmp_path / qa.EMBEDDING_BOOTSTRAP_CANDIDATE,
            tmp_path / qa.EMBEDDING_QUARANTINE_ROOT,
        )

    assert caught.value.symbol == transport_symbol
    assert caught.value.exit_code == 4


def test_embedding_phase_a_command_wires_fixed_metadata_members_and_runtime_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    metadata_url = (
        "https://huggingface.co/api/models/Qdrant/bge-small-zh-v1.5/revision/"
        f"{revision}?blobs=true"
    )
    members = {
        name: (
            "https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/"
            f"{revision}/{name}?download=true"
        )
        for name in sorted(
            {
                "config.json",
                "special_tokens_map.json",
                "tokenizer.json",
                "tokenizer_config.json",
            }
        )
    }
    captured: dict[str, object] = {}

    class FakeModelCache:
        PINNED_MODEL_METADATA_URL = metadata_url

        @staticmethod
        def load_bootstrap_manifest(_path: Path) -> object:
            return SimpleNamespace(package=object())

        @staticmethod
        def resolve_runtime_wheels_from_pypi(
            package: object,
            *,
            fetch_metadata: object,
        ) -> tuple[str, dict[str, str], str]:
            captured["resolved_package"] = package
            assert callable(fetch_metadata)
            assert fetch_metadata("https://pypi.org/pypi/fastembed/json") == b"pypi"
            return "runtime", {"fastembed": "a" * 64}, "b" * 64

        @staticmethod
        def run_bootstrap_phase(
            manifest: object,
            **kwargs: object,
        ) -> None:
            captured.update(kwargs)
            assert kwargs["approved_root"] == tmp_path
            assert kwargs["fetch_model_metadata"](metadata_url) == b"metadata"
            fetch_member = kwargs["fetch_member"]
            assert callable(fetch_member)
            for name, url in members.items():
                assert fetch_member(url, len(name)) == name.encode("ascii")
            resolve_runtime = kwargs["resolve_runtime"]
            assert callable(resolve_runtime)
            assert resolve_runtime(manifest.package)[0] == "runtime"

    calls: list[tuple[str, str, int | None]] = []

    def fetch(
        url: str,
        *,
        url_policy: object,
        max_bytes: int,
        failure_symbol: str,
        expected_size: int | None = None,
        **_kwargs: object,
    ) -> bytes:
        assert callable(url_policy) and url_policy(url, 0) is True
        calls.append((url, failure_symbol, expected_size))
        if url == metadata_url:
            return b"metadata"
        if url == "https://pypi.org/pypi/fastembed/json":
            return b"pypi"
        name = next(name for name, member_url in members.items() if member_url == url)
        assert max_bytes == len(name) == expected_size
        return name.encode("ascii")

    monkeypatch.setattr(qa, "_embedding_https_fetch", fetch)
    context = qa.EmbeddingManifestContext(
        phase="bootstrap-required",
        manifest_digest="a" * 64,
        model_cache=FakeModelCache,
        manifest=SimpleNamespace(package=object()),
    )

    qa.run_embedding_bootstrap_phase(
        tmp_path,
        context,
        tmp_path / qa.EMBEDDING_BOOTSTRAP_CANDIDATE,
        tmp_path / qa.EMBEDDING_QUARANTINE_ROOT,
    )

    assert [url for url, _symbol, _size in calls] == [
        metadata_url,
        *members.values(),
        "https://pypi.org/pypi/fastembed/json",
    ]
    assert all("model_optimized.onnx" not in url for url, _symbol, _size in calls)


@pytest.mark.parametrize(
    ("stage", "expected_symbol"),
    (
        ("metadata-transport", "EMBEDDING_MODEL_METADATA_TRANSPORT_FAILED"),
        ("metadata-identity", "EMBEDDING_MODEL_METADATA_IDENTITY_FAILED"),
        ("member-transport", "EMBEDDING_MODEL_MEMBER_TRANSPORT_FAILED"),
        ("member-identity", "EMBEDDING_MODEL_MEMBER_IDENTITY_FAILED"),
    ),
)
def test_embedding_phase_a_failures_emit_only_sanitized_stage_symbol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    expected_symbol: str,
) -> None:
    revision = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
    metadata_url = (
        "https://huggingface.co/api/models/Qdrant/bge-small-zh-v1.5/revision/"
        f"{revision}?blobs=true"
    )
    member_url = (
        "https://huggingface.co/Qdrant/bge-small-zh-v1.5/resolve/"
        f"{revision}/config.json?download=true"
    )

    class InjectedModelError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code
            super().__init__(code)

    class FakeModelCache:
        PINNED_MODEL_METADATA_URL = metadata_url
        ModelCacheError = InjectedModelError

        @staticmethod
        def run_bootstrap_phase(_manifest: object, **kwargs: object) -> None:
            if stage == "metadata-transport":
                kwargs["fetch_model_metadata"](metadata_url)
            elif stage == "metadata-identity":
                raise InjectedModelError("MODEL_BOOTSTRAP_METADATA_INVALID")
            elif stage == "member-transport":
                kwargs["fetch_member"](member_url, 1)
            else:
                raise InjectedModelError("MODEL_BOOTSTRAP_MEMBER_MISMATCH")

    def fail_transport(
        _url: str,
        *,
        failure_symbol: str,
        **_kwargs: object,
    ) -> bytes:
        raise qa.EmbeddingLiveFailure(failure_symbol, 4)

    monkeypatch.setattr(qa, "_embedding_https_fetch", fail_transport)
    context = qa.EmbeddingManifestContext(
        phase="bootstrap-required",
        manifest_digest="a" * 64,
        model_cache=FakeModelCache,
        manifest=SimpleNamespace(package=object()),
    )

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.run_embedding_bootstrap_phase(
            tmp_path,
            context,
            tmp_path / qa.EMBEDDING_BOOTSTRAP_CANDIDATE,
            tmp_path / qa.EMBEDDING_QUARANTINE_ROOT,
        )

    assert caught.value.symbol == expected_symbol
    assert caught.value.exit_code == 4
    assert "http" not in caught.value.symbol.casefold()
    assert str(tmp_path) not in caught.value.symbol


def test_embedding_model_live_rejects_noncanonical_receipt_path_before_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", "1")
    monkeypatch.delenv("COURSE_NETWORK_VISUAL_TEST", raising=False)
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    called = False

    def forbidden(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("producer must not run")

    monkeypatch.setattr(qa, "produce_embedding_model_live", forbidden, raising=False)

    assert qa.main(["embedding-model-live", "--receipt", str(tmp_path / "wrong.json")]) == 3
    assert called is False
    assert "EMBEDDING_MODEL_PATH_POLICY_MISMATCH" in capsys.readouterr().err


def _copy_bootstrap_manifest(repo_root: Path) -> Path:
    source_root = Path(__file__).resolve().parents[2]
    source = source_root / qa.EMBEDDING_MODEL_MANIFEST
    destination = repo_root / qa.EMBEDDING_MODEL_MANIFEST
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["phase"] = "bootstrap-required"
    for member in payload["files"]:
        if member["officialIdentity"]["kind"] == "git-blob-sha1":
            member["sha256"] = None
    payload["runtime"] = {
        "python": "3.12",
        "os": "windows",
        "architecture": "x86_64",
        "wheels": "bootstrap-required",
    }
    payload.pop("aggregateDigest", None)
    payload["aggregateDigest"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    source_module = source_root / "platform/helper/course_helper/model_cache.py"
    destination_module = repo_root / "platform/helper/course_helper/model_cache.py"
    destination_module.parent.mkdir(parents=True, exist_ok=True)
    destination_module.write_bytes(source_module.read_bytes())
    return destination


def test_embedding_manifest_loader_ignores_poisoned_sys_modules_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _copy_bootstrap_manifest(tmp_path)
    source_root = Path(__file__).resolve().parents[2]
    live = qa._embedding_live_module(source_root)
    poison = SimpleNamespace(
        __file__=str(tmp_path / "outside/model_cache.py"),
        load_bootstrap_manifest_bytes=lambda _raw: (_ for _ in ()).throw(
            AssertionError("poisoned module reused")
        ),
    )
    monkeypatch.setitem(sys.modules, "course_helper.model_cache", poison)

    authority = live.LiveEmbeddingAuthority.load()
    try:
        context = qa.embedding_manifest_phase(manifest, authority)
    finally:
        authority.close()

    assert context.phase == "bootstrap-required"
    assert len(context.manifest_digest) == 64


def test_embedding_manifest_is_parsed_by_the_single_live_authority_module(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / qa.EMBEDDING_MODEL_MANIFEST
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text('{"phase":"complete"}', encoding="utf-8")

    class ExactManifest:
        aggregate_digest = "a" * 64

    exact_manifest = ExactManifest()

    class AuthorityModelCache:
        ModelManifest = ExactManifest

        @staticmethod
        def load_model_manifest_bytes(raw: bytes) -> object:
            assert raw == b'{"phase":"complete"}'
            return exact_manifest

    authority = SimpleNamespace(model_cache_module=AuthorityModelCache)

    context = qa.embedding_manifest_phase(manifest_path, authority)

    assert context.authority is authority
    assert context.model_cache is AuthorityModelCache
    assert type(context.manifest) is AuthorityModelCache.ModelManifest
    assert context.manifest is exact_manifest


def _valid_embedding_receipt(manifest_digest: str = "a" * 64) -> dict[str, Any]:
    model_files = [
        ("config.json", 739, "1" * 64),
        (
            "model_optimized.onnx",
            94781076,
            "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
        ),
        ("special_tokens_map.json", 125, "2" * 64),
        ("tokenizer.json", 439125, "3" * 64),
        ("tokenizer_config.json", 367, "4" * 64),
    ]
    receipt: dict[str, Any] = {
        "schemaVersion": 1,
        "producer": "course-helper/embedding-model-live@1",
        "status": "verified",
        "policyId": "course-studio-rrf-v1",
        "manifestDigest": manifest_digest,
        "model": {
            "id": "BAAI/bge-small-zh-v1.5",
            "revision": "7999e1d3359715c523056ef9478215996d62a620",
            "artifactRepository": "Qdrant/bge-small-zh-v1.5",
            "artifactRevision": "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
            "dimension": 512,
            "encodingPolicy": "utf8-nfkc-no-prefix",
        },
        "provider": {"name": "fastembed", "version": "0.8.0"},
        "modelFiles": [
            {"path": path, "size": size, "sha256": digest}
            for path, size, digest in model_files
        ],
        "runtime": {
            "python": "3.12",
            "os": "windows",
            "architecture": "x86_64",
            "runtimeDigest": "5" * 64,
            "wheelSetDigest": "6" * 64,
            "generationDigest": "7" * 64,
            "wheels": [
                {
                    "name": "fastembed",
                    "version": "0.8.0",
                    "filename": "fastembed-0.8.0-py3-none-any.whl",
                    "size": 116572,
                    "sha256": "40bee672657574a1009e35ec50030a55f2b426842cb011845379817641bbbbd0",
                },
                {
                    "name": "onnxruntime",
                    "version": "1.23.2",
                    "filename": "onnxruntime-1.23.2-cp312-cp312-win_amd64.whl",
                    "size": 100,
                    "sha256": "8" * 64,
                },
            ],
        },
        "cacheDigest": "9" * 64,
        "fixtureFingerprint": "a" * 64,
        "indexSnapshot": {
            "id": "snapshot-live-1",
            "digest": "b" * 64,
            "candidateDigest": "c" * 64,
            "publishedDigest": "d" * 64,
        },
        "retrieval": {
            "queryDigest": "e" * 64,
            "filteredCandidateDigest": "f" * 64,
            "snapshotDigest": "b" * 64,
            "rrfK": 60,
            "hits": [
                {
                    "cardVersionId": "card-live-1",
                    "ftsRank": 1,
                    "semanticRank": 1,
                    "score": 2 / 61,
                }
            ],
        },
        "osNetworkIsolation": {
            "status": "not-certified",
            "scope": "trusted-hash-locked-cpython-runtime",
            "pythonAuditHook": "verified",
            "cpythonSocketGuards": "verified",
            "nativeWinsockCoverage": "not-certified",
        },
        "zeroNetworkReplayDigest": "2" * 64,
        "zeroWriteProof": {
            "scope": "verified-generation-tree",
            "status": "write-denied",
            "nativeGlobalCoverage": "not-certified",
            "evidenceDigest": "1" * 64,
        },
        "checks": [
            {"code": code, "status": "passed"}
            for code in (
                "model-members-verified",
                "runtime-wheel-closure",
                "specific-model-path",
                "cpython-socket-denied-inference",
                "index-snapshot-consistent",
                "hybrid-retrieval",
                "cpython-socket-denied-replay",
                "generation-tree-write-barrier",
            )
        ],
        "startedAt": "2026-07-17T00:00:00+00:00",
        "finishedAt": "2026-07-17T00:00:01+00:00",
    }
    receipt["receiptDigest"] = hashlib.sha256(
        json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return receipt


def _resign_embedding_receipt(receipt: dict[str, Any]) -> None:
    receipt.pop("receiptDigest", None)
    receipt["receiptDigest"] = hashlib.sha256(
        json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _expected_embedding_manifest(receipt: dict[str, Any]) -> object:
    model = receipt["model"]
    runtime = receipt["runtime"]
    return SimpleNamespace(
        aggregate_digest=receipt["manifestDigest"],
        package=SimpleNamespace(name="fastembed", version="0.8.0"),
        model=SimpleNamespace(
            id=model["id"],
            revision=model["revision"],
            artifact_repository=model["artifactRepository"],
            artifact_revision=model["artifactRevision"],
            dimension=model["dimension"],
            encoding_policy=model["encodingPolicy"],
        ),
        files=tuple(
            SimpleNamespace(
                path=item["path"],
                size=item["size"],
                sha256=item["sha256"],
                artifact_url=f"https://huggingface.co/fixed/{item['path']}",
            )
            for item in receipt["modelFiles"]
        ),
        runtime=SimpleNamespace(
            python=runtime["python"],
            os=runtime["os"],
            architecture=runtime["architecture"],
            wheels=tuple(
                SimpleNamespace(
                    name=item["name"],
                    version=item["version"],
                    filename=item["filename"],
                    size=item["size"],
                    sha256=item["sha256"],
                    artifact_url=f"https://files.pythonhosted.org/fixed/{item['filename']}",
                )
                for item in runtime["wheels"]
            ),
        ),
    )


def test_embedding_receipt_requires_truthful_native_winsock_non_certification(
    tmp_path: Path,
) -> None:
    receipt = _valid_embedding_receipt()
    _resign_embedding_receipt(receipt)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    manifest = _expected_embedding_manifest(receipt)

    validated = qa.validate_embedding_model_receipt(
        path,
        expected_manifest_digest=receipt["manifestDigest"],
        expected_manifest=manifest,
    )
    assert validated["osNetworkIsolation"]["status"] == "not-certified"
    assert validated["osNetworkIsolation"]["scope"] == (
        "trusted-hash-locked-cpython-runtime"
    )
    assert validated["osNetworkIsolation"]["nativeWinsockCoverage"] == "not-certified"
    assert validated["zeroNetworkReplayDigest"] == "2" * 64
    assert "cpythonSocketDeniedReplayDigest" not in validated
    assert "zero-network-replay" not in {
        item["code"] for item in validated["checks"]
    }

    receipt["osNetworkIsolation"]["nativeWinsockCoverage"] = "certified"
    _resign_embedding_receipt(receipt)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.validate_embedding_model_receipt(
            path,
            expected_manifest_digest=receipt["manifestDigest"],
            expected_manifest=manifest,
        )
    assert caught.value.symbol == "EMBEDDING_MODEL_RECEIPT_INVALID"


def test_embedding_receipt_manifest_binding_is_required_not_optional(
    tmp_path: Path,
) -> None:
    receipt = _valid_embedding_receipt()
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(TypeError):
        qa.validate_embedding_model_receipt(
            path,
            expected_manifest_digest=receipt["manifestDigest"],
        )


@pytest.mark.parametrize(
    ("started", "finished"),
    (
        ("2026-07-17T00:00:00Z", "2026-07-17T00:00:01+00:00"),
        ("2026-07-17T00:00:00+08:00", "2026-07-17T00:00:01+00:00"),
        ("2026-07-17T00:00:02+00:00", "2026-07-17T00:00:01+00:00"),
    ),
)
def test_embedding_receipt_validator_requires_ordered_canonical_utc_timestamps(
    tmp_path: Path,
    started: str,
    finished: str,
) -> None:
    receipt = _valid_embedding_receipt()
    manifest = _expected_embedding_manifest(receipt)
    receipt["startedAt"] = started
    receipt["finishedAt"] = finished
    _resign_embedding_receipt(receipt)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.validate_embedding_model_receipt(
            path,
            expected_manifest_digest=receipt["manifestDigest"],
            expected_manifest=manifest,
        )
    assert caught.value.symbol == "EMBEDDING_MODEL_RECEIPT_INVALID"


@pytest.mark.parametrize("case", ("duplicate-key", "nan", "infinity", "overflow"))
def test_embedding_receipt_rejects_noncanonical_json_before_digest_trust(
    tmp_path: Path,
    case: str,
) -> None:
    receipt = _valid_embedding_receipt()
    manifest = _expected_embedding_manifest(receipt)
    if case in {"nan", "infinity"}:
        receipt["retrieval"]["hits"][0]["score"] = (
            math.nan if case == "nan" else math.inf
        )
        receipt.pop("receiptDigest")
        receipt["receiptDigest"] = hashlib.sha256(
            json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    raw = json.dumps(receipt, separators=(",", ":"))
    if case == "duplicate-key":
        raw = raw.replace('"status":"verified"', '"status":"forged","status":"verified"', 1)
    elif case == "overflow":
        raw = raw.replace(str(2 / 61), "1e999", 1)
    path = tmp_path / "receipt.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.validate_embedding_model_receipt(
            path,
            expected_manifest_digest=receipt["manifestDigest"],
            expected_manifest=manifest,
        )

    assert caught.value.symbol == "EMBEDDING_MODEL_RECEIPT_INVALID"


def test_legacy_embedding_receipt_validator_rejects_linked_path(
    tmp_path: Path,
) -> None:
    receipt = _valid_embedding_receipt()
    manifest = _expected_embedding_manifest(receipt)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    os.link(path, tmp_path / "receipt-hardlink.json")

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.validate_embedding_model_receipt(
            path,
            expected_manifest_digest=receipt["manifestDigest"],
            expected_manifest=manifest,
        )

    assert caught.value.symbol == "EMBEDDING_MODEL_RECEIPT_INVALID"


def test_resigned_receipt_cannot_change_any_manifest_bound_field(
    tmp_path: Path,
) -> None:
    baseline = _valid_embedding_receipt()
    manifest = _expected_embedding_manifest(baseline)
    mutations: list[tuple[str, Any]] = []
    for key in (
        "id",
        "revision",
        "artifactRepository",
        "artifactRevision",
        "encodingPolicy",
    ):
        mutations.append((f"model.{key}", ("model", key, "changed")))
    mutations.append(("model.dimension", ("model", "dimension", 513)))
    for index, item in enumerate(baseline["modelFiles"]):
        mutations.extend(
            (
                (f"file[{index}].path", ("modelFiles", index, "path", f"changed-{index}.json")),
                (f"file[{index}].size", ("modelFiles", index, "size", item["size"] + 1)),
                (f"file[{index}].sha256", ("modelFiles", index, "sha256", "f" * 64)),
            )
        )
    for key, value in (
        ("python", "3.13"),
        ("os", "other"),
        ("architecture", "other"),
    ):
        mutations.append((f"runtime.{key}", ("runtime", key, value)))
    for index, item in enumerate(baseline["runtime"]["wheels"]):
        mutations.extend(
            (
                (f"wheel[{index}].name", ("runtime", "wheels", index, "name", f"changed-{index}")),
                (f"wheel[{index}].version", ("runtime", "wheels", index, "version", "99.0")),
                (f"wheel[{index}].filename", ("runtime", "wheels", index, "filename", f"changed-{index}.whl")),
                (f"wheel[{index}].size", ("runtime", "wheels", index, "size", item["size"] + 1)),
                (f"wheel[{index}].sha256", ("runtime", "wheels", index, "sha256", "f" * 64)),
            )
        )
    mutations.extend(
        (
            ("provider.name", ("provider", "name", "changed")),
            ("provider.version", ("provider", "version", "99.0")),
            ("modelFiles.order", ("modelFiles", "reverse")),
            ("modelFiles.count", ("modelFiles", "drop")),
            ("wheels.order", ("runtime", "wheels", "reverse")),
            ("wheels.count", ("runtime", "wheels", "drop")),
        )
    )
    path = tmp_path / "receipt.json"

    for label, mutation in mutations:
        receipt = json.loads(json.dumps(baseline))
        if mutation[-1] == "reverse":
            target: Any = receipt
            for key in mutation[:-1]:
                target = target[key]
            target.reverse()
        elif mutation[-1] == "drop":
            target = receipt
            for key in mutation[:-1]:
                target = target[key]
            target.pop()
        else:
            target = receipt
            for key in mutation[:-2]:
                target = target[key]
            target[mutation[-2]] = mutation[-1]
        _resign_embedding_receipt(receipt)
        path.write_text(json.dumps(receipt), encoding="utf-8")
        with pytest.raises(qa.EmbeddingLiveFailure, match="EMBEDDING_MODEL_RECEIPT_INVALID"):
            qa.validate_embedding_model_receipt(
                path,
                expected_manifest_digest=baseline["manifestDigest"],
                expected_manifest=manifest,
            )


def test_phase_b_receipt_cannot_resign_a_model_hash_different_from_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _valid_embedding_receipt()
    manifest = _expected_embedding_manifest(expected)
    forged = json.loads(json.dumps(expected))
    forged["modelFiles"][0]["sha256"] = "f" * 64
    _resign_embedding_receipt(forged)

    manifest_path = tmp_path / qa.EMBEDDING_MODEL_MANIFEST
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}", encoding="utf-8")
    temporary = tmp_path / qa.EMBEDDING_QUARANTINE_ROOT / "phase-b" / "receipt.tmp"
    temporary.parent.mkdir(parents=True)
    temporary.write_text(json.dumps(forged), encoding="utf-8")
    sealed = tmp_path / qa.EMBEDDING_MODEL_RECEIPT
    sealed.parent.mkdir(parents=True)
    sealed.write_bytes(b"prior-sealed-receipt")
    class FakeLiveError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code
            super().__init__(code)

    class FakeAuthority:
        model_cache_module = object()

        def __enter__(self) -> "FakeAuthority":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    authority = FakeAuthority()

    class FakeAuthorityType:
        @classmethod
        def load(cls) -> FakeAuthority:
            return authority

    class FakeLive:
        LiveEmbeddingAuthority = FakeAuthorityType
        EmbeddingLiveError = FakeLiveError

        @staticmethod
        def seal_receipt(*_args: object, **_kwargs: object) -> object:
            try:
                qa.validate_embedding_model_receipt(
                    temporary,
                    expected_manifest_digest=expected["manifestDigest"],
                    expected_manifest=manifest,
                )
            except qa.EmbeddingLiveFailure as error:
                raise FakeLiveError(error.symbol) from error
            raise AssertionError("forged receipt was accepted")

    context = qa.EmbeddingManifestContext(
        phase="complete",
        manifest_digest=expected["manifestDigest"],
        model_cache=authority.model_cache_module,
        manifest=manifest,
        authority=authority,
    )
    artifacts = qa.EmbeddingFinalPhaseArtifacts(
        temporary_receipt=temporary,
        final_result=SimpleNamespace(quarantine_root=temporary.parent),
        expectation=object(),
        first_pipeline={},
        replay_pipeline={},
    )
    monkeypatch.setattr(qa, "_embedding_live_module", lambda _root: FakeLive)
    monkeypatch.setattr(
        qa,
        "embedding_manifest_phase",
        lambda _path, _authority: context,
    )
    monkeypatch.setattr(
        qa,
        "run_embedding_final_phase",
        lambda *_args, **_kwargs: artifacts,
    )

    assert qa.produce_embedding_model_live(tmp_path, sealed) == 5
    assert sealed.read_bytes() == b"prior-sealed-receipt"


def test_phase_b_dispatch_never_reads_phase_a_candidate_or_stale_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / qa.EMBEDDING_BOOTSTRAP_CANDIDATE
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"phase-a-candidate-must-not-be-reused")
    quarantine = tmp_path / qa.EMBEDDING_QUARANTINE_ROOT
    quarantine.mkdir(parents=True)
    stale = quarantine / "phase-a-stale.bin"
    stale.write_bytes(b"phase-a-quarantine-must-not-be-reused")
    forbidden = {candidate.absolute(), stale.absolute()}
    forbidden_directories = {candidate.parent.absolute(), quarantine.absolute()}
    forbidden_reads: list[Path] = []
    forbidden_enumerations: list[Path] = []
    forbidden_mmaps: list[int] = []
    original_builtin_open = builtins.open
    original_path_open = Path.open
    original_path_iterdir = Path.iterdir
    original_os_open = qa.os.open
    original_os_scandir = qa.os.scandir
    original_os_listdir = qa.os.listdir
    original_mmap = mmap.mmap

    def check_read(path: object, mode: str) -> None:
        if "r" not in mode and "+" not in mode:
            return
        try:
            normalized = Path(path).absolute()
        except TypeError:
            return
        if normalized in forbidden:
            forbidden_reads.append(normalized)
            raise AssertionError("Phase B read a Phase A artifact")

    def guarded_builtin_open(
        file: object,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        check_read(file, mode)
        return original_builtin_open(file, mode, *args, **kwargs)

    def guarded_path_open(
        self: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> object:
        check_read(self, mode)
        return original_path_open(self, mode, *args, **kwargs)

    def guarded_path_iterdir(self: Path) -> object:
        normalized = self.absolute()
        if normalized in forbidden_directories:
            forbidden_enumerations.append(normalized)
            raise AssertionError("Phase B enumerated a Phase A directory")
        return original_path_iterdir(self)

    def guarded_os_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        normalized = Path(path).absolute()
        if (
            normalized in forbidden
            and flags & (qa.os.O_WRONLY | qa.os.O_RDWR) == 0
        ):
            forbidden_reads.append(normalized)
            raise AssertionError("Phase B os.open read a Phase A artifact")
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_scandir(path: object = ".") -> object:
        normalized = Path(path).absolute()
        if normalized in forbidden_directories:
            forbidden_enumerations.append(normalized)
            raise AssertionError("Phase B scandir enumerated Phase A")
        return original_os_scandir(path)

    def guarded_listdir(path: object = ".") -> object:
        normalized = Path(path).absolute()
        if normalized in forbidden_directories:
            forbidden_enumerations.append(normalized)
            raise AssertionError("Phase B listdir enumerated Phase A")
        return original_os_listdir(path)

    def guarded_mmap(fileno: int, *args: object, **kwargs: object) -> object:
        forbidden_mmaps.append(fileno)
        raise AssertionError("Phase B must not mmap Phase A artifacts")

    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "iterdir", guarded_path_iterdir)
    monkeypatch.setattr(qa.os, "open", guarded_os_open)
    monkeypatch.setattr(qa.os, "scandir", guarded_scandir)
    monkeypatch.setattr(qa.os, "listdir", guarded_listdir)
    monkeypatch.setattr(mmap, "mmap", guarded_mmap)
    captured: dict[str, object] = {}

    class BoundaryObserved(RuntimeError):
        pass

    class FakeModelCache:
        @staticmethod
        def run_final_phase(manifest: object, **kwargs: object) -> object:
            captured["manifest"] = manifest
            captured.update(kwargs)
            raise BoundaryObserved

    manifest = SimpleNamespace(files=(), runtime=SimpleNamespace(wheels=()))
    context = qa.EmbeddingManifestContext(
        phase="complete",
        manifest_digest="a" * 64,
        model_cache=FakeModelCache,
        manifest=manifest,
    )

    with pytest.raises(BoundaryObserved):
        qa.run_embedding_final_phase(
            tmp_path,
            context,
            tmp_path / qa.EMBEDDING_MODEL_CACHE,
            quarantine,
        )

    assert forbidden_reads == []
    assert forbidden_enumerations == []
    assert forbidden_mmaps == []
    assert captured["manifest"] is manifest
    assert captured["generation_parent"] == tmp_path / qa.EMBEDDING_MODEL_CACHE
    assert captured["quarantine_root"] == quarantine
    assert captured["approved_root"] == tmp_path
    assert callable(captured["fetch_artifact"])
    assert callable(captured["install_runtime"])
    assert callable(captured["verify_generation"])


def _phase_b_fetch_manifest() -> object:
    files = tuple(
        SimpleNamespace(
            artifact_url=f"https://huggingface.co/fixed/model-{index}.bin",
            size=index + 1,
        )
        for index in range(5)
    )
    wheels = tuple(
        SimpleNamespace(
            artifact_url=f"https://files.pythonhosted.org/fixed/runtime-{index}.whl",
            size=index + 6,
        )
        for index in range(2)
    )
    return SimpleNamespace(files=files, runtime=SimpleNamespace(wheels=wheels))


@pytest.mark.parametrize(
    "case",
    ("duplicate", "unknown", "wrong-size", "missing", "complete"),
)
def test_phase_b_fetch_ledger_is_exact_once_and_complete_before_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    manifest = _phase_b_fetch_manifest()
    expected = [
        (item.artifact_url, item.size)
        for item in (*manifest.files, *manifest.runtime.wheels)
    ]
    transport_calls: list[tuple[str, int]] = []

    def transport(
        url: str,
        *,
        expected_size: int,
        **_kwargs: object,
    ) -> bytes:
        transport_calls.append((url, expected_size))
        return bytes([expected_size]) * expected_size

    monkeypatch.setattr(qa, "_embedding_https_fetch", transport)

    class FakeModelError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code
            super().__init__(code)

    class FakeModelCache:
        ModelCacheError = FakeModelError

        @staticmethod
        def install_locked_runtime(*_args: object) -> None:
            return None

        @staticmethod
        def run_final_phase(_manifest: object, **kwargs: object) -> object:
            fetch = kwargs["fetch_artifact"]
            verify = kwargs["verify_generation"]
            if case == "duplicate":
                fetch(*expected[0])
                fetch(*expected[0])
            elif case == "unknown":
                fetch("https://example.invalid/unknown", 1)
            elif case == "wrong-size":
                fetch(expected[0][0], expected[0][1] + 1)
            elif case == "missing":
                for item in expected[:-1]:
                    fetch(*item)
                verify(object())
            else:
                for item in expected:
                    fetch(*item)
            return object()

    context = qa.EmbeddingManifestContext(
        phase="complete",
        manifest_digest="a" * 64,
        model_cache=FakeModelCache,
        manifest=manifest,
    )

    with pytest.raises(qa.EmbeddingLiveFailure) as caught:
        qa.run_embedding_final_phase(
            tmp_path,
            context,
            tmp_path / qa.EMBEDDING_MODEL_CACHE,
            tmp_path / qa.EMBEDDING_QUARANTINE_ROOT,
        )

    if case == "complete":
        assert caught.value.symbol == "EMBEDDING_MODEL_RECEIPT_INVALID"
        assert transport_calls == expected
    else:
        assert caught.value.symbol == "EMBEDDING_MODEL_ARTIFACT_LEDGER_INVALID"
        assert len(transport_calls) == {
            "duplicate": 1,
            "unknown": 0,
            "wrong-size": 0,
            "missing": len(expected) - 1,
        }[case]


def test_phase_b_runs_first_pipeline_inside_runner_then_independent_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    state = {"final_locks": False, "final_socket": False, "replay_socket": False}

    class ExactManifest:
        def __init__(self) -> None:
            fixture = _phase_b_fetch_manifest()
            self.files = fixture.files
            self.runtime = fixture.runtime

    manifest = ExactManifest()

    class ExactVerified:
        def __init__(self) -> None:
            self.manifest = manifest

    class ExactFinalResult:
        def __init__(self, verified: object, root: Path, verification: dict[str, object]):
            self.verified = verified
            self.quarantine_root = root
            self.verification = verification

    class FakeModelError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code
            super().__init__(code)

    class FakeModelCache:
        ModelManifest = ExactManifest
        VerifiedModelCache = ExactVerified
        FinalPhaseResult = ExactFinalResult
        ModelCacheError = FakeModelError

        @staticmethod
        def install_locked_runtime(*_args: object) -> None:
            return None

        @staticmethod
        @contextmanager
        def _socket_denied_verification() -> object:
            state["replay_socket"] = True
            try:
                yield
            finally:
                state["replay_socket"] = False

        @staticmethod
        def run_final_phase(_manifest: object, **kwargs: object) -> object:
            assert _manifest is manifest
            quarantine = Path(kwargs["quarantine_root"])
            quarantine.mkdir(parents=True, exist_ok=True)
            fetch = kwargs["fetch_artifact"]
            for item in (*manifest.files, *manifest.runtime.wheels):
                fetch(item.artifact_url, item.size)
            verified = ExactVerified()
            state["final_locks"] = True
            state["final_socket"] = True
            raw = kwargs["verify_generation"](verified)
            assert "providerOrigins" not in raw
            state["final_socket"] = False
            state["final_locks"] = False
            phase_root = quarantine / "phase-b-result"
            phase_root.mkdir()
            return ExactFinalResult(
                verified,
                phase_root,
                {**raw, "providerOrigins": [{"distribution": "fastembed"}]},
            )

    authority = SimpleNamespace(model_cache_module=FakeModelCache)

    def pipeline(database: Path, temp_parent: Path, ordinal: int) -> dict[str, object]:
        database.write_bytes(f"database-{ordinal}".encode("ascii"))
        return {
            "fixtureDigest": "1" * 64,
            "indexVectorDigest": "2" * 64,
            "indexSnapshotDigest": "3" * 64,
            "retrievalDigest": "4" * 64,
            "zeroNetworkReplayDigest": "5" * 64,
            "providerEvidence": {
                "processId": 10_000 + ordinal,
                "challengeDigest": f"{ordinal:x}" * 64,
                "tempTokenDigest": f"{ordinal + 2:x}" * 64,
            },
            "allowedWriteLedger": {
                "allowedRoots": [
                    str(database.resolve()),
                    str(temp_parent.resolve()),
                ]
            },
        }

    class FakeExpectation:
        def __init__(self, final_result: ExactFinalResult) -> None:
            self.pipeline = final_result.verification["pipeline"]

        @classmethod
        def from_authority(
            cls, checked_manifest: object, final_result: object, checked_authority: object
        ) -> object:
            assert checked_manifest is manifest
            assert type(final_result) is ExactFinalResult
            assert checked_authority is authority
            assert state["final_locks"] is False
            assert state["final_socket"] is False
            events.append("expectation")
            return cls(final_result)

    class FakeEmbeddingLive:
        FinalExpectation = FakeExpectation

        class EmbeddingLiveError(RuntimeError):
            pass

        @staticmethod
        def run_final_verification_callback(
            checked_authority: object,
            checked_manifest: object,
            verified: object,
            *,
            database_path: Path,
            temp_parent: Path,
            clock: object,
        ) -> dict[str, object]:
            assert checked_authority is authority
            assert checked_manifest is manifest
            assert type(verified) is ExactVerified
            assert state["final_locks"] is True
            assert state["final_socket"] is True
            assert callable(clock)
            events.append("callback")
            first = pipeline(database_path, temp_parent, 1)
            return {
                "generationDigest": "6" * 64,
                "childEvidenceDigest": "7" * 64,
                "childLoadedOrigins": [],
                "pipeline": first,
                "pipelineDigest": "8" * 64,
            }

        @staticmethod
        def run_fresh_pipeline(
            expectation: object,
            *,
            database_path: Path,
            temp_parent: Path,
            clock: object,
        ) -> dict[str, object]:
            assert type(expectation) is FakeExpectation
            assert state["replay_socket"] is True
            assert callable(clock)
            events.append("replay")
            return pipeline(database_path, temp_parent, 2)

        @staticmethod
        def build_receipt(
            expectation: object,
            final_result: object,
            pipeline_evidence: object,
            _started: object,
            _finished: object,
        ) -> dict[str, object]:
            assert type(expectation) is FakeExpectation
            assert type(final_result) is ExactFinalResult
            assert pipeline_evidence is final_result.verification["pipeline"]
            events.append("build")
            return {
                key: index
                for index, key in enumerate(sorted(qa._EMBEDDING_RECEIPT_KEYS))
            }

    monkeypatch.setattr(
        qa,
        "_embedding_https_fetch",
        lambda _url, *, expected_size, **_kwargs: b"x" * expected_size,
    )
    context = qa.EmbeddingManifestContext(
        phase="complete",
        manifest_digest="a" * 64,
        model_cache=FakeModelCache,
        manifest=manifest,
        authority=authority,
    )

    artifacts = qa.run_embedding_final_phase(
        tmp_path,
        context,
        tmp_path / qa.EMBEDDING_MODEL_CACHE,
        tmp_path / qa.EMBEDDING_QUARANTINE_ROOT,
        embedding_live=FakeEmbeddingLive,
    )

    assert events == ["callback", "expectation", "replay", "build"]
    assert type(artifacts.final_result) is ExactFinalResult
    assert artifacts.expectation.pipeline is artifacts.first_pipeline
    assert artifacts.first_pipeline["fixtureDigest"] == artifacts.replay_pipeline[
        "fixtureDigest"
    ]
    assert artifacts.first_pipeline["providerEvidence"]["processId"] != artifacts.replay_pipeline[
        "providerEvidence"
    ]["processId"]
    assert artifacts.first_pipeline["allowedWriteLedger"]["allowedRoots"] != artifacts.replay_pipeline[
        "allowedWriteLedger"
    ]["allowedRoots"]
    assert set(
        json.loads(artifacts.temporary_receipt.read_text(encoding="utf-8"))
    ) == qa._EMBEDDING_RECEIPT_KEYS


def test_bootstrap_manifest_dispatch_never_promotes_or_overwrites_sealed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", "1")
    monkeypatch.delenv("COURSE_NETWORK_VISUAL_TEST", raising=False)
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    receipt = tmp_path / qa.EMBEDDING_MODEL_RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b"sealed-before")
    manifest = _copy_bootstrap_manifest(tmp_path)
    live = qa._embedding_live_module(Path(__file__).resolve().parents[2])
    monkeypatch.setattr(qa, "_embedding_live_module", lambda _root: live)
    bundle = tmp_path / qa.EMBEDDING_MODEL_CACHE
    bundle.mkdir(parents=True)
    (bundle / "prior.bin").write_bytes(b"prior-cache")
    captured: dict[str, Path] = {}

    def phase_a(
        repo_root: Path,
        context: object,
        candidate_path: Path,
        quarantine_root: Path,
    ) -> None:
        captured.update(
            repo_root=repo_root,
            phase=context.phase,
            candidate_path=candidate_path,
            quarantine_root=quarantine_root,
        )

    monkeypatch.setattr(qa, "run_embedding_bootstrap_phase", phase_a, raising=False)

    assert qa.main(["embedding-model-live", "--receipt", str(receipt)]) == 3
    assert receipt.read_bytes() == b"sealed-before"
    assert (bundle / "prior.bin").read_bytes() == b"prior-cache"
    assert captured == {
        "repo_root": tmp_path,
        "phase": "bootstrap-required",
        "candidate_path": tmp_path / qa.EMBEDDING_BOOTSTRAP_CANDIDATE,
        "quarantine_root": tmp_path / qa.EMBEDDING_QUARANTINE_ROOT,
    }
    assert "MODEL_MANIFEST_BOOTSTRAP_REQUIRED" in capsys.readouterr().err


def test_final_phase_validates_temp_receipt_before_atomic_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", "1")
    monkeypatch.delenv("COURSE_NETWORK_VISUAL_TEST", raising=False)
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    manifest = tmp_path / qa.EMBEDDING_MODEL_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"phase":"complete"}', encoding="utf-8")
    receipt = tmp_path / qa.EMBEDDING_MODEL_RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_bytes(b"old-sealed")
    temporary = tmp_path / qa.EMBEDDING_QUARANTINE_ROOT / "receipt.tmp"
    temporary.parent.mkdir(parents=True)
    temporary.write_text("{}", encoding="utf-8")
    phase_root = temporary.parent / "phase-b-result"
    phase_root.mkdir()
    final_result = SimpleNamespace(quarantine_root=phase_root)
    expectation = object()
    artifacts = qa.EmbeddingFinalPhaseArtifacts(
        temporary_receipt=temporary,
        final_result=final_result,
        expectation=expectation,
        first_pipeline={},
        replay_pipeline={},
    )
    calls: list[str] = []
    seal_attempt = 0
    validation_mismatch = False

    class FakeLiveError(RuntimeError):
        def __init__(self, code: str) -> None:
            self.code = code
            super().__init__(code)

    class FakeAuthority:
        model_cache_module = object()

        def __enter__(self) -> "FakeAuthority":
            calls.append("authority-enter")
            return self

        def __exit__(self, *_args: object) -> None:
            calls.append("authority-exit")

    authority = FakeAuthority()

    class FakeAuthorityType:
        @classmethod
        def load(cls) -> FakeAuthority:
            calls.append("authority-load")
            return authority

    class FakeLive:
        LiveEmbeddingAuthority = FakeAuthorityType
        EmbeddingLiveError = FakeLiveError

        @staticmethod
        def seal_receipt(
            candidate: Path,
            sealed_path: Path,
            checked_expectation: object,
            checked_result: object,
            quarantine_root: Path,
            *,
            defer_commit: bool,
        ) -> object:
            nonlocal seal_attempt
            seal_attempt += 1
            calls.append("seal")
            assert candidate == temporary
            assert checked_expectation is expectation
            assert checked_result is final_result
            assert quarantine_root == phase_root
            assert defer_commit is True
            if seal_attempt == 1:
                raise FakeLiveError("EMBEDDING_MODEL_RECEIPT_INVALID")
            prior_payload = sealed_path.read_bytes()
            sealed_payload = {"status": "verified"}
            sealed_path.write_text(json.dumps(sealed_payload), encoding="utf-8")

            class Transaction:
                @staticmethod
                def commit() -> dict[str, object]:
                    calls.append("commit")
                    return sealed_payload

                @staticmethod
                def finalize() -> dict[str, object]:
                    calls.append("finalize")
                    return sealed_payload

                @staticmethod
                def rollback() -> None:
                    calls.append("rollback")
                    sealed_path.write_bytes(prior_payload)

            return Transaction()

        @staticmethod
        def validate_receipt(
            sealed_path: Path,
            checked_expectation: object,
            checked_result: object,
        ) -> dict[str, object]:
            calls.append("validate-after-seal")
            assert checked_expectation is expectation
            assert checked_result is final_result
            validated = json.loads(sealed_path.read_text(encoding="utf-8"))
            return {"status": "switched"} if validation_mismatch else validated

    context = qa.EmbeddingManifestContext(
        phase="complete",
        manifest_digest="a" * 64,
        model_cache=authority.model_cache_module,
        manifest=object(),
        authority=authority,
    )
    monkeypatch.setattr(qa, "_embedding_live_module", lambda _root: FakeLive)
    monkeypatch.setattr(
        qa,
        "embedding_manifest_phase",
        lambda _path, checked_authority: context
        if checked_authority is authority
        else (_ for _ in ()).throw(AssertionError("wrong authority")),
    )
    monkeypatch.setattr(
        qa,
        "run_embedding_final_phase",
        lambda *_args, **kwargs: artifacts
        if kwargs["embedding_live"] is FakeLive
        else (_ for _ in ()).throw(AssertionError("wrong live module")),
    )
    monkeypatch.setattr(
        qa.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(AssertionError("run.py must not seal")),
    )

    assert qa.main(["embedding-model-live", "--receipt", str(receipt)]) == 5
    assert receipt.read_bytes() == b"old-sealed"
    assert "EMBEDDING_MODEL_RECEIPT_INVALID" in capsys.readouterr().err

    assert qa.main(["embedding-model-live", "--receipt", str(receipt)]) == 0
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "verified"
    assert calls[-5:] == [
        "seal",
        "validate-after-seal",
        "commit",
        "finalize",
        "authority-exit",
    ]
    success = capsys.readouterr()
    assert success.err == ""
    assert success.out.splitlines() == [
        "EMBEDDING_MODEL_LIVE_VERIFIED: CPYTHON SOCKET-DENIED VERIFIED",
        "OS NETWORK ISOLATION NOT CERTIFIED",
    ]

    prior_success = receipt.read_bytes()
    validation_mismatch = True
    assert qa.main(["embedding-model-live", "--receipt", str(receipt)]) == 5
    assert receipt.read_bytes() == prior_success
    assert calls[-5:] == [
        "seal",
        "validate-after-seal",
        "commit",
        "rollback",
        "authority-exit",
    ]
    assert "EMBEDDING_MODEL_RECEIPT_INVALID" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("symbol", "exit_code"),
    (
        ("MODEL_MANIFEST_POLICY_MISMATCH", 3),
        ("EMBEDDING_MODEL_ACQUISITION_FAILED", 4),
        ("EMBEDDING_MODEL_RECEIPT_INVALID", 5),
        ("EMBEDDING_MODEL_PROTECTED_BOUNDARY", 6),
    ),
)
def test_live_producer_maps_symbolic_failures_without_path_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    symbol: str,
    exit_code: int,
) -> None:
    monkeypatch.setattr(qa, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", "1")
    monkeypatch.delenv("COURSE_NETWORK_VISUAL_TEST", raising=False)
    monkeypatch.delenv("COURSE_REFERENCE_ROOT", raising=False)
    manifest = tmp_path / qa.EMBEDDING_MODEL_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"phase":"complete"}', encoding="utf-8")
    receipt = tmp_path / qa.EMBEDDING_MODEL_RECEIPT
    receipt.parent.mkdir(parents=True)

    class FakeAuthority:
        def __enter__(self) -> "FakeAuthority":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    authority = FakeAuthority()

    class FakeAuthorityType:
        @classmethod
        def load(cls) -> FakeAuthority:
            return authority

    fake_live = SimpleNamespace(LiveEmbeddingAuthority=FakeAuthorityType)
    monkeypatch.setattr(qa, "_embedding_live_module", lambda _root: fake_live)

    def fail(_path: Path, checked_authority: object):
        assert checked_authority is authority
        raise qa.EmbeddingLiveFailure(symbol, exit_code)

    monkeypatch.setattr(qa, "embedding_manifest_phase", fail, raising=False)

    assert qa.main(["embedding-model-live", "--receipt", str(receipt)]) == exit_code
    error = capsys.readouterr().err.strip()
    assert error == symbol
    assert str(tmp_path) not in error


@pytest.mark.parametrize("mode", ("focused", "all", "knowledge-demo"))
@pytest.mark.parametrize("opt_in", ("1", ""))
def test_offline_modes_reject_embedding_opt_in_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    opt_in: str,
) -> None:
    monkeypatch.setenv("COURSE_EMBEDDING_MODEL_DOWNLOAD", opt_in)
    monkeypatch.setattr(
        qa,
        "run_focused",
        lambda *_args: (_ for _ in ()).throw(AssertionError("offline dispatch ran")),
    )
    monkeypatch.setattr(
        qa,
        "run_all",
        lambda *_args: (_ for _ in ()).throw(AssertionError("offline dispatch ran")),
    )
    monkeypatch.setattr(
        qa,
        "run_knowledge_demo_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("offline dispatch ran")
        ),
    )

    assert qa.main([mode]) == 2
    assert capsys.readouterr().err.strip() == "OFFLINE_GATE_LIVE_OPT_IN_SET"

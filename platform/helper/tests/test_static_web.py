from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _static_web_module():
    spec = find_spec("course_helper.static_web")
    assert spec is not None, "course_helper.static_web must exist"
    return import_module("course_helper.static_web")


def _web_root(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / ".vite").mkdir(parents=True)
    (root / "index.html").write_text(
        "<main>Course Studio</main>", encoding="utf-8"
    )
    (root / ".vite" / "manifest.json").write_text("{}", encoding="utf-8")
    return root


def test_product_static_web_serves_spa_and_preserves_api_404(
    tmp_path: Path,
) -> None:
    module = _static_web_module()
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    module.mount_static_web(app, _web_root(tmp_path))
    client = TestClient(app)

    assert client.get("/").text == "<main>Course Studio</main>"
    assert client.get("/courses/current").text == "<main>Course Studio</main>"
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/v1/unknown").status_code == 404


def test_static_web_rejects_missing_index_or_manifest(tmp_path: Path) -> None:
    module = _static_web_module()
    root = tmp_path / "dist"
    root.mkdir()

    with pytest.raises(ValueError, match="web root is invalid"):
        module.validate_web_root(root)

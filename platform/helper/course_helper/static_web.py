"""Validated same-origin hosting for the built Course Studio web app."""

from __future__ import annotations

import mimetypes
import os
import stat
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Windows registry may map .js to text/plain, which causes browsers to refuse
# executing module scripts (strict MIME checking). Override with correct types.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("image/svg+xml", ".svg")


class CourseStudioStaticFiles(StaticFiles):
    """Serve immutable build assets with an SPA fallback outside API paths."""

    async def get_response(self, path: str, scope: Scope):
        normalized = str(scope.get("path", path)).lstrip("/")
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or _is_api_path(normalized):
                raise
            response = await super().get_response("index.html", scope)
        if response.status_code == 404 and not _is_api_path(normalized):
            response = await super().get_response("index.html", scope)
        # Prevent browsers from caching stale responses (e.g. wrong MIME type
        # from a previous server version). Vite assets have hashed filenames so
        # re-fetching is cheap; index.html must always be fresh.
        response.headers["Cache-Control"] = "no-store"
        return response


def validate_web_root(path: Path) -> Path:
    """Return a canonical, non-reparse Vite build root or fail closed."""

    try:
        requested = Path(os.path.abspath(path))
        resolved = requested.resolve(strict=True)
        if (
            str(requested).casefold() != str(resolved).casefold()
            or not resolved.is_dir()
            or resolved.is_symlink()
            or _is_reparse(resolved)
        ):
            raise ValueError
        for relative in (Path("index.html"), Path(".vite/manifest.json")):
            candidate = resolved / relative
            candidate_resolved = candidate.resolve(strict=True)
            details = candidate.stat(follow_symlinks=False)
            if (
                str(candidate).casefold() != str(candidate_resolved).casefold()
                or candidate.is_symlink()
                or _is_reparse(candidate)
                or not stat.S_ISREG(details.st_mode)
            ):
                raise ValueError
            candidate_resolved.relative_to(resolved)
    except (OSError, ValueError) as error:
        raise ValueError("web root is invalid") from error
    return resolved


def mount_static_web(app: FastAPI, web_root: Path) -> Path:
    root = validate_web_root(web_root)
    app.mount("/", CourseStudioStaticFiles(directory=root, html=True), name="web")
    return root


def _is_api_path(path: str) -> bool:
    return path == "health" or path.startswith("health/") or path == "v1" or path.startswith("v1/")


def _is_reparse(path: Path) -> bool:
    try:
        return bool(path.stat(follow_symlinks=False).st_file_attributes & 0x400)
    except AttributeError:
        return False

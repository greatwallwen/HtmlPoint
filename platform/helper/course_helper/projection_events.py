"""Bounded redacted evidence receipts for native projection supervision."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID


_ALLOWED_EVENTS = frozenset(
    {
        "session_opened",
        "asset_bundle_verified",
        "assignment_verified",
        "session_invalidated",
        "session_closed",
        "host_failure",
        "final_summary",
    }
)
_ALLOWED_STATUS = frozenset(
    {
        "undetected",
        "candidate",
        "assigned",
        "fullscreen",
        "syncing",
        "witness_pending",
        "certified",
        "invalidated",
        "closed",
    }
)
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DIGEST_KEY = re.compile(r"^[a-z][a-zA-Z0-9]{0,31}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_RECEIPT_BYTES = 32 * 1024


class ProjectionEvidenceStore:
    """Write one canonical atomic receipt per meaningful lifecycle event."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(os.path.abspath(root))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._root.mkdir(parents=True, exist_ok=True)
        _ensure_plain_directory(self._root)

    def record(
        self,
        *,
        event_type: str,
        session_id: str,
        command_id: str,
        generation: int,
        sequence: int,
        status: str,
        code: str,
        digests: Mapping[str, str],
    ) -> dict[str, Any]:
        if event_type not in _ALLOWED_EVENTS:
            raise ValueError("evidence_event_not_allowed")
        if status not in _ALLOWED_STATUS:
            raise ValueError("evidence_status_invalid")
        if not _CODE.fullmatch(code):
            raise ValueError("evidence_code_invalid")
        try:
            normalized_session = str(UUID(session_id))
            normalized_command = str(UUID(command_id))
        except ValueError as error:
            raise ValueError("evidence_identity_invalid") from error
        if (
            type(generation) is not int
            or not 0 <= generation <= 2_147_483_647
            or type(sequence) is not int
            or not 0 <= sequence <= 2_147_483_647
            or len(digests) > 16
        ):
            raise ValueError("evidence_bounds_invalid")
        normalized_digests: dict[str, str] = {}
        for key, value in sorted(digests.items()):
            if not _DIGEST_KEY.fullmatch(key) or not _DIGEST.fullmatch(value):
                raise ValueError("evidence_digest_invalid")
            normalized_digests[key] = value

        occurred_at = self._clock()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("evidence_clock_invalid")
        receipt: dict[str, Any] = {
            "schemaVersion": 1,
            "eventType": event_type,
            "sessionId": normalized_session,
            "commandId": normalized_command,
            "generation": generation,
            "sequence": sequence,
            "occurredAt": occurred_at.isoformat(),
            "status": status,
            "code": code,
            "digests": normalized_digests,
        }
        encoded = _canonical_json(receipt)
        if len(encoded) > _MAX_RECEIPT_BYTES:
            raise ValueError("evidence_receipt_too_large")
        receipt_digest = hashlib.sha256(encoded).hexdigest()
        target = self._root / f"projection-{receipt_digest}.json"
        _atomic_write(target, encoded + b"\n")
        return receipt


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _atomic_write(target: Path, payload: bytes) -> None:
    _ensure_plain_directory(target.parent)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".projection-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _ensure_plain_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("evidence_root_invalid")
    stat = path.stat(follow_symlinks=False)
    if getattr(stat, "st_file_attributes", 0) & 0x400:
        raise ValueError("evidence_root_invalid")


__all__ = ["ProjectionEvidenceStore"]

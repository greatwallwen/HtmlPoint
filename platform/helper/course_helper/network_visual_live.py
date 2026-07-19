"""Strict live producer for one bounded Wikimedia acquire/revalidate receipt."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Callable, Mapping

from course_helper.artifacts import ArtifactStore
from course_helper.catalog import KnowledgeCatalog
from course_helper.network_visuals import (
    NetworkVisualOutcome,
    PinnedHttpsTransport,
    WikimediaApiClient,
    acquire_network_visuals,
    discover_network_visuals,
    revalidate_network_visual,
)


Clock = Callable[[], datetime]
PRODUCER = "course-helper/network-visual-acquisition-live@1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LICENSE_IDS = {
    "CC-BY-3.0",
    "CC-BY-4.0",
    "CC-BY-SA-3.0",
    "CC-BY-SA-4.0",
    "CC0-1.0",
    "PUBLIC-DOMAIN",
    "GFDL-1.2-OR-LATER",
}
_TOP_KEYS = {
    "schemaVersion",
    "producer",
    "status",
    "provider",
    "policyId",
    "startedAt",
    "finishedAt",
    "candidate",
    "acquisition",
    "verification",
    "coursePublicationVerified",
    "checks",
    "receiptDigest",
}


class NetworkVisualLiveError(RuntimeError):
    _CODES = {
        "NETWORK_VISUAL_ACQUISITION_FAILED",
        "NETWORK_VISUAL_RECEIPT_INVALID",
        "NETWORK_VISUAL_PROTECTED_BOUNDARY",
    }

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("invalid network visual live error code")
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_time(value: datetime) -> str:
    if value.utcoffset() is None:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _exact(value: object, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    return value


def _canonical_receipt_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID") from error
    if _canonical_time(parsed) != value:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    return parsed


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, str):
        lowered = value.casefold()
        return (
            "http://" in lowered
            or "https://" in lowered
            or ":\\" in value
            or "course_aiproduct" in lowered
            or "references" in lowered
        )
    if isinstance(value, Mapping):
        return any(_contains_forbidden(key) or _contains_forbidden(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return False


def validate_receipt_value(value: object) -> Mapping[str, Any]:
    receipt = _exact(value, _TOP_KEYS)
    unsigned = dict(receipt)
    digest = unsigned.pop("receiptDigest")
    candidate = _exact(receipt["candidate"], {"candidateId", "metadataDigest"})
    acquisition = _exact(
        receipt["acquisition"],
        {
            "acquisitionId",
            "visualVersionId",
            "artifactId",
            "artifactDigest",
            "evidenceId",
            "licenseId",
            "providerSha1",
        },
    )
    verification = _exact(
        receipt["verification"],
        {"status", "revision", "evidenceId", "verifiedAt", "expiresAt"},
    )
    checks = receipt["checks"]
    if (
        type(receipt["schemaVersion"]) is not int
        or receipt["schemaVersion"] != 1
        or receipt["producer"] != PRODUCER
        or receipt["status"] != "verified"
        or receipt["provider"] != "wikimedia-commons"
        or receipt["policyId"] != "course-studio-authenticity-v1"
        or receipt["coursePublicationVerified"] is not False
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or hashlib.sha256(_canonical_json(unsigned)).hexdigest() != digest
        or not isinstance(candidate["candidateId"], str)
        or not candidate["candidateId"].startswith("network-candidate-")
        or not isinstance(candidate["metadataDigest"], str)
        or _SHA256.fullmatch(candidate["metadataDigest"]) is None
        or not isinstance(acquisition["acquisitionId"], str)
        or not acquisition["acquisitionId"].startswith("network-acquisition-")
        or not isinstance(acquisition["visualVersionId"], str)
        or _ID.fullmatch(acquisition["visualVersionId"]) is None
        or not isinstance(acquisition["artifactId"], str)
        or not acquisition["artifactId"].startswith("artifact-")
        or not isinstance(acquisition["artifactDigest"], str)
        or _SHA256.fullmatch(acquisition["artifactDigest"]) is None
        or acquisition["artifactId"] != "artifact-" + acquisition["artifactDigest"]
        or not isinstance(acquisition["providerSha1"], str)
        or _SHA1.fullmatch(acquisition["providerSha1"]) is None
        or not isinstance(acquisition["evidenceId"], str)
        or not acquisition["evidenceId"].startswith("network-visual-evidence-")
        or acquisition["licenseId"] not in _LICENSE_IDS
        or verification["status"] != "verified"
        or type(verification["revision"]) is not int
        or verification["revision"] != 2
        or not isinstance(verification["evidenceId"], str)
        or not verification["evidenceId"].startswith("network-visual-evidence-")
        or verification["evidenceId"] == acquisition["evidenceId"]
        or not isinstance(checks, list)
        or len(checks) != 5
        or _contains_forbidden(receipt)
    ):
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    expected_codes = (
        "provider-policy",
        "public-dns-pinning",
        "artifact-verified",
        "freshness-revalidated",
        "course-publication-not-certified",
    )
    actual_codes: list[str] = []
    for check in checks:
        item = _exact(check, {"code", "status"})
        if item["status"] != "passed" or not isinstance(item["code"], str):
            raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
        actual_codes.append(item["code"])
    if tuple(actual_codes) != expected_codes:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    started = _canonical_receipt_time(receipt["startedAt"])
    finished = _canonical_receipt_time(receipt["finishedAt"])
    verified = _canonical_receipt_time(verification["verifiedAt"])
    expires = _canonical_receipt_time(verification["expiresAt"])
    if (
        any(value.utcoffset() is None for value in (started, finished, verified, expires))
        or finished < started
        or not started <= verified <= finished
        or expires - verified != timedelta(hours=24)
    ):
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
    return receipt


def _read_receipt(path: Path) -> bytes:
    try:
        before = os.lstat(path)
        attributes = getattr(before, "st_file_attributes", 0)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
            or before.st_size > 256 * 1024
        ):
            raise OSError("unsafe receipt")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("receipt identity changed")
            payload = os.read(descriptor, 256 * 1024 + 1)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            len(payload) != before.st_size
            or len(payload) > 256 * 1024
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise OSError("receipt changed")
        return payload
    except OSError as error:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID") from error


def validate_receipt(path: Path) -> Mapping[str, Any]:
    try:
        raw = _read_receipt(path)

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
                value[key] = item
            return value

        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        validated = validate_receipt_value(parsed)
        if raw != _canonical_json(validated):
            raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
        return validated
    except NetworkVisualLiveError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID") from error


def build_live_receipt(work_root: Path, *, clock: Clock = _now) -> Mapping[str, Any]:
    started = clock()
    if started.utcoffset() is None:
        raise NetworkVisualLiveError("NETWORK_VISUAL_ACQUISITION_FAILED")
    work_root.mkdir(parents=True, exist_ok=False)
    catalog = KnowledgeCatalog.open(work_root / "catalog.sqlite3")
    try:
        provider = WikimediaApiClient(PinnedHttpsTransport())
        candidates = discover_network_visuals(
            catalog,
            provider,
            query="Example.jpg",
            limit=5,
            clock=lambda: started,
        )
        acquired: NetworkVisualOutcome | None = None
        selected = None
        store = ArtifactStore(work_root / ".artifacts")
        for candidate in candidates:
            outcome = acquire_network_visuals(
                catalog,
                provider,
                store,
                (candidate.candidate_id,),
                clock=lambda: started,
            )[0]
            if outcome.status == "acquired":
                acquired = outcome
                selected = candidate
                break
        if acquired is None or selected is None or acquired.acquisition is None:
            raise NetworkVisualLiveError("NETWORK_VISUAL_ACQUISITION_FAILED")
        refreshed = revalidate_network_visual(
            catalog,
            provider,
            visual_version_id=str(acquired.visual_version_id),
            clock=lambda: started,
        )
        if (
            refreshed.status != "revalidated"
            or refreshed.verification is None
            or refreshed.verification.status != "verified"
            or refreshed.verification.revision != 2
        ):
            raise NetworkVisualLiveError("NETWORK_VISUAL_ACQUISITION_FAILED")
        artifact = catalog.get_artifact(str(acquired.artifact_id))
        if artifact is None or not store.verify(artifact.payload):
            raise NetworkVisualLiveError("NETWORK_VISUAL_ACQUISITION_FAILED")
        finished = clock()
        unsigned: dict[str, Any] = {
            "schemaVersion": 1,
            "producer": PRODUCER,
            "status": "verified",
            "provider": "wikimedia-commons",
            "policyId": "course-studio-authenticity-v1",
            "startedAt": _canonical_time(started),
            "finishedAt": _canonical_time(finished),
            "candidate": {
                "candidateId": selected.candidate_id,
                "metadataDigest": selected.metadata_digest,
            },
            "acquisition": {
                "acquisitionId": acquired.acquisition.acquisition_id,
                "visualVersionId": acquired.acquisition.visual_version_id,
                "artifactId": artifact.payload.artifact_id,
                "artifactDigest": artifact.payload.content_digest,
                "evidenceId": acquired.acquisition.evidence_id,
                "licenseId": acquired.acquisition.license_id,
                "providerSha1": acquired.acquisition.provider_sha1,
            },
            "verification": {
                "status": refreshed.verification.status,
                "revision": refreshed.verification.revision,
                "evidenceId": refreshed.verification.evidence_id,
                "verifiedAt": _canonical_time(refreshed.verification.verified_at),
                "expiresAt": _canonical_time(refreshed.verification.expires_at),
            },
            "coursePublicationVerified": False,
            "checks": [
                {"code": "provider-policy", "status": "passed"},
                {"code": "public-dns-pinning", "status": "passed"},
                {"code": "artifact-verified", "status": "passed"},
                {"code": "freshness-revalidated", "status": "passed"},
                {"code": "course-publication-not-certified", "status": "passed"},
            ],
        }
        receipt = dict(unsigned)
        receipt["receiptDigest"] = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        return validate_receipt_value(receipt)
    except NetworkVisualLiveError:
        raise
    except Exception as error:
        raise NetworkVisualLiveError("NETWORK_VISUAL_ACQUISITION_FAILED") from error
    finally:
        catalog.close()


def write_temporary_receipt(receipt: Mapping[str, Any], directory: Path) -> Path:
    validated = validate_receipt_value(receipt)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="network-visual-receipt-", suffix=".tmp", dir=directory)
    path = Path(name)
    try:
        payload = _canonical_json(validated)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        validate_receipt(path)
        return path
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


class SealTransaction:
    def __init__(self, temporary: Path, sealed: Path) -> None:
        self._sealed = sealed
        self._recovery: Path | None = None
        self._finalized = False
        sealed.parent.mkdir(parents=True, exist_ok=True)
        validate_receipt(temporary)
        if sealed.exists():
            prior = _read_receipt(sealed)
            descriptor, name = tempfile.mkstemp(prefix="network-visual-recovery-", suffix=".tmp", dir=sealed.parent)
            self._recovery = Path(name)
            with os.fdopen(descriptor, "wb") as target:
                target.write(prior)
                target.flush()
                os.fsync(target.fileno())
        try:
            os.replace(temporary, sealed)
            self._value = validate_receipt(sealed)
        except Exception:
            self.rollback()
            raise

    def commit(self) -> Mapping[str, Any]:
        return self._value

    def finalize(self) -> Mapping[str, Any]:
        if self._recovery is not None:
            self._recovery.unlink(missing_ok=True)
        self._finalized = True
        return self._value

    def rollback(self) -> None:
        if self._finalized:
            return
        if self._recovery is None:
            self._sealed.unlink(missing_ok=True)
        elif self._recovery.exists():
            os.replace(self._recovery, self._sealed)


def seal_receipt(temporary: Path, sealed: Path, *, defer_commit: bool = False):
    transaction = SealTransaction(temporary, sealed)
    if defer_commit:
        return transaction
    transaction.commit()
    return transaction.finalize()


__all__ = [
    "NetworkVisualLiveError",
    "PRODUCER",
    "SealTransaction",
    "build_live_receipt",
    "seal_receipt",
    "validate_receipt",
    "validate_receipt_value",
    "write_temporary_receipt",
]

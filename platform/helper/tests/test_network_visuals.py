from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket

import pytest

from course_helper.artifacts import ArtifactStore
from course_helper.catalog import KnowledgeCatalog
from course_helper.domain.visual_policy import VisualPolicyContext, evaluate_visual_publication
from course_helper.network_visuals import (
    NetworkVisualError,
    PinnedHttpsTransport,
    ProviderDownload,
    ProviderHttpResponse,
    ProviderNotFound,
    WikimediaApiClient,
    acquire_network_visuals,
    current_network_visual_verification,
    discover_network_visuals,
    revalidate_network_visual,
)


NOW = datetime(2026, 7, 18, 20, 30, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "visual-providers" / "wikimedia"


def _json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _image() -> bytes:
    return base64.b64decode((FIXTURES / "tiny.png.b64").read_text(encoding="ascii"))


class FixtureTransport:
    def __init__(self, *, search: str = "search.json", lookup: str = "search.json", body: bytes | None = None, final_url: str | None = None, media_type: str = "image/png", content_length: int | None = None) -> None:
        self.search_name = search
        self.lookup_name = lookup
        self.body = _image() if body is None else body
        self.final_url = final_url or "https://upload.wikimedia.org/wikipedia/commons/a/ab/Verified.png"
        self.media_type = media_type
        self.content_length = len(self.body) if content_length is None else content_length
        self.calls: list[str] = []

    def get_json(self, url: str, *, max_bytes: int):
        assert max_bytes == 1024 * 1024
        self.calls.append(url)
        return _json(self.search_name if "generator=search" in url else self.lookup_name)

    def get_bytes(self, url: str, *, max_bytes: int) -> ProviderDownload:
        assert max_bytes == 32 * 1024 * 1024
        self.calls.append(url)
        return ProviderDownload(
            body=self.body,
            final_url=self.final_url,
            media_type=self.media_type,
            content_length=self.content_length,
            redirect_count=0,
            resolved_host_count=1,
        )


def _catalog(tmp_path: Path) -> KnowledgeCatalog:
    return KnowledgeCatalog.open(tmp_path / "catalog.sqlite3")


def _discover(catalog: KnowledgeCatalog, transport: FixtureTransport, *, clock=lambda: NOW):
    return discover_network_visuals(
        catalog,
        WikimediaApiClient(transport),
        query="verified AI diagram",
        clock=clock,
    )


def _acquire(tmp_path: Path, catalog: KnowledgeCatalog, transport: FixtureTransport, candidate_id: str, *, clock=lambda: NOW):
    return acquire_network_visuals(
        catalog,
        WikimediaApiClient(transport),
        ArtifactStore(tmp_path / ".artifacts"),
        (candidate_id,),
        clock=clock,
    )[0]


def test_fixture_manifest_is_hash_pinned_and_default_flow_opens_no_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _json("manifest.json")
    expected = manifest["files"]
    hashes = {
        name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        for name in expected
    }
    assert hashes == expected
    image = _image()
    assert len(image) == manifest["decodedImage"]["byteSize"]
    assert hashlib.sha1(image).hexdigest() == manifest["decodedImage"]["sha1"]
    assert hashlib.sha256(image).hexdigest() == manifest["decodedImage"]["sha256"]
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("offline network visual tests must not open sockets")
        ),
    )
    catalog = _catalog(tmp_path)
    transport = FixtureTransport()

    candidate = _discover(catalog, transport)[0]
    outcome = _acquire(tmp_path, catalog, transport, candidate.candidate_id)

    assert outcome.status == "acquired"
    assert outcome.acquisition is not None
    assert outcome.acquisition.provider == "wikimedia-commons"
    assert outcome.acquisition.landing_link.provenance_kind == "licensed-secondary"
    assert outcome.acquisition.license_link.label == "CC-BY-SA-4.0"
    assert outcome.verification is not None and outcome.verification.status == "verified"
    visual_payload = catalog.connection.execute(
        "SELECT payload_json FROM visuals WHERE version_id = ?",
        (outcome.visual_version_id,),
    ).fetchone()[0]
    assert '"authenticity":"licensed-secondary"' in visual_payload
    assert "official-primary" not in visual_payload
    evidence_payload = catalog.connection.execute(
        "SELECT payload_json FROM evidence WHERE evidence_id = ?",
        (outcome.evidence_id,),
    ).fetchone()[0]
    assert "http" not in evidence_payload
    assert str(tmp_path) not in outcome.acquisition.model_dump_json()
    assert catalog.connection.execute("SELECT count(*) FROM lineage").fetchone()[0] == 1
    catalog.close()


def test_candidate_is_opaque_bounded_expiring_and_exact_metadata_bound(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    transport = FixtureTransport()
    candidate = _discover(catalog, transport)[0]

    assert candidate.candidate_id.startswith("network-candidate-")
    assert candidate.expires_at - candidate.created_at == timedelta(minutes=10)
    assert "http" not in candidate.model_dump_json()
    expired = _acquire(
        tmp_path,
        catalog,
        transport,
        candidate.candidate_id,
        clock=lambda: NOW + timedelta(minutes=11),
    )
    assert expired.error_code == "CANDIDATE_EXPIRED"

    fresh = _discover(catalog, transport, clock=lambda: NOW + timedelta(hours=1))[0]
    stale_transport = FixtureTransport(lookup="license-changed.json")
    stale = _acquire(tmp_path, catalog, stale_transport, fresh.candidate_id, clock=lambda: NOW + timedelta(hours=1))
    assert stale.error_code == "CANDIDATE_STALE"
    assert catalog.connection.execute("SELECT count(*) FROM artifact_metadata").fetchone()[0] == 0
    catalog.close()


@pytest.mark.parametrize(
    ("transport", "code"),
    (
        (FixtureTransport(body=b"not an image", content_length=12), "MEDIA_MISMATCH"),
        (FixtureTransport(media_type="text/html"), "MEDIA_MISMATCH"),
        (FixtureTransport(final_url="https://example.invalid/image.png"), "MEDIA_MISMATCH"),
        (FixtureTransport(body=_image() + b"x", content_length=78), "MEDIA_MISMATCH"),
    ),
)
def test_media_mime_hash_redirect_and_html_mismatch_fail_without_catalog_rows(
    tmp_path: Path, transport: FixtureTransport, code: str
) -> None:
    catalog = _catalog(tmp_path)
    candidate = _discover(catalog, FixtureTransport())[0]
    outcome = _acquire(tmp_path, catalog, transport, candidate.candidate_id)

    assert outcome.status == "failed"
    assert outcome.error_code == code
    assert catalog.connection.execute("SELECT count(*) FROM artifact_metadata").fetchone()[0] == 0
    assert catalog.connection.execute("SELECT count(*) FROM network_visual_acquisitions").fetchone()[0] == 0
    catalog.close()


def test_mixed_acquisition_failure_does_not_roll_back_valid_sibling(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    transport = FixtureTransport()
    candidate = _discover(catalog, transport)[0]
    outcomes = acquire_network_visuals(
        catalog,
        WikimediaApiClient(transport),
        ArtifactStore(tmp_path / ".artifacts"),
        ("network-candidate-" + "0" * 64, candidate.candidate_id),
        clock=lambda: NOW,
    )

    assert [value.status for value in outcomes] == ["failed", "acquired"]
    assert outcomes[0].error_code == "CANDIDATE_NOT_FOUND"
    assert catalog.connection.execute("SELECT count(*) FROM network_visual_acquisitions").fetchone()[0] == 1
    catalog.close()


def test_replay_reuses_exact_artifact_visual_and_evidence(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    transport = FixtureTransport()
    candidate = _discover(catalog, transport)[0]
    first = _acquire(tmp_path, catalog, transport, candidate.candidate_id)
    second = _acquire(
        tmp_path,
        catalog,
        transport,
        candidate.candidate_id,
        clock=lambda: NOW + timedelta(minutes=1),
    )

    assert second.status == "acquired" and second.reused is True
    assert second.acquisition == first.acquisition
    assert second.artifact_id == first.artifact_id
    assert second.visual_version_id == first.visual_version_id
    assert second.evidence_id == first.evidence_id
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    catalog.close()


def test_freshness_projection_expires_and_revalidation_preserves_history(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    transport = FixtureTransport()
    candidate = _discover(catalog, transport)[0]
    acquired = _acquire(tmp_path, catalog, transport, candidate.candidate_id)
    visual_id = str(acquired.visual_version_id)

    expired = current_network_visual_verification(
        catalog, visual_id, now=NOW + timedelta(hours=25)
    )
    assert expired.status == "expired"
    blocked = evaluate_visual_publication(
        VisualPolicyContext(
            usage_scope="public",
            authenticity="licensed-secondary",
            license_status="licensed",
            rights_verified=True,
            network_metadata_verified_at=expired.verified_at,
            now=NOW + timedelta(hours=25),
        )
    )
    assert blocked.allowed is False
    assert "NETWORK_METADATA_EXPIRED" in blocked.blockers

    refreshed = revalidate_network_visual(
        catalog,
        WikimediaApiClient(transport),
        visual_version_id=visual_id,
        clock=lambda: NOW + timedelta(hours=25),
    )
    assert refreshed.verification is not None
    assert refreshed.verification.status == "verified"
    assert refreshed.verification.revision == 2
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 2
    assert catalog.connection.execute("SELECT count(*) FROM network_visual_acquisitions").fetchone()[0] == 1
    catalog.close()


@pytest.mark.parametrize(
    ("lookup", "expected"),
    (
        ("removed.json", "removed"),
        ("license-changed.json", "license-changed"),
        ("unknown-license.json", "license-changed"),
    ),
)
def test_revalidation_marks_removed_or_license_changed(
    tmp_path: Path, lookup: str, expected: str
) -> None:
    catalog = _catalog(tmp_path)
    candidate = _discover(catalog, FixtureTransport())[0]
    acquired = _acquire(tmp_path, catalog, FixtureTransport(), candidate.candidate_id)

    outcome = revalidate_network_visual(
        catalog,
        WikimediaApiClient(FixtureTransport(lookup=lookup)),
        visual_version_id=str(acquired.visual_version_id),
        clock=lambda: NOW + timedelta(hours=1),
    )

    assert outcome.status == "revalidated"
    assert outcome.verification is not None
    assert outcome.verification.status == expected
    assert catalog.connection.execute("SELECT count(*) FROM evidence").fetchone()[0] == 2
    catalog.close()


def test_provider_response_rejects_unknown_fields_license_svg_and_active_html(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    unexpected = _json("search.json")
    unexpected["poison"] = True

    class RawTransport(FixtureTransport):
        def get_json(self, _url: str, *, max_bytes: int):
            assert max_bytes == 1024 * 1024
            return unexpected

    with pytest.raises(NetworkVisualError, match="unexpected fields"):
        _discover(catalog, RawTransport())
    assert _discover(catalog, FixtureTransport(search="unknown-license.json")) == ()

    svg = _json("search.json")
    svg["query"]["pages"][0]["imageinfo"][0]["mime"] = "image/svg+xml"

    class SvgTransport(FixtureTransport):
        def get_json(self, _url: str, *, max_bytes: int):
            return svg

    with pytest.raises(NetworkVisualError, match="invalid"):
        _discover(catalog, SvgTransport())
    active = _json("search.json")
    active["query"]["pages"][0]["imageinfo"][0]["extmetadata"]["Artist"]["value"] = "<script>alert(1)</script>"

    class ActiveTransport(FixtureTransport):
        def get_json(self, _url: str, *, max_bytes: int):
            return active

    with pytest.raises(NetworkVisualError, match="invalid"):
        _discover(catalog, ActiveTransport())
    catalog.close()


def test_candidate_and_acquisition_rows_are_immutable(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    candidate = _discover(catalog, FixtureTransport())[0]
    acquired = _acquire(tmp_path, catalog, FixtureTransport(), candidate.candidate_id)

    with pytest.raises(Exception, match="immutable network visual candidate"):
        catalog.connection.execute(
            "UPDATE network_visual_candidates SET provider_page_id = 99 WHERE candidate_id = ?",
            (candidate.candidate_id,),
        )
    catalog.connection.rollback()
    with pytest.raises(Exception, match="immutable network visual acquisition"):
        catalog.connection.execute(
            "DELETE FROM network_visual_acquisitions WHERE acquisition_id = ?",
            (acquired.acquisition.acquisition_id,),
        )
    catalog.connection.rollback()
    catalog.close()


def _resolver(*addresses: str):
    def resolve(_host: str, port: int, **_kwargs: object):
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
            for address in addresses
        ]

    return resolve


def test_pinned_transport_rejects_private_or_mixed_dns_before_request() -> None:
    opened: list[str] = []

    def requester(url: str, _ip: str, _limit: int) -> ProviderHttpResponse:
        opened.append(url)
        raise AssertionError("private or mixed DNS must not open a request")

    for addresses in (("127.0.0.1",), ("93.184.216.34", "10.0.0.8"), ("::1",)):
        transport = PinnedHttpsTransport(resolver=_resolver(*addresses), requester=requester)
        with pytest.raises(NetworkVisualError, match="not public"):
            transport.get_json(
                "https://commons.wikimedia.org/w/api.php?action=query",
                max_bytes=1024,
            )
    assert opened == []


def test_pinned_transport_uses_validated_ip_and_rechecks_redirect_dns() -> None:
    resolver_calls = 0
    request_calls: list[tuple[str, str]] = []

    def resolver(host: str, port: int, **_kwargs: object):
        nonlocal resolver_calls
        resolver_calls += 1
        address = "93.184.216.34" if resolver_calls == 1 else "10.0.0.8"
        return _resolver(address)(host, port)

    def requester(url: str, ip: str, _limit: int) -> ProviderHttpResponse:
        request_calls.append((url, ip))
        return ProviderHttpResponse(
            status=302,
            headers=(("location", "/w/api.php?action=query&continue=1"),),
            body=b"",
        )

    transport = PinnedHttpsTransport(resolver=resolver, requester=requester)
    with pytest.raises(NetworkVisualError, match="not public"):
        transport.get_json(
            "https://commons.wikimedia.org/w/api.php?action=query",
            max_bytes=1024,
        )
    assert request_calls == [
        ("https://commons.wikimedia.org/w/api.php?action=query", "93.184.216.34")
    ]


@pytest.mark.parametrize(
    "location",
    (
        "http://commons.wikimedia.org/w/api.php?action=query",
        "https://example.invalid/w/api.php?action=query",
        "https://commons.wikimedia.org:444/w/api.php?action=query",
    ),
)
def test_pinned_transport_rejects_downgrade_host_or_port_redirect(location: str) -> None:
    def requester(_url: str, _ip: str, _limit: int) -> ProviderHttpResponse:
        return ProviderHttpResponse(
            status=302,
            headers=(("location", location),),
            body=b"",
        )

    transport = PinnedHttpsTransport(
        resolver=_resolver("93.184.216.34"), requester=requester
    )
    with pytest.raises(NetworkVisualError, match="outside its policy"):
        transport.get_json(
            "https://commons.wikimedia.org/w/api.php?action=query",
            max_bytes=1024,
        )


def test_pinned_transport_enforces_status_headers_mime_size_and_json_uniqueness() -> None:
    url = "https://commons.wikimedia.org/w/api.php?action=query"

    def transport_for(response: ProviderHttpResponse) -> PinnedHttpsTransport:
        return PinnedHttpsTransport(
            resolver=_resolver("93.184.216.34"),
            requester=lambda *_args: response,
        )

    with pytest.raises(NetworkVisualError, match="length"):
        transport_for(
            ProviderHttpResponse(
                status=200,
                headers=(("content-type", "application/json"), ("content-length", "9999")),
                body=b"{}",
            )
        ).get_json(url, max_bytes=1024)
    with pytest.raises(NetworkVisualError, match="did not return JSON"):
        transport_for(
            ProviderHttpResponse(
                status=200,
                headers=(("content-type", "text/html"),),
                body=b"<html></html>",
            )
        ).get_json(url, max_bytes=1024)
    with pytest.raises(NetworkVisualError, match="repeats a key"):
        transport_for(
            ProviderHttpResponse(
                status=200,
                headers=(("content-type", "application/json"),),
                body=b'{"query":{},"query":{}}',
            )
        ).get_json(url, max_bytes=1024)
    with pytest.raises(ProviderNotFound):
        transport_for(
            ProviderHttpResponse(status=404, headers=(), body=b"")
        ).get_json(url, max_bytes=1024)


def test_pinned_media_transport_allows_only_raster_commons_bytes() -> None:
    media_url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Verified.png"
    seen: list[tuple[str, str]] = []

    def requester(url: str, ip: str, _limit: int) -> ProviderHttpResponse:
        seen.append((url, ip))
        return ProviderHttpResponse(
            status=200,
            headers=(
                ("content-type", "image/png"),
                ("content-length", str(len(_image()))),
                ("content-encoding", "identity"),
            ),
            body=_image(),
        )

    download = PinnedHttpsTransport(
        resolver=_resolver("93.184.216.34"), requester=requester
    ).get_bytes(media_url, max_bytes=1024)
    assert download.body == _image()
    assert download.final_url == media_url
    assert download.resolved_host_count == 1
    assert seen == [(media_url, "93.184.216.34")]

    html_transport = PinnedHttpsTransport(
        resolver=_resolver("93.184.216.34"),
        requester=lambda *_args: ProviderHttpResponse(
            status=200,
            headers=(("content-type", "text/html"),),
            body=b"<html></html>",
        ),
    )
    with pytest.raises(NetworkVisualError, match="unsupported"):
        html_transport.get_bytes(media_url, max_bytes=1024)


def test_live_receipt_build_validate_and_seal_are_offline_testable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from course_helper import network_visual_live as live

    monkeypatch.setattr(live, "PinnedHttpsTransport", lambda: FixtureTransport())
    ticks = iter((NOW, NOW + timedelta(seconds=1)))
    receipt = live.build_live_receipt(
        tmp_path / "work",
        clock=lambda: next(ticks),
    )

    assert receipt["status"] == "verified"
    assert receipt["coursePublicationVerified"] is False
    assert receipt["verification"]["revision"] == 2
    assert "http" not in json.dumps(receipt).casefold()
    temporary = live.write_temporary_receipt(receipt, tmp_path / "quarantine")
    sealed = tmp_path / "evidence" / "network-visual-acquisition-live.json"
    transaction = live.seal_receipt(temporary, sealed, defer_commit=True)
    assert live.validate_receipt(sealed) == transaction.commit()
    assert transaction.finalize() == receipt


@pytest.mark.parametrize(
    "mutation",
    (
        lambda value: value.update(extra=True),
        lambda value: value.update(coursePublicationVerified=True),
        lambda value: value.update(provider="https://bad"),
        lambda value: value.update(receiptDigest="0" * 64),
        lambda value: value["checks"].reverse(),
    ),
)
def test_live_receipt_validation_fails_closed(mutation) -> None:
    from course_helper import network_visual_live as live

    # Construct through the same exact schema without opening a network path.
    started = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
    expires = (NOW + timedelta(hours=24)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    value = {
        "schemaVersion": 1,
        "producer": live.PRODUCER,
        "status": "verified",
        "provider": "wikimedia-commons",
        "policyId": "course-studio-authenticity-v1",
        "startedAt": started,
        "finishedAt": started,
        "candidate": {"candidateId": "network-candidate-" + "a" * 64, "metadataDigest": "b" * 64},
        "acquisition": {
            "acquisitionId": "network-acquisition-" + "c" * 64,
            "visualVersionId": "visual-live-1",
            "artifactId": "artifact-" + "d" * 64,
            "artifactDigest": "d" * 64,
            "evidenceId": "network-visual-evidence-" + "e" * 64,
            "licenseId": "CC-BY-SA-4.0",
            "providerSha1": "f" * 40,
        },
        "verification": {
            "status": "verified",
            "revision": 2,
            "evidenceId": "network-visual-evidence-" + "1" * 64,
            "verifiedAt": started,
            "expiresAt": expires,
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
    value["receiptDigest"] = hashlib.sha256(live._canonical_json(value)).hexdigest()
    mutation(value)
    with pytest.raises(live.NetworkVisualLiveError):
        live.validate_receipt_value(value)


def test_live_receipt_file_requires_canonical_unique_unlinked_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from course_helper import network_visual_live as live

    monkeypatch.setattr(live, "PinnedHttpsTransport", lambda: FixtureTransport())
    ticks = iter((NOW, NOW + timedelta(seconds=1)))
    receipt = live.build_live_receipt(tmp_path / "work", clock=lambda: next(ticks))
    canonical = live.write_temporary_receipt(receipt, tmp_path / "canonical")

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    with pytest.raises(live.NetworkVisualLiveError):
        live.validate_receipt(noncanonical)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"schemaVersion":1,' + canonical.read_bytes()[1:])
    with pytest.raises(live.NetworkVisualLiveError):
        live.validate_receipt(duplicate)

    linked = tmp_path / "linked.json"
    os.link(canonical, linked)
    with pytest.raises(live.NetworkVisualLiveError):
        live.validate_receipt(linked)


def test_live_seal_failure_restores_prior_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from course_helper import network_visual_live as live

    monkeypatch.setattr(live, "PinnedHttpsTransport", lambda: FixtureTransport())
    first_ticks = iter((NOW, NOW + timedelta(seconds=1)))
    first = live.build_live_receipt(tmp_path / "first-work", clock=lambda: next(first_ticks))
    sealed = tmp_path / "evidence" / "network-visual-acquisition-live.json"
    live.seal_receipt(
        live.write_temporary_receipt(first, tmp_path / "first-temp"),
        sealed,
    )
    prior = sealed.read_bytes()
    second_ticks = iter((NOW + timedelta(minutes=1), NOW + timedelta(minutes=1, seconds=1)))
    second = live.build_live_receipt(tmp_path / "second-work", clock=lambda: next(second_ticks))
    temporary = live.write_temporary_receipt(second, tmp_path / "second-temp")
    real_validate = live.validate_receipt

    def reject_sealed(path: Path):
        if path == sealed:
            raise live.NetworkVisualLiveError("NETWORK_VISUAL_RECEIPT_INVALID")
        return real_validate(path)

    monkeypatch.setattr(live, "validate_receipt", reject_sealed)
    with pytest.raises(live.NetworkVisualLiveError):
        live.seal_receipt(temporary, sealed, defer_commit=True)
    assert sealed.read_bytes() == prior

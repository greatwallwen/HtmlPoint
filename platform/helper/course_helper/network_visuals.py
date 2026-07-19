"""Provider-scoped Wikimedia discovery, acquisition, and freshness authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from html.parser import HTMLParser
import http.client
from io import BytesIO
import ipaddress
import json
import socket
import ssl
from typing import Any, Callable, Literal, Mapping, Protocol
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from course_helper.artifacts import ArtifactError, ArtifactMetadata, ArtifactStore
from course_helper.catalog import (
    CatalogReferenceError,
    ImmutableVersionConflict,
    KnowledgeCatalog,
    canonical_model_json,
)
from course_helper.domain.common import ActorRef
from course_helper.domain.composition import canonical_digest
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject, LineageEdge
from course_helper.domain.sources import VisualAssetVersion
from course_helper.domain.visual_policy import TrustedExternalLink, canonical_external_url_identity
from course_helper.source_roots import candidate_logical_id, candidate_version_id


Clock = Callable[[], datetime]
PROVIDER = "wikimedia-commons"
PRODUCER = "course-helper/network-visuals"
PRODUCER_VERSION = "1"
_ACTOR = ActorRef(actor_type="service", actor_id=PRODUCER)
_CANDIDATE_TTL = timedelta(minutes=10)
_VERIFICATION_TTL = timedelta(hours=24)
_MAX_SEARCH_RESULTS = 10
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_API_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
_LICENSES = {
    "CC BY 4.0": ("CC-BY-4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "CC BY-SA 4.0": ("CC-BY-SA-4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "CC BY 3.0": ("CC-BY-3.0", "https://creativecommons.org/licenses/by/3.0/"),
    "CC BY-SA 3.0": ("CC-BY-SA-3.0", "https://creativecommons.org/licenses/by-sa/3.0/"),
    "CC0": ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "CC0 1.0": ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "Public domain": ("PDM-1.0", "https://creativecommons.org/publicdomain/mark/1.0/"),
    "GFDL": ("GFDL-1.2+", "https://www.gnu.org/copyleft/fdl.html"),
}


class NetworkVisualError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class ProviderNotFound(NetworkVisualError):
    def __init__(self) -> None:
        super().__init__("PROVIDER_NOT_FOUND", "Provider visual was not found")


class ProviderDownload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    body: bytes = Field(min_length=1, max_length=_MAX_IMAGE_BYTES)
    final_url: str = Field(min_length=1, max_length=2048)
    media_type: str = Field(min_length=1, max_length=100)
    content_length: int = Field(ge=1, le=_MAX_IMAGE_BYTES)
    redirect_count: int = Field(ge=0, le=3)
    resolved_host_count: int = Field(ge=1, le=4)

    @model_validator(mode="after")
    def exact_size(self) -> ProviderDownload:
        if len(self.body) != self.content_length:
            raise ValueError("provider download length does not match its bytes")
        return self


class WikimediaFileMetadata(BaseModel):
    """Server-only normalized provider response; media URL never reaches API output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    page_id: int = Field(ge=1)
    file_title: str = Field(min_length=6, max_length=500)
    media_url: str = Field(min_length=1, max_length=2048)
    landing_url: str = Field(min_length=1, max_length=2048)
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    byte_size: int = Field(ge=1, le=_MAX_IMAGE_BYTES)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    provider_sha1: str = Field(pattern=r"^[0-9a-f]{40}$")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=1000)
    creator: str = Field(min_length=1, max_length=500)
    license_id: Literal[
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC-BY-3.0",
        "CC-BY-SA-3.0",
        "CC0-1.0",
        "PDM-1.0",
        "GFDL-1.2+",
    ]
    license_url: str = Field(min_length=1, max_length=2048)

    @field_validator("file_title")
    @classmethod
    def safe_file_title(cls, value: str) -> str:
        if (
            not value.startswith("File:")
            or any(ord(char) < 32 for char in value)
            or any(char in value for char in "<>")
        ):
            raise ValueError("provider file title is unsafe")
        return value

    @model_validator(mode="after")
    def provider_urls(self) -> WikimediaFileMetadata:
        for value, host in (
            (self.media_url, "upload.wikimedia.org"),
            (self.landing_url, "commons.wikimedia.org"),
        ):
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or parsed.hostname != host
                or parsed.port not in {None, 443}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or parsed.query
                or any(character.isspace() for character in value)
            ):
                raise ValueError("provider URL is outside its HTTPS host policy")
        if not self.media_url.startswith("https://upload.wikimedia.org/wikipedia/commons/"):
            raise ValueError("provider media path is outside its policy")
        if not self.landing_url.startswith("https://commons.wikimedia.org/wiki/File:"):
            raise ValueError("provider landing path is outside its policy")
        expected_license = next(
            value[1] for value in _LICENSES.values() if value[0] == self.license_id
        )
        if self.license_url.rstrip("/") != expected_license.rstrip("/"):
            raise ValueError("provider license URL does not match the normalized license")
        return self


class NetworkVisualCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^network-candidate-[0-9a-f]{64}$")
    provider: Literal["wikimedia-commons"] = PROVIDER
    provider_page_id: int = Field(ge=1)
    file_title: str = Field(min_length=6, max_length=500)
    query_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    license_id: str = Field(min_length=1, max_length=80)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def bounded_lifetime(self) -> NetworkVisualCandidate:
        if (
            self.created_at.utcoffset() is None
            or self.expires_at.utcoffset() is None
            or self.expires_at - self.created_at != _CANDIDATE_TTL
        ):
            raise ValueError("network candidate lifetime is invalid")
        return self


class NetworkVisualAcquisition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    acquisition_id: str = Field(pattern=r"^network-acquisition-[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^network-candidate-[0-9a-f]{64}$")
    visual_version_id: str = Field(min_length=1, max_length=128)
    artifact_id: str = Field(pattern=r"^artifact-[0-9a-f]{64}$")
    evidence_id: str = Field(pattern=r"^network-visual-evidence-[0-9a-f]{64}$")
    provider: Literal["wikimedia-commons"] = PROVIDER
    provider_page_id: int = Field(ge=1)
    metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_sha1: str = Field(pattern=r"^[0-9a-f]{40}$")
    license_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    creator: str = Field(min_length=1, max_length=500)
    landing_link: TrustedExternalLink
    license_link: TrustedExternalLink
    acquired_at: datetime


class NetworkVisualVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    visual_version_id: str = Field(min_length=1, max_length=128)
    status: Literal["verified", "expired", "removed", "license-changed", "content-changed"]
    metadata_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_sha1: str = Field(pattern=r"^[0-9a-f]{40}$")
    license_id: str = Field(min_length=1, max_length=80)
    verified_at: datetime
    expires_at: datetime
    revision: int = Field(ge=1)
    evidence_id: str = Field(pattern=r"^network-visual-evidence-[0-9a-f]{64}$")

    @model_validator(mode="after")
    def aware_projection(self) -> NetworkVisualVerification:
        if self.verified_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("network verification timestamps must be timezone-aware")
        if self.expires_at - self.verified_at != _VERIFICATION_TTL:
            raise ValueError("network verification TTL is invalid")
        return self


@dataclass(frozen=True)
class NetworkVisualOutcome:
    subject_id: str
    status: Literal["acquired", "revalidated", "failed"]
    acquisition: NetworkVisualAcquisition | None = None
    verification: NetworkVisualVerification | None = None
    artifact_id: str | None = None
    visual_version_id: str | None = None
    evidence_id: str | None = None
    reused: bool = False
    error_code: str | None = None
    message: str | None = None


class WikimediaProvider(Protocol):
    def search(self, query: str, limit: int) -> tuple[WikimediaFileMetadata, ...]: ...
    def lookup(self, page_id: int) -> WikimediaFileMetadata: ...
    def download(self, media_url: str, max_bytes: int) -> ProviderDownload: ...


class JsonTransport(Protocol):
    def get_json(self, url: str, *, max_bytes: int) -> Mapping[str, Any]: ...
    def get_bytes(self, url: str, *, max_bytes: int) -> ProviderDownload: ...


@dataclass(frozen=True)
class ProviderHttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


Resolver = Callable[..., list[tuple[int, int, int, str, tuple[Any, ...]]]]
Requester = Callable[[str, str, int], ProviderHttpResponse]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, *, pinned_ip: str, timeout: float) -> None:
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _live_request(url: str, pinned_ip: str, max_bytes: int) -> ProviderHttpResponse:
    parsed = urlsplit(url)
    if parsed.hostname is None:
        raise NetworkVisualError("NETWORK_URL_REJECTED", "Provider URL is invalid")
    connection = _PinnedHTTPSConnection(parsed.hostname, pinned_ip=pinned_ip, timeout=15.0)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request(
            "GET",
            target,
            headers={
                "Accept": "application/json,image/png,image/jpeg,image/webp",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
                "User-Agent": "CourseStudio/0.1 (local educational visual verifier)",
            },
        )
        response = connection.getresponse()
        headers = tuple((name.lower(), value.strip()) for name, value in response.getheaders())
        body = bytearray()
        while True:
            block = response.read(min(64 * 1024, max_bytes + 1 - len(body)))
            if not block:
                break
            body.extend(block)
            if len(body) > max_bytes:
                raise NetworkVisualError("NETWORK_RESPONSE_OVERSIZE", "Provider response is oversized")
        return ProviderHttpResponse(status=response.status, headers=headers, body=bytes(body))
    except NetworkVisualError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
        raise NetworkVisualError("NETWORK_REQUEST_FAILED", "Provider request failed") from error
    finally:
        connection.close()


class PinnedHttpsTransport:
    """Direct HTTPS transport pinned to one prevalidated public DNS answer per hop."""

    def __init__(
        self,
        *,
        resolver: Resolver = socket.getaddrinfo,
        requester: Requester = _live_request,
        max_redirects: int = 3,
    ) -> None:
        if not 0 <= max_redirects <= 3:
            raise ValueError("provider redirect limit is invalid")
        self._resolver = resolver
        self._requester = requester
        self._max_redirects = max_redirects

    @staticmethod
    def _validate_url(url: str, *, kind: Literal["api", "media"]) -> str:
        parsed = urlsplit(url)
        expected_host = "commons.wikimedia.org" if kind == "api" else "upload.wikimedia.org"
        expected_path = "/w/api.php" if kind == "api" else "/wikipedia/commons/"
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(character.isspace() for character in url)
            or (parsed.path != expected_path if kind == "api" else not parsed.path.startswith(expected_path))
            or (kind == "media" and parsed.query)
        ):
            raise NetworkVisualError("NETWORK_URL_REJECTED", "Provider URL is outside its policy")
        return expected_host

    def _public_addresses(self, host: str) -> tuple[str, ...]:
        try:
            answers = self._resolver(
                host,
                443,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as error:
            raise NetworkVisualError("NETWORK_DNS_FAILED", "Provider DNS resolution failed") from error
        addresses: set[str] = set()
        for answer in answers:
            try:
                address = str(answer[4][0])
                parsed = ipaddress.ip_address(address)
            except (IndexError, TypeError, ValueError) as error:
                raise NetworkVisualError("NETWORK_DNS_REJECTED", "Provider DNS answer is invalid") from error
            if not parsed.is_global:
                raise NetworkVisualError("NETWORK_DNS_REJECTED", "Provider DNS answer is not public")
            addresses.add(parsed.compressed)
        if not addresses or len(addresses) > 8:
            raise NetworkVisualError("NETWORK_DNS_REJECTED", "Provider DNS answer count is invalid")
        return tuple(sorted(addresses))

    @staticmethod
    def _headers(values: tuple[tuple[str, str], ...]) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in values:
            key = name.casefold()
            if key in result:
                if key in {"content-length", "content-type", "content-encoding", "location"}:
                    raise NetworkVisualError("NETWORK_HEADERS_REJECTED", "Provider response has duplicate security headers")
                continue
            if any(ord(character) < 32 and character != "\t" for character in value):
                raise NetworkVisualError("NETWORK_HEADERS_REJECTED", "Provider response header is invalid")
            result[key] = value
        if result.get("content-encoding", "identity").casefold() != "identity":
            raise NetworkVisualError("NETWORK_HEADERS_REJECTED", "Compressed provider responses are unsupported")
        return result

    def _get(self, url: str, *, kind: Literal["api", "media"], max_bytes: int) -> tuple[bytes, str, str, int, int]:
        if max_bytes < 1:
            raise ValueError("network response ceiling must be positive")
        current = url
        resolved_hops = 0
        for depth in range(self._max_redirects + 1):
            host = self._validate_url(current, kind=kind)
            addresses = self._public_addresses(host)
            resolved_hops += 1
            response = self._requester(current, addresses[0], max_bytes)
            headers = self._headers(response.headers)
            if len(response.body) > max_bytes:
                raise NetworkVisualError("NETWORK_RESPONSE_OVERSIZE", "Provider response is oversized")
            content_length = headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError as error:
                    raise NetworkVisualError("NETWORK_HEADERS_REJECTED", "Provider content length is invalid") from error
                if declared < 0 or declared > max_bytes or declared != len(response.body):
                    raise NetworkVisualError("NETWORK_RESPONSE_OVERSIZE", "Provider response length is invalid")
            if response.status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if location is None or depth == self._max_redirects:
                    raise NetworkVisualError("NETWORK_REDIRECT_REJECTED", "Provider redirect chain is invalid")
                current = urljoin(current, location)
                continue
            if response.status != 200:
                if response.status == 404:
                    raise ProviderNotFound()
                raise NetworkVisualError("NETWORK_STATUS_REJECTED", "Provider returned an unexpected status")
            media_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            return response.body, current, media_type, depth, resolved_hops
        raise NetworkVisualError("NETWORK_REDIRECT_REJECTED", "Provider redirect chain is invalid")

    def get_json(self, url: str, *, max_bytes: int) -> Mapping[str, Any]:
        body, _final, media_type, _redirects, _resolved = self._get(
            url, kind="api", max_bytes=max_bytes
        )
        if media_type != "application/json":
            raise NetworkVisualError("NETWORK_MIME_REJECTED", "Provider API did not return JSON")

        def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider JSON repeats a key")
                result[key] = value
            return result

        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=no_duplicates)
        except NetworkVisualError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider JSON is invalid") from error
        if not isinstance(value, Mapping):
            raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider JSON root is invalid")
        return value

    def get_bytes(self, url: str, *, max_bytes: int) -> ProviderDownload:
        body, final_url, media_type, redirects, resolved = self._get(
            url, kind="media", max_bytes=max_bytes
        )
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise NetworkVisualError("NETWORK_MIME_REJECTED", "Provider media type is unsupported")
        return ProviderDownload(
            body=body,
            final_url=final_url,
            media_type=media_type,
            content_length=len(body),
            redirect_count=redirects,
            resolved_host_count=resolved,
        )


class WikimediaApiClient:
    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def search(self, query: str, limit: int) -> tuple[WikimediaFileMetadata, ...]:
        url = _API_ENDPOINT + "?" + urlencode(
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": query,
                "gsrlimit": str(limit),
                "prop": "imageinfo",
                "iiprop": "url|mime|size|sha1|extmetadata",
            }
        )
        payload = self._transport.get_json(url, max_bytes=1024 * 1024)
        return _parse_pages(payload, limit=limit, skip_unusable=True)

    def lookup(self, page_id: int) -> WikimediaFileMetadata:
        url = _API_ENDPOINT + "?" + urlencode(
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "pageids": str(page_id),
                "prop": "imageinfo",
                "iiprop": "url|mime|size|sha1|extmetadata",
            }
        )
        values = _parse_pages(self._transport.get_json(url, max_bytes=1024 * 1024), limit=1)
        if len(values) != 1 or values[0].page_id != page_id:
            raise ProviderNotFound()
        return values[0]

    def download(self, media_url: str, max_bytes: int) -> ProviderDownload:
        return self._transport.get_bytes(media_url, max_bytes=max_bytes)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.forbidden = False

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "iframe", "object", "embed"}:
            self.forbidden = True

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _safe_text(value: object, *, fallback: str, limit: int) -> str:
    if not isinstance(value, str):
        value = ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
    except Exception as error:
        raise NetworkVisualError("PROVIDER_METADATA_INVALID", "Provider metadata is invalid") from error
    text = " ".join("".join(parser.parts).split()) or fallback
    if parser.forbidden or len(text) > limit or any(ord(char) < 32 for char in text):
        raise NetworkVisualError("PROVIDER_METADATA_INVALID", "Provider metadata is invalid")
    return text


def _ext_value(extmetadata: Mapping[str, Any], key: str) -> object:
    item = extmetadata.get(key)
    return item.get("value") if isinstance(item, Mapping) else None


def _parse_page(value: object) -> WikimediaFileMetadata:
    if not isinstance(value, Mapping) or value.get("missing") is True:
        raise ProviderNotFound()
    imageinfo = value.get("imageinfo")
    if not isinstance(imageinfo, list) or len(imageinfo) != 1 or not isinstance(imageinfo[0], Mapping):
        raise NetworkVisualError("PROVIDER_METADATA_INVALID", "Provider image metadata is invalid")
    info = imageinfo[0]
    extmetadata = info.get("extmetadata")
    if not isinstance(extmetadata, Mapping):
        raise NetworkVisualError("PROVIDER_METADATA_INVALID", "Provider license metadata is invalid")
    short_name = _safe_text(_ext_value(extmetadata, "LicenseShortName"), fallback="", limit=80)
    license_value = _LICENSES.get(short_name)
    if license_value is None:
        raise NetworkVisualError("UNKNOWN_LICENSE", "Provider license is not allowlisted")
    license_id, license_url = license_value
    provider_license_url = _ext_value(extmetadata, "LicenseUrl")
    if not isinstance(provider_license_url, str) or provider_license_url.rstrip("/") != license_url.rstrip("/"):
        raise NetworkVisualError("LICENSE_MISMATCH", "Provider license URL is not authoritative")
    title = str(value.get("title", ""))
    if not title.startswith("File:"):
        raise NetworkVisualError("PROVIDER_METADATA_INVALID", "Provider file identity is invalid")
    try:
        return WikimediaFileMetadata(
            page_id=value.get("pageid"),
            file_title=title,
            media_url=info.get("url"),
            landing_url=info.get("descriptionurl"),
            media_type=info.get("mime"),
            byte_size=info.get("size"),
            width=info.get("width"),
            height=info.get("height"),
            provider_sha1=str(info.get("sha1", "")).lower(),
            title=_safe_text(_ext_value(extmetadata, "ObjectName"), fallback=title[5:], limit=500),
            description=_safe_text(_ext_value(extmetadata, "ImageDescription"), fallback=title[5:], limit=1000),
            creator=_safe_text(_ext_value(extmetadata, "Artist"), fallback="Unknown creator", limit=500),
            license_id=license_id,
            license_url=license_url,
        )
    except ValidationError as error:
        raise NetworkVisualError(
            "PROVIDER_METADATA_INVALID", "Provider image metadata is invalid"
        ) from error


def _parse_pages(
    payload: Mapping[str, Any],
    *,
    limit: int,
    skip_unusable: bool = False,
) -> tuple[WikimediaFileMetadata, ...]:
    if set(payload) - {"batchcomplete", "continue", "query", "warnings"}:
        raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider response has unexpected fields")
    query = payload.get("query")
    pages = query.get("pages") if isinstance(query, Mapping) else None
    if not isinstance(pages, list):
        raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider response has no pages")
    if len(pages) > limit:
        raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider response exceeds its result limit")
    results: list[WikimediaFileMetadata] = []
    seen: set[int] = set()
    for page in pages[: limit + 1]:
        try:
            item = _parse_page(page)
        except ProviderNotFound:
            continue
        except NetworkVisualError as error:
            if skip_unusable and error.code in {
                "UNKNOWN_LICENSE",
                "LICENSE_MISMATCH",
            }:
                continue
            raise
        if item.page_id in seen:
            raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider response repeats a page")
        seen.add(item.page_id)
        results.append(item)
    if len(results) > limit:
        raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider response exceeds its result limit")
    return tuple(results)


def _metadata_digest(metadata: WikimediaFileMetadata) -> str:
    return canonical_digest(metadata.model_dump(mode="json"))


def _clock(clock: Clock) -> datetime:
    value = clock()
    if value.utcoffset() is None:
        raise ValueError("network visual clock must be timezone-aware")
    return value


def _candidate_payload(candidate: NetworkVisualCandidate) -> tuple[str, str]:
    payload = canonical_model_json(candidate)
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _register_candidate(catalog: KnowledgeCatalog, candidate: NetworkVisualCandidate) -> None:
    payload, digest = _candidate_payload(candidate)
    with catalog.atomic_write():
        row = catalog.connection.execute(
            "SELECT payload_json, content_digest FROM network_visual_candidates WHERE candidate_id = ?",
            (candidate.candidate_id,),
        ).fetchone()
        if row is not None:
            if row != (payload, digest):
                raise ImmutableVersionConflict("network candidate identity conflict")
            return
        catalog.connection.execute(
            "INSERT INTO network_visual_candidates(candidate_id, provider, provider_page_id, query_digest, metadata_digest, content_digest, payload_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate.candidate_id,
                candidate.provider,
                candidate.provider_page_id,
                candidate.query_digest,
                candidate.metadata_digest,
                digest,
                payload,
                candidate.created_at.isoformat(),
                candidate.expires_at.isoformat(),
            ),
        )


def discover_network_visuals(
    catalog: KnowledgeCatalog,
    provider: WikimediaProvider,
    *,
    query: str,
    limit: int = 5,
    clock: Clock,
) -> tuple[NetworkVisualCandidate, ...]:
    normalized_query = " ".join(query.split()) if isinstance(query, str) else ""
    if (
        not 2 <= len(normalized_query) <= 120
        or any(ord(char) < 32 for char in normalized_query)
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _MAX_SEARCH_RESULTS
    ):
        raise ValueError("network visual search is invalid or oversized")
    now = _clock(clock)
    query_digest = canonical_digest({"provider": PROVIDER, "query": normalized_query})
    candidates: list[NetworkVisualCandidate] = []
    results = provider.search(normalized_query, limit)
    if len(results) > limit or len({item.page_id for item in results}) != len(results):
        raise NetworkVisualError("PROVIDER_RESPONSE_INVALID", "Provider search result is invalid")
    for metadata in results:
        metadata_digest = _metadata_digest(metadata)
        identity = canonical_digest(
            {
                "provider": PROVIDER,
                "page_id": metadata.page_id,
                "query_digest": query_digest,
                "metadata_digest": metadata_digest,
                "created_at": now.isoformat(),
            }
        )
        candidate = NetworkVisualCandidate(
            candidate_id="network-candidate-" + identity,
            provider_page_id=metadata.page_id,
            file_title=metadata.file_title,
            query_digest=query_digest,
            metadata_digest=metadata_digest,
            media_type=metadata.media_type,
            width=metadata.width,
            height=metadata.height,
            license_id=metadata.license_id,
            created_at=now,
            expires_at=now + _CANDIDATE_TTL,
        )
        _register_candidate(catalog, candidate)
        candidates.append(candidate)
    return tuple(candidates)


def _load_candidate(catalog: KnowledgeCatalog, candidate_id: str) -> NetworkVisualCandidate:
    row = catalog.connection.execute(
        "SELECT provider, provider_page_id, query_digest, metadata_digest, content_digest, payload_json, created_at, expires_at FROM network_visual_candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise NetworkVisualError("CANDIDATE_NOT_FOUND", "Network visual candidate was not found")
    try:
        candidate = NetworkVisualCandidate.model_validate_json(row[5])
    except ValidationError as error:
        raise NetworkVisualError("CANDIDATE_INVALID", "Network visual candidate is invalid") from error
    payload, digest = _candidate_payload(candidate)
    if (
        candidate.candidate_id != candidate_id
        or canonical_model_json(candidate) != row[5]
        or digest != row[4]
        or (candidate.provider, candidate.provider_page_id, candidate.query_digest, candidate.metadata_digest)
        != tuple(row[:4])
        or (candidate.created_at.isoformat(), candidate.expires_at.isoformat()) != tuple(row[6:8])
    ):
        raise NetworkVisualError("CANDIDATE_INVALID", "Network visual candidate is invalid")
    return candidate


def _links(metadata: WikimediaFileMetadata) -> tuple[TrustedExternalLink, TrustedExternalLink]:
    identity = canonical_digest({"provider": PROVIDER, "page_id": metadata.page_id})
    return (
        TrustedExternalLink(
            link_id="landing-" + identity,
            link_type="landing",
            href=metadata.landing_url,
            provenance_kind="licensed-secondary",
            label="Wikimedia Commons file page",
        ),
        TrustedExternalLink(
            link_id="license-" + canonical_digest({"license_id": metadata.license_id}),
            link_type="license",
            href=metadata.license_url,
            provenance_kind="licensed-secondary",
            label=metadata.license_id,
        ),
    )


def _verification_evidence(
    *,
    visual_version_id: str,
    status: str,
    metadata_digest: str,
    provider_sha1: str,
    license_id: str,
    revision: int,
    now: datetime,
) -> EvidenceObject:
    semantics = {
        "visual_version_id": visual_version_id,
        "status": status,
        "metadata_digest": metadata_digest,
        "provider_sha1": provider_sha1,
        "license_id": license_id,
        "revision": revision,
        "verified_at": now.isoformat(),
    }
    evidence_id = "network-visual-evidence-" + canonical_digest(semantics)
    return EvidenceObject(
        evidence_id=evidence_id,
        kind="validation",
        subject_version_id=visual_version_id,
        status="verified" if status == "verified" else "warning",
        input_summary={
            "provider": PROVIDER,
            "metadata_digest": metadata_digest,
            "provider_sha1": provider_sha1,
            "license_id": license_id,
            "revision": revision,
        },
        output_summary={
            "visual_version_id": visual_version_id,
            "verification_status": status,
            "freshness_hours": 24,
        },
        producer=PRODUCER,
        producer_version=PRODUCER_VERSION,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="provider-metadata",
                status="passed" if status == "verified" else "warning",
                message="Authoritative provider metadata was revalidated",
                details={"status": status},
            ),
        ),
    )


def _acquisition_evidence(
    *,
    visual_version_id: str,
    artifact: ArtifactMetadata,
    metadata: WikimediaFileMetadata,
    metadata_digest: str,
    download: ProviderDownload,
    now: datetime,
) -> EvidenceObject:
    semantics = {
        "visual_version_id": visual_version_id,
        "artifact_id": artifact.artifact_id,
        "artifact_digest": artifact.content_digest,
        "provider_page_id": metadata.page_id,
        "metadata_digest": metadata_digest,
        "provider_sha1": metadata.provider_sha1,
        "license_id": metadata.license_id,
    }
    return EvidenceObject(
        evidence_id="network-visual-evidence-" + canonical_digest(semantics),
        kind="validation",
        subject_version_id=visual_version_id,
        status="verified",
        input_summary={
            "provider": PROVIDER,
            "provider_page_id": metadata.page_id,
            "metadata_digest": metadata_digest,
            "provider_sha1": metadata.provider_sha1,
            "license_id": metadata.license_id,
        },
        output_summary={
            "visual_version_id": visual_version_id,
            "artifact_id": artifact.artifact_id,
            "artifact_digest": artifact.content_digest,
            "media_type": artifact.media_type,
            "byte_size": artifact.byte_size,
            "width": artifact.width,
            "height": artifact.height,
        },
        producer=PRODUCER,
        producer_version=PRODUCER_VERSION,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        checks=(
            EvidenceCheck(
                code="provider-hosts-public",
                status="passed",
                message="Every provider request used a validated public HTTPS host",
                details={"resolved_host_count": download.resolved_host_count},
            ),
            EvidenceCheck(
                code="redirect-chain",
                status="passed",
                message="Every bounded redirect remained inside the media host policy",
                details={"redirect_count": download.redirect_count},
            ),
            EvidenceCheck(
                code="provider-media-digest",
                status="passed",
                message="Downloaded bytes match provider SHA-1 and local SHA-256 metadata",
                details={"provider_sha1": metadata.provider_sha1},
            ),
            EvidenceCheck(
                code="provider-license",
                status="passed",
                message="Provider license identity and canonical license link were verified",
                details={"license_id": metadata.license_id},
            ),
        ),
    )


def _projection(
    *,
    visual_version_id: str,
    status: Literal["verified", "removed", "license-changed", "content-changed"],
    metadata_digest: str,
    provider_sha1: str,
    license_id: str,
    revision: int,
    evidence_id: str,
    now: datetime,
) -> NetworkVisualVerification:
    return NetworkVisualVerification(
        visual_version_id=visual_version_id,
        status=status,
        metadata_digest=metadata_digest,
        provider_sha1=provider_sha1,
        license_id=license_id,
        verified_at=now,
        expires_at=now + _VERIFICATION_TTL,
        revision=revision,
        evidence_id=evidence_id,
    )


def _store_projection(
    catalog: KnowledgeCatalog,
    projection: NetworkVisualVerification,
    *,
    expected_revision: int,
) -> None:
    payload = canonical_model_json(projection)
    if expected_revision == 0:
        catalog.connection.execute(
            "INSERT INTO network_visual_verifications(visual_version_id, status, metadata_digest, provider_sha1, license_id, verified_at, expires_at, revision, evidence_id, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                projection.visual_version_id,
                projection.status,
                projection.metadata_digest,
                projection.provider_sha1,
                projection.license_id,
                projection.verified_at.isoformat(),
                projection.expires_at.isoformat(),
                projection.revision,
                projection.evidence_id,
                payload,
                projection.verified_at.isoformat(),
            ),
        )
        return
    cursor = catalog.connection.execute(
        "UPDATE network_visual_verifications SET status = ?, metadata_digest = ?, provider_sha1 = ?, license_id = ?, verified_at = ?, expires_at = ?, revision = ?, evidence_id = ?, payload_json = ?, updated_at = ? WHERE visual_version_id = ? AND revision = ?",
        (
            projection.status,
            projection.metadata_digest,
            projection.provider_sha1,
            projection.license_id,
            projection.verified_at.isoformat(),
            projection.expires_at.isoformat(),
            projection.revision,
            projection.evidence_id,
            payload,
            projection.verified_at.isoformat(),
            projection.visual_version_id,
            expected_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise ImmutableVersionConflict("network visual verification revision changed")


def _acquire_one(
    catalog: KnowledgeCatalog,
    provider: WikimediaProvider,
    artifact_store: ArtifactStore,
    candidate_id: str,
    *,
    clock: Clock,
) -> NetworkVisualOutcome:
    candidate = _load_candidate(catalog, candidate_id)
    now = _clock(clock)
    if now > candidate.expires_at:
        raise NetworkVisualError("CANDIDATE_EXPIRED", "Network visual candidate expired")
    metadata = provider.lookup(candidate.provider_page_id)
    metadata_digest = _metadata_digest(metadata)
    if metadata.page_id != candidate.provider_page_id or metadata_digest != candidate.metadata_digest:
        raise NetworkVisualError("CANDIDATE_STALE", "Network visual candidate metadata changed")
    download = provider.download(metadata.media_url, _MAX_IMAGE_BYTES)
    if (
        canonical_external_url_identity(download.final_url)
        != canonical_external_url_identity(metadata.media_url)
        or download.media_type != metadata.media_type
        or download.content_length != metadata.byte_size
        or hashlib.sha1(download.body).hexdigest() != metadata.provider_sha1
    ):
        raise NetworkVisualError("MEDIA_MISMATCH", "Provider media bytes do not match metadata")
    write = artifact_store.put_stream(
        BytesIO(download.body),
        declared_media_type=metadata.media_type,
        clock=lambda: now,
        byte_size_hint=metadata.byte_size,
    )
    artifact = write.metadata
    existing_artifact = catalog.get_artifact(artifact.artifact_id)
    metadata_reused = False
    if existing_artifact is not None:
        stored = existing_artifact.payload
        if (
            stored.content_digest,
            stored.byte_size,
            stored.media_type,
            stored.width,
            stored.height,
        ) != (
            artifact.content_digest,
            artifact.byte_size,
            artifact.media_type,
            artifact.width,
            artifact.height,
        ):
            raise NetworkVisualError("ARTIFACT_CONFLICT", "Stored artifact metadata changed")
        artifact = stored
        metadata_reused = True
    artifact_store.verify(artifact)
    if (artifact.width, artifact.height) != (metadata.width, metadata.height):
        raise NetworkVisualError("MEDIA_MISMATCH", "Provider media dimensions do not match metadata")
    logical_id = candidate_logical_id("visual", f"{PROVIDER}:page:{metadata.page_id}")
    visual_version_id = candidate_version_id(
        logical_id, ("provider-metadata:" + metadata_digest,), artifact.content_digest
    )
    landing_link, license_link = _links(metadata)
    visual = VisualAssetVersion(
        logical_id=logical_id,
        version_id=visual_version_id,
        revision=1,
        content_digest=artifact.content_digest,
        created_at=artifact.created_at,
        created_by=_ACTOR,
        media_type=artifact.media_type,
        width=artifact.width,
        height=artifact.height,
        alt_text=metadata.description,
        publisher="Wikimedia Commons",
        author=metadata.creator,
        license_status="licensed",
        authenticity="licensed-secondary",
        usage_scope=("private-training", "internal", "public"),
    )
    evidence = _acquisition_evidence(
        visual_version_id=visual.version_id,
        artifact=artifact,
        metadata=metadata,
        metadata_digest=metadata_digest,
        download=download,
        now=artifact.created_at,
    )
    acquisition_id = "network-acquisition-" + canonical_digest(
        {
            "candidate_id": candidate.candidate_id,
            "visual_version_id": visual.version_id,
            "artifact_id": artifact.artifact_id,
            "metadata_digest": metadata_digest,
        }
    )
    acquisition = NetworkVisualAcquisition(
        acquisition_id=acquisition_id,
        candidate_id=candidate.candidate_id,
        visual_version_id=visual.version_id,
        artifact_id=artifact.artifact_id,
        evidence_id=evidence.evidence_id,
        provider_page_id=metadata.page_id,
        metadata_digest=metadata_digest,
        provider_sha1=metadata.provider_sha1,
        license_id=metadata.license_id,
        title=metadata.title,
        creator=metadata.creator,
        landing_link=landing_link,
        license_link=license_link,
        acquired_at=artifact.created_at,
    )
    projection = _projection(
        visual_version_id=visual.version_id,
        status="verified",
        metadata_digest=metadata_digest,
        provider_sha1=metadata.provider_sha1,
        license_id=metadata.license_id,
        revision=1,
        evidence_id=evidence.evidence_id,
        now=artifact.created_at,
    )
    acquisition_payload = canonical_model_json(acquisition)
    acquisition_digest = hashlib.sha256(acquisition_payload.encode("utf-8")).hexdigest()
    with catalog.atomic_write():
        current = catalog.connection.execute(
            "SELECT payload_json, content_digest FROM network_visual_acquisitions WHERE acquisition_id = ?",
            (acquisition.acquisition_id,),
        ).fetchone()
        if current is not None:
            if current != (acquisition_payload, acquisition_digest):
                raise ImmutableVersionConflict("network acquisition identity conflict")
            return NetworkVisualOutcome(
                subject_id=candidate_id,
                status="acquired",
                acquisition=acquisition,
                verification=current_network_visual_verification(catalog, visual.version_id, now=now),
                artifact_id=artifact.artifact_id,
                visual_version_id=visual.version_id,
                evidence_id=evidence.evidence_id,
                reused=True,
            )
        catalog.register_artifact(artifact)
        catalog.insert_visual(visual)
        catalog.insert_evidence(evidence)
        catalog.connection.execute(
            "INSERT INTO network_visual_acquisitions(acquisition_id, candidate_id, visual_version_id, artifact_id, evidence_id, provider, provider_page_id, metadata_digest, provider_sha1, license_id, content_digest, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                acquisition.acquisition_id,
                acquisition.candidate_id,
                acquisition.visual_version_id,
                acquisition.artifact_id,
                acquisition.evidence_id,
                acquisition.provider,
                acquisition.provider_page_id,
                acquisition.metadata_digest,
                acquisition.provider_sha1,
                acquisition.license_id,
                acquisition_digest,
                acquisition_payload,
                acquisition.acquired_at.isoformat(),
            ),
        )
        _store_projection(catalog, projection, expected_revision=0)
        catalog.insert_lineage(
            LineageEdge(
                edge_id="network-visual-lineage-" + canonical_digest(
                    {"artifact_id": artifact.artifact_id, "visual_version_id": visual.version_id}
                ),
                from_version_id=artifact.artifact_id,
                to_version_id=visual.version_id,
                relation="derived_from",
                evidence_id=evidence.evidence_id,
                created_at=artifact.created_at,
            )
        )
    return NetworkVisualOutcome(
        subject_id=candidate_id,
        status="acquired",
        acquisition=acquisition,
        verification=projection,
        artifact_id=artifact.artifact_id,
        visual_version_id=visual.version_id,
        evidence_id=evidence.evidence_id,
        reused=write.reused or metadata_reused,
    )


def acquire_network_visuals(
    catalog: KnowledgeCatalog,
    provider: WikimediaProvider,
    artifact_store: ArtifactStore,
    candidate_ids: tuple[str, ...],
    *,
    clock: Clock,
) -> tuple[NetworkVisualOutcome, ...]:
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("network visual candidate IDs must be unique")
    outcomes: list[NetworkVisualOutcome] = []
    for candidate_id in candidate_ids:
        try:
            outcomes.append(
                _acquire_one(catalog, provider, artifact_store, candidate_id, clock=clock)
            )
        except NetworkVisualError as error:
            outcomes.append(NetworkVisualOutcome(
                subject_id=candidate_id,
                status="failed",
                error_code=error.code,
                message=error.safe_message,
            ))
        except ArtifactError:
            outcomes.append(NetworkVisualOutcome(
                subject_id=candidate_id,
                status="failed",
                error_code="ARTIFACT_REJECTED",
                message="Provider media artifact was rejected",
            ))
        except (CatalogReferenceError, ImmutableVersionConflict):
            outcomes.append(NetworkVisualOutcome(
                subject_id=candidate_id,
                status="failed",
                error_code="CATALOG_REJECTED",
                message="Network visual catalog registration was rejected",
            ))
    return tuple(outcomes)


def _load_acquisition(catalog: KnowledgeCatalog, visual_version_id: str) -> NetworkVisualAcquisition:
    row = catalog.connection.execute(
        "SELECT content_digest, payload_json FROM network_visual_acquisitions WHERE visual_version_id = ?",
        (visual_version_id,),
    ).fetchone()
    if row is None:
        raise NetworkVisualError("ACQUISITION_NOT_FOUND", "Network visual acquisition was not found")
    try:
        value = NetworkVisualAcquisition.model_validate_json(row[1])
    except ValidationError as error:
        raise NetworkVisualError("ACQUISITION_INVALID", "Network visual acquisition is invalid") from error
    payload = canonical_model_json(value)
    if (
        value.visual_version_id != visual_version_id
        or payload != row[1]
        or hashlib.sha256(payload.encode("utf-8")).hexdigest() != row[0]
    ):
        raise NetworkVisualError("ACQUISITION_INVALID", "Network visual acquisition is invalid")
    return value


def current_network_visual_verification(
    catalog: KnowledgeCatalog,
    visual_version_id: str,
    *,
    now: datetime,
) -> NetworkVisualVerification:
    if now.utcoffset() is None:
        raise ValueError("network verification clock must be timezone-aware")
    row = catalog.connection.execute(
        "SELECT status, metadata_digest, provider_sha1, license_id, verified_at, expires_at, revision, evidence_id, payload_json FROM network_visual_verifications WHERE visual_version_id = ?",
        (visual_version_id,),
    ).fetchone()
    if row is None:
        raise NetworkVisualError("VERIFICATION_NOT_FOUND", "Network visual verification was not found")
    try:
        value = NetworkVisualVerification.model_validate_json(row[8])
    except ValidationError as error:
        raise NetworkVisualError("VERIFICATION_INVALID", "Network visual verification is invalid") from error
    if canonical_model_json(value) != row[8] or (
        value.status,
        value.metadata_digest,
        value.provider_sha1,
        value.license_id,
        value.verified_at.isoformat(),
        value.expires_at.isoformat(),
        value.revision,
        value.evidence_id,
    ) != tuple(row[:8]):
        raise NetworkVisualError("VERIFICATION_INVALID", "Network visual verification is invalid")
    if value.status == "verified" and now > value.expires_at:
        return value.model_copy(update={"status": "expired"})
    return value


def revalidate_network_visual(
    catalog: KnowledgeCatalog,
    provider: WikimediaProvider,
    *,
    visual_version_id: str,
    clock: Clock,
) -> NetworkVisualOutcome:
    acquisition = _load_acquisition(catalog, visual_version_id)
    now = _clock(clock)
    current = current_network_visual_verification(catalog, visual_version_id, now=now)
    status: Literal["verified", "removed", "license-changed", "content-changed"]
    metadata_digest = current.metadata_digest
    provider_sha1 = current.provider_sha1
    license_id = current.license_id
    try:
        metadata = provider.lookup(acquisition.provider_page_id)
        metadata_digest = _metadata_digest(metadata)
        provider_sha1 = metadata.provider_sha1
        license_id = metadata.license_id
        if metadata.license_id != acquisition.license_id:
            status = "license-changed"
        elif metadata.provider_sha1 != acquisition.provider_sha1:
            status = "content-changed"
        else:
            status = "verified"
    except ProviderNotFound:
        status = "removed"
    except NetworkVisualError as error:
        if error.code in {"UNKNOWN_LICENSE", "LICENSE_MISMATCH"}:
            status = "license-changed"
        else:
            return NetworkVisualOutcome(
                subject_id=visual_version_id,
                status="failed",
                error_code=error.code,
                message=error.safe_message,
            )
    revision = current.revision + 1
    evidence = _verification_evidence(
        visual_version_id=visual_version_id,
        status=status,
        metadata_digest=metadata_digest,
        provider_sha1=provider_sha1,
        license_id=license_id,
        revision=revision,
        now=now,
    )
    projection = _projection(
        visual_version_id=visual_version_id,
        status=status,
        metadata_digest=metadata_digest,
        provider_sha1=provider_sha1,
        license_id=license_id,
        revision=revision,
        evidence_id=evidence.evidence_id,
        now=now,
    )
    try:
        with catalog.atomic_write():
            catalog.insert_evidence(evidence)
            _store_projection(catalog, projection, expected_revision=current.revision)
    except (CatalogReferenceError, ImmutableVersionConflict):
        return NetworkVisualOutcome(
            subject_id=visual_version_id,
            status="failed",
            error_code="CATALOG_REJECTED",
            message="Network visual verification update was rejected",
        )
    return NetworkVisualOutcome(
        subject_id=visual_version_id,
        status="revalidated",
        verification=projection,
        visual_version_id=visual_version_id,
        evidence_id=evidence.evidence_id,
    )


__all__ = [
    "NetworkVisualAcquisition",
    "NetworkVisualCandidate",
    "NetworkVisualError",
    "NetworkVisualOutcome",
    "NetworkVisualVerification",
    "PinnedHttpsTransport",
    "ProviderHttpResponse",
    "ProviderDownload",
    "ProviderNotFound",
    "WikimediaApiClient",
    "WikimediaFileMetadata",
    "acquire_network_visuals",
    "current_network_visual_verification",
    "discover_network_visuals",
    "revalidate_network_visual",
]

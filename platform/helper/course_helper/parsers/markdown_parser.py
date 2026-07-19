"""AST-first extraction from registered Markdown sources."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import posixpath
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit
from uuid import uuid5

from markdown_it import MarkdownIt, __version__ as MARKDOWN_IT_VERSION
from markdown_it.token import Token
from PIL import Image

from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject
from course_helper.domain.sources import (
    ChunkLocator,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.source_roots import (
    COURSE_STUDIO_ID_NAMESPACE,
    SourceRootRegistry,
    SourceRootViolation,
    candidate_logical_id,
    candidate_version_id,
    chunk_logical_id,
    chunk_version_id,
    source_logical_id,
    source_version_id,
    stream_sha256,
)


MARKDOWN_MEDIA_TYPE = "text/markdown"
PARSER_NAME = "markdown-it-py"
PARSER_PRODUCER = "course-helper/markdown-parser"
_ACTOR = ActorRef(actor_type="service", actor_id=PARSER_PRODUCER)


@dataclass(frozen=True)
class _ImageReference:
    target: str
    alt_text: str


@dataclass
class _Section:
    heading: str | None
    level: int
    heading_path: tuple[str, ...]
    ast_path: tuple[int, ...]
    blocks: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)
    table_rows: list[tuple[str, ...]] = field(default_factory=list)
    images: list[_ImageReference] = field(default_factory=list)

    def normalized_text(self) -> str:
        parts = ([self.heading] if self.heading else []) + self.blocks
        return _normalize_text("\n\n".join(part for part in parts if part.strip()))


@dataclass(frozen=True)
class _HeadingFrame:
    level: int
    heading: str
    ast_path: tuple[int, ...]


class MarkdownParser:
    """Parse allowlisted Markdown into immutable section chunks and evidence."""

    def __init__(self, source_roots: SourceRootRegistry) -> None:
        self._source_roots = source_roots

    def parse(
        self,
        locator: SourceLocator,
        heading_selectors: tuple[str, ...] = (),
    ) -> ExtractionResult:
        path = self._source_roots.resolve(locator)
        source_digest = stream_sha256(path)
        source_logical = source_logical_id(locator)
        source_version = source_version_id(source_logical, source_digest)
        source_time = _source_time(path)
        parser_config_digest = _parser_config_digest(heading_selectors)
        text = path.read_text(encoding="utf-8-sig")
        tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(text)
        sections = _selected_sections(_parse_sections(tokens), heading_selectors)

        chunks: list[ExtractedChunk] = []
        visuals: list[VisualAssetVersion] = []
        checks: list[EvidenceCheck] = []
        visual_by_version: dict[str, VisualAssetVersion] = {}

        for section in sections:
            normalized = section.normalized_text()
            chunk_locator: ChunkLocator | None = None
            chunk_digest: str | None = None
            chunk_id: str | None = None
            if normalized:
                chunk_locator = ChunkLocator(
                    kind="markdown-section",
                    ast_path=section.ast_path,
                    heading_path=section.heading_path,
                )
                chunk_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                chunk_logical = chunk_logical_id(source_logical, chunk_locator)
                chunk_id = chunk_version_id(
                    chunk_logical,
                    source_version,
                    chunk_digest,
                )
            media_version_ids: list[str] = []
            warnings: list[str] = []

            for image_index, image_reference in enumerate(section.images):
                image_locator = _relative_image_locator(locator, image_reference.target)
                image_path: Path | None = None
                if image_locator is not None:
                    try:
                        image_path = self._source_roots.resolve(image_locator)
                    except SourceRootViolation:
                        image_path = None
                if image_locator is None or image_path is None:
                    reason = (
                        _unsafe_image_target_reason(locator, image_reference.target)
                        if image_locator is None
                        else "not-found"
                    )
                    target_digest = hashlib.sha256(
                        image_reference.target.encode("utf-8")
                    ).hexdigest()
                    warning = (
                        f"Unresolved image link ({reason}; "
                        f"ref=sha256:{target_digest[:12]})"
                    )
                    warnings.append(warning)
                    checks.append(
                        EvidenceCheck(
                            code="unresolved-link",
                            status="warning",
                            message=warning,
                            details={
                                "target_digest": target_digest,
                                "reason": reason,
                                "heading_path": list(section.heading_path),
                            },
                        )
                    )
                    continue

                visual = _visual_from_image(
                    path=image_path,
                    locator=image_locator,
                    source_version_id_value=source_version,
                    chunk_version_id_value=chunk_id,
                    section=section,
                    image_index=image_index,
                    alt_text=image_reference.alt_text,
                    created_at=source_time,
                )
                visual = visual_by_version.setdefault(visual.version_id, visual)
                media_version_ids.append(visual.version_id)

            if chunk_locator is None or chunk_digest is None or chunk_id is None:
                continue
            chunks.append(
                ExtractedChunk(
                    chunk_id=chunk_id,
                    source_version_id=source_version,
                    ordinal=len(chunks),
                    modality="text",
                    language="und",
                    normalized_text=normalized,
                    content_digest=chunk_digest,
                    locator=chunk_locator,
                    breadcrumb=section.heading_path,
                    heading=section.heading,
                    code_blocks=tuple(section.code_blocks),
                    table_rows=tuple(section.table_rows),
                    media_version_ids=tuple(media_version_ids),
                    warnings=tuple(warnings),
                )
            )

        visuals.extend(visual_by_version.values())
        has_warnings = any(check.status == "warning" for check in checks)
        extraction_status = "partial" if has_warnings else "parsed"
        evidence_status = "warning" if has_warnings else "verified"
        source = SourceAssetVersion(
            logical_id=source_logical,
            version_id=source_version,
            revision=1,
            content_digest=source_digest,
            created_at=source_time,
            created_by=_ACTOR,
            locator=locator,
            display_name=Path(locator.relative_path).name,
            source_kind="markdown",
            media_type=MARKDOWN_MEDIA_TYPE,
            byte_size=path.stat().st_size,
            modified_at=source_time,
            content_summary=(
                f"{len(chunks)} Markdown section chunks and {len(visuals)} visual assets"
            ),
            extraction_status=extraction_status,
            parser_name=PARSER_NAME,
            parser_version=MARKDOWN_IT_VERSION,
            parser_config_digest=parser_config_digest,
        )
        checks.append(
            EvidenceCheck(
                code="markdown-extraction",
                status="warning" if has_warnings else "passed",
                message=(
                    f"Extracted {len(chunks)} Markdown sections and "
                    f"{len(visuals)} visual assets"
                ),
                details={
                    "heading_selectors": list(heading_selectors),
                    "chunk_count": len(chunks),
                    "visual_count": len(visuals),
                    "unresolved_link_count": sum(
                        check.code == "unresolved-link" for check in checks
                    ),
                },
            )
        )
        evidence = EvidenceObject(
            evidence_id=_evidence_id(source_version, parser_config_digest),
            kind="extraction",
            subject_version_id=source_version,
            status=evidence_status,
            input_summary={
                "source_locator": locator.model_dump(mode="json"),
                "heading_selectors": list(heading_selectors),
            },
            output_summary={
                "chunk_count": len(chunks),
                "visual_count": len(visuals),
                "unresolved_link_count": sum(
                    check.code == "unresolved-link" for check in checks
                ),
            },
            producer=PARSER_PRODUCER,
            producer_version="1",
            started_at=source_time,
            finished_at=source_time,
            duration_ms=0,
            checks=tuple(checks),
        )
        return ExtractionResult(
            source=source,
            chunks=tuple(chunks),
            visuals=tuple(visuals),
            evidence=evidence,
        )


def _parse_sections(tokens: list[Token]) -> tuple[_Section, ...]:
    sections: list[_Section] = []
    heading_stack: list[_HeadingFrame] = []
    current: _Section | None = None
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.type == "heading_open":
            level = int(token.tag[1:])
            inline = tokens[index + 1]
            heading = _inline_plain_text(inline).strip()
            while heading_stack and heading_stack[-1].level >= level:
                heading_stack.pop()
            parent_path = heading_stack[-1].ast_path if heading_stack else ()
            source_position = token.map[0] if token.map is not None else index
            ast_path = parent_path + (source_position,)
            frame = _HeadingFrame(level=level, heading=heading, ast_path=ast_path)
            heading_stack.append(frame)
            current = _Section(
                heading=heading,
                level=level,
                heading_path=tuple(item.heading for item in heading_stack),
                ast_path=ast_path,
            )
            current.images.extend(_inline_images(inline))
            sections.append(current)
            index += 3
            continue

        if current is None and token.type in {"paragraph_open", "fence", "table_open"}:
            current = _Section(heading=None, level=0, heading_path=(), ast_path=(0,))
            sections.append(current)

        if token.type == "paragraph_open" and current is not None:
            inline = tokens[index + 1]
            current.blocks.append(_inline_plain_text(inline))
            current.images.extend(_inline_images(inline))
            index += 3
            continue

        if token.type == "fence" and current is not None:
            info = token.info.strip()
            current.code_blocks.append(token.content)
            current.blocks.append(f"```{info}\n{token.content}```")
            index += 1
            continue

        if token.type == "table_open" and current is not None:
            rows, images, next_index = _read_table(tokens, index)
            current.table_rows.extend(rows)
            current.images.extend(images)
            current.blocks.append(
                "\n".join("| " + " | ".join(row) + " |" for row in rows)
            )
            index = next_index
            continue

        index += 1

    return tuple(
        section for section in sections if section.normalized_text() or section.images
    )


def _read_table(
    tokens: list[Token],
    start_index: int,
) -> tuple[list[tuple[str, ...]], list[_ImageReference], int]:
    rows: list[tuple[str, ...]] = []
    images: list[_ImageReference] = []
    current_row: list[str] | None = None
    index = start_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token.type == "table_close":
            return rows, images, index + 1
        if token.type == "tr_open":
            current_row = []
        elif token.type == "tr_close" and current_row is not None:
            rows.append(tuple(current_row))
            current_row = None
        elif token.type == "inline" and current_row is not None:
            current_row.append(_inline_plain_text(token).strip())
            images.extend(_inline_images(token))
        index += 1
    return rows, images, index


def _inline_plain_text(token: Token) -> str:
    if not token.children:
        return token.content
    parts: list[str] = []
    for child in token.children:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type == "image":
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
    return "".join(parts)


def _inline_images(token: Token) -> tuple[_ImageReference, ...]:
    images: list[_ImageReference] = []

    def walk(children: list[Token] | None) -> None:
        for child in children or []:
            if child.type == "image":
                target = child.attrGet("src")
                if target:
                    images.append(
                        _ImageReference(target=target, alt_text=child.content.strip())
                    )
            walk(child.children)

    walk(token.children)
    return tuple(images)


def _selected_sections(
    sections: tuple[_Section, ...],
    heading_selectors: tuple[str, ...],
) -> tuple[_Section, ...]:
    if not heading_selectors:
        return sections
    selected: list[_Section] = []
    selectors = set(heading_selectors)
    selected_levels: list[int] = []
    for section in sections:
        selected_levels = [
            level for level in selected_levels if section.level > level
        ]
        if section.heading in selectors:
            selected_levels.append(section.level)
        if selected_levels:
            selected.append(section)
    return tuple(selected)


def _relative_image_locator(
    source_locator: SourceLocator,
    target: str,
) -> SourceLocator | None:
    if _unsafe_image_target_reason(source_locator, target) is not None:
        return None
    parsed = urlsplit(target)
    raw_path = unquote(parsed.path).replace("\\", "/")
    source_parent = PurePosixPath(source_locator.relative_path).parent.as_posix()
    combined = posixpath.normpath(posixpath.join(source_parent, raw_path))
    try:
        return SourceLocator(root_id=source_locator.root_id, relative_path=combined)
    except ValueError:
        return None


def _unsafe_image_target_reason(
    source_locator: SourceLocator,
    target: str,
) -> str | None:
    unquoted_target = unquote(target).replace("\\", "/")
    windows_target = PureWindowsPath(unquoted_target)
    if windows_target.drive or windows_target.is_absolute():
        return "absolute-path"
    parsed = urlsplit(target)
    raw_path = unquote(parsed.path).replace("\\", "/")
    if parsed.scheme or parsed.netloc:
        return "external-uri"
    if not raw_path or PurePosixPath(raw_path).is_absolute():
        return "absolute-path" if raw_path else "empty-target"
    source_parent = PurePosixPath(source_locator.relative_path).parent.as_posix()
    combined = posixpath.normpath(posixpath.join(source_parent, raw_path))
    if combined == ".." or combined.startswith("../"):
        return "root-escape"
    try:
        SourceLocator(root_id=source_locator.root_id, relative_path=combined)
    except ValueError:
        return "invalid-relative-path"
    return None


def _visual_from_image(
    *,
    path: Path,
    locator: SourceLocator,
    source_version_id_value: str,
    chunk_version_id_value: str | None,
    section: _Section,
    image_index: int,
    alt_text: str,
    created_at: datetime,
) -> VisualAssetVersion:
    content_digest = stream_sha256(path)
    logical_id = candidate_logical_id(
        "visual", f"{locator.root_id}\0{locator.relative_path}"
    )
    parent_version_ids = (
        (source_version_id_value, chunk_version_id_value)
        if chunk_version_id_value is not None
        else (source_version_id_value,)
    )
    version_id = candidate_version_id(logical_id, parent_version_ids, content_digest)
    width: int | None = None
    height: int | None = None
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except OSError:
        width = None
        height = None
    media_type = (
        mimetypes.guess_type(locator.relative_path)[0] or "application/octet-stream"
    )
    return VisualAssetVersion(
        logical_id=logical_id,
        version_id=version_id,
        revision=1,
        content_digest=content_digest,
        created_at=created_at,
        created_by=_ACTOR,
        media_type=media_type,
        width=width,
        height=height,
        alt_text=alt_text,
        source_locator=ChunkLocator(
            kind="markdown-block",
            ast_path=section.ast_path + (image_index,),
            heading_path=section.heading_path,
        ),
        asset_url=locator.relative_path,
        license_status="source-provided",
        authenticity="source-provided",
    )


def _normalize_text(value: str) -> str:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def _parser_config_digest(heading_selectors: tuple[str, ...]) -> str:
    payload = json.dumps(
        {
            "parser": PARSER_NAME,
            "parser_version": MARKDOWN_IT_VERSION,
            "heading_selectors": heading_selectors,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _source_time(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _evidence_id(source_version_id_value: str, parser_config_digest: str) -> str:
    return str(
        uuid5(
            COURSE_STUDIO_ID_NAMESPACE,
            f"evidence\0markdown\0{source_version_id_value}\0{parser_config_digest}",
        )
    )

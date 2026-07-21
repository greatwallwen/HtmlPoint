"""Transactional immutable SQLite repository for course knowledge metadata."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Callable, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from course_helper.domain.common import ActorRef, SourceLocator
from course_helper.domain.composition import (
    CardPlacement,
    CourseOutline,
    CourseRequirement,
    CourseVersion,
    canonical_digest,
    course_outline_content_digest,
    course_version_content_digest,
)
from course_helper.domain.evidence import EvidenceObject, LineageEdge
from course_helper.domain.knowledge import (
    CardContentNode,
    KnowledgeCardVersion,
    ReviewTask,
    TagVocabularyVersion,
)
from course_helper.domain.sources import (
    DatasetAssetVersion,
    ExtractedChunk,
    SourceAssetVersion,
    VisualAssetVersion,
)
from course_helper.domain.slide_ast import (
    RuntimeManifest,
    SlideDeckAst,
    SlideNode,
    runtime_manifest_content_digest,
    slide_deck_content_digest,
)
from course_helper.domain.visual_policy import VisualPlacement
from course_helper.lifecycle import (
    append_card_lifecycle_event,
    lifecycle_schema_available,
    rebuild_card_lifecycle_projection,
    refresh_card_fts,
    register_card_lifecycle,
    reopen_card_version,
)
from course_helper.source_roots import (
    candidate_version_id,
    source_logical_id,
    source_version_id,
)

if TYPE_CHECKING:
    from course_helper.artifacts import ArtifactMetadata
    from course_helper.source_visuals import SourceVisualMaterialization


CURRENT_MIGRATION_VERSION = 8
_MIGRATION_PATHS = {
    1: Path(__file__).with_name("migrations") / "0001_knowledge_catalog.sql",
    2: Path(__file__).with_name("migrations") / "0002_card_lifecycle.sql",
    3: Path(__file__).with_name("migrations") / "0003_course_composition.sql",
    4: Path(__file__).with_name("migrations") / "0004_embeddings.sql",
    5: Path(__file__).with_name("migrations") / "0005_artifact_metadata.sql",
    6: Path(__file__).with_name("migrations") / "0006_visual_provenance.sql",
    7: Path(__file__).with_name("migrations") / "0007_import_sources.sql",
    8: Path(__file__).with_name("migrations") / "0008_personal_course_runs.sql",
}


PayloadT = TypeVar("PayloadT", bound=BaseModel)
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class StoredImmutable(Generic[PayloadT]):
    """One exact immutable JSON payload plus its first storage timestamp."""

    payload: PayloadT
    payload_json: str
    content_digest: str
    created_at: datetime


class OutlineConfirmation(BaseModel):
    """Digest-bound intent to accept one exact outline version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    confirmation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    requirement_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    outline_version_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    expected_outline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed_by: ActorRef


class ImmutableVersionConflict(ValueError):
    """An immutable database identity was reused for a different payload."""


class CatalogMigrationError(RuntimeError):
    """The catalog schema could not be created or has an unsupported version."""


class CatalogReferenceError(ValueError):
    """A repository write refers to metadata that has not been persisted."""


class SourceRegistrationInput(BaseModel):
    """Explicit ingest metadata used to register one content digest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    locator: SourceLocator
    display_name: str = Field(min_length=1)
    source_kind: Literal[
        "pptx",
        "markdown",
        "csv",
        "parquet",
        "xls",
        "xlsx",
        "duckdb",
        "image",
        "other",
    ]
    media_type: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    modified_at: datetime | None = None
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_summary: str | None = None
    extraction_status: Literal[
        "registered",
        "parsing",
        "parsed",
        "partial",
        "failed",
        "unsupported",
    ] = "registered"
    parser_name: str | None = None
    parser_version: str | None = None
    parser_config_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: ActorRef


SourceRegistration = SourceAssetVersion


def canonical_model_json(model: BaseModel) -> str:
    """Serialize every stored model with one stable, order-independent contract."""

    return json.dumps(
        model.model_dump(mode="json", by_alias=False, exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("sha256_hex accepts text only")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _register_sql_functions(connection: sqlite3.Connection) -> None:
    connection.create_function("sha256_hex", 1, _sha256_text, deterministic=True)


def _stored_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise CatalogMigrationError("stored immutable timestamp is not timezone-aware")
    return parsed


def _clock_value(clock: Clock) -> datetime:
    value = clock()
    if value.utcoffset() is None:
        raise ValueError("catalog clock must return a timezone-aware datetime")
    return value


def _execute_migration_sql(connection: sqlite3.Connection, script: str) -> None:
    """Execute complete SQL statements without ``executescript`` auto-commits."""

    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if not sqlite3.complete_statement(pending):
            continue
        statement = pending.strip()
        pending = ""
        if statement:
            connection.execute(statement)
    if pending.strip():
        raise sqlite3.OperationalError("migration ended with an incomplete SQL statement")


class KnowledgeCatalog:
    """One SQLite connection with migration and immutable-write helpers."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        path: Path,
        *,
        read_only: bool = False,
    ) -> None:
        self.connection = connection
        self.path = path
        self.read_only = read_only
        self._atomic_depth = 0
        self._savepoint_sequence = 0

    @classmethod
    def open(cls, path: Path) -> KnowledgeCatalog:
        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30.0)
        _register_sql_functions(connection)
        connection.execute("PRAGMA foreign_keys = ON")
        catalog = cls(connection, database_path)
        try:
            catalog._apply_or_validate_migration()
        except Exception:
            connection.close()
            raise
        return catalog

    @classmethod
    def open_read_only(cls, path: Path) -> KnowledgeCatalog:
        """Open an existing catalog without migrations or mutation authority."""

        database_path = Path(path).resolve()
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
            timeout=30.0,
        )
        _register_sql_functions(connection)
        connection.execute("PRAGMA query_only = ON")
        return cls(connection, database_path, read_only=True)

    def __enter__(self) -> KnowledgeCatalog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def register_course_requirement(
        self,
        requirement: CourseRequirement,
        *,
        clock: Clock,
    ) -> StoredImmutable[CourseRequirement]:
        """Store or reopen the exact first bytes for one requirement ID."""

        payload = canonical_model_json(requirement)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self._get_stored_model(
                table="course_requirements",
                identity_column="requirement_id",
                identity=requirement.requirement_id,
                model_type=CourseRequirement,
            )
            if existing is not None:
                if existing.payload_json != payload or existing.content_digest != digest:
                    raise ImmutableVersionConflict(
                        f"course_requirements identity {requirement.requirement_id!r} "
                        "already has different bytes"
                    )
                return existing
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO course_requirements("
                "requirement_id, content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?)",
                (
                    requirement.requirement_id,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
        return StoredImmutable(requirement, payload, digest, created_at)

    def get_course_requirement(
        self,
        requirement_id: str,
    ) -> StoredImmutable[CourseRequirement] | None:
        return self._get_stored_model(
            table="course_requirements",
            identity_column="requirement_id",
            identity=requirement_id,
            model_type=CourseRequirement,
        )

    def register_course_outline(
        self,
        outline: CourseOutline,
        *,
        clock: Clock,
    ) -> StoredImmutable[CourseOutline]:
        payload = canonical_model_json(outline)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_course_outline(outline.version_id)
            if existing is not None:
                self._require_same_stored_bytes(existing, payload, digest, "course_outlines")
                return existing
            requirement = self.get_course_requirement(outline.requirement_id)
            if requirement is None:
                raise CatalogReferenceError(
                    f"outline requirement is not persisted: {outline.requirement_id!r}"
                )
            self._validate_composer_binding(outline, requirement)
            placements = tuple(
                placement for chapter in outline.chapters for placement in chapter.placements
            )
            for placement in placements:
                eligible = self.connection.execute(
                    "SELECT 1 FROM cards "
                    "JOIN card_lifecycle_current AS lifecycle "
                    "ON lifecycle.card_version_id = cards.version_id "
                    "WHERE cards.version_id = ? "
                    "AND lifecycle.status = 'published' AND lifecycle.suspended = 0",
                    (placement.card_version_id,),
                ).fetchone()
                if eligible is None:
                    raise CatalogReferenceError(
                        "outline card must be published, non-suspended, and persisted: "
                        f"{placement.card_version_id!r}"
                    )
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO course_outlines("
                "version_id, logical_id, revision, requirement_id, domain_digest, "
                "content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outline.version_id,
                    outline.logical_id,
                    outline.revision,
                    outline.requirement_id,
                    outline.content_digest,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
            for placement in placements:
                placement_payload = canonical_model_json(placement)
                self.connection.execute(
                    "INSERT INTO card_placements("
                    "placement_id, outline_version_id, card_version_id, content_digest, "
                    "payload_json, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        placement.placement_id,
                        outline.version_id,
                        placement.card_version_id,
                        _sha256_text(placement_payload),
                        placement_payload,
                        created_at.isoformat(),
                    ),
                )
        return StoredImmutable(outline, payload, digest, created_at)

    def get_course_outline(
        self,
        version_id: str,
    ) -> StoredImmutable[CourseOutline] | None:
        return self._get_stored_model(
            table="course_outlines",
            identity_column="version_id",
            identity=version_id,
            model_type=CourseOutline,
        )

    def get_card_placement(
        self,
        placement_id: str,
    ) -> StoredImmutable[CardPlacement] | None:
        return self._get_stored_model(
            table="card_placements",
            identity_column="placement_id",
            identity=placement_id,
            model_type=CardPlacement,
        )

    def _load_evidence(self, evidence_id: str) -> EvidenceObject:
        row = self.connection.execute(
            "SELECT kind, status, payload_json FROM evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise CatalogReferenceError(f"evidence is not persisted: {evidence_id!r}")
        try:
            evidence = EvidenceObject.model_validate_json(str(row[2]), strict=False)
        except Exception as error:
            raise CatalogMigrationError("evidence payload is invalid") from error
        if (
            evidence.evidence_id != evidence_id
            or evidence.kind != row[0]
            or evidence.status != row[1]
            or canonical_model_json(evidence) != row[2]
        ):
            raise CatalogMigrationError("evidence storage envelope is invalid")
        return evidence

    def _validate_composer_binding(
        self,
        outline: CourseOutline,
        requirement: StoredImmutable[CourseRequirement],
    ) -> None:
        """Fail closed unless composer v2 binds every authoritative semantic."""

        from course_helper.composer import (
            COMPOSER_BINDING_KIND,
            COMPOSER_PRODUCER,
            COMPOSER_PRODUCER_VERSION,
            RETRIEVAL_PRODUCER,
            RETRIEVAL_PRODUCER_VERSION,
            ComposerBindingPayload,
            composer_binding_identity_digest,
        )
        from course_helper.index_outbox import (
            IndexSnapshotIntegrityError,
            reopen_index_snapshot,
        )
        from course_helper.retrieval import (
            RetrievalQuery,
            retrieval_query_contract,
            retrieval_query_digest,
        )

        receipt = self._load_evidence(outline.retrieval_evidence_id)
        if (
            receipt.kind != "composition"
            or receipt.status != "verified"
            or receipt.producer != COMPOSER_PRODUCER
            or receipt.producer_version != COMPOSER_PRODUCER_VERSION
        ):
            raise CatalogReferenceError(
                "outline requires a verified authoritative composer binding"
            )
        try:
            binding = ComposerBindingPayload.model_validate(
                dict(receipt.output_summary), strict=False
            )
        except Exception as error:
            raise CatalogReferenceError("composer binding payload is invalid") from error
        if binding.receipt_kind != COMPOSER_BINDING_KIND:
            raise CatalogReferenceError("composer binding kind is invalid")

        binding_digest = canonical_digest(binding)
        identity_digest = composer_binding_identity_digest(binding, outline)
        if (
            receipt.input_summary.get("binding_digest") != binding_digest
            or receipt.input_summary.get("binding_identity_digest") != identity_digest
            or receipt.evidence_id != f"composition-binding-{identity_digest[:44]}"
        ):
            raise CatalogReferenceError("composer binding identity is not canonical")
        if (
            course_outline_content_digest(outline) != outline.content_digest
            or binding.outline_content_digest != outline.content_digest
            or binding.outline_version_id != outline.version_id
            or binding.requirement_id != requirement.payload.requirement_id
            or outline.requirement_id != requirement.payload.requirement_id
            or binding.requirement_storage_digest != requirement.content_digest
            or binding.requirement_domain_digest != canonical_digest(requirement.payload)
            or binding.requirement_duration_minutes
            != requirement.payload.duration_minutes
            or binding.learning_goals != requirement.payload.learning_goals
        ):
            raise CatalogReferenceError(
                "composer binding does not match requirement or outline bytes"
            )

        if (
            binding.index_snapshot_id != outline.index_snapshot_id
            or binding.options.index_snapshot_id != outline.index_snapshot_id
        ):
            raise CatalogReferenceError("composer binding snapshot ID is inconsistent")
        try:
            snapshot = reopen_index_snapshot(self, outline.index_snapshot_id)
        except IndexSnapshotIntegrityError as error:
            raise CatalogReferenceError(
                "composer binding retrieval snapshot is unavailable or invalid"
            ) from error
        if snapshot.snapshot_digest != binding.index_snapshot_digest:
            raise CatalogReferenceError("composer binding snapshot digest is stale")

        expected_filters = {
            "required_tag_ids": requirement.payload.required_tag_ids,
            "excluded_tag_ids": requirement.payload.excluded_tag_ids,
            "audience_tag_id": binding.options.audience_tag_id,
            "difficulty_tag_id": binding.options.difficulty_tag_id,
        }
        if dict(binding.filters) != expected_filters:
            raise CatalogReferenceError("composer binding filters are inconsistent")
        if tuple(item.goal for item in binding.retrievals) != requirement.payload.learning_goals:
            raise CatalogReferenceError("composer binding goal retrieval partition is invalid")

        returned_union: dict[str, str] = {}
        for item in binding.retrievals:
            try:
                query = RetrievalQuery(**dict(item.query_contract))
            except Exception as error:
                raise CatalogReferenceError(
                    "composer binding query contract is invalid"
                ) from error
            if (
                retrieval_query_contract(query) != dict(item.query_contract)
                or query.text != item.goal
                or query.required_tag_ids != requirement.payload.required_tag_ids
                or query.excluded_tag_ids != requirement.payload.excluded_tag_ids
                or query.audience_tag_id != binding.options.audience_tag_id
                or query.difficulty_tag_id != binding.options.difficulty_tag_id
                or query.index_snapshot_id != outline.index_snapshot_id
                or retrieval_query_digest(
                    query,
                    resolved_vocabulary_version_id=(
                        item.resolved_vocabulary_version_id
                    ),
                )
                != item.query_digest
            ):
                raise CatalogReferenceError("composer binding query digest is invalid")
            raw = self._load_evidence(item.evidence_id)
            if (
                raw.kind != "retrieval"
                or raw.status not in {"verified", "degraded"}
                or raw.producer != RETRIEVAL_PRODUCER
                or raw.producer_version != RETRIEVAL_PRODUCER_VERSION
                or canonical_digest(raw) != item.evidence_content_digest
                or raw.input_summary.get("query_digest") != item.query_digest
                or raw.output_summary.get("index_snapshot_id")
                != outline.index_snapshot_id
                or raw.output_summary.get("index_snapshot_digest")
                != binding.index_snapshot_digest
            ):
                raise CatalogReferenceError(
                    "raw retrieval evidence does not match the composer binding"
                )
            returned_ids = tuple(card.card_version_id for card in item.returned_cards)
            if (
                raw.output_summary.get("returned_hit_count") != len(returned_ids)
                or raw.output_summary.get("returned_hit_order_digest")
                != canonical_digest(returned_ids)
            ):
                raise CatalogReferenceError(
                    "raw retrieval evidence does not bind returned hit order"
                )
            for card in item.returned_cards:
                previous = returned_union.get(card.card_version_id)
                if previous is not None and previous != card.content_digest:
                    raise CatalogReferenceError("returned retrieval card bytes conflict")
                returned_union.setdefault(card.card_version_id, card.content_digest)

        bound_returned = tuple(
            (item.card_version_id, item.content_digest) for item in binding.returned_cards
        )
        if bound_returned != tuple(returned_union.items()):
            raise CatalogReferenceError("composer returned-card union is inconsistent")
        selected = tuple(
            (item.card_version_id, item.content_digest) for item in binding.selected_cards
        )
        if (
            len(selected) != len(set(card_id for card_id, _ in selected))
            or any(returned_union.get(card_id) != digest for card_id, digest in selected)
            or any(
                card_id in binding.options.exclude_card_version_ids
                for card_id, _digest in selected
            )
            or any(
                card_id not in {item[0] for item in selected}
                for card_id in binding.options.include_card_version_ids
            )
        ):
            raise CatalogReferenceError(
                "composer selected cards are not an exact returned-hit subset"
            )

        placements = tuple(
            placement for chapter in outline.chapters for placement in chapter.placements
        )
        bound_placements = tuple(item.placement for item in binding.placements)
        if (
            bound_placements != placements
            or any(
                item.content_digest != canonical_digest(item.placement)
                for item in binding.placements
            )
            or set(item.card_version_id for item in placements)
            != {card_id for card_id, _digest in selected}
            or len(placements) != len(selected)
            or binding.total_allocated_minutes
            != sum(item.allocated_minutes for item in placements)
            or binding.total_allocated_minutes != requirement.payload.duration_minutes
        ):
            raise CatalogReferenceError(
                "composer placement payload or duration allocation is inconsistent"
            )

        covered = binding.covered_goals
        uncovered = binding.uncovered_goals
        expected_covered = tuple(
            goal for goal in requirement.payload.learning_goals if goal not in uncovered
        )
        if (
            covered != expected_covered
            or tuple(goal for goal in requirement.payload.learning_goals if goal not in covered)
            != uncovered
            or outline.uncovered_goals != uncovered
            or tuple(item.goal for item in binding.goal_selections)
            != requirement.payload.learning_goals
        ):
            raise CatalogReferenceError("composer goal coverage is not an exact partition")
        selected_ids = {card_id for card_id, _digest in selected}
        for item in binding.goal_selections:
            if any(card_id not in selected_ids for card_id in item.selected_card_version_ids):
                raise CatalogReferenceError("goal selection references an unselected card")
            if (item.goal in covered) != bool(item.selected_card_version_ids):
                raise CatalogReferenceError("goal selection does not match coverage")

        for card_id, card_digest in selected:
            card = self.get_card(card_id)
            eligible = self.connection.execute(
                "SELECT 1 FROM card_lifecycle_current WHERE card_version_id = ? "
                "AND status = 'published' AND suspended = 0",
                (card_id,),
            ).fetchone()
            if (
                card is None
                or card.content_digest != card_digest
                or eligible is None
            ):
                raise CatalogReferenceError(
                    "composer selected card lifecycle or digest is no longer eligible"
                )

    def confirm_course_outline(
        self,
        confirmation: OutlineConfirmation,
        *,
        clock: Clock,
    ) -> StoredImmutable[OutlineConfirmation]:
        payload = canonical_model_json(confirmation)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing_identity = self._get_stored_model(
                table="outline_confirmations",
                identity_column="confirmation_id",
                identity=confirmation.confirmation_id,
                model_type=OutlineConfirmation,
            )
            if existing_identity is not None:
                self._require_same_stored_bytes(
                    existing_identity, payload, digest, "outline_confirmations"
                )
                return existing_identity
            winner = self.get_outline_confirmation(confirmation.outline_version_id)
            if winner is not None:
                if winner.payload_json == payload and winner.content_digest == digest:
                    return winner
                raise ImmutableVersionConflict(
                    "outline version already has a different persisted confirmation"
                )
            requirement = self.get_course_requirement(confirmation.requirement_id)
            outline = self.get_course_outline(confirmation.outline_version_id)
            if requirement is None or outline is None:
                raise CatalogReferenceError("confirmation requirement/outline is not persisted")
            if outline.payload.requirement_id != confirmation.requirement_id:
                raise CatalogReferenceError("confirmation requirement does not own the outline")
            if outline.payload.content_digest != confirmation.expected_outline_digest:
                raise CatalogReferenceError("confirmation outline digest does not match storage")
            if outline.payload.uncovered_goals:
                raise CatalogReferenceError(
                    "outline with uncovered goals cannot be confirmed or published"
                )
            newer_outline = self.connection.execute(
                "SELECT 1 FROM course_outlines WHERE logical_id = ? AND revision > ? LIMIT 1",
                (outline.payload.logical_id, outline.payload.revision),
            ).fetchone()
            if newer_outline is not None:
                raise CatalogReferenceError(
                    "confirmation response is stale because a newer outline revision exists"
                )
            self._validate_composer_binding(outline.payload, requirement)
            from course_helper.composer import confirmation_summary

            expected_confirmation_digest = confirmation_summary(
                outline.payload, requirement.payload
            ).confirmation_digest
            if confirmation.confirmation_digest != expected_confirmation_digest:
                raise CatalogReferenceError(
                    "confirmation digest does not match the exact outline summary"
                )
            for chapter in outline.payload.chapters:
                for placement in chapter.placements:
                    eligible = self.connection.execute(
                        "SELECT 1 FROM cards JOIN card_lifecycle_current AS lifecycle "
                        "ON lifecycle.card_version_id = cards.version_id "
                        "WHERE cards.version_id = ? AND lifecycle.status = 'published' "
                        "AND lifecycle.suspended = 0",
                        (placement.card_version_id,),
                    ).fetchone()
                    if eligible is None:
                        raise CatalogReferenceError(
                            "outline card lifecycle is no longer eligible for confirmation"
                        )
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO outline_confirmations("
                "confirmation_id, requirement_id, outline_version_id, "
                "expected_outline_digest, confirmation_digest, content_digest, "
                "payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    confirmation.confirmation_id,
                    confirmation.requirement_id,
                    confirmation.outline_version_id,
                    confirmation.expected_outline_digest,
                    confirmation.confirmation_digest,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
        return StoredImmutable(confirmation, payload, digest, created_at)

    def get_outline_confirmation(
        self,
        outline_version_id: str,
    ) -> StoredImmutable[OutlineConfirmation] | None:
        return self._get_stored_model(
            table="outline_confirmations",
            identity_column="outline_version_id",
            identity=outline_version_id,
            model_type=OutlineConfirmation,
        )

    def register_course_version(
        self,
        course: CourseVersion,
        *,
        clock: Clock,
    ) -> StoredImmutable[CourseVersion]:
        payload = canonical_model_json(course)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_course_version(course.version_id)
            if existing is not None:
                self._require_same_stored_bytes(existing, payload, digest, "course_versions")
                return existing
            if course.status not in {"confirmed", "published"}:
                raise CatalogReferenceError(
                    "course registration accepts only confirmed or published snapshots"
                )
            if course.status == "confirmed":
                if course.supersedes_version_id is not None or course.visual_placement_ids:
                    raise CatalogReferenceError(
                        "confirmed course cannot supersede or bind publication visuals"
                    )
            else:
                parent = (
                    None
                    if course.supersedes_version_id is None
                    else self.get_course_version(course.supersedes_version_id)
                )
                if (
                    parent is None
                    or parent.payload.status not in {"confirmed", "published"}
                    or parent.payload.logical_id != course.logical_id
                    or course.revision != parent.payload.revision + 1
                    or course.requirement_id != parent.payload.requirement_id
                    or course.outline_version_id != parent.payload.outline_version_id
                    or course.outline_digest != parent.payload.outline_digest
                    or course.placement_ids != parent.payload.placement_ids
                    or course.usage_scope != parent.payload.usage_scope
                    or course.confirmation_digest != parent.payload.confirmation_digest
                ):
                    raise CatalogReferenceError(
                        "published course must supersede the exact confirmed or published snapshot"
                    )
                if course_version_content_digest(course) != course.content_digest:
                    raise CatalogReferenceError("published course content digest is invalid")
                for visual_placement_id in course.visual_placement_ids:
                    if self.get_visual_placement(visual_placement_id) is None:
                        raise CatalogReferenceError(
                            "published course visual placement is not persisted"
                        )
            requirement = self.get_course_requirement(course.requirement_id)
            outline = self.get_course_outline(course.outline_version_id)
            confirmation_row = self.connection.execute(
                "SELECT confirmation_id FROM outline_confirmations "
                "WHERE confirmation_digest = ?",
                (course.confirmation_digest,),
            ).fetchone()
            confirmation = (
                None
                if confirmation_row is None
                else self._get_stored_model(
                    table="outline_confirmations",
                    identity_column="confirmation_id",
                    identity=str(confirmation_row[0]),
                    model_type=OutlineConfirmation,
                )
            )
            if requirement is None or outline is None or confirmation is None:
                raise CatalogReferenceError(
                    "course requirement, outline, or confirmation is not persisted"
                )
            if outline.payload.uncovered_goals:
                raise CatalogReferenceError(
                    "outline with uncovered goals cannot be registered as a course"
                )
            if course.usage_scope != requirement.payload.usage_scope:
                raise CatalogReferenceError(
                    "course usage scope must exactly match its confirmed requirement"
                )
            confirmed = confirmation.payload
            if (
                outline.payload.requirement_id != course.requirement_id
                or course.outline_digest != outline.payload.content_digest
                or confirmed.outline_version_id != course.outline_version_id
                or confirmed.requirement_id != course.requirement_id
                or confirmed.confirmation_digest != course.confirmation_digest
            ):
                raise CatalogReferenceError("course confirmation does not match its outline")
            expected_placement_ids = tuple(
                placement.placement_id
                for chapter in outline.payload.chapters
                for placement in chapter.placements
            )
            if tuple(course.placement_ids) != expected_placement_ids:
                raise CatalogReferenceError(
                    "course placement order does not exactly match the confirmed outline"
                )
            self._validate_composer_binding(outline.payload, requirement)
            for chapter in outline.payload.chapters:
                for placement in chapter.placements:
                    eligible = self.connection.execute(
                        "SELECT 1 FROM card_lifecycle_current "
                        "WHERE card_version_id = ? AND status = 'published' "
                        "AND suspended = 0",
                        (placement.card_version_id,),
                    ).fetchone()
                    if eligible is None:
                        raise CatalogReferenceError(
                            "course placement lifecycle is no longer eligible"
                        )
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO course_versions("
                "version_id, logical_id, revision, requirement_id, outline_version_id, "
                "confirmation_digest, domain_digest, content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    course.version_id,
                    course.logical_id,
                    course.revision,
                    course.requirement_id,
                    course.outline_version_id,
                    course.confirmation_digest,
                    course.content_digest,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
        return StoredImmutable(course, payload, digest, created_at)

    def get_course_version(self, version_id: str) -> StoredImmutable[CourseVersion] | None:
        return self._get_stored_model(
            table="course_versions",
            identity_column="version_id",
            identity=version_id,
            model_type=CourseVersion,
        )

    def register_slide_deck(
        self,
        deck: SlideDeckAst,
        *,
        clock: Clock,
    ) -> StoredImmutable[SlideDeckAst]:
        payload = canonical_model_json(deck)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_slide_deck(deck.version_id)
            if existing is not None:
                self._require_same_stored_bytes(existing, payload, digest, "slide_decks")
                return existing
            if slide_deck_content_digest(deck) != deck.content_digest:
                raise CatalogReferenceError("slide deck content digest is invalid")
            course = self.get_course_version(deck.course_version_id)
            if course is None:
                raise CatalogReferenceError("slide deck course is not persisted")
            outline = self.get_course_outline(course.payload.outline_version_id)
            if outline is None:
                raise CatalogReferenceError("slide deck course outline is not persisted")
            outline_placements = {
                placement.placement_id: placement
                for chapter in outline.payload.chapters
                for placement in chapter.placements
            }
            if tuple(outline_placements) != tuple(course.payload.placement_ids):
                raise CatalogReferenceError(
                    "course placement envelope does not match its immutable outline"
                )
            nodes = _flatten_slide_nodes(deck.nodes)
            for node in nodes:
                if not node.chunk_ids or not node.source_version_ids:
                    raise CatalogReferenceError(
                        "slide content lineage requires chunk and source version IDs"
                    )
                if node.node_type == "slide":
                    if not node.presenter_notes or not node.presenter_notes.strip():
                        raise CatalogReferenceError(
                            "slide root requires separate presenter notes"
                        )
                    if node.text and node.presenter_notes.strip() == node.text.strip():
                        raise CatalogReferenceError(
                            "presenter notes cannot repeat stage text"
                        )
                node_placement_cards: list[str] = []
                for placement_id in node.placement_ids:
                    expected_placement = outline_placements.get(placement_id)
                    if expected_placement is None:
                        raise CatalogReferenceError(
                            "slide placement is outside the immutable course version"
                        )
                    placement = self.get_card_placement(placement_id)
                    if placement is None:
                        raise CatalogReferenceError(
                            f"slide placement is not persisted: {placement_id!r}"
                        )
                    owner = self.connection.execute(
                        "SELECT outline_version_id FROM card_placements "
                        "WHERE placement_id = ?",
                        (placement_id,),
                    ).fetchone()
                    if (
                        owner is None
                        or owner[0] != course.payload.outline_version_id
                        or placement.payload_json
                        != canonical_model_json(expected_placement)
                    ):
                        raise CatalogReferenceError(
                            "slide placement does not match its exact course outline intent"
                        )
                    node_placement_cards.append(expected_placement.card_version_id)
                if tuple(node.card_version_ids) != tuple(node_placement_cards):
                    raise CatalogReferenceError(
                        "slide card IDs do not exactly match their ordered placements"
                    )
                for card_version_id in node_placement_cards:
                    card_row = self.connection.execute(
                        "SELECT content_digest, payload_json FROM cards WHERE version_id = ?",
                        (card_version_id,),
                    ).fetchone()
                    lifecycle = self.connection.execute(
                        "SELECT status, suspended FROM card_lifecycle_current "
                        "WHERE card_version_id = ?",
                        (card_version_id,),
                    ).fetchone()
                    if card_row is None:
                        raise CatalogReferenceError(
                            f"slide card is not persisted: {card_version_id!r}"
                        )
                    try:
                        raw_card = KnowledgeCardVersion.model_validate_json(card_row[1])
                    except Exception as error:
                        raise CatalogMigrationError("slide card payload is invalid") from error
                    if (
                        canonical_model_json(raw_card) != card_row[1]
                        or raw_card.version_id != card_version_id
                        or raw_card.content_digest != card_row[0]
                    ):
                        raise CatalogMigrationError(
                            "slide card content digest or storage envelope is invalid"
                        )
                    if lifecycle != ("published", 0):
                        raise CatalogReferenceError(
                            "slide card lifecycle is not eligible"
                        )
                expected_sources: list[str] = []
                for chunk_id in node.chunk_ids:
                    chunk_row = self.connection.execute(
                        "SELECT source_version_id, ordinal, content_digest, payload_json "
                        "FROM chunks WHERE chunk_id = ?",
                        (chunk_id,),
                    ).fetchone()
                    if chunk_row is None:
                        raise CatalogReferenceError(
                            f"slide chunk is not persisted: {chunk_id!r}"
                        )
                    try:
                        chunk = ExtractedChunk.model_validate_json(chunk_row[3])
                    except Exception as error:
                        raise CatalogMigrationError("slide chunk payload is invalid") from error
                    if (
                        canonical_model_json(chunk) != chunk_row[3]
                        or (
                            chunk.source_version_id,
                            chunk.ordinal,
                            chunk.content_digest,
                        )
                        != tuple(chunk_row[:3])
                    ):
                        raise CatalogMigrationError(
                            "slide chunk storage envelope is invalid"
                        )
                    if chunk.source_version_id not in expected_sources:
                        expected_sources.append(chunk.source_version_id)
                    edge = self.connection.execute(
                        "SELECT evidence_id FROM lineage WHERE from_version_id IN "
                        f"({','.join('?' for _ in node.card_version_ids)}) "
                        "AND to_version_id = ? AND relation = 'cites' "
                        "AND evidence_id IN "
                        f"({','.join('?' for _ in node.evidence_ids)}) "
                        "ORDER BY evidence_id LIMIT 1",
                        (*node.card_version_ids, chunk_id, *node.evidence_ids),
                    ).fetchone()
                    if edge is None:
                        raise CatalogReferenceError(
                            "slide chunk lacks card citation lineage and evidence"
                        )
                if tuple(node.source_version_ids) != tuple(expected_sources):
                    raise CatalogReferenceError(
                        "slide source IDs do not match the ordered chunk owners"
                    )
                for source_version_id in node.source_version_ids:
                    source_row = self.connection.execute(
                        "SELECT logical_id, revision, content_digest, payload_json "
                        "FROM sources WHERE version_id = ?",
                        (source_version_id,),
                    ).fetchone()
                    if source_row is None:
                        raise CatalogReferenceError(
                            f"slide source is not persisted: {source_version_id!r}"
                        )
                    try:
                        source = SourceAssetVersion.model_validate_json(source_row[3])
                    except Exception as error:
                        raise CatalogMigrationError("slide source payload is invalid") from error
                    if (
                        canonical_model_json(source) != source_row[3]
                        or (source.logical_id, source.revision, source.content_digest)
                        != tuple(source_row[:3])
                        or source.version_id != source_version_id
                        or not self.source_is_extractable(source)
                    ):
                        raise CatalogMigrationError(
                            "slide source storage envelope or extraction state is invalid"
                        )
                for evidence_id in node.evidence_ids:
                    evidence = self._load_evidence(evidence_id)
                    if evidence.status != "verified":
                        raise CatalogReferenceError(
                            f"slide evidence is not verified: {evidence_id!r}"
                        )
                for binding in node.asset_bindings:
                    visual_placement = self.get_visual_placement(
                        binding.visual_placement_id
                    )
                    if visual_placement is None:
                        raise CatalogReferenceError(
                            "slide asset binding placement is not persisted"
                        )
                    intent = visual_placement.payload
                    visual_row = self.connection.execute(
                        "SELECT content_digest, payload_json FROM visuals WHERE version_id = ?",
                        (binding.visual_version_id,),
                    ).fetchone()
                    if visual_row is None:
                        raise CatalogReferenceError(
                            "slide asset binding visual is not persisted"
                        )
                    visual = VisualAssetVersion.model_validate_json(
                        visual_row[1], strict=False
                    )
                    if (
                        intent.slide_node_id != node.node_id
                        or intent.visual_version_id != binding.visual_version_id
                        or intent.alt_text != binding.alt_text
                        or visual.media_type != binding.media_type
                        or intent.authenticity_evidence_id
                        != binding.authenticity_evidence_id
                        or intent.license_evidence_id != binding.license_evidence_id
                        or intent.attribution != binding.attribution
                        or intent.transformation != binding.transformation
                        or intent.transformation.transformation_id
                        != binding.transformation_id
                        or (
                            intent.originating_card_version_id is not None
                            and intent.originating_card_version_id
                            not in node.card_version_ids
                        )
                    ):
                        raise CatalogReferenceError(
                            "slide asset binding alt text, media, policy, or lineage does not "
                            "match its immutable placement intent"
                        )
                    artifact = self.get_artifact(binding.artifact_id)
                    if artifact is None:
                        raise CatalogReferenceError(
                            "slide asset binding artifact is not persisted"
                        )
                    if (
                        canonical_model_json(visual) != visual_row[1]
                        or visual.content_digest != visual_row[0]
                        or artifact.payload.content_digest != binding.artifact_digest
                        or artifact.payload.content_digest != visual.content_digest
                        or artifact.payload.media_type != binding.media_type
                        or artifact.payload.width != visual.width
                        or artifact.payload.height != visual.height
                    ):
                        raise CatalogReferenceError(
                            "slide asset binding artifact, policy, media, or lineage does not match "
                            "its immutable placement intent"
                        )
                    required_visual_evidence = {
                        binding.authenticity_evidence_id,
                        binding.license_evidence_id,
                    }
                    if not required_visual_evidence.issubset(set(node.evidence_ids)):
                        raise CatalogReferenceError(
                            "slide asset binding evidence is not pinned by its node"
                        )
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO slide_decks("
                "version_id, logical_id, revision, course_version_id, domain_digest, "
                "content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    deck.version_id,
                    deck.logical_id,
                    deck.revision,
                    deck.course_version_id,
                    deck.content_digest,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
        return StoredImmutable(deck, payload, digest, created_at)

    def get_slide_deck(self, version_id: str) -> StoredImmutable[SlideDeckAst] | None:
        return self._get_stored_model(
            table="slide_decks",
            identity_column="version_id",
            identity=version_id,
            model_type=SlideDeckAst,
        )

    def register_runtime_manifest(
        self,
        manifest: RuntimeManifest,
        *,
        clock: Clock,
    ) -> StoredImmutable[RuntimeManifest]:
        payload = canonical_model_json(manifest)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_runtime_manifest(manifest.version_id)
            if existing is not None:
                self._require_same_stored_bytes(existing, payload, digest, "runtime_manifests")
                return existing
            if runtime_manifest_content_digest(manifest) != manifest.content_digest:
                raise CatalogReferenceError("runtime manifest content digest is invalid")
            course = self.get_course_version(manifest.course_version_id)
            deck = self.get_slide_deck(manifest.slide_deck_version_id)
            if course is None or deck is None:
                raise CatalogReferenceError("runtime course/deck is not persisted")
            if deck.payload.course_version_id != manifest.course_version_id:
                raise CatalogReferenceError(
                    "runtime deck is owned by a different course version"
                )
            if deck.payload.content_digest != manifest.slide_deck_digest:
                raise CatalogReferenceError("runtime slide deck digest does not match storage")
            for evidence_id in manifest.evidence_ids:
                evidence = self._load_evidence(evidence_id)
                if evidence.status != "verified":
                    raise CatalogReferenceError(
                        f"runtime evidence is not verified: {evidence_id!r}"
                    )
            flattened = _flatten_slide_nodes(deck.payload.nodes)
            required_evidence_ids = tuple(
                dict.fromkeys(
                    [
                        *(evidence_id for node in flattened for evidence_id in node.evidence_ids),
                        *(job.evidence_id for job in manifest.job_bindings),
                    ]
                )
            )
            if manifest.evidence_ids != required_evidence_ids:
                raise CatalogReferenceError(
                    "runtime manifest evidence snapshot is not exact"
                )
            required_artifact_ids = tuple(
                dict.fromkeys(
                    binding.artifact_id
                    for node in flattened
                    for binding in node.asset_bindings
                )
            )
            if manifest.artifact_ids != required_artifact_ids:
                raise CatalogReferenceError(
                    "runtime manifest artifact snapshot is not exact"
                )
            for job in manifest.job_bindings:
                if job.evidence_id not in manifest.evidence_ids:
                    raise CatalogReferenceError(
                        "runtime job evidence is not pinned by the manifest"
                    )
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO runtime_manifests("
                "version_id, logical_id, revision, course_version_id, slide_deck_version_id, "
                "domain_digest, content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    manifest.version_id,
                    manifest.logical_id,
                    manifest.revision,
                    manifest.course_version_id,
                    manifest.slide_deck_version_id,
                    manifest.content_digest,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
        return StoredImmutable(manifest, payload, digest, created_at)

    def get_runtime_manifest(
        self, version_id: str
    ) -> StoredImmutable[RuntimeManifest] | None:
        return self._get_stored_model(
            table="runtime_manifests",
            identity_column="version_id",
            identity=version_id,
            model_type=RuntimeManifest,
        )

    def register_visual_placement(
        self,
        placement: VisualPlacement,
        *,
        clock: Clock,
    ) -> StoredImmutable[VisualPlacement]:
        payload = canonical_model_json(placement)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_visual_placement(placement.placement_id)
            if existing is not None:
                self._require_same_stored_bytes(existing, payload, digest, "visual_placements")
                return existing
            if not self._row_exists("visuals", "version_id", placement.visual_version_id):
                raise CatalogReferenceError("visual placement asset is not persisted")
            for evidence_id in (
                placement.authenticity_evidence_id,
                placement.license_evidence_id,
            ):
                if not self._row_exists("evidence", "evidence_id", evidence_id):
                    raise CatalogReferenceError(
                        f"visual placement evidence is not persisted: {evidence_id!r}"
                    )
            typed_origins = (
                ("cards", placement.originating_card_version_id),
                ("sources", placement.originating_source_version_id),
                ("datasets", placement.originating_dataset_version_id),
            )
            if any(
                value is not None and not self._row_exists(table, "version_id", value)
                for table, value in typed_origins
            ):
                raise CatalogReferenceError(
                    "visual placement typed origin is not persisted in its exact table"
                )
            if (
                placement.originating_card_version_id is not None
                and self.connection.execute(
                    "SELECT 1 FROM card_lifecycle_current "
                    "WHERE card_version_id = ? AND status = 'published' AND suspended = 0",
                    (placement.originating_card_version_id,),
                ).fetchone()
                is None
            ):
                raise CatalogReferenceError(
                    "visual placement card origin must be published and non-suspended"
                )
            created_at = _clock_value(clock)
            self.connection.execute(
                "INSERT INTO visual_placements("
                "placement_id, visual_version_id, authenticity_evidence_id, "
                "license_evidence_id, content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    placement.placement_id,
                    placement.visual_version_id,
                    placement.authenticity_evidence_id,
                    placement.license_evidence_id,
                    digest,
                    payload,
                    created_at.isoformat(),
                ),
            )
        return StoredImmutable(placement, payload, digest, created_at)

    def get_visual_placement(
        self, placement_id: str
    ) -> StoredImmutable[VisualPlacement] | None:
        return self._get_stored_model(
            table="visual_placements",
            identity_column="placement_id",
            identity=placement_id,
            model_type=VisualPlacement,
        )

    @staticmethod
    def _require_same_stored_bytes(
        existing: StoredImmutable[PayloadT],
        payload: str,
        digest: str,
        table: str,
    ) -> None:
        if existing.payload_json != payload or existing.content_digest != digest:
            raise ImmutableVersionConflict(
                f"{table} immutable identity already has different bytes"
            )

    def _get_stored_model(
        self,
        *,
        table: str,
        identity_column: str,
        identity: str,
        model_type: type[PayloadT],
    ) -> StoredImmutable[PayloadT] | None:
        envelope_fields: dict[str, tuple[tuple[str, str], ...]] = {
            "course_requirements": (("requirement_id", "requirement_id"),),
            "course_outlines": (
                ("version_id", "version_id"),
                ("logical_id", "logical_id"),
                ("revision", "revision"),
                ("requirement_id", "requirement_id"),
                ("domain_digest", "content_digest"),
            ),
            "card_placements": (
                ("placement_id", "placement_id"),
                ("card_version_id", "card_version_id"),
            ),
            "outline_confirmations": (
                ("confirmation_id", "confirmation_id"),
                ("requirement_id", "requirement_id"),
                ("outline_version_id", "outline_version_id"),
                ("expected_outline_digest", "expected_outline_digest"),
                ("confirmation_digest", "confirmation_digest"),
            ),
            "course_versions": (
                ("version_id", "version_id"),
                ("logical_id", "logical_id"),
                ("revision", "revision"),
                ("requirement_id", "requirement_id"),
                ("outline_version_id", "outline_version_id"),
                ("confirmation_digest", "confirmation_digest"),
                ("domain_digest", "content_digest"),
            ),
            "slide_decks": (
                ("version_id", "version_id"),
                ("logical_id", "logical_id"),
                ("revision", "revision"),
                ("course_version_id", "course_version_id"),
                ("domain_digest", "content_digest"),
            ),
            "runtime_manifests": (
                ("version_id", "version_id"),
                ("logical_id", "logical_id"),
                ("revision", "revision"),
                ("course_version_id", "course_version_id"),
                ("slide_deck_version_id", "slide_deck_version_id"),
                ("domain_digest", "content_digest"),
            ),
            "visual_placements": (
                ("placement_id", "placement_id"),
                ("visual_version_id", "visual_version_id"),
                ("authenticity_evidence_id", "authenticity_evidence_id"),
                ("license_evidence_id", "license_evidence_id"),
            ),
            "artifact_metadata": (
                ("artifact_id", "artifact_id"),
                ("artifact_digest", "content_digest"),
                ("byte_size", "byte_size"),
                ("media_type", "media_type"),
                ("width", "width"),
                ("height", "height"),
            ),
            "source_visual_artifacts": (
                ("materialization_id", "materialization_id"),
                ("visual_version_id", "visual_version_id"),
                ("artifact_id", "artifact_id"),
                ("source_version_id", "source_version_id"),
                ("source_content_digest", "source_content_digest"),
                ("visual_content_digest", "visual_content_digest"),
                ("slide_number", "slide_number"),
                ("relationship_id", "relationship_id"),
                ("evidence_id", "evidence_id"),
            ),
        }
        fields = envelope_fields.get(table)
        if fields is None:
            raise ValueError(f"unsupported immutable model table: {table}")
        selected_columns = ", ".join(column for column, _ in fields)
        row = self.connection.execute(
            f"SELECT payload_json, content_digest, created_at, {selected_columns} "
            f"FROM {table} WHERE {identity_column} = ?",
            (identity,),
        ).fetchone()
        if row is None:
            return None
        payload_json, content_digest, created_at = row[:3]
        if _sha256_text(payload_json) != content_digest:
            raise CatalogMigrationError(f"{table} immutable payload digest mismatch")
        try:
            model = model_type.model_validate_json(payload_json, strict=False)
        except Exception as error:
            raise CatalogMigrationError(
                f"{table} immutable payload cannot be validated"
            ) from error
        envelope_matches = all(
            getattr(model, attribute) == row[index + 3]
            for index, (_, attribute) in enumerate(fields)
        )
        if canonical_model_json(model) != payload_json or not envelope_matches:
            raise CatalogMigrationError(
                f"{table} immutable envelope does not match its canonical payload"
            )
        return StoredImmutable(
            payload=model,
            payload_json=payload_json,
            content_digest=content_digest,
            created_at=_stored_datetime(created_at),
        )

    def insert_source(self, source: SourceAssetVersion) -> SourceAssetVersion:
        with self._write_scope():
            self._insert_source_row(source)
        return source

    def get_source(self, version_id: str) -> SourceAssetVersion | None:
        row = self.connection.execute(
            "SELECT payload_json FROM sources WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return SourceAssetVersion.model_validate_json(row[0])

    def latest_source(self, logical_id: str) -> SourceAssetVersion | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM sources
            WHERE logical_id = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (logical_id,),
        ).fetchone()
        if row is None:
            return None
        return SourceAssetVersion.model_validate_json(row[0])

    def register_or_reuse_source(
        self,
        source_input: SourceRegistrationInput,
    ) -> SourceRegistration:
        logical_id = source_logical_id(source_input.locator)
        version_id = source_version_id(logical_id, source_input.content_digest)
        with self._immediate_writer_transaction():
            existing = self.get_source(version_id)
            if existing is not None:
                return existing
            previous = self.latest_source(logical_id)
            registration = SourceAssetVersion(
                logical_id=logical_id,
                version_id=version_id,
                revision=1 if previous is None else previous.revision + 1,
                supersedes_version_id=None if previous is None else previous.version_id,
                **source_input.model_dump(),
            )
            self._insert_source_row(registration)
        return registration

    def source_is_extractable(self, source: SourceAssetVersion) -> bool:
        if source.extraction_status in {"parsed", "partial"}:
            return True
        if (
            source.extraction_status != "registered"
            or source.locator.root_id != "governed-upload"
        ):
            return False
        from course_helper.uploads import GovernedSourceBlob

        row = self.connection.execute(
            "SELECT blob_digest, status, payload_json FROM governed_source_blobs "
            "WHERE source_version_id = ?",
            (source.version_id,),
        ).fetchone()
        if row is None:
            return False
        try:
            blob = GovernedSourceBlob.model_validate_json(row[2])
        except Exception:
            return False
        if (
            canonical_model_json(blob) != row[2]
            or blob.source_version_id != source.version_id
            or blob.source_logical_id != source.logical_id
            or blob.blob_digest != source.content_digest
            or (row[0], row[1]) != (source.content_digest, "active")
            or blob.status != "active"
        ):
            return False
        chunk_count = self.connection.execute(
            "SELECT count(*) FROM chunks WHERE source_version_id = ?",
            (source.version_id,),
        ).fetchone()[0]
        if chunk_count < 1:
            return False
        evidence_rows = self.connection.execute(
            "SELECT payload_json FROM evidence WHERE kind = 'extraction' "
            "AND status = 'verified' ORDER BY evidence_id"
        ).fetchall()
        for evidence_row in evidence_rows:
            try:
                evidence = EvidenceObject.model_validate_json(evidence_row[0])
            except Exception:
                continue
            if (
                canonical_model_json(evidence) == evidence_row[0]
                and evidence.subject_version_id == source.version_id
                and evidence.output_summary.get("chunkCount") == chunk_count
            ):
                return True
        return False

    def insert_chunk(self, chunk: ExtractedChunk) -> ExtractedChunk:
        payload = canonical_model_json(chunk)
        with self._write_scope():
            inserted = self._assert_new_or_identical(
                table="chunks",
                identity_column="chunk_id",
                identity=chunk.chunk_id,
                payload=payload,
            )
            if inserted:
                self.connection.execute(
                    """
                    INSERT INTO chunks(
                        chunk_id, source_version_id, ordinal, content_digest, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.source_version_id,
                        chunk.ordinal,
                        chunk.content_digest,
                        payload,
                    ),
                )
        return chunk

    def insert_visual(self, visual: VisualAssetVersion) -> VisualAssetVersion:
        with self._write_scope():
            self._insert_version_row("visuals", visual)
        return visual

    def insert_dataset(self, dataset: DatasetAssetVersion) -> DatasetAssetVersion:
        with self._write_scope():
            self._insert_version_row("datasets", dataset)
        return dataset

    def insert_evidence(self, evidence: EvidenceObject) -> EvidenceObject:
        payload = canonical_model_json(evidence)
        with self._write_scope():
            inserted = self._assert_new_or_identical(
                table="evidence",
                identity_column="evidence_id",
                identity=evidence.evidence_id,
                payload=payload,
            )
            if inserted:
                if (
                    evidence.subject_version_id is not None
                    and not self._version_exists(evidence.subject_version_id)
                ):
                    raise CatalogReferenceError(
                        "evidence subject has not been persisted: "
                        f"{evidence.subject_version_id!r}"
                    )
                self.connection.execute(
                    """
                    INSERT INTO evidence(evidence_id, kind, status, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (evidence.evidence_id, evidence.kind, evidence.status, payload),
                )
        return evidence

    def register_artifact(
        self,
        artifact: ArtifactMetadata,
    ) -> StoredImmutable[ArtifactMetadata]:
        """Persist path-free metadata after the content-addressed file is verified."""

        from course_helper.artifacts import ArtifactMetadata

        if artifact.created_at.utcoffset() is None:
            raise ValueError("artifact created_at must be timezone-aware")
        if artifact.artifact_id != f"artifact-{artifact.content_digest}":
            raise CatalogReferenceError("artifact identity does not match its digest")
        payload = canonical_model_json(artifact)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_artifact(artifact.artifact_id)
            if existing is not None:
                self._require_same_stored_bytes(
                    existing, payload, digest, "artifact_metadata"
                )
                return existing
            self.connection.execute(
                "INSERT INTO artifact_metadata("
                "artifact_id, artifact_digest, byte_size, media_type, width, height, "
                "content_digest, payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id,
                    artifact.content_digest,
                    artifact.byte_size,
                    artifact.media_type,
                    artifact.width,
                    artifact.height,
                    digest,
                    payload,
                    artifact.created_at.isoformat(),
                ),
            )
        stored = self.get_artifact(artifact.artifact_id)
        if stored is None:
            raise CatalogMigrationError("artifact metadata insert could not be reopened")
        return stored

    def get_artifact(
        self,
        artifact_id: str,
    ) -> StoredImmutable[ArtifactMetadata] | None:
        from course_helper.artifacts import ArtifactMetadata

        stored = self._get_stored_model(
            table="artifact_metadata",
            identity_column="artifact_id",
            identity=artifact_id,
            model_type=ArtifactMetadata,
        )
        if stored is not None and stored.payload.created_at != stored.created_at:
            raise CatalogMigrationError(
                "artifact metadata timestamp envelope is invalid"
            )
        return stored

    def register_source_visual_materialization(
        self,
        materialization: SourceVisualMaterialization,
    ) -> StoredImmutable[SourceVisualMaterialization]:
        """Bind one exact source relationship and visual to verified artifact bytes."""

        from course_helper.source_visuals import SourceVisualMaterialization
        from course_helper.source_visuals import validate_source_visual_identity

        if materialization.created_at.utcoffset() is None:
            raise ValueError("source visual created_at must be timezone-aware")
        payload = canonical_model_json(materialization)
        digest = _sha256_text(payload)
        with self._immediate_writer_transaction():
            existing = self.get_source_visual_materialization(
                materialization.visual_version_id
            )
            if existing is not None:
                self._require_same_stored_bytes(
                    existing,
                    payload,
                    digest,
                    "source_visual_artifacts",
                )
                return existing
            identity_row = self.connection.execute(
                "SELECT payload_json FROM source_visual_artifacts "
                "WHERE materialization_id = ?",
                (materialization.materialization_id,),
            ).fetchone()
            if identity_row is not None:
                raise ImmutableVersionConflict(
                    "source visual materialization ID already has different bytes"
                )
            source_row = self.connection.execute(
                "SELECT content_digest, payload_json FROM sources WHERE version_id = ?",
                (materialization.source_version_id,),
            ).fetchone()
            visual_row = self.connection.execute(
                "SELECT content_digest, payload_json FROM visuals WHERE version_id = ?",
                (materialization.visual_version_id,),
            ).fetchone()
            artifact = self.get_artifact(materialization.artifact_id)
            if source_row is None or visual_row is None or artifact is None:
                raise CatalogReferenceError(
                    "source visual source, visual, or artifact is not persisted"
                )
            try:
                source = SourceAssetVersion.model_validate_json(source_row[1])
                visual = VisualAssetVersion.model_validate_json(visual_row[1])
            except Exception as error:
                raise CatalogMigrationError(
                    "source visual dependency payload is invalid"
                ) from error
            if (
                canonical_model_json(source) != source_row[1]
                or source.content_digest != source_row[0]
                or source.content_digest != materialization.source_content_digest
            ):
                raise CatalogMigrationError(
                    "source visual source digest or envelope is invalid"
                )
            locator = visual.source_locator
            if (
                canonical_model_json(visual) != visual_row[1]
                or visual.content_digest != visual_row[0]
                or visual.content_digest != materialization.visual_content_digest
                or materialization.source_version_id
                not in visual.derived_from_version_ids
                or locator is None
                or locator.slide_number != materialization.slide_number
                or locator.relationship_id != materialization.relationship_id
            ):
                raise CatalogReferenceError(
                    "source visual metadata does not match its pinned relationship"
                )
            try:
                validate_source_visual_identity(self, source, visual)
            except ValueError as error:
                raise CatalogReferenceError(
                    "source visual identity does not match parser semantics"
                ) from error
            artifact_value = artifact.payload
            if (
                artifact_value.content_digest != visual.content_digest
                or artifact_value.media_type != visual.media_type
                or artifact_value.width != visual.width
                or artifact_value.height != visual.height
            ):
                raise CatalogReferenceError(
                    "source visual artifact bytes do not match visual metadata"
                )
            evidence = self._load_evidence(materialization.evidence_id)
            if (
                evidence.kind != "validation"
                or evidence.status != "verified"
                or evidence.subject_version_id != visual.version_id
                or evidence.producer != "course-helper/source-visuals"
                or evidence.producer_version != "1"
            ):
                raise CatalogReferenceError(
                    "source visual requires exact verified materialization evidence"
                )
            expected_input = {
                "source_version_id": source.version_id,
                "source_content_digest": source.content_digest,
                "visual_version_id": visual.version_id,
                "visual_content_digest": visual.content_digest,
                "slide_number": locator.slide_number,
                "relationship_id": locator.relationship_id,
            }
            expected_output = {
                "artifact_id": artifact_value.artifact_id,
                "artifact_digest": artifact_value.content_digest,
                "byte_size": artifact_value.byte_size,
                "media_type": artifact_value.media_type,
                "width": artifact_value.width,
                "height": artifact_value.height,
            }
            if (
                dict(evidence.input_summary) != expected_input
                or dict(evidence.output_summary) != expected_output
            ):
                raise CatalogReferenceError(
                    "source visual evidence does not bind exact dependency semantics"
                )
            self.connection.execute(
                "INSERT INTO source_visual_artifacts("
                "materialization_id, visual_version_id, artifact_id, "
                "source_version_id, source_content_digest, visual_content_digest, "
                "slide_number, relationship_id, evidence_id, content_digest, "
                "payload_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    materialization.materialization_id,
                    materialization.visual_version_id,
                    materialization.artifact_id,
                    materialization.source_version_id,
                    materialization.source_content_digest,
                    materialization.visual_content_digest,
                    materialization.slide_number,
                    materialization.relationship_id,
                    materialization.evidence_id,
                    digest,
                    payload,
                    materialization.created_at.isoformat(),
                ),
            )
        stored = self.get_source_visual_materialization(
            materialization.visual_version_id
        )
        if stored is None:
            raise CatalogMigrationError(
                "source visual materialization insert could not be reopened"
            )
        return stored

    def get_source_visual_materialization(
        self,
        visual_version_id: str,
    ) -> StoredImmutable[SourceVisualMaterialization] | None:
        from course_helper.source_visuals import SourceVisualMaterialization

        stored = self._get_stored_model(
            table="source_visual_artifacts",
            identity_column="visual_version_id",
            identity=visual_version_id,
            model_type=SourceVisualMaterialization,
        )
        if stored is not None and stored.payload.created_at != stored.created_at:
            raise CatalogMigrationError(
                "source visual timestamp envelope is invalid"
            )
        return stored

    def publication_receipt(
        self,
        *,
        submitted: KnowledgeCardVersion,
        published: KnowledgeCardVersion,
    ) -> EvidenceObject:
        """Return the unique publish/dedup receipt for one concrete submission."""

        dedup_rows = self.connection.execute(
            """
            SELECT archived.payload_json, receipt.payload_json
            FROM lineage AS dedup_lineage
            JOIN cards AS archived
              ON archived.version_id = dedup_lineage.from_version_id
            JOIN evidence AS receipt
              ON receipt.evidence_id = dedup_lineage.evidence_id
            WHERE dedup_lineage.relation = 'deduplicates'
              AND dedup_lineage.to_version_id = ?
              AND receipt.kind = 'dedup'
            ORDER BY archived.version_id, receipt.evidence_id
            """,
            (published.version_id,),
        ).fetchall()
        dedup_receipts: list[EvidenceObject] = []
        for archived_payload, evidence_payload in dedup_rows:
            archived = KnowledgeCardVersion.model_validate_json(archived_payload)
            evidence = EvidenceObject.model_validate_json(evidence_payload)
            if (
                archived.status == "archived"
                and evidence.subject_version_id == archived.version_id
                and _same_publication_submission(submitted, archived)
            ):
                dedup_receipts.append(evidence)
        if len(dedup_receipts) == 1:
            return dedup_receipts[0]
        if dedup_receipts:
            raise CatalogReferenceError("publication receipt is ambiguous")

        publish_rows = self.connection.execute(
            "SELECT payload_json FROM evidence WHERE kind = 'publish' ORDER BY evidence_id"
        ).fetchall()
        publish_receipts = tuple(
            evidence
            for row in publish_rows
            if (evidence := EvidenceObject.model_validate_json(row[0])).subject_version_id
            == published.version_id
        )
        if len(publish_receipts) == 1:
            return publish_receipts[0]
        if publish_receipts:
            raise CatalogReferenceError("publication receipt is ambiguous")
        raise CatalogReferenceError("publication receipt is unavailable")

    def insert_vocabulary(
        self,
        vocabulary: TagVocabularyVersion,
    ) -> TagVocabularyVersion:
        payload = canonical_model_json(vocabulary)
        with self._write_scope():
            inserted = self._assert_new_or_identical(
                table="tag_vocabularies",
                identity_column="version_id",
                identity=vocabulary.version_id,
                payload=payload,
            )
            if not inserted:
                return vocabulary
            self.connection.execute(
                """
                INSERT INTO tag_vocabularies(version_id, content_digest, payload_json)
                VALUES (?, ?, ?)
                """,
                (vocabulary.version_id, vocabulary.content_digest, payload),
            )
            for dimension in vocabulary.dimensions:
                for value in dimension.values:
                    self.connection.execute(
                        """
                        INSERT INTO tag_values(
                            vocabulary_version_id, tag_id, dimension_id, status, payload_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            vocabulary.version_id,
                            value.id,
                            dimension.id,
                            value.status,
                            canonical_model_json(value),
                        ),
                    )
        return vocabulary

    def insert_card(self, card: KnowledgeCardVersion) -> KnowledgeCardVersion:
        with self._immediate_writer_transaction():
            existing_row = self.connection.execute(
                "SELECT payload_json FROM cards WHERE version_id = ?",
                (card.version_id,),
            ).fetchone()
            if existing_row is not None:
                stored = KnowledgeCardVersion.model_validate_json(existing_row[0])
                reopened = reopen_card_version(self.connection, stored.version_id)
                if card.status == "published" and reopened.suspended:
                    raise CatalogReferenceError(
                        "suspended card version cannot be published"
                    )
                if not _same_direct_card_identity(card, stored):
                    raise ImmutableVersionConflict(
                        f"cards identity {card.version_id!r} already has a different payload"
                    )
                return reopened.card

            replay = self._find_direct_card_replay(card)
            if replay is not None:
                return replay

            effective = self._effective_direct_card(card)
            effective_row = self.connection.execute(
                "SELECT payload_json FROM cards WHERE version_id = ?",
                (effective.version_id,),
            ).fetchone()
            if effective_row is not None:
                stored = KnowledgeCardVersion.model_validate_json(effective_row[0])
                reopened = reopen_card_version(self.connection, stored.version_id)
                if effective.status == "published" and reopened.suspended:
                    raise CatalogReferenceError(
                        "suspended card version cannot be published"
                    )
                if _same_direct_card_identity(effective, stored):
                    return reopened.card
                raise ImmutableVersionConflict(
                    f"cards identity {effective.version_id!r} already has a different payload"
                )
            if not self._row_exists(
                "tag_vocabularies",
                "version_id",
                effective.vocabulary_version_id,
            ):
                raise CatalogReferenceError(
                    f"card vocabulary {effective.vocabulary_version_id!r} has not been persisted"
                )
            for assignment in effective.tag_assignments:
                if not self._row_exists(
                    "tag_values",
                    "vocabulary_version_id",
                    assignment.vocabulary_version_id,
                    secondary_column="tag_id",
                    secondary_value=assignment.tag_id,
                ):
                    raise CatalogReferenceError(
                        "card tag has not been persisted in its pinned vocabulary: "
                        f"{assignment.vocabulary_version_id!r}/{assignment.tag_id!r}"
                    )

            if effective.status == "published":
                current_rows = self.connection.execute(
                    """
                    SELECT cards.version_id
                    FROM cards
                    JOIN card_lifecycle_current lifecycle
                      ON lifecycle.card_version_id = cards.version_id
                    WHERE cards.logical_id = ?
                      AND lifecycle.status = 'published'
                    ORDER BY cards.revision, cards.version_id
                    """,
                    (effective.logical_id,),
                ).fetchall()
                for row in current_rows:
                    transition_card_status(self.connection, row[0], "superseded")

            payload = canonical_model_json(effective)
            self.connection.execute(
                """
                INSERT INTO cards(
                    version_id, logical_id, revision, status, content_digest, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    effective.version_id,
                    effective.logical_id,
                    effective.revision,
                    effective.status,
                    effective.content_digest,
                    payload,
                ),
            )
            for assignment in effective.tag_assignments:
                self.connection.execute(
                    """
                    INSERT INTO card_tags(card_version_id, vocabulary_version_id, tag_id)
                    VALUES (?, ?, ?)
                    """,
                    (
                        effective.version_id,
                        assignment.vocabulary_version_id,
                        assignment.tag_id,
                    ),
                )
            register_card_lifecycle(
                self.connection,
                effective,
                event_id=f"register:{effective.version_id}",
                request_digest=effective.content_digest,
                occurred_at=effective.created_at,
                actor_id=effective.created_by.actor_id,
            )
        return effective

    def get_card(self, version_id: str) -> KnowledgeCardVersion | None:
        """Return a card with effective lifecycle status projected in memory."""

        row = self.connection.execute(
            "SELECT 1 FROM cards WHERE version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            return None
        return reopen_card_version(self.connection, version_id).card

    def _find_direct_card_replay(
        self,
        card: KnowledgeCardVersion,
    ) -> KnowledgeCardVersion | None:
        if card.status != "published":
            return None
        rows = self.connection.execute(
            """
            SELECT payload_json
            FROM cards
            WHERE logical_id = ?
            ORDER BY revision DESC, version_id DESC
            """,
            (card.logical_id,),
        ).fetchall()
        for row in rows:
            raw = KnowledgeCardVersion.model_validate_json(row[0])
            reopened = reopen_card_version(self.connection, raw.version_id)
            stored = reopened.card
            predecessor = stored.supersedes_version_id
            parent_ids = card_parent_version_ids(
                stored,
                root_version_id=card.version_id,
            )
            if predecessor is not None:
                parent_ids = tuple(dict.fromkeys((*parent_ids, predecessor)))
            expected_version_id = candidate_version_id(
                stored.logical_id,
                parent_ids,
                stored.content_digest,
            )
            if expected_version_id != stored.version_id:
                continue
            if not _same_direct_card_submission(card, stored):
                raise ImmutableVersionConflict(
                    f"cards submission {card.version_id!r} already has different content"
                )
            if reopened.suspended:
                raise CatalogReferenceError(
                    "suspended card version cannot be published"
                )
            return stored
        return None

    def _effective_direct_card(
        self,
        card: KnowledgeCardVersion,
    ) -> KnowledgeCardVersion:
        if card.status != "published":
            return card
        latest_row = self.connection.execute(
            """
            SELECT version_id, revision
            FROM cards
            WHERE logical_id = ?
            ORDER BY revision DESC, version_id DESC
            LIMIT 1
            """,
            (card.logical_id,),
        ).fetchone()
        predecessor = None if latest_row is None else latest_row[0]
        revision = 1 if latest_row is None else latest_row[1] + 1
        parent_ids = card_parent_version_ids(card)
        if predecessor is not None:
            parent_ids = tuple(dict.fromkeys((*parent_ids, predecessor)))
        return card.model_copy(
            update={
                "version_id": candidate_version_id(
                    card.logical_id,
                    parent_ids,
                    card.content_digest,
                ),
                "revision": revision,
                "supersedes_version_id": predecessor,
            }
        )

    def insert_lineage(self, edge: LineageEdge) -> LineageEdge:
        with self._write_scope():
            missing_endpoints = [
                version_id
                for version_id in (edge.from_version_id, edge.to_version_id)
                if not self._version_exists(version_id)
            ]
            if missing_endpoints:
                raise CatalogReferenceError(
                    f"lineage endpoint has not been persisted: {missing_endpoints!r}"
                )
            if not self._row_exists("evidence", "evidence_id", edge.evidence_id):
                raise CatalogReferenceError(
                    f"lineage evidence {edge.evidence_id!r} has not been persisted"
                )
            existing = self.connection.execute(
                """
                SELECT from_version_id, to_version_id, relation, evidence_id
                FROM lineage
                WHERE edge_id = ?
                """,
                (edge.edge_id,),
            ).fetchone()
            expected = (
                edge.from_version_id,
                edge.to_version_id,
                edge.relation,
                edge.evidence_id,
            )
            if existing is not None:
                if existing == expected:
                    return edge
                raise ImmutableVersionConflict(
                    f"lineage edge {edge.edge_id!r} already has a different payload"
                )
            self.connection.execute(
                """
                INSERT INTO lineage(
                    edge_id, from_version_id, to_version_id, relation, evidence_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (edge.edge_id, *expected),
            )
        return edge

    def insert_review_task(self, task: ReviewTask) -> ReviewTask:
        payload = canonical_model_json(task)
        with self._write_scope():
            inserted = self._assert_new_or_identical(
                table="review_tasks",
                identity_column="task_id",
                identity=task.task_id,
                payload=payload,
            )
            if inserted:
                if (
                    task.status != "open"
                    or task.resolved_at is not None
                    or task.resolved_by is not None
                ):
                    raise CatalogReferenceError(
                        "new review tasks must be unresolved and open"
                    )
                if task.created_at.utcoffset() is None:
                    raise CatalogReferenceError(
                        "new review task created_at must be timezone-aware"
                    )
                if not self._version_exists(task.subject_version_id):
                    raise CatalogReferenceError(
                        "review task subject has not been persisted: "
                        f"{task.subject_version_id!r}"
                    )
                missing_evidence_ids = tuple(
                    evidence_id
                    for evidence_id in task.evidence_ids
                    if not self._row_exists("evidence", "evidence_id", evidence_id)
                )
                if missing_evidence_ids:
                    raise CatalogReferenceError(
                        "review task evidence has not been persisted: "
                        f"{missing_evidence_ids!r}"
                    )
                self.connection.execute(
                    """
                    INSERT INTO review_tasks(task_id, kind, subject_version_id, status, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (task.task_id, task.kind, task.subject_version_id, task.status, payload),
                )
        return task

    def _apply_or_validate_migration(self) -> None:
        migration_table_exists = self.connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()
        if migration_table_exists is None:
            versions: tuple[int, ...] = ()
        else:
            versions = tuple(
                row[0]
                for row in self.connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            )
        expected_prefix = tuple(range(1, len(versions) + 1))
        if versions != expected_prefix or any(
            version > CURRENT_MIGRATION_VERSION for version in versions
        ):
            raise CatalogMigrationError(f"unsupported migration versions: {versions!r}")
        if len(versions) < CURRENT_MIGRATION_VERSION:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                for version in range(len(versions) + 1, CURRENT_MIGRATION_VERSION + 1):
                    _execute_migration_sql(
                        self.connection,
                        _MIGRATION_PATHS[version].read_text(encoding="utf-8"),
                    )
                    self.connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                        (version,),
                    )
                if len(versions) < 4:
                    rebuild_card_lifecycle_projection(self.connection)
                self.connection.commit()
            except Exception as error:
                if self.connection.in_transaction:
                    self.connection.rollback()
                raise CatalogMigrationError(
                    f"could not apply catalog migration: {error}"
                ) from error
        final_versions = tuple(
            row[0]
            for row in self.connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        )
        if final_versions != tuple(range(1, CURRENT_MIGRATION_VERSION + 1)):
            raise CatalogMigrationError(f"unsupported migration versions: {final_versions!r}")
        if self.connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise CatalogMigrationError("SQLite foreign keys could not be enabled")

    @contextmanager
    def atomic_write(self) -> Iterator[None]:
        """Commit one catalog-owned write bundle or roll it back in full."""

        if self.read_only:
            raise RuntimeError("read-only catalog cannot start a write transaction")

        if self._atomic_depth == 0:
            if self.connection.in_transaction:
                raise RuntimeError("catalog atomic write cannot start inside an active transaction")
            self.connection.execute("BEGIN IMMEDIATE")
            self._atomic_depth = 1
            try:
                yield
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise
            finally:
                self._atomic_depth = 0
            return

        self._savepoint_sequence += 1
        savepoint = f"catalog_atomic_{self._savepoint_sequence}"
        self.connection.execute(f"SAVEPOINT {savepoint}")
        self._atomic_depth += 1
        try:
            yield
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        finally:
            self._atomic_depth -= 1

    @contextmanager
    def _write_scope(self) -> Iterator[None]:
        if self._atomic_depth:
            yield
            return
        with self.atomic_write():
            yield

    @contextmanager
    def _immediate_writer_transaction(self) -> Iterator[None]:
        with self.atomic_write():
            yield

    def _insert_source_row(self, source: SourceAssetVersion) -> None:
        payload = canonical_model_json(source)
        inserted = self._assert_new_or_identical(
            table="sources",
            identity_column="version_id",
            identity=source.version_id,
            payload=payload,
        )
        if inserted:
            self.connection.execute(
                """
                INSERT INTO sources(
                    version_id, logical_id, revision, content_digest, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source.version_id,
                    source.logical_id,
                    source.revision,
                    source.content_digest,
                    payload,
                ),
            )

    def _insert_version_row(
        self,
        table: Literal["visuals", "datasets"],
        model: VisualAssetVersion | DatasetAssetVersion,
    ) -> None:
        payload = canonical_model_json(model)
        inserted = self._assert_new_or_identical(
            table=table,
            identity_column="version_id",
            identity=model.version_id,
            payload=payload,
        )
        if inserted:
            self.connection.execute(
                f"""
                INSERT INTO {table}(
                    version_id, logical_id, revision, content_digest, payload_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    model.version_id,
                    model.logical_id,
                    model.revision,
                    model.content_digest,
                    payload,
                ),
            )

    def _assert_new_or_identical(
        self,
        *,
        table: str,
        identity_column: str,
        identity: str,
        payload: str,
    ) -> bool:
        row = self.connection.execute(
            f"SELECT payload_json FROM {table} WHERE {identity_column} = ?",
            (identity,),
        ).fetchone()
        if row is None:
            return True
        if row[0] == payload:
            return False
        raise ImmutableVersionConflict(
            f"{table} identity {identity!r} already has a different payload"
        )

    def _row_exists(
        self,
        table: str,
        identity_column: str,
        identity: str,
        *,
        secondary_column: str | None = None,
        secondary_value: str | None = None,
    ) -> bool:
        if secondary_column is None:
            row = self.connection.execute(
                f"SELECT 1 FROM {table} WHERE {identity_column} = ?",
                (identity,),
            ).fetchone()
        else:
            row = self.connection.execute(
                f"""
                SELECT 1 FROM {table}
                WHERE {identity_column} = ? AND {secondary_column} = ?
                """,
                (identity, secondary_value),
            ).fetchone()
        return row is not None

    def _version_exists(self, version_id: str) -> bool:
        version_tables = (
            ("sources", "version_id"),
            ("chunks", "chunk_id"),
            ("visuals", "version_id"),
            ("datasets", "version_id"),
            ("cards", "version_id"),
            ("tag_vocabularies", "version_id"),
            ("course_requirements", "requirement_id"),
            ("course_outlines", "version_id"),
            ("card_placements", "placement_id"),
            ("outline_confirmations", "confirmation_id"),
            ("course_versions", "version_id"),
            ("slide_decks", "version_id"),
            ("runtime_manifests", "version_id"),
            ("visual_placements", "placement_id"),
            ("artifact_metadata", "artifact_id"),
            ("source_visual_artifacts", "materialization_id"),
        )
        return any(
            self._row_exists(table, identity_column, version_id)
            for table, identity_column in version_tables
        )


def register_or_reuse_source(
    catalog: KnowledgeCatalog,
    source_input: SourceRegistrationInput,
) -> SourceRegistration:
    """Register one explicit content hash or return its original stored version."""

    return catalog.register_or_reuse_source(source_input)


def card_parent_version_ids(
    card: KnowledgeCardVersion,
    *,
    root_version_id: str | None = None,
) -> tuple[str, ...]:
    """Return the canonical card parents used for deterministic version IDs."""

    return tuple(
        dict.fromkeys(
            (
                card.version_id if root_version_id is None else root_version_id,
                *card.prerequisite_card_version_ids,
                *(citation.source_version_id for citation in card.chunk_citations),
                *(citation.chunk_id for citation in card.chunk_citations),
                *(reference.visual_version_id for reference in card.visual_refs),
                *(reference.dataset_version_id for reference in card.dataset_refs),
            )
        )
    )


def _same_direct_card_submission(
    candidate: KnowledgeCardVersion,
    stored: KnowledgeCardVersion,
) -> bool:
    def submission_payload(card: KnowledgeCardVersion) -> str:
        values = card.model_dump(mode="json", exclude_none=True)
        for field in ("version_id", "revision", "supersedes_version_id", "status"):
            values.pop(field, None)
        return json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return submission_payload(candidate) == submission_payload(stored)


def _same_publication_submission(
    submitted: KnowledgeCardVersion,
    archived: KnowledgeCardVersion,
) -> bool:
    def publication_payload(card: KnowledgeCardVersion) -> str:
        values = card.model_dump(mode="json", exclude_none=True)
        for field in (
            "version_id",
            "revision",
            "supersedes_version_id",
            "status",
            "content_digest",
        ):
            values.pop(field, None)
        return json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return publication_payload(submitted) == publication_payload(archived)


def _same_direct_card_identity(
    candidate: KnowledgeCardVersion,
    stored: KnowledgeCardVersion,
) -> bool:
    if canonical_model_json(candidate) == canonical_model_json(stored):
        return True
    if candidate.status != "published":
        return False
    if stored.status == "published" and stored.supersedes_version_id is None:
        return False
    if stored.status not in {"published", "superseded", "archived"}:
        return False

    def identity_payload(card: KnowledgeCardVersion) -> str:
        values = card.model_dump(mode="json", exclude_none=True)
        for field in ("revision", "supersedes_version_id", "status"):
            values.pop(field, None)
        return json.dumps(
            values,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return identity_payload(candidate) == identity_payload(stored)


def index_published_card(
    connection: sqlite3.Connection,
    card: KnowledgeCardVersion,
) -> None:
    """Refresh FTS from lifecycle truth, never from mutable ``cards.status``."""

    if not lifecycle_schema_available(connection):
        raise CatalogMigrationError("card lifecycle schema is unavailable")
    refresh_card_fts(connection, card.version_id)


def transition_card_status(
    connection: sqlite3.Connection,
    version_id: str,
    status: Literal["superseded", "archived"],
) -> KnowledgeCardVersion:
    """Append a supersede/archive event and project status only in memory."""

    row = connection.execute(
        "SELECT 1 FROM cards WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise CatalogReferenceError(f"card version has not been persisted: {version_id!r}")
    event_type: Literal["supersede", "archive"] = (
        "supersede" if status == "superseded" else "archive"
    )
    request_digest = hashlib.sha256(
        f"{version_id}\0{event_type}".encode("utf-8")
    ).hexdigest()
    append_card_lifecycle_event(
        connection,
        card_version_id=version_id,
        event_id=f"lifecycle:{event_type}:{version_id}",
        request_digest=request_digest,
        event_type=event_type,
        occurred_at=datetime.now(timezone.utc),
        actor_id="course-helper/catalog",
    )
    return reopen_card_version(connection, version_id).card


def _card_body(nodes: tuple[CardContentNode, ...]) -> str:
    parts: list[str] = []
    for node in nodes:
        if node.text:
            parts.append(node.text)
        parts.extend(cell for row in node.rows for cell in row if cell)
        if node.children:
            child_body = _card_body(node.children)
            if child_body:
                parts.append(child_body)
    return "\n".join(parts)


def _flatten_slide_nodes(nodes: tuple[SlideNode, ...]) -> tuple[SlideNode, ...]:
    ordered: list[SlideNode] = []
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(node.children))
    return tuple(ordered)

import json
import warnings
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import get_args, get_origin

import pytest
from pydantic import ValidationError

from course_helper.domain.common import ActorRef, FrozenDict, SourceLocator, VersionMeta
from course_helper.domain.evidence import EvidenceCheck, EvidenceObject, LineageEdge
from course_helper.domain.knowledge import (
    CardContentNode,
    ChunkCitation,
    KnowledgeCardVersion,
    ReviewTask,
    TagAssignment,
    TagDimension,
    TagValue,
    TagVocabularyVersion,
)
from course_helper.domain.sources import (
    ChunkLocator,
    DatasetAssetVersion,
    DatasetColumn,
    ExtractedChunk,
    ExtractionResult,
    SourceAssetVersion,
    VisualAssetVersion,
)


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
DIGEST = "a" * 64


def actor_fixture() -> ActorRef:
    return ActorRef(actor_type="human", actor_id="trainer-1", display_name="Trainer")


def version_meta_fixture(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "logical_id": "logical-1",
        "version_id": "version-1",
        "revision": 1,
        "content_digest": DIGEST,
        "created_at": NOW,
        "created_by": actor_fixture(),
    }
    values.update(overrides)
    return values


def vocabulary_fixture() -> TagVocabularyVersion:
    return TagVocabularyVersion(
        **version_meta_fixture(logical_id="vocabulary", version_id="vocabulary-v1"),
        dimensions=(
            TagDimension(
                id="difficulty",
                cardinality="one",
                values=(
                    TagValue(
                        id="difficulty:beginner",
                        labels={"en": "Beginner", "zh-CN": "入门"},
                        aliases=("basic",),
                        status="active",
                    ),
                ),
            ),
        ),
    )


def evidence_fixture() -> EvidenceObject:
    return EvidenceObject(
        evidence_id="evidence-1",
        kind="validation",
        subject_version_id="card-v1",
        status="verified",
        input_summary={"query": {"terms": ["citation"]}, "secret": "remove-me"},
        output_summary={"counts": {"checks": 1}, "secret": "remove-me"},
        checks=(
            EvidenceCheck(
                code="citation-present",
                status="passed",
                message="Citation found",
                details={"citation": {"chunk_ids": ["chunk-1"]}, "secret": "remove-me"},
            ),
        ),
        started_at=NOW,
        finished_at=NOW,
        producer="course-helper/tests",
    )


def lineage_fixture() -> LineageEdge:
    return LineageEdge(
        edge_id="edge-1",
        from_version_id="chunk-v1",
        to_version_id="card-v1",
        relation="composed_into",
        evidence_id="evidence-1",
        created_at=NOW,
    )


def review_task_fixture() -> ReviewTask:
    return ReviewTask(
        task_id="review-1",
        kind="source-changed",
        subject_version_id="card-v1",
        status="open",
        blocking=True,
        evidence_ids=("evidence-1",),
        created_at=NOW,
        created_by=actor_fixture(),
    )


def card_fixture(*, status: str = "review", main_type_id: str = "concept") -> KnowledgeCardVersion:
    citations = (
        ChunkCitation(chunk_id="chunk-1", source_version_id="source-v1"),
    )
    return KnowledgeCardVersion(
        **version_meta_fixture(logical_id="card-1", version_id="card-v1"),
        main_type_id=main_type_id,
        title="Language-model boundaries",
        learning_objective="Explain one evidence-backed model limitation.",
        content_ast=(CardContentNode(type="paragraph", text="Models can make unsupported claims."),),
        suggested_minutes=5,
        prerequisite_card_version_ids=(),
        vocabulary_version_id="vocabulary-v1",
        tag_assignments=(
            TagAssignment(
                vocabulary_version_id="vocabulary-v1",
                dimension_id="difficulty",
                tag_id="difficulty:beginner",
            ),
        ),
        chunk_citations=citations,
        visual_refs=(),
        dataset_refs=(),
        status=status,
    )


def dataset_fixture() -> DatasetAssetVersion:
    return DatasetAssetVersion(
        **version_meta_fixture(logical_id="dataset-1", version_id="dataset-v1", content_digest="e" * 64),
        locator=SourceLocator(root_id="fixture", relative_path="sales.csv"),
        format="csv",
        row_count=2,
        columns=(DatasetColumn(name="order_id", data_type="BIGINT", nullable=False),),
        grain="one row per order_id",
        missingness={"order_id": 0.0, "internal": 0.5},
        category_tags=("sales",),
        relation_name="sales_profile",
        sample_rows=({"order_id": 1, "metrics": {"values": [10, 20]}, "secret": "remove-me"},),
        review_status="ready",
        evidence=evidence_fixture(),
    )


def test_source_locator_rejects_absolute_and_parent_paths() -> None:
    for invalid in (r"C:\\secret.txt", "/etc/passwd", "../escape.md"):
        try:
            SourceLocator(root_id="reference-demo", relative_path=invalid)
        except ValidationError:
            continue
        raise AssertionError(f"accepted unsafe locator: {invalid}")


def test_source_locator_normalizes_safe_windows_separators() -> None:
    locator = SourceLocator(root_id="reference-demo", relative_path=r"slides\unit-1\demo.pptx")
    assert locator.relative_path == "slides/unit-1/demo.pptx"


def test_version_meta_rejects_non_sha256_digest_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        VersionMeta(**version_meta_fixture(content_digest="not-a-digest"))
    with pytest.raises(ValidationError):
        VersionMeta(**version_meta_fixture(), unexpected=True)


def test_published_card_is_frozen_and_requires_a_citation() -> None:
    card = card_fixture(status="published")
    assert card.chunk_citations
    try:
        card.title = "mutated"
    except ValidationError:
        pass
    else:
        raise AssertionError("published version was mutable")

    with pytest.raises(ValidationError, match="chunk citation"):
        KnowledgeCardVersion.model_validate({**card.model_dump(), "chunk_citations": ()})


def test_source_asset_contracts_round_trip_as_immutable_models() -> None:
    source = SourceAssetVersion(
        **version_meta_fixture(logical_id="source-1", version_id="source-v1"),
        locator=SourceLocator(root_id="fixture", relative_path="demo.pptx"),
        display_name="demo.pptx",
        source_kind="pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        byte_size=1024,
        extraction_status="parsed",
        parser_name="python-pptx",
        parser_version="1.0.2",
        parser_config_digest="b" * 64,
    )
    chunk = ExtractedChunk(
        chunk_id="chunk-1",
        source_version_id=source.version_id,
        ordinal=0,
        modality="slide",
        language="en",
        normalized_text="Transformer",
        content_digest="c" * 64,
        locator=ChunkLocator(kind="pptx-slide", slide_number=1),
        breadcrumb=("Transformer",),
        notes_text="Presenter notes",
        slide_text="Transformer",
    )
    visual = VisualAssetVersion(
        **version_meta_fixture(logical_id="visual-1", version_id="visual-v1", content_digest="d" * 64),
        media_type="image/png",
        width=1,
        height=1,
        alt_text="One pixel",
        source_locator=ChunkLocator(kind="pptx-slide", slide_number=1, relationship_id="rId2"),
        authenticity="source-provided",
        license_status="source-provided",
        usage_scope=("private-training",),
    )
    dataset = dataset_fixture()
    result = ExtractionResult(
        source=source,
        chunks=(chunk,),
        visuals=(visual,),
        datasets=(dataset,),
        evidence=evidence_fixture(),
    )

    assert ExtractionResult.model_validate(result.model_dump()) == result
    with pytest.raises(ValidationError):
        SourceAssetVersion.model_validate({**source.model_dump(), "byte_size": "1024"})


def test_vocabulary_lineage_and_review_contracts_forbid_unknown_fields() -> None:
    vocabulary = vocabulary_fixture()
    assert isinstance(TagVocabularyVersion.model_validate(vocabulary.model_dump()), TagVocabularyVersion)
    for model in (lineage_fixture(), review_task_fixture()):
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unexpected": True})


def test_lineage_requires_version_endpoints_and_evidence() -> None:
    lineage = lineage_fixture()
    for missing in ("from_version_id", "to_version_id", "evidence_id"):
        payload = lineage.model_dump()
        payload.pop(missing)
        with pytest.raises(ValidationError):
            LineageEdge.model_validate(payload)


def test_vocabulary_labels_are_recursively_immutable() -> None:
    vocabulary = vocabulary_fixture()

    with pytest.raises(TypeError):
        vocabulary.dimensions[0].values[0].labels["en"] = "Mutated"


def test_evidence_json_containers_are_recursively_immutable() -> None:
    evidence = evidence_fixture()

    with pytest.raises(TypeError):
        evidence.checks[0].details["new"] = True
    with pytest.raises(TypeError):
        evidence.input_summary["query"]["terms"][0] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        evidence.output_summary["counts"]["checks"] = 2  # type: ignore[index]


def test_dataset_profile_containers_are_recursively_immutable() -> None:
    dataset = dataset_fixture()

    with pytest.raises(TypeError):
        dataset.missingness["order_id"] = 1.0
    with pytest.raises(TypeError):
        dataset.sample_rows[0]["order_id"] = 2
    with pytest.raises(TypeError):
        dataset.sample_rows[0]["metrics"]["values"][0] = 99  # type: ignore[index]


def test_default_json_containers_are_immutable() -> None:
    check = EvidenceCheck(code="default-details", status="passed", message="No details")
    with pytest.raises(TypeError):
        check.details["new"] = True

    evidence = EvidenceObject(
        evidence_id="default-summaries",
        kind="validation",
        status="verified",
        producer="course-helper/tests",
        started_at=NOW,
        finished_at=NOW,
    )
    with pytest.raises(TypeError):
        evidence.input_summary["new"] = True
    with pytest.raises(TypeError):
        evidence.output_summary["new"] = True

    dataset_payload = dataset_fixture().model_dump()
    dataset_payload.pop("missingness")
    dataset = DatasetAssetVersion.model_validate(dataset_payload)
    with pytest.raises(TypeError):
        dataset.missingness["new"] = 1.0


def test_vocabulary_label_serializer_preserves_nested_include_and_exclude() -> None:
    vocabulary = vocabulary_fixture()
    path = {"dimensions": {0: {"values": {0: {"labels": {"en"}}}}}}

    included = vocabulary.model_dump(include=path)
    excluded = vocabulary.model_dump(
        exclude={"dimensions": {0: {"values": {0: {"labels": {"zh-CN"}}}}}},
    )

    assert included["dimensions"][0]["values"][0]["labels"] == {"en": "Beginner"}
    assert excluded["dimensions"][0]["values"][0]["labels"] == {"en": "Beginner"}


def test_evidence_detail_serializer_preserves_nested_include_and_exclude() -> None:
    check = evidence_fixture().checks[0]

    included = check.model_dump(include={"details": {"citation"}})
    excluded = check.model_dump(exclude={"details": {"secret"}})

    assert included == {"details": {"citation": {"chunk_ids": ["chunk-1"]}}}
    assert excluded["details"] == {"citation": {"chunk_ids": ["chunk-1"]}}


def test_evidence_summary_serializer_preserves_nested_include_and_exclude() -> None:
    evidence = evidence_fixture()
    include = {"input_summary": {"query"}, "output_summary": {"counts"}}
    exclude = {"input_summary": {"secret"}, "output_summary": {"secret"}}

    included = evidence.model_dump(include=include)
    excluded = evidence.model_dump(exclude=exclude)

    assert included == {
        "input_summary": {"query": {"terms": ["citation"]}},
        "output_summary": {"counts": {"checks": 1}},
    }
    assert excluded["input_summary"] == {"query": {"terms": ["citation"]}}
    assert excluded["output_summary"] == {"counts": {"checks": 1}}


def test_dataset_missingness_serializer_preserves_nested_include_and_exclude() -> None:
    dataset = dataset_fixture()

    included = dataset.model_dump(include={"missingness": {"order_id"}})
    excluded = dataset.model_dump(exclude={"missingness": {"internal"}})

    assert included == {"missingness": {"order_id": 0.0}}
    assert excluded["missingness"] == {"order_id": 0.0}


def test_sample_row_serializer_preserves_nested_include_and_exclude() -> None:
    dataset = dataset_fixture()

    included = dataset.model_dump(include={"sample_rows": {0: {"order_id"}}})
    excluded = dataset.model_dump(exclude={"sample_rows": {0: {"secret"}}})

    assert included == {"sample_rows": ({"order_id": 1},)}
    assert excluded["sample_rows"][0] == {"order_id": 1, "metrics": {"values": [10, 20]}}


def test_public_json_container_annotations_and_runtime_values_are_read_only() -> None:
    assert get_origin(TagValue.model_fields["labels"].annotation) is Mapping
    assert get_origin(EvidenceCheck.model_fields["details"].annotation) is Mapping
    assert get_origin(EvidenceObject.model_fields["input_summary"].annotation) is Mapping
    assert get_origin(DatasetAssetVersion.model_fields["missingness"].annotation) is Mapping
    sample_rows_annotation = DatasetAssetVersion.model_fields["sample_rows"].annotation
    assert get_origin(get_args(sample_rows_annotation)[0]) is Mapping

    evidence = evidence_fixture()
    terms = evidence.input_summary["query"]["terms"]  # type: ignore[index]
    assert isinstance(evidence.input_summary, Mapping)
    assert not isinstance(evidence.input_summary, dict)
    assert isinstance(terms, Sequence)
    assert not isinstance(terms, list)
    assert tuple(terms) == ("citation",)


def test_frozen_dict_uses_mapping_equality_and_read_apis() -> None:
    frozen = FrozenDict({"one": 1, "two": 2})

    assert frozen == {"one": 1, "two": 2}
    assert {"one": 1, "two": 2} == frozen
    assert frozen != {"one": 1}
    assert frozen != [("one", 1), ("two", 2)]
    assert dict(frozen.items()) == {"one": 1, "two": 2}
    assert tuple(frozen) == ("one", "two")


@pytest.mark.parametrize("model", [vocabulary_fixture(), evidence_fixture(), dataset_fixture()])
def test_recursively_immutable_models_keep_json_compatible_dumps(model: object) -> None:
    python_payload = model.model_dump()  # type: ignore[attr-defined]
    assert type(model).model_validate(python_payload) == model  # type: ignore[attr-defined]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
        assert json.loads(model.model_dump_json()) == payload  # type: ignore[attr-defined]
    json.dumps(payload, ensure_ascii=False, sort_keys=True)

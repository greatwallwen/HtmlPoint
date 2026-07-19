from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from course_helper.domain.common import ActorRef
from course_helper.domain.knowledge import ReviewTask
from course_helper.domain.sources import VisualAssetVersion


CONTRACT_MODULES = (
    "course_helper.domain.composition",
    "course_helper.domain.slide_ast",
    "course_helper.domain.visual_policy",
)
MISSING_CONTRACT_MODULES = tuple(
    module for module in CONTRACT_MODULES if importlib.util.find_spec(module) is None
)

if not MISSING_CONTRACT_MODULES:
    from course_helper.domain.composition import (
        CardPlacement,
        CourseOutline,
        CourseOutlineChapter,
        CourseRequirement,
        CourseVersion,
        canonical_digest,
        canonical_json,
    )
    from course_helper.domain.slide_ast import (
        RuntimeJobBinding,
        RuntimeManifest,
        SlideAssetBinding,
        SlideDeckAst,
        SlideNode,
        runtime_manifest_content_digest,
        slide_deck_content_digest,
    )
    from course_helper.domain.visual_policy import (
        AttributionBlock,
        AuthenticityPolicy,
        CropRect,
        LicensePolicy,
        TransformationManifest,
        TrustedExternalLink,
        VisualPlacement,
        VisualPolicyContext,
        evaluate_visual_publication,
    )


NOW = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
REQUIRES_CONTRACTS = pytest.mark.skipif(
    bool(MISSING_CONTRACT_MODULES),
    reason=f"Task 1 contract modules are missing: {MISSING_CONTRACT_MODULES!r}",
)


def actor() -> ActorRef:
    return ActorRef(actor_type="human", actor_id="trainer-1", display_name="Trainer")


def version_meta(*, logical_id: str, version_id: str, digest: str = DIGEST_A) -> dict[str, object]:
    return {
        "logical_id": logical_id,
        "version_id": version_id,
        "revision": 1,
        "content_digest": digest,
        "created_at": NOW,
        "created_by": actor(),
    }


def fixture_path() -> Path:
    return Path(__file__).parent / "fixtures" / "schema-v1" / "review-visual.json"


def test_task_1_contract_modules_exist() -> None:
    assert not MISSING_CONTRACT_MODULES, (
        "Task 1 must provide the canonical composition, Slide AST, and visual-policy modules; "
        f"missing {MISSING_CONTRACT_MODULES!r}"
    )


@REQUIRES_CONTRACTS
def test_course_requirement_is_frozen_strict_bounded_and_tag_disjoint() -> None:
    requirement = CourseRequirement(
        requirement_id="requirement-1",
        title="Evidence-backed AI course",
        audience="Product managers",
        learning_goals=("Explain grounded generation", "Validate one evidence trail"),
        duration_minutes=60,
        required_tag_ids=("topic:ai",),
        excluded_tag_ids=("topic:unsafe",),
        usage_scope="internal",
    )
    with pytest.raises(ValidationError):
        requirement.title = "mutated"
    with pytest.raises(ValidationError):
        CourseRequirement.model_validate({**requirement.model_dump(), "duration_minutes": True})
    with pytest.raises(ValidationError, match="five-minute"):
        CourseRequirement.model_validate({**requirement.model_dump(), "duration_minutes": 63})
    with pytest.raises(ValidationError):
        CourseRequirement.model_validate({**requirement.model_dump(), "duration_minutes": 485})
    with pytest.raises(ValidationError, match="disjoint"):
        CourseRequirement.model_validate(
            {**requirement.model_dump(), "excluded_tag_ids": ("topic:unsafe", "topic:ai")}
        )
    with pytest.raises(ValidationError):
        CourseRequirement.model_validate({**requirement.model_dump(), "raw_html": "<script>"})


@REQUIRES_CONTRACTS
def test_course_requirement_rejects_empty_duplicate_and_excessive_goals() -> None:
    base = {
        "requirement_id": "requirement-1",
        "title": "Course",
        "audience": "Managers",
        "duration_minutes": 60,
        "required_tag_ids": (),
        "excluded_tag_ids": (),
        "usage_scope": "private-training",
    }
    with pytest.raises(ValidationError):
        CourseRequirement(**base, learning_goals=())
    with pytest.raises(ValidationError, match="unique"):
        CourseRequirement(**base, learning_goals=("One goal", "One goal"))
    with pytest.raises(ValidationError):
        CourseRequirement(**base, learning_goals=tuple(f"Goal {index}" for index in range(21)))


@REQUIRES_CONTRACTS
def test_outline_rejects_duplicate_or_cross_chapter_placements() -> None:
    placement = CardPlacement(
        placement_id="placement-1",
        card_version_id="card-v1",
        chapter_id="chapter-1",
        lesson_id="lesson-1",
        purpose="core",
        allocated_minutes=10,
    )
    chapter = CourseOutlineChapter(
        chapter_id="chapter-1",
        title="Grounding",
        objective="Trace a grounded claim",
        placements=(placement,),
    )
    outline = CourseOutline(
        **version_meta(logical_id="outline-1", version_id="outline-v1"),
        requirement_id="requirement-1",
        chapters=(chapter,),
        uncovered_goals=(),
        retrieval_evidence_id="evidence-retrieval-1",
        index_snapshot_id="index-snapshot-1",
    )
    assert outline.chapters[0].placements == (placement,)

    duplicate_chapter = CourseOutlineChapter(
        chapter_id="chapter-2",
        title="Duplicate",
        objective="Must fail",
        placements=(
            CardPlacement(
                **{
                    **placement.model_dump(),
                    "chapter_id": "chapter-2",
                }
            ),
        ),
    )
    with pytest.raises(ValidationError, match="placement IDs"):
        CourseOutline.model_validate(
            {**outline.model_dump(), "chapters": (chapter, duplicate_chapter)}
        )
    with pytest.raises(ValidationError, match="chapter"):
        CourseOutlineChapter.model_validate(
            {
                **chapter.model_dump(),
                "placements": (
                    CardPlacement.model_validate(
                        {**placement.model_dump(), "chapter_id": "chapter-other"}
                    ),
                ),
            }
        )


@REQUIRES_CONTRACTS
def test_course_versions_are_digest_bound_and_canonical_serialization_is_stable() -> None:
    course = CourseVersion(
        **version_meta(logical_id="course-1", version_id="course-v1"),
        requirement_id="requirement-1",
        outline_version_id="outline-v1",
        outline_digest=DIGEST_B,
        placement_ids=("placement-1", "placement-2"),
        usage_scope="internal",
        confirmation_digest="c" * 64,
        status="confirmed",
    )
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})
    assert canonical_json(course) == canonical_json(CourseVersion.model_validate(course.model_dump()))
    with pytest.raises(ValidationError):
        CourseVersion.model_validate(
            {**course.model_dump(), "placement_ids": ("placement-1", "placement-1")}
        )
    with pytest.raises(ValidationError):
        CourseVersion.model_validate({**course.model_dump(), "version_id": "../course-v1"})


@REQUIRES_CONTRACTS
@pytest.mark.parametrize(
    "bad_id",
    ("..", "../secret", r"C:\secret", "https://example.com/id", "bad id", "bad\x00id", "x" * 129),
)
def test_all_opaque_id_collections_reject_path_url_and_unbounded_values(bad_id: str) -> None:
    course = CourseVersion(
        **version_meta(logical_id="course-1", version_id="course-v1"),
        requirement_id="requirement-1",
        outline_version_id="outline-v1",
        outline_digest=DIGEST_B,
        placement_ids=("placement-1",),
        usage_scope="internal",
        confirmation_digest="c" * 64,
        status="confirmed",
    )
    node = SlideNode(
        node_id="node-1",
        node_type="paragraph",
        text="Grounded content",
        placement_ids=("placement-1",),
        card_version_ids=("card-v1",),
        evidence_ids=("evidence-1",),
    )
    manifest = RuntimeManifest(
        **version_meta(logical_id="runtime-1", version_id="runtime-v1"),
        course_version_id="course-v1",
        slide_deck_version_id="deck-v1",
        slide_deck_digest=DIGEST_A,
        artifact_ids=("artifact-1",),
        evidence_ids=("evidence-1",),
    )
    review = ReviewTask(
        task_id="review-1",
        kind="manual-review",
        subject_version_id="card-v1",
        status="open",
        blocking=True,
        evidence_ids=("evidence-1",),
        created_at=NOW,
        created_by=actor(),
    )

    invalid_payloads = (
        (CourseVersion, {**course.model_dump(), "placement_ids": (bad_id,)}),
        (SlideNode, {**node.model_dump(), "placement_ids": (bad_id,)}),
        (SlideNode, {**node.model_dump(), "card_version_ids": (bad_id,)}),
        (SlideNode, {**node.model_dump(), "chunk_ids": (bad_id,)}),
        (SlideNode, {**node.model_dump(), "source_version_ids": (bad_id,)}),
        (SlideNode, {**node.model_dump(), "evidence_ids": (bad_id,)}),
        (RuntimeManifest, {**manifest.model_dump(), "artifact_ids": (bad_id,)}),
        (RuntimeManifest, {**manifest.model_dump(), "evidence_ids": (bad_id,)}),
        (ReviewTask, {**review.model_dump(), "task_id": bad_id}),
        (ReviewTask, {**review.model_dump(), "subject_version_id": bad_id}),
        (ReviewTask, {**review.model_dump(), "evidence_ids": (bad_id,)}),
    )
    for model, payload in invalid_payloads:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@REQUIRES_CONTRACTS
def test_slide_nodes_bind_only_immutable_ids_and_reject_unsafe_extra_fields() -> None:
    attribution = AttributionBlock(title="Source visual", license_label="source-provided")
    transformation = TransformationManifest(
        transformation_id="transformation-binding-1",
        scale_mode="contain",
        derivative_license_decision="not-derivative",
        share_alike_compatible=True,
        gfdl_compatible=True,
        no_derivatives_compatible=True,
    )
    binding = SlideAssetBinding(
        binding_id="binding-1",
        visual_placement_id="visual-placement-1",
        visual_version_id="visual-v1",
        artifact_id="artifact-1",
        artifact_digest=DIGEST_A,
        media_type="image/png",
        alt_text="A traced source visual",
        authenticity_evidence_id="evidence-authenticity-1",
        license_evidence_id="evidence-license-1",
        attribution_id="attribution-"
        + canonical_digest(attribution.model_dump(mode="json", exclude_none=True)),
        attribution=attribution,
        transformation_id=transformation.transformation_id,
        transformation=transformation,
    )
    node = SlideNode(
        node_id="node-1",
        node_type="visual",
        text=None,
        placement_ids=("placement-1",),
        card_version_ids=("card-v1",),
        evidence_ids=("evidence-1",),
        asset_bindings=(binding,),
    )
    assert node.asset_bindings[0].artifact_digest == DIGEST_A
    for unsafe in ("raw_html", "shell_command", "local_path", "browser_url"):
        with pytest.raises(ValidationError):
            SlideNode.model_validate({**node.model_dump(), unsafe: "unsafe"})
    with pytest.raises(ValidationError, match="binding IDs"):
        SlideNode.model_validate(
            {**node.model_dump(), "asset_bindings": (binding, binding)}
        )


@REQUIRES_CONTRACTS
def test_slide_deck_and_runtime_manifest_enforce_unique_pinned_bindings() -> None:
    node = SlideNode(
        node_id="node-1",
        node_type="paragraph",
        text="Grounded content",
        placement_ids=("placement-1",),
        card_version_ids=("card-v1",),
        evidence_ids=("evidence-1",),
    )
    deck = SlideDeckAst(
        **version_meta(logical_id="deck-1", version_id="deck-v1"),
        course_version_id="course-v1",
        nodes=(node,),
    )
    job = RuntimeJobBinding(
        job_id="job-1",
        job_type="chart_build",
        spec_id="chart-spec-1",
        evidence_id="evidence-1",
        timeout_seconds=30,
    )
    manifest = RuntimeManifest(
        **version_meta(logical_id="runtime-1", version_id="runtime-v1"),
        course_version_id="course-v1",
        slide_deck_version_id=deck.version_id,
        slide_deck_digest=deck.content_digest,
        job_bindings=(job,),
        artifact_ids=("artifact-1",),
        evidence_ids=("evidence-1",),
    )
    assert manifest.slide_deck_digest == deck.content_digest
    with pytest.raises(ValidationError, match="node IDs"):
        SlideDeckAst.model_validate({**deck.model_dump(), "nodes": (node, node)})
    with pytest.raises(ValidationError):
        RuntimeJobBinding.model_validate({**job.model_dump(), "job_type": "shell"})
    with pytest.raises(ValidationError):
        RuntimeJobBinding.model_validate({**job.model_dump(), "command": "rm -rf"})
    with pytest.raises(ValidationError, match="job IDs"):
        RuntimeManifest.model_validate(
            {**manifest.model_dump(), "job_bindings": (job, job)}
        )


@REQUIRES_CONTRACTS
def test_slide_and_runtime_semantic_digests_bind_lineage_notes_and_jobs() -> None:
    node = SlideNode(
        node_id="slide-1",
        node_type="slide",
        text="Grounding",
        placement_ids=("placement-1",),
        card_version_ids=("card-v1",),
        chunk_ids=("chunk-v1",),
        source_version_ids=("source-v1",),
        evidence_ids=("evidence-v1",),
        presenter_notes="Explain the cited grounding evidence.",
    )
    deck = SlideDeckAst(
        **version_meta(logical_id="deck-1", version_id="deck-v1"),
        course_version_id="course-v1",
        nodes=(node,),
    )
    assert slide_deck_content_digest(deck) != slide_deck_content_digest(
        deck.model_copy(
            update={
                "nodes": (
                    node.model_copy(update={"presenter_notes": "Different notes"}),
                )
            }
        )
    )
    manifest = RuntimeManifest(
        **version_meta(logical_id="runtime-1", version_id="runtime-v1"),
        course_version_id="course-v1",
        slide_deck_version_id=deck.version_id,
        slide_deck_digest=slide_deck_content_digest(deck),
        evidence_ids=("evidence-v1",),
    )
    job = RuntimeJobBinding(
        job_id="job-1",
        job_type="dataset_sql",
        spec_id="dataset-spec-1",
        evidence_id="evidence-v1",
        timeout_seconds=30,
    )
    assert runtime_manifest_content_digest(manifest) != runtime_manifest_content_digest(
        manifest.model_copy(update={"job_bindings": (job,)})
    )


@REQUIRES_CONTRACTS
def test_slide_deck_bounds_total_nodes_and_depth_before_recursive_walk() -> None:
    leaf: dict[str, object] = {
        "node_id": "node-depth-33",
        "node_type": "paragraph",
        "text": "leaf",
        "placement_ids": ("placement-1",),
        "card_version_ids": ("card-v1",),
        "evidence_ids": ("evidence-1",),
    }
    for depth in range(32, 0, -1):
        leaf = {
            "node_id": f"node-depth-{depth}",
            "node_type": "slide",
            "text": None,
            "placement_ids": ("placement-1",),
            "card_version_ids": ("card-v1",),
            "evidence_ids": ("evidence-1",),
            "children": (leaf,),
        }
    with pytest.raises(ValidationError, match="depth"):
        SlideDeckAst.model_validate(
            {
                **version_meta(logical_id="deck-1", version_id="deck-v1"),
                "course_version_id": "course-v1",
                "nodes": (leaf,),
            }
        )

    children = tuple(
        {
            "node_id": f"child-{index}",
            "node_type": "paragraph",
            "text": "bounded",
            "placement_ids": ("placement-1",),
            "card_version_ids": ("card-v1",),
            "evidence_ids": ("evidence-1",),
            "children": tuple(
                {
                    "node_id": f"grandchild-{index}-{nested}",
                    "node_type": "paragraph",
                    "text": "bounded",
                    "placement_ids": ("placement-1",),
                    "card_version_ids": ("card-v1",),
                    "evidence_ids": ("evidence-1",),
                }
                for nested in range(5)
            ),
        }
        for index in range(100)
    )
    with pytest.raises(ValidationError, match="500"):
        SlideDeckAst.model_validate(
            {
                **version_meta(logical_id="deck-2", version_id="deck-v2"),
                "course_version_id": "course-v1",
                "nodes": (
                    {
                        "node_id": "root",
                        "node_type": "slide",
                        "text": None,
                        "placement_ids": ("placement-1",),
                        "card_version_ids": ("card-v1",),
                        "evidence_ids": ("evidence-1",),
                        "children": children,
                    },
                ),
            }
        )


@REQUIRES_CONTRACTS
def test_visual_placement_binds_lineage_attribution_and_transformation_decisions() -> None:
    landing = TrustedExternalLink(
        link_id="link-landing-1",
        link_type="landing",
        href="https://commons.wikimedia.org/wiki/File:Grounded.png",
        provenance_kind="licensed-secondary",
        label="Source page",
    )
    attribution = AttributionBlock(
        title="Grounded visual",
        creator="Example Author",
        publisher="Wikimedia Commons",
        license_label="CC BY-SA 4.0",
        landing_link=landing,
    )
    transformation = TransformationManifest(
        transformation_id="transform-1",
        crop=CropRect(x=0.0, y=0.0, width=1.0, height=1.0),
        scale_mode="contain",
        color_adjustments=(),
        change_notice="Scaled without content changes",
        derivative_license_decision="same-license",
        export_license="CC BY-SA 4.0",
        share_alike_compatible=True,
        gfdl_compatible=True,
        no_derivatives_compatible=True,
    )
    placement = VisualPlacement(
        placement_id="visual-placement-1",
        visual_version_id="visual-v1",
        slide_node_id="node-1",
        slot_id="hero",
        fit="contain",
        crop=transformation.crop,
        alt_text="Grounded visual",
        authenticity_evidence_id="evidence-authenticity-1",
        license_evidence_id="evidence-license-1",
        attribution=attribution,
        transformation=transformation,
        originating_card_version_id="card-v1",
        originating_source_version_id="source-v1",
    )
    assert placement.attribution.landing_link == landing
    with pytest.raises(ValidationError, match="lineage"):
        VisualPlacement.model_validate(
            {
                **placement.model_dump(),
                "originating_card_version_id": None,
                "originating_source_version_id": None,
                "originating_dataset_version_id": None,
            }
        )
    with pytest.raises(ValidationError):
        CropRect(x=0.8, y=0.0, width=0.4, height=1.0)


@REQUIRES_CONTRACTS
def test_visual_transformation_and_license_decisions_cannot_contradict_rendering() -> None:
    crop = CropRect(x=0.0, y=0.0, width=0.5, height=1.0)
    transformation = TransformationManifest(
        transformation_id="transform-1",
        crop=crop,
        scale_mode="cover",
        color_adjustments=("contrast:+5",),
        change_notice="Cropped and adjusted contrast",
        derivative_license_decision="same-license",
        export_license="CC BY-SA 4.0",
        share_alike_compatible=True,
        gfdl_compatible=True,
        no_derivatives_compatible=False,
    )
    attribution = AttributionBlock(
        title="Grounded visual",
        license_label="CC BY-SA 4.0",
    )
    with pytest.raises(ValidationError, match="crop"):
        VisualPlacement(
            placement_id="visual-placement-1",
            visual_version_id="visual-v1",
            slide_node_id="node-1",
            slot_id="hero",
            fit="cover",
            crop=None,
            alt_text="Grounded visual",
            authenticity_evidence_id="evidence-authenticity-1",
            license_evidence_id="evidence-license-1",
            attribution=attribution,
            transformation=transformation,
            originating_source_version_id="source-v1",
        )
    with pytest.raises(ValidationError, match="prohibited"):
        TransformationManifest.model_validate(
            {
                **transformation.model_dump(),
                "crop": None,
                "color_adjustments": (),
                "derivative_license_decision": "prohibited",
                "export_license": None,
                "share_alike_compatible": True,
                "gfdl_compatible": True,
                "no_derivatives_compatible": True,
            }
        )


@REQUIRES_CONTRACTS
def test_trusted_external_link_is_https_typed_and_credential_free() -> None:
    link = TrustedExternalLink(
        link_id="link-1",
        link_type="license",
        href="https://creativecommons.org/licenses/by/4.0/",
        provenance_kind="licensed-secondary",
        label="CC BY 4.0",
    )
    assert link.href.startswith("https://")
    for unsafe in (
        "http://example.com/license",
        "file:///secret.txt",
        "https://user:password@example.com/license",
        "https://example.com/license#token",
    ):
        with pytest.raises(ValidationError):
            TrustedExternalLink.model_validate({**link.model_dump(), "href": unsafe})
    with pytest.raises(ValidationError):
        TrustedExternalLink.model_validate({**link.model_dump(), "asset_url": "https://example.com/a.png"})


@REQUIRES_CONTRACTS
def test_authenticity_and_license_policies_have_exact_versioned_defaults() -> None:
    authenticity = AuthenticityPolicy()
    license_policy = LicensePolicy()
    assert authenticity.policy_id == "course-studio-authenticity-v1"
    assert authenticity.selection_order == (
        "official-primary",
        "source-provided",
        "data-derived",
        "licensed-secondary",
        "generated",
    )
    assert authenticity.network_metadata_ttl_hours == 24
    assert license_policy.policy_id == "course-studio-license-v1"
    with pytest.raises(ValidationError):
        AuthenticityPolicy(selection_order=("generated",))
    with pytest.raises(ValidationError):
        LicensePolicy(public_requires_verified_authorization=False)


@REQUIRES_CONTRACTS
def test_publication_policy_fails_closed_but_keeps_private_warnings_explicit() -> None:
    private_unknown = VisualPolicyContext(
        usage_scope="private-training",
        authenticity="source-provided",
        license_status="unknown",
        rights_verified=False,
        now=NOW,
    )
    private_decision = evaluate_visual_publication(private_unknown)
    assert private_decision.allowed is True
    assert "SOURCE_RIGHTS_UNVERIFIED" in private_decision.warnings

    public_unknown = VisualPolicyContext.model_validate(
        {**private_unknown.model_dump(), "usage_scope": "public"}
    )
    public_decision = evaluate_visual_publication(public_unknown)
    assert public_decision.allowed is False
    assert "VERIFIED_AUTHORIZATION_REQUIRED" in public_decision.blockers

    expired_network = VisualPolicyContext(
        usage_scope="public",
        authenticity="licensed-secondary",
        license_status="licensed",
        rights_verified=True,
        network_metadata_verified_at=NOW - timedelta(hours=25),
        now=NOW,
    )
    expired_decision = evaluate_visual_publication(expired_network)
    assert expired_decision.allowed is False
    assert "NETWORK_METADATA_EXPIRED" in expired_decision.blockers

    with pytest.raises(ValidationError):
        VisualPolicyContext.model_validate({**private_unknown.model_dump(), "rights_verified": 1})


@REQUIRES_CONTRACTS
def test_public_data_derived_requires_authorization_integrity_and_attribution() -> None:
    base = VisualPolicyContext(
        usage_scope="public",
        authenticity="data-derived",
        license_status="verified",
        rights_verified=False,
        dataset_integrity_verified=True,
        attribution_verified=True,
        now=NOW,
    )
    assert "VERIFIED_AUTHORIZATION_REQUIRED" in evaluate_visual_publication(base).blockers
    missing_attribution = VisualPolicyContext.model_validate(
        {**base.model_dump(), "rights_verified": True, "attribution_verified": False}
    )
    assert "ATTRIBUTION_REQUIRED" in evaluate_visual_publication(missing_attribution).blockers
    complete = VisualPolicyContext.model_validate(
        {**base.model_dump(), "rights_verified": True, "attribution_verified": True}
    )
    assert evaluate_visual_publication(complete).allowed


@REQUIRES_CONTRACTS
def test_data_derived_and_generated_policy_require_their_specific_evidence() -> None:
    data_context = VisualPolicyContext(
        usage_scope="internal",
        authenticity="data-derived",
        license_status="verified",
        rights_verified=True,
        dataset_integrity_verified=False,
        now=NOW,
    )
    assert "DATASET_INTEGRITY_REQUIRED" in evaluate_visual_publication(data_context).blockers
    assert evaluate_visual_publication(
        VisualPolicyContext.model_validate(
            {**data_context.model_dump(), "dataset_integrity_verified": True}
        )
    ).allowed

    generated = VisualPolicyContext(
        usage_scope="private-training",
        authenticity="generated",
        license_status="generated",
        rights_verified=False,
        generated_asset_existing=True,
        generated_labeled=False,
        rights_statement_present=False,
        now=NOW,
    )
    decision = evaluate_visual_publication(generated)
    assert decision.allowed is False
    assert set(decision.blockers) >= {"GENERATED_LABEL_REQUIRED", "RIGHTS_STATEMENT_REQUIRED"}


@REQUIRES_CONTRACTS
def test_schema_v1_review_payloads_keep_bytes_and_digest_while_exposing_mapping() -> None:
    fixture = json.loads(fixture_path().read_text(encoding="utf-8"))
    expected_mapping = {
        "source-changed": ("source-changed", "source-changed"),
        "near-duplicate": ("near-duplicate", "near-duplicate"),
        "unknown-tag": ("tag", "unknown-tag"),
        "deprecated-tag": ("tag", "deprecated-tag"),
        "tag-conflict": ("tag", "tag-conflict"),
        "citation-missing": ("candidate-card", "citation-missing"),
        "visual-rights": ("visual-rights", "visual-rights"),
        "visual-unverified": ("visual-rights", "visual-unverified"),
        "dataset-reference": ("candidate-card", "dataset-reference"),
        "sensitive-sample": ("candidate-card", "sensitive-sample"),
        "grain-needs-review": ("candidate-card", "grain-needs-review"),
        "provenance": ("candidate-card", "provenance"),
        "manual-review": ("candidate-card", "manual-review"),
    }
    assert {record["kind"] for record in fixture["reviewPayloads"]} == set(expected_mapping)

    for record in fixture["reviewPayloads"]:
        payload = record["originalCanonicalPayload"]
        assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == record["originalSha256"]
        task = ReviewTask.model_validate_json(payload)
        serialized = canonical_json(task)
        assert serialized == payload
        assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == record["originalSha256"]
        assert (task.category, task.reason_code) == expected_mapping[task.kind]


@REQUIRES_CONTRACTS
def test_new_review_kinds_use_exact_category_and_reason_without_rewriting_legacy_fields() -> None:
    common = {
        "subject_version_id": "card-v2",
        "status": "open",
        "blocking": True,
        "evidence_ids": ("evidence-2",),
        "created_at": NOW,
        "created_by": actor(),
    }
    exact = ReviewTask(task_id="review-exact", kind="exact-duplicate", **common)
    feedback = ReviewTask(task_id="review-feedback", kind="course-feedback", **common)
    assert (exact.category, exact.reason_code) == ("exact-duplicate", "exact-duplicate")
    assert (feedback.category, feedback.reason_code) == ("course-feedback", "course-feedback")
    assert "category" not in exact.model_dump()
    assert "reason_code" not in exact.model_dump()


@REQUIRES_CONTRACTS
def test_schema_v1_visual_round_trip_keeps_server_urls_out_of_browser_projection() -> None:
    fixture = json.loads(fixture_path().read_text(encoding="utf-8"))
    record = fixture["visualPayload"]
    payload = record["originalCanonicalPayload"]
    assert hashlib.sha256(payload.encode("utf-8")).hexdigest() == record["originalSha256"]
    visual = VisualAssetVersion.model_validate_json(payload)
    serialized = canonical_json(visual)
    assert serialized == payload
    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == record["originalSha256"]

    original = json.loads(payload)

    server_only = visual.server_provenance()
    assert server_only.landing_page_url == original["landing_page_url"]
    assert server_only.asset_url == original["asset_url"]

    browser = visual.to_browser_view()
    browser_json = browser.model_dump_json()
    assert "landing_page_url" not in browser_json
    assert "asset_url" not in browser_json
    assert original["landing_page_url"] not in browser_json
    assert original["asset_url"] not in browser_json

    disguised_urls = (
        original["asset_url"],
        original["asset_url"].replace("upload.wikimedia.org", "UPLOAD.WIKIMEDIA.ORG"),
        original["asset_url"].replace("upload.wikimedia.org", "upload.wikimedia.org:443"),
        original["asset_url"].replace("schema-v1-final.png", "%73chema-v1-final.png"),
    )
    for index, disguised_url in enumerate(disguised_urls):
        disguised_final_media = TrustedExternalLink(
            link_id=f"link-final-media-{index}",
            link_type="landing",
            href=disguised_url,
            provenance_kind="licensed-secondary",
            label="Not a landing page",
        )
        with pytest.raises(ValueError, match="final media"):
            visual.to_browser_view(trusted_links=(disguised_final_media,))

    with pytest.raises(ValidationError):
        VisualAssetVersion.model_validate_json(
            json.dumps(
                {**original, "usage_scope": ["private-training", "arbitrary"]},
                ensure_ascii=False,
            )
        )
    with pytest.raises(ValidationError, match="unique"):
        VisualAssetVersion.model_validate_json(
            json.dumps(
                {**original, "usage_scope": ["private-training", "private-training"]},
                ensure_ascii=False,
            )
        )
    with pytest.raises(ValidationError):
        VisualAssetVersion.model_validate_json(
            json.dumps({**original, "version_id": "../visual-v1"}, ensure_ascii=False)
        )


@REQUIRES_CONTRACTS
def test_compatibility_re_exports_share_the_canonical_class_objects() -> None:
    from course_helper import domain
    from course_helper.domain.composition import CourseRequirement as CanonicalRequirement
    from course_helper.domain.slide_ast import SlideDeckAst as CanonicalDeck
    from course_helper.domain.visual_policy import VisualPlacement as CanonicalPlacement

    assert domain.CourseRequirement is CanonicalRequirement
    assert domain.SlideDeckAst is CanonicalDeck
    assert domain.VisualPlacement is CanonicalPlacement

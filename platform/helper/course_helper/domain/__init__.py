"""Strict domain contracts shared by helper workflows."""

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
    VisualPolicyDecision,
    evaluate_visual_publication,
)


__all__ = [
    "AttributionBlock",
    "AuthenticityPolicy",
    "CardPlacement",
    "CourseOutline",
    "CourseOutlineChapter",
    "CourseRequirement",
    "CourseVersion",
    "CropRect",
    "LicensePolicy",
    "RuntimeJobBinding",
    "RuntimeManifest",
    "SlideAssetBinding",
    "SlideDeckAst",
    "SlideNode",
    "TransformationManifest",
    "TrustedExternalLink",
    "VisualPlacement",
    "VisualPolicyContext",
    "VisualPolicyDecision",
    "canonical_digest",
    "canonical_json",
    "evaluate_visual_publication",
]

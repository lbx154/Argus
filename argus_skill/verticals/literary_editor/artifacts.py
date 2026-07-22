"""literary_editor review + artifact vocabularies and consumers."""
from __future__ import annotations

from typing import Any

from ..literary.shared.artifact_manifest import normalize_manifest
from ..literary.shared.review_contract import normalize_review, revision_plan
from .edit_ops import EDIT_FINDING_TYPES

#: Editor finding vocabulary: the machine edit-discipline types (blocking) PLUS
#: live-reviewer craft types. Craft is NON-blocking and never mechanized — whether
#: the edit reads better, whether a fact was invented, whether the edit overreached
#: the allowed scope are live judgements.
EDITOR_CRAFT_TYPES: frozenset[str] = frozenset({
    "edit_quality", "fact_fidelity", "coherence", "over_reach",
})
EDITOR_FINDING_TYPES: frozenset[str] = EDIT_FINDING_TYPES | EDITOR_CRAFT_TYPES

EDITOR_ARTIFACT_KINDS: frozenset[str] = frozenset({
    "task_envelope", "source_text", "edit_brief", "review", "revision_plan",
    "edited_text", "change_summary",
})


def editor_revision_plan(review_raw: dict[str, Any]) -> list[dict[str, Any]]:
    review = normalize_review(review_raw, type_vocabulary=EDITOR_FINDING_TYPES)
    return revision_plan(review)


def build_editor_manifest(task_id: str) -> dict[str, Any]:
    """Canonical editing chain: source + brief -> diagnosis(review) -> revision
    plan -> edited text (supersedes the source, traces back to source + review)
    -> change summary."""
    artifacts = [
        {"artifact_id": "env", "kind": "task_envelope", "version": 1,
         "producer_stage": "intake", "content_path": "editor/task_envelope.json",
         "status": "active"},
        {"artifact_id": "source", "kind": "source_text", "version": 1,
         "producer_stage": "intake", "content_path": "editor/source.txt",
         "status": "superseded"},
        {"artifact_id": "brief", "kind": "edit_brief", "version": 1,
         "producer_stage": "intake", "content_path": "editor/edit_brief.json",
         "parent_artifact_ids": ["env"], "status": "active"},
        {"artifact_id": "review", "kind": "review", "version": 1,
         "producer_stage": "diagnose", "content_path": "editor/review.json",
         "parent_artifact_ids": ["source", "brief"], "status": "active"},
        {"artifact_id": "revplan", "kind": "revision_plan", "version": 1,
         "producer_stage": "revision_plan", "content_path": "editor/revision_plan.json",
         "parent_artifact_ids": ["review"], "status": "active"},
        {"artifact_id": "edited", "kind": "edited_text", "version": 2,
         "producer_stage": "edit", "content_path": "editor/edited.txt",
         "parent_artifact_ids": ["source", "revplan"], "supersedes": "source",
         "change_reason": "applied the edit within its mode discipline",
         "status": "final"},
        {"artifact_id": "summary", "kind": "change_summary", "version": 1,
         "producer_stage": "verify", "content_path": "editor/change_summary.json",
         "parent_artifact_ids": ["edited"], "status": "active"},
    ]
    return normalize_manifest({"task_id": task_id, "artifacts": artifacts},
                              kind_vocabulary=EDITOR_ARTIFACT_KINDS)


__all__ = [
    "EDITOR_CRAFT_TYPES", "EDITOR_FINDING_TYPES", "EDITOR_ARTIFACT_KINDS",
    "editor_revision_plan", "build_editor_manifest",
]

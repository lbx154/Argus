"""modern_poetry review + artifact vocabularies and consumers."""
from __future__ import annotations

from typing import Any

from ..literary.shared.artifact_manifest import normalize_manifest
from ..literary.shared.review_contract import normalize_review, revision_plan
from .form import FORM_FINDING_TYPES

#: Modern-poetry finding vocabulary: the machine hard-constraint types (blocking)
#: PLUS live-reviewer craft types (imagery/lineation/tone/cliche/coherence). Craft
#: is NON-blocking heuristic and never mechanized.
MODERN_CRAFT_TYPES: frozenset[str] = frozenset({
    "imagery", "lineation", "tone", "cliche", "coherence", "reference_fidelity",
})
MODERN_FINDING_TYPES: frozenset[str] = FORM_FINDING_TYPES | MODERN_CRAFT_TYPES

MODERN_ARTIFACT_KINDS: frozenset[str] = frozenset({
    "task_envelope", "poem_brief", "form_spec", "draft_poem", "form_report",
    "review", "revision_plan", "final_poem",
})


def modern_revision_plan(review_raw: dict[str, Any]) -> list[dict[str, Any]]:
    review = normalize_review(review_raw, type_vocabulary=MODERN_FINDING_TYPES)
    return revision_plan(review)


def build_modern_manifest(task_id: str) -> dict[str, Any]:
    artifacts = [
        {"artifact_id": "env", "kind": "task_envelope", "version": 1,
         "producer_stage": "intake", "content_path": "poetry/task_envelope.json",
         "status": "active"},
        {"artifact_id": "brief", "kind": "poem_brief", "version": 1,
         "producer_stage": "intake", "content_path": "poetry/poem_brief.json",
         "parent_artifact_ids": ["env"], "status": "active"},
        {"artifact_id": "spec", "kind": "form_spec", "version": 1,
         "producer_stage": "plan", "content_path": "poetry/form_spec.json",
         "parent_artifact_ids": ["brief"], "status": "active"},
        {"artifact_id": "draft", "kind": "draft_poem", "version": 1,
         "producer_stage": "compose", "content_path": "poetry/draft_poem.txt",
         "parent_artifact_ids": ["spec"], "status": "superseded"},
        {"artifact_id": "form", "kind": "form_report", "version": 1,
         "producer_stage": "form_check", "content_path": "poetry/form_report.json",
         "parent_artifact_ids": ["draft"], "status": "active"},
        {"artifact_id": "review", "kind": "review", "version": 1,
         "producer_stage": "review", "content_path": "poetry/review.json",
         "parent_artifact_ids": ["draft", "form"], "status": "active"},
        {"artifact_id": "revplan", "kind": "revision_plan", "version": 1,
         "producer_stage": "revise", "content_path": "poetry/revision_plan.json",
         "parent_artifact_ids": ["review"], "status": "active"},
        {"artifact_id": "final", "kind": "final_poem", "version": 2,
         "producer_stage": "revise", "content_path": "poetry/final_poem.txt",
         "parent_artifact_ids": ["draft", "revplan"], "supersedes": "draft",
         "change_reason": "applied form + craft fixes", "status": "final"},
    ]
    return normalize_manifest({"task_id": task_id, "artifacts": artifacts},
                              kind_vocabulary=MODERN_ARTIFACT_KINDS)


__all__ = [
    "MODERN_CRAFT_TYPES", "MODERN_FINDING_TYPES", "MODERN_ARTIFACT_KINDS",
    "modern_revision_plan", "build_modern_manifest",
]

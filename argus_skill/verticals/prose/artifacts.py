"""prose review + artifact vocabularies and consumers."""
from __future__ import annotations

from typing import Any

from ...literary.artifact_manifest import normalize_manifest
from ...literary.review_contract import normalize_review, revision_plan
from .structure import STRUCTURE_FINDING_TYPES

#: Prose finding vocabulary: machine structure/hard-constraint types (blocking)
#: PLUS live-reviewer craft types. Craft is NON-blocking and never mechanized —
#: fact_memory (is fact kept distinct from memory?), fabrication (did the model
#: invent a user fact?), movement, observation, ending, template are all live.
PROSE_CRAFT_TYPES: frozenset[str] = frozenset({
    "observation", "fact_memory", "fabrication", "movement", "imagery",
    "ending", "template",
})
PROSE_FINDING_TYPES: frozenset[str] = STRUCTURE_FINDING_TYPES | PROSE_CRAFT_TYPES

PROSE_ARTIFACT_KINDS: frozenset[str] = frozenset({
    "task_envelope", "prose_brief", "prose_state", "draft", "structure_report",
    "review", "revision_plan", "final",
})


def prose_revision_plan(review_raw: dict[str, Any]) -> list[dict[str, Any]]:
    review = normalize_review(review_raw, type_vocabulary=PROSE_FINDING_TYPES)
    return revision_plan(review)


def build_prose_manifest(task_id: str) -> dict[str, Any]:
    artifacts = [
        {"artifact_id": "env", "kind": "task_envelope", "version": 1,
         "producer_stage": "intake", "content_path": "prose/task_envelope.json",
         "status": "active"},
        {"artifact_id": "brief", "kind": "prose_brief", "version": 1,
         "producer_stage": "intake", "content_path": "prose/prose_brief.json",
         "parent_artifact_ids": ["env"], "status": "active"},
        {"artifact_id": "state", "kind": "prose_state", "version": 1,
         "producer_stage": "plan", "content_path": "prose/prose_state.json",
         "parent_artifact_ids": ["brief"], "status": "active"},
        {"artifact_id": "draft", "kind": "draft", "version": 1,
         "producer_stage": "draft", "content_path": "prose/draft.md",
         "parent_artifact_ids": ["state"], "status": "superseded"},
        {"artifact_id": "struct", "kind": "structure_report", "version": 1,
         "producer_stage": "structure_check", "content_path": "prose/structure_report.json",
         "parent_artifact_ids": ["draft", "state"], "status": "active"},
        {"artifact_id": "review", "kind": "review", "version": 1,
         "producer_stage": "review", "content_path": "prose/review.json",
         "parent_artifact_ids": ["draft", "struct"], "status": "active"},
        {"artifact_id": "revplan", "kind": "revision_plan", "version": 1,
         "producer_stage": "revise", "content_path": "prose/revision_plan.json",
         "parent_artifact_ids": ["review"], "status": "active"},
        {"artifact_id": "final", "kind": "final", "version": 2,
         "producer_stage": "revise", "content_path": "prose/final.md",
         "parent_artifact_ids": ["draft", "revplan"], "supersedes": "draft",
         "change_reason": "applied structure + craft fixes", "status": "final"},
    ]
    return normalize_manifest({"task_id": task_id, "artifacts": artifacts},
                              kind_vocabulary=PROSE_ARTIFACT_KINDS)


__all__ = [
    "PROSE_CRAFT_TYPES", "PROSE_FINDING_TYPES", "PROSE_ARTIFACT_KINDS",
    "prose_revision_plan", "build_prose_manifest",
]

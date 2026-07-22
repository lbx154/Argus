"""classical_poetry review + artifact vocabularies and consumers.

Binds the shared Review and Artifact contracts to poetry's own vocabularies —
proving those contracts serve a second, very different vertical without any
poetry semantics leaking into the shared layer.
"""
from __future__ import annotations

from typing import Any

from ..literary.shared.artifact_manifest import normalize_manifest
from ..literary.shared.review_contract import normalize_review, revision_plan
from .prosody import PROSODY_FINDING_TYPES

#: Poetry finding vocabulary: the machine-decidable prosody types (rhyme/meter/
#: hard_fault/parallelism) PLUS the live-reviewer craft types (意境/意象/炼字/典故/
#: 反AI). Craft types are NON-blocking heuristics; only prosody types gate.
POETRY_CRAFT_TYPES: frozenset[str] = frozenset({
    "conception", "imagery", "diction", "allusion", "tone", "anti_ai",
})
POETRY_FINDING_TYPES: frozenset[str] = PROSODY_FINDING_TYPES | POETRY_CRAFT_TYPES

#: Poetry's artifact kinds (the manifest KIND vocabulary is vertical-local).
POETRY_ARTIFACT_KINDS: frozenset[str] = frozenset({
    "task_envelope", "poem_brief", "form_plan", "draft_poem", "prosody_report",
    "review", "revision_plan", "final_poem",
})


def poetry_revision_plan(review_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a review against poetry's vocabulary and return the ordered plan."""
    review = normalize_review(review_raw, type_vocabulary=POETRY_FINDING_TYPES)
    return revision_plan(review)


def build_poetry_manifest(task_id: str) -> dict[str, Any]:
    """Canonical poetry artifact chain: brief -> form_plan -> draft -> prosody
    report -> review -> revision_plan -> final; final supersedes the draft and
    traces back to both the draft and the review."""
    artifacts = [
        {"artifact_id": "env", "kind": "task_envelope", "version": 1,
         "producer_stage": "intake", "content_path": "poetry/task_envelope.json",
         "status": "active"},
        {"artifact_id": "brief", "kind": "poem_brief", "version": 1,
         "producer_stage": "intake", "content_path": "poetry/poem_brief.json",
         "parent_artifact_ids": ["env"], "status": "active"},
        {"artifact_id": "plan", "kind": "form_plan", "version": 1,
         "producer_stage": "form_plan", "content_path": "poetry/form_plan.json",
         "parent_artifact_ids": ["brief"], "status": "active"},
        {"artifact_id": "draft", "kind": "draft_poem", "version": 1,
         "producer_stage": "compose", "content_path": "poetry/draft_poem.txt",
         "parent_artifact_ids": ["plan"], "status": "superseded"},
        {"artifact_id": "prosody", "kind": "prosody_report", "version": 1,
         "producer_stage": "prosody_check", "content_path": "poetry/prosody_report.json",
         "parent_artifact_ids": ["draft"], "status": "active"},
        {"artifact_id": "review", "kind": "review", "version": 1,
         "producer_stage": "review", "content_path": "poetry/review.json",
         "parent_artifact_ids": ["draft", "prosody"], "status": "active"},
        {"artifact_id": "revplan", "kind": "revision_plan", "version": 1,
         "producer_stage": "revise", "content_path": "poetry/revision_plan.json",
         "parent_artifact_ids": ["review"], "status": "active"},
        {"artifact_id": "final", "kind": "final_poem", "version": 2,
         "producer_stage": "revise", "content_path": "poetry/final_poem.txt",
         "parent_artifact_ids": ["draft", "revplan"], "supersedes": "draft",
         "change_reason": "applied prosody + craft fixes", "status": "final"},
    ]
    return normalize_manifest({"task_id": task_id, "artifacts": artifacts},
                              kind_vocabulary=POETRY_ARTIFACT_KINDS)


__all__ = [
    "POETRY_CRAFT_TYPES",
    "POETRY_FINDING_TYPES",
    "POETRY_ARTIFACT_KINDS",
    "poetry_revision_plan",
    "build_poetry_manifest",
]

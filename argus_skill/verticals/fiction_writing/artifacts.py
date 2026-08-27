"""fiction_writing artifact lineage: bind the shared artifact-manifest contract
to fiction's own artifact VOCABULARY and canonical production chain.

A fiction mission produces a fixed chain of artifacts across its six stages;
:func:`build_fiction_manifest` is the reference producer of that chain (with
correct parents, producer_stage, content paths, and supersede/version
bookkeeping). The shared contract can validate a recorded manifest.

The canonical chain and its lineage:

    creative_brief ─┬─ story_plan ─┬─ chapter_goal
                    └─ style_profile │
                                     ▼
                                   draft ──┬─ state_patch ─ story_state ─ review
                                           │                                 │
                                           │                          revision_plan
                                           ▼                                 │
                                  final (supersedes draft) ◄─────────────────┘
                                           │
                            updated_story_state (supersedes story_state)

So ``final.md`` traces back to both the ``draft`` it revised and the ``review``
that drove it, and each replaced artifact (draft, story_state) is explicitly
``superseded`` — never silently overwritten.
"""
from __future__ import annotations

from typing import Any

from ..literary.shared.artifact_manifest import normalize_manifest

#: Fiction's artifact kinds — the closed vocabulary the shared contract validates
#: this vertical's manifest against. A kind outside this set is rejected.
FICTION_ARTIFACT_KINDS: frozenset[str] = frozenset({
    "creative_brief", "style_profile", "story_plan", "chapter_goal", "draft",
    "state_patch", "story_state", "review", "revision_plan", "final",
    "updated_story_state",
})


def build_fiction_manifest(task_id: str) -> dict[str, Any]:
    """Return the canonical, validated fiction artifact manifest for ``task_id``.

    This is the deterministic reference chain an engineer can record in
    ``fiction/artifact_manifest.json``; it is normalized + validated against
    :data:`FICTION_ARTIFACT_KINDS` before it is returned, so a drift in the chain
    (a broken parent, a supersede left incoherent) fails during construction.
    """
    artifacts: list[dict[str, Any]] = [
        {"artifact_id": "brief", "kind": "creative_brief", "version": 1,
         "producer_stage": "intake", "content_path": "fiction/creative_brief.json",
         "status": "active"},
        {"artifact_id": "style", "kind": "style_profile", "version": 1,
         "producer_stage": "intake", "content_path": "fiction/style_profile.json",
         "parent_artifact_ids": ["brief"], "status": "active"},
        {"artifact_id": "plan", "kind": "story_plan", "version": 1,
         "producer_stage": "plan", "content_path": "fiction/story_plan.json",
         "parent_artifact_ids": ["brief"], "status": "active"},
        {"artifact_id": "goal", "kind": "chapter_goal", "version": 1,
         "producer_stage": "plan", "content_path": "fiction/chapter_goal.json",
         "parent_artifact_ids": ["brief", "plan"], "status": "active"},
        {"artifact_id": "draft", "kind": "draft", "version": 1,
         "producer_stage": "draft", "content_path": "fiction/draft.md",
         "parent_artifact_ids": ["plan", "goal", "style"],
         "status": "superseded"},
        {"artifact_id": "patch", "kind": "state_patch", "version": 1,
         "producer_stage": "state_update", "content_path": "fiction/state_patch.json",
         "parent_artifact_ids": ["draft"], "status": "active"},
        {"artifact_id": "state", "kind": "story_state", "version": 1,
         "producer_stage": "state_update", "content_path": "fiction/story_state.json",
         "parent_artifact_ids": ["patch"], "status": "superseded"},
        {"artifact_id": "review", "kind": "review", "version": 1,
         "producer_stage": "review", "content_path": "fiction/review.json",
         "parent_artifact_ids": ["draft", "state"], "status": "active"},
        {"artifact_id": "revplan", "kind": "revision_plan", "version": 1,
         "producer_stage": "revise", "content_path": "fiction/revision_plan.json",
         "parent_artifact_ids": ["review"], "status": "active"},
        {"artifact_id": "final", "kind": "final", "version": 2,
         "producer_stage": "revise", "content_path": "fiction/final.md",
         "parent_artifact_ids": ["draft", "revplan"], "supersedes": "draft",
         "change_reason": "applied review's blocking fixes", "status": "final"},
        {"artifact_id": "final_state", "kind": "updated_story_state", "version": 2,
         "producer_stage": "revise", "content_path": "fiction/updated_story_state.json",
         "parent_artifact_ids": ["state", "final"], "supersedes": "state",
         "change_reason": "state reconciled with revised prose", "status": "final"},
    ]
    return normalize_manifest({"task_id": task_id, "artifacts": artifacts},
                              kind_vocabulary=FICTION_ARTIFACT_KINDS)


__all__ = [
    "FICTION_ARTIFACT_KINDS",
    "build_fiction_manifest",
]

"""Automatic (unattended) library housekeeping: ``SkillLoopConfig.auto_compact_enabled``.

Near-duplicate skills/wiki-pages that slip past the create-time independence
checks (pre-existing skills seeded before the check existed, or two
concurrent missions each comparing against a library snapshot that doesn't
yet contain the other's proposal) would otherwise accumulate forever in an
unattended 7x24 daemon. This wires the LLM-judged batched clustering
(``llm_plan_compaction`` / ``llm_cluster_wiki``) into the automatic
post-mission flow — mirroring the wiki mechanical hooks' always-on cadence —
so it runs after EVERY mission with no operator action required, and no
lexical/scored fallback: when the judge is unavailable or returns nothing
usable, the pass is simply a no-op for that mission.

Safety floor pinned here: a protected/governing skill must NEVER be an
auto-archive candidate, even when it loses the naive version/task_history
tiebreak to a heavily-reused ordinary near-duplicate — auto-compaction must
not become a backdoor around SkillRouter's self-governance floor.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig, SkillStore
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.store import Skill
from argus_skill.wiki.bootstrap import init_wiki
from argus_skill.wiki.schema import PageCard
from argus_skill.wiki.store import WikiStore

_DEBUG_CUDA_OOM = """## Title
Debug CUDA OOM in training loop

## Description
Diagnose and fix out-of-memory errors during GPU training by reducing batch size, enabling gradient checkpointing, or clearing cache.

## Category
gpu-debug

## When to use
- training crashes with CUDA out of memory

## How to solve
1. Check nvidia-smi for current memory usage.
2. Reduce batch size or enable gradient accumulation.
3. Enable gradient checkpointing if using a large model.
4. Call torch.cuda.empty_cache() between runs.
"""

_FIX_GPU_OVERFLOW = """## Title
Fix GPU memory overflow during model training

## Description
Resolve CUDA out-of-memory crashes in the training loop by shrinking batch size, turning on gradient checkpointing, or freeing cached memory.

## Category
gpu-debug

## When to use
- training process dies with an out-of-memory CUDA error

## How to solve
1. Inspect nvidia-smi to see current GPU memory usage.
2. Shrink the batch size or use gradient accumulation instead.
3. Turn on gradient checkpointing for large models.
4. Call torch.cuda.empty_cache() between training runs.
"""


def _done_review() -> str:
    return json.dumps({
        "status": "done",
        "reason": "ok", "next_action": "none",
        "round_summary_markdown": "# Review\n\n- done\n",
        "completion_summary_markdown": "",
    })


def _loop(skills_dir: Path, backend: MemoryBackend, events: list,
          *, auto_compact: bool) -> SkillLoop:
    return SkillLoop(
        skills_dir=skills_dir,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(max_rounds=1, auto_compact_enabled=auto_compact),
        on_event=events.append,
    )


def _seed_page(store: WikiStore, *, id: str, status: str, title: str, body: str,
               sources: list[str] | None = None) -> None:
    store.write_page(PageCard(
        id=id, type="technique", status=status, title=title, tags=[],
        sources=sources or [], related_runs=[], related_projects=[],
        revisit_after=None, created_at=date(2026, 7, 4),
        last_reviewed_at=date(2026, 7, 4), reviewer_note="", body=body,
    ))


def test_auto_compact_merges_preexisting_near_duplicate_skills(tmp_path: Path) -> None:
    """Two near-duplicate skills that predate the create-time independence
    check (seeded directly, not via SkillRouter) are merged automatically
    after the NEXT ordinary mission closes — no operator action."""
    skills_dir = tmp_path / "skills"
    store = SkillStore(skills_dir)
    store.save_distilled(task_description="t1", raw_distill_output=_DEBUG_CUDA_OOM)
    store.save_distilled(task_description="t2", raw_distill_output=_FIX_GPU_OVERFLOW)
    assert len(store.list_summaries()) == 2

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("scientist.skill_distill", CannedResponse(message="NONE"))
    backend.queue("engineer-r1", CannedResponse(message="unrelated work"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [[
            "Debug CUDA OOM in training loop",
            "Fix GPU memory overflow during model training",
        ]],
    })))

    events: list[dict] = []
    _loop(skills_dir, backend, events, auto_compact=True).run(
        "some unrelated task", workdir=tmp_path)

    assert any(e.get("type") == "skill.compacted" for e in events), [
        e.get("type") for e in events]
    remaining = SkillStore(skills_dir).list_summaries()
    assert len(remaining) == 1


def test_auto_compact_disabled_by_default_leaves_duplicates(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    store = SkillStore(skills_dir)
    store.save_distilled(task_description="t1", raw_distill_output=_DEBUG_CUDA_OOM)
    store.save_distilled(task_description="t2", raw_distill_output=_FIX_GPU_OVERFLOW)

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("scientist.skill_distill", CannedResponse(message="NONE"))
    backend.queue("engineer-r1", CannedResponse(message="unrelated work"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))

    events: list[dict] = []
    _loop(skills_dir, backend, events, auto_compact=False).run(
        "some unrelated task", workdir=tmp_path)

    assert not any(e.get("type") == "skill.compacted" for e in events)
    assert len(SkillStore(skills_dir).list_summaries()) == 2


def test_auto_compact_never_archives_a_protected_skill(tmp_path: Path) -> None:
    """A protected/governing skill must survive even when it loses the naive
    version/task_history tiebreak to a heavily-reused ordinary near-duplicate —
    auto-compaction is not a backdoor around the self-governance floor."""
    skills_dir = tmp_path / "skills"
    store = SkillStore(skills_dir)
    store.save(Skill(
        name="Anti-cheat guardrail", description="Never fabricate a metric.",
        category="anti-cheat",
        content=(
            "## When to use\nalways\n\n"
            "## How to solve\nNever report a fabricated or unverified metric.\n"
        ),
        protected=True, version=1, task_history=[],
    ))
    store.save(Skill(
        name="Never fake a metric enforcement", description="Do not fabricate metrics ever.",
        category="misc",
        content=(
            "## When to use\nalways applies to every mission\n\n"
            "## How to solve\nDo not report a fabricated or unverified metric result.\n"
        ),
        # Heavily "proven" so the naive version*history heuristic alone would
        # prefer this one over the protected skill above.
        version=9, task_history=[f"m{i}" for i in range(9)],
    ))
    assert len(store.list_summaries()) == 2

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("scientist.skill_distill", CannedResponse(message="NONE"))
    backend.queue("engineer-r1", CannedResponse(message="unrelated work"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    backend.queue("skill.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [["Anti-cheat guardrail", "Never fake a metric enforcement"]],
    })))

    events: list[dict] = []
    _loop(skills_dir, backend, events, auto_compact=True).run(
        "some unrelated task", workdir=tmp_path)

    remaining_names = {s["name"] for s in SkillStore(skills_dir).list_summaries()}
    assert "Anti-cheat guardrail" in remaining_names


def test_auto_compact_merges_preexisting_near_duplicate_wiki_pages(tmp_path: Path) -> None:
    """Wiki-side counterpart: two near-duplicate pages that predate (or slipped
    past) the create-time independence check are merged automatically."""
    wiki_root = init_wiki("demo", base=tmp_path)
    store = WikiStore(wiki_root)
    _seed_page(
        store, id="grpo-async-clip", status="scratch",
        title="GRPO Asymmetric Clipping",
        body="GRPO clips the ratio asymmetrically to keep training stable. "
             "Use asymmetric epsilon bounds when the policy diverges.",
    )
    _seed_page(
        store, id="asymmetric-ratio-clip-grpo", status="candidate",
        title="Asymmetric Ratio Clipping for GRPO",
        body="To keep GRPO training stable, clip the ratio asymmetrically using "
             "different upper/lower epsilon bounds when the policy diverges.",
        sources=["grpo-tricks"],
    )
    assert len(store.iter_pages()) == 2

    skills_dir = tmp_path / "skills"
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("scientist.skill_distill", CannedResponse(message="NONE"))
    backend.queue("engineer-r1", CannedResponse(message="unrelated work"))
    backend.queue("reviewer", CannedResponse(message=_done_review()))
    backend.queue("wiki.compaction_batch", CannedResponse(message=json.dumps({
        "clusters": [[
            "GRPO Asymmetric Clipping",
            "Asymmetric Ratio Clipping for GRPO",
        ]],
    })))

    events: list[dict] = []
    _loop(skills_dir, backend, events, auto_compact=True).run(
        "some unrelated task", workdir=tmp_path)

    assert any(e.get("type") == "wiki.compacted" for e in events), [
        e.get("type") for e in events]
    remaining = WikiStore(wiki_root).iter_pages()
    assert len(remaining) == 1
    # The higher-status (candidate) page won over the scratch one.
    assert remaining[0].id == "asymmetric-ratio-clip-grpo"

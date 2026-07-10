"""LLM-judged skill independence check: ``SkillRouter.judge_runner``.

Independence (duplicate) detection is judged ENTIRELY by an LLM, over
COMPACT SUMMARIES only (name + description + category — progressive
disclosure, same shape the skill matcher already uses). The model answers
a plain yes/no-shaped verdict — never a similarity score. There is no
lexical/scored fallback: when a non-empty library needs the duplicate
judge, missing/broken/unusable judge infrastructure now rejects the
proposal explicitly instead of silently letting it through. This module
tests that LLM-judge path directly (with a scripted judge response), and
pins the explicit-rejection behavior when no usable verdict is available.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.store import SkillStore

_VALID_SKILL = (
    "## Title\n{name}\n\n"
    "## Description\n{desc}\n\n"
    "## Category\nmisc\n\n"
    "## When to use\nWhen this applies.\n\n"
    "## How to solve\nDo the thing.\n\n"
    "## Sources\n"
    "- [Python documentation](https://docs.python.org/3/) — implementation reference.\n"
    "- [Git documentation](https://git-scm.com/docs) — workflow reference.\n"
)


def _content(name: str = "New Skill", desc: str = "A description.") -> str:
    return _VALID_SKILL.format(name=name, desc=desc)


def _store_with_existing(tmp_path: Path, *, name: str = "Existing Skill") -> SkillStore:
    store = SkillStore(tmp_path / "skills")
    store.save_distilled(
        task_description="seed", raw_distill_output=_content(name=name, desc="Existing capability."),
    )
    return store


def test_llm_judge_rejects_when_model_says_duplicate(tmp_path: Path) -> None:
    """Even content that would NOT trip the mechanical cosine threshold is
    rejected when the judge explicitly says it duplicates an existing skill —
    the LLM verdict is authoritative when a judge_runner is configured."""
    store = _store_with_existing(tmp_path, name="Existing Skill")
    backend = MemoryBackend()
    backend.queue("skill.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": True, "of": "Existing Skill", "why": "same underlying capability",
    })))
    router = SkillRouter(skill_store=store, judge_runner=backend, judge_model="m")

    events: list[dict] = []
    counts = router.apply_ops(
        [{"op": "create", "content": _content(name="Totally Different Wording"),
          "why": "x"}],
        task="t", on_event=events.append,
    )
    assert counts == {"created": 0, "updated": 0, "archived": 0, "rejected": 1}
    rejected = [e for e in events if e.get("type") == "skill.proposal.rejected"]
    assert rejected and "llm judge" in rejected[0]["text"]
    assert len(store.list_summaries()) == 1


def test_llm_judge_allows_when_model_says_not_duplicate(tmp_path: Path) -> None:
    store = _store_with_existing(tmp_path)
    backend = MemoryBackend()
    backend.queue("skill.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": False, "of": "", "why": "different capability",
    })))
    router = SkillRouter(skill_store=store, judge_runner=backend, judge_model="m")

    counts = router.apply_ops(
        [{"op": "create", "content": _content(name="Genuinely New Skill"), "why": "x"}],
        task="t",
    )
    assert counts == {"created": 1, "updated": 0, "archived": 0, "rejected": 0}
    assert len(store.list_summaries()) == 2


def test_scientist_candidate_uses_zero_token_exact_dedupe_only(tmp_path: Path) -> None:
    store = _store_with_existing(tmp_path)

    class _MustNotRun:
        def run_exec(self, **_kwargs):  # pragma: no cover - must stay zero-call
            raise AssertionError("Scientist candidate must not trigger a second LLM judge")

    router = SkillRouter(skill_store=store, judge_runner=_MustNotRun(), judge_model="m")
    created = router.create_candidate(
        _content(name="Different Exact Name", desc="Different exact description."),
        task="new task",
    )
    assert created is not None
    assert created.provisional is True


def test_llm_judge_empty_library_short_circuits_without_calling_runner(tmp_path: Path) -> None:
    """An empty library can never contain a duplicate — must not waste a
    call on the judge runner (and must not accidentally consume its queue)."""
    store = SkillStore(tmp_path / "skills")

    class _ExplodingRunner:
        def run_exec(self, **_kwargs):  # pragma: no cover - must never run
            raise AssertionError("judge runner must not be called against an empty library")

    router = SkillRouter(skill_store=store, judge_runner=_ExplodingRunner(), judge_model="m")
    counts = router.apply_ops(
        [{"op": "create", "content": _content(), "why": "x"}], task="t",
    )
    assert counts == {"created": 1, "updated": 0, "archived": 0, "rejected": 0}


def test_no_judge_runner_rejects_when_library_is_nonempty(tmp_path: Path) -> None:
    store = _store_with_existing(tmp_path, name="Write a hello message")
    router = SkillRouter(skill_store=store)  # no judge_runner
    dup_content = (
        "## Title\nAlternative hello workflow\n\n"
        "## Description\nDifferent wording around the same capability.\n\n"
            "## Category\nmisc\n\n"
            "## When to use\nWhen this applies.\n\n"
            "## How to solve\nDo the thing.\n\n"
            "## Sources\n"
            "- [Python documentation](https://docs.python.org/3/) — implementation reference.\n"
            "- [Git documentation](https://git-scm.com/docs) — workflow reference.\n"
        )
    events: list[dict] = []
    counts = router.apply_ops(
        [{"op": "create", "content": dup_content, "why": "x"}],
        task="t", on_event=events.append,
    )
    assert counts == {"created": 0, "updated": 0, "archived": 0, "rejected": 1}
    rejected = [e for e in events if e.get("type") == "skill.proposal.rejected"]
    assert rejected and "duplicate judge unavailable" in rejected[0]["text"]
    assert len(store.list_summaries()) == 1


def test_judge_runner_exception_rejects_proposal(tmp_path: Path) -> None:
    store = _store_with_existing(tmp_path, name="Write a hello message")

    class _BrokenRunner:
        def run_exec(self, **_kwargs):
            raise RuntimeError("backend exploded")

    router = SkillRouter(skill_store=store, judge_runner=_BrokenRunner(), judge_model="m")
    dup_content = (
        "## Title\nWrite a hello message\n\n"
        "## Description\nExisting capability.\n\n"
        "## Category\nmisc\n\n"
        "## When to use\nWhen this applies.\n\n"
        "## How to solve\nDo the thing.\n"
    )
    counts = router.apply_ops([{"op": "create", "content": dup_content, "why": "x"}], task="t")
    assert counts["rejected"] == 1
    assert counts["created"] == 0


def test_judge_malformed_json_rejects_proposal(tmp_path: Path) -> None:
    store = _store_with_existing(tmp_path, name="Write a hello message")
    backend = MemoryBackend()
    backend.queue("skill.duplicate_check", CannedResponse(message="not valid json at all"))
    router = SkillRouter(skill_store=store, judge_runner=backend, judge_model="m")
    dup_content = (
        "## Title\nWrite a hello message\n\n"
        "## Description\nExisting capability.\n\n"
        "## Category\nmisc\n\n"
        "## When to use\nWhen this applies.\n\n"
        "## How to solve\nDo the thing.\n"
    )
    counts = router.apply_ops([{"op": "create", "content": dup_content, "why": "x"}], task="t")
    assert counts["rejected"] == 1
    assert counts["created"] == 0


def test_judge_duplicate_without_target_rejects_proposal(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.save_distilled(task_description="seed", raw_distill_output=(
        "## Title\nDebug CUDA OOM\n\n## Description\nFix GPU memory crashes.\n\n"
        "## Category\ngpu-debug\n\n## When to use\nGPU runs out of memory.\n\n"
        "## How to solve\nReduce batch size, enable checkpointing.\n"
    ))
    backend = MemoryBackend()
    backend.queue("skill.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": True, "of": "", "why": "vague",
    })))
    router = SkillRouter(skill_store=store, judge_runner=backend, judge_model="m")
    counts = router.apply_ops(
        [{"op": "create", "content": (
            "## Title\nWrite unit tests for a REST endpoint\n\n"
            "## Description\nAdd HTTP status/body assertions for a new route.\n\n"
            "## Category\ntesting\n\n## When to use\nA new endpoint lacks coverage.\n\n"
            "## How to solve\nSpin up a test client, assert status and body.\n"
        ), "why": "x"}],
        task="t",
    )
    assert counts["rejected"] == 1
    assert counts["created"] == 0


def test_update_excludes_own_name_from_judge_summaries(tmp_path: Path) -> None:
    """An update proposal must be judged against every OTHER skill, never
    against itself — mirrors the mechanical path's ``exclude_name``."""
    store = _store_with_existing(tmp_path, name="Target Skill")
    backend = MemoryBackend()
    backend.queue("skill.duplicate_check", CannedResponse(message=json.dumps({
        "duplicate": False, "of": "", "why": "no other skills to compare",
    })))
    router = SkillRouter(skill_store=store, judge_runner=backend, judge_model="m")
    counts = router.apply_ops(
        [{"op": "update", "name": "Target Skill",
          "content": _content(name="Target Skill", desc="Revised."), "why": "x"}],
        task="t",
    )
    assert counts["updated"] == 1


def test_judge_prompt_is_progressive_disclosure_not_full_content_dump(tmp_path: Path) -> None:
    """Cost-control regression: the judge prompt must show SUMMARIES (name +
    description + category) for existing skills, never their full playbook
    body — otherwise the "cheap even against a large library" claim breaks."""
    long_body_marker = "STEP-BY-STEP PROCEDURE MARKER " * 50
    store = SkillStore(tmp_path / "skills")
    store.save_distilled(task_description="seed", raw_distill_output=(
        "## Title\nExisting Skill\n\n## Description\nExisting capability.\n\n"
        "## Category\nmisc\n\n## When to use\nWhen needed.\n\n"
        f"## How to solve\n{long_body_marker}\n"
    ))
    captured: dict[str, str] = {}

    class _CapturingRunner:
        def run_exec(self, *, prompt, **_kwargs):
            captured["prompt"] = prompt
            raise RuntimeError("stop after capturing — no real call needed")

    router = SkillRouter(skill_store=store, judge_runner=_CapturingRunner(), judge_model="m")
    router.apply_ops([{"op": "create", "content": _content(name="New Skill"), "why": "x"}], task="t")
    assert "prompt" in captured
    assert long_body_marker not in captured["prompt"]
    assert "Existing Skill" in captured["prompt"]
    assert "Existing capability." in captured["prompt"]

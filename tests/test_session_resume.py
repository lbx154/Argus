"""Role-session defaults and fresh-only fallback contract."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.adapters.memory_backend import CannedResponse, MemoryBackend
from argus_skill.engineer.runner import SupervisedConfig

SKILL_MD = (
    "## Title\nDemo skill\n\n"
    "## Description\nFresh-session test.\n\n"
    "## Category\ndemo\n\n"
    "## When to use\n- demo\n\n"
    "## When NOT to use\n- never\n\n"
    "## How to solve\n- work\n\n"
    "## Examples\n- demo\n\n"
    "## Response shape\n- concise\n"
)


def _review(status: str) -> str:
    return json.dumps({
        "status": status,
        "reason": "reviewed",
        "next_action": "Finish the work." if status == "continue" else "—",
        "round_summary_markdown": "# review\n",
        "completion_summary_markdown": "done" if status == "done" else "",
    })


def _loop(backend: MemoryBackend, skills: Path, checkpoint: Path | None = None) -> SkillLoop:
    return SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="m",
            reviewer_model="m",
            max_rounds=3,
            backend_failure_backoff_seconds=0,
            checkpoint_path=checkpoint,
        ),
    )


def test_role_session_policy_defaults_to_backend_aware_auto() -> None:
    assert SupervisedConfig().role_session_policy == "auto"


def test_engineer_and_reviewer_never_resume_across_rounds_or_missions(
    tmp_path: Path,
) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("continue"), thread_id="v1"))
    backend.queue("engineer-r2", CannedResponse(message="r2", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="v2"))

    out = _loop(backend, tmp_path / "skills").run(
        "task",
        workdir=tmp_path,
        seed_thread_id="old-mission-thread",
    )

    assert out.successful
    assert out.last_thread_id is None
    role_resumes = [
        (label, tid)
        for label, tid in backend.resume_history
        if label.startswith("engineer-") or label == "reviewer"
    ]
    assert role_resumes == [
        ("engineer-r1", None),
        ("reviewer", None),
        ("engineer-r2", None),
        ("reviewer", None),
    ]


def test_backend_retry_also_starts_fresh(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue(
        "engineer-r1",
        CannedResponse(message="", thread_id="poison", fatal_error="502 Bad Gateway"),
    )
    backend.queue("engineer-r2", CannedResponse(message="recovered", thread_id="healthy"))
    backend.queue("reviewer", CannedResponse(message=_review("done")))

    out = _loop(backend, tmp_path / "skills").run(
        "task", workdir=tmp_path, seed_thread_id="incoming"
    )

    assert out.successful
    assert out.last_thread_id is None
    assert [
        tid for label, tid in backend.resume_history if label.startswith("engineer-")
    ] == [None, None]


def test_continuation_engineer_round_uses_compact_checkpoint_prompt(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("continue")))
    backend.queue("engineer-r2", CannedResponse(message="r2", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("done")))

    # Compact continuation prompts apply only to resumable provider threads;
    # a fresh-policy session repeats the static contract every round.
    loop = _loop(backend, tmp_path / "skills")
    loop.config.role_session_policy = "rolling"
    out = loop.run("task", workdir=tmp_path)
    assert out.successful

    prompts = [
        prompt for label, prompt, _ in backend.history if label.startswith("engineer-")
    ]
    assert len(prompts) == 2
    assert "## This turn" in prompts[0]
    assert "## Current mission task" in prompts[0]
    assert "## Continuation turn" in prompts[1]
    assert "## Current mission task" not in prompts[1]
    assert len(prompts[1]) < len(prompts[0])
    assert all("## Handoff" in prompt for prompt in prompts)


def test_shared_checkpoint_file_survives_across_missions(tmp_path: Path) -> None:
    checkpoint = tmp_path / "CHECKPOINT.md"
    checkpoint.write_text(
        "# Current State\n\nReviewed state from mission A\n",
        encoding="utf-8",
    )

    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))

    def engineer(_prompt, _options) -> str:
        assert "Reviewed state from mission A" in checkpoint.read_text(encoding="utf-8")
        return "mission B work"

    backend.queue("engineer-r1", CannedResponse(message_factory=engineer))
    backend.queue("reviewer", CannedResponse(message=_review("done")))

    out = _loop(backend, tmp_path / "skills", checkpoint).run(
        "task B", workdir=tmp_path
    )
    assert out.successful
    prompt = next(p for label, p, _ in backend.history if label == "engineer-r1")
    assert str(checkpoint.resolve()) in prompt


def test_no_checkpoint_path_creates_no_checkpoint_file(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="work"))
    backend.queue("reviewer", CannedResponse(message=_review("done")))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful
    assert not (tmp_path / "CHECKPOINT.md").exists()

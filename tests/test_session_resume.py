"""Role-session defaults and fresh-only fallback contract."""
from __future__ import annotations

from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from tests.hand_off import hand_off

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


def _review(status: str) -> tuple[str, dict]:
    return hand_off("engineer" if status == "continue" else "done", "reviewed", "Finish the work.")


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


def test_backend_retry_also_starts_fresh(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue(
        "engineer-r1",
        CannedResponse(
            message="", thread_id="poison",
            fatal_error="Process exited with code 2 before turn completion.",
        ),
    )
    backend.queue("engineer-r2", CannedResponse(action=hand_off("reviewer", "recovered"), thread_id="healthy"))
    backend.queue("reviewer", CannedResponse(action=_review("done")))

    out = _loop(backend, tmp_path / "skills").run(
        "task", workdir=tmp_path, seed_thread_id="incoming"
    )

    assert out.successful
    assert out.last_thread_id is None
    assert [
        tid for label, tid in backend.resume_history if label.startswith("engineer-")
    ] == [None, None]

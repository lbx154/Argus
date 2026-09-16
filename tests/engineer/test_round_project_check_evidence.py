"""Host-run project checks reach the Reviewer as raw evidence and the next
Engineer round as a short note, through the ordinary supervised round loop."""
from __future__ import annotations

import json
from pathlib import Path

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.project_checks import NOT_A_GATE_SENTENCE

SKILL_MD = (
    "## Title\nDemo\n\n## Description\nFixed playbook.\n\n## Category\ndemo\n\n"
    "## When to use\n- demo\n\n## When NOT to use\n- prod\n\n"
    "## How to solve\n- do it\n\n## Examples\n- demo\n\n## Response shape\n- inline\n"
)


def _review(status: str) -> str:
    return json.dumps({
        "status": status,
        "reason": "r",
        "next_action": "do the next thing",
        "round_summary_markdown": "# r\n",
        "completion_summary_markdown": "done" if status == "done" else "",
    })


def _loop(backend: MemoryBackend, skills: Path) -> SkillLoop:
    return SkillLoop(
        skills_dir=skills,
        engineer_runner=backend,
        reviewer_runner=backend,
        config=SkillLoopConfig(
            engineer_model="m", reviewer_model="m", max_rounds=5,
            backend_failure_backoff_seconds=0,
        ),
    )


def _project_with_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "tests" / "spec"
    spec.mkdir(parents=True)
    (spec / "test_spec.py").write_text(
        "def test_holds():\n    assert True\n\ndef test_breaks():\n    assert False\n",
        encoding="utf-8",
    )
    return tmp_path


def test_reviewer_sees_host_checks_as_raw_evidence_and_engineer_gets_a_note(
    tmp_path: Path,
) -> None:
    workdir = _project_with_spec(tmp_path)
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("continue"), thread_id="rv1"))
    backend.queue("engineer-r2", CannedResponse(message="r2 work", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="rv2"))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=workdir)
    assert out.successful

    prompts = {}
    for label, prompt, _options in backend.history:
        prompts.setdefault(label, []).append(prompt)
    reviewer_prompts = prompts["reviewer"]
    assert len(reviewer_prompts) == 2
    for index, prompt in enumerate(reviewer_prompts, start=1):
        assert "Raw verification evidence:" in prompt
        assert f"## Host-run project checks (round {index})" in prompt
        assert "tests/spec/test_spec.py::test_breaks" in prompt
        assert NOT_A_GATE_SENTENCE in prompt
        # Evidence, not subagent chatter: it must not ride in the background block.
        assert prompt.index("Raw verification evidence:") > prompt.index(
            "## Engineer's account of this round"
        )

    assert "Host-run project checks" not in prompts["engineer-r1"][0]
    assert "## Host-run project checks from your previous round" in prompts["engineer-r2"][0]
    assert "tests/spec/test_spec.py::test_breaks" in prompts["engineer-r2"][0]

    logs = sorted((workdir / ".argus" / "life" / "round-checks").glob("round-*.txt"))
    assert [path.name for path in logs] == ["round-1.txt", "round-2.txt"]


def test_project_without_spec_suite_adds_no_evidence(tmp_path: Path) -> None:
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(message=_review("done"), thread_id="rv1"))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful
    reviewer_prompt = next(p for label, p, _o in backend.history if label == "reviewer")
    assert "Host-run project checks" not in reviewer_prompt
    assert not (tmp_path / ".argus" / "life" / "round-checks").exists()

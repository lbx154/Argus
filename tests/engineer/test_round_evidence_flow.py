"""Host-gathered round evidence reaches the Reviewer as raw evidence and the
next Engineer round as a short note, through the ordinary supervised round
loop. A fake provider stands in for any vertical; no test suite is run."""
from __future__ import annotations

from pathlib import Path

import pytest

from argus import SkillLoop, SkillLoopConfig
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer import round_evidence as registry
from argus.engineer.round_evidence import RoundEvidence, RoundEvidenceRequest

SKILL_MD = (
    "## Title\nDemo\n\n## Description\nFixed playbook.\n\n## Category\ndemo\n\n"
    "## When to use\n- demo\n\n## When NOT to use\n- prod\n\n"
    "## How to solve\n- do it\n\n## Examples\n- demo\n\n## Response shape\n- inline\n"
)
_NOT_A_GATE = "weigh them as evidence, they are not a gate"


def _review(status: str) -> tuple[str, dict]:
    return ("approve_review" if status == "done" else "revise_review"), {"review": "r; do the next thing"}


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


def _queue_two_rounds(backend: MemoryBackend) -> None:
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(review_action=_review("continue"), thread_id="rv1"))
    backend.queue("engineer-r2", CannedResponse(message="r2 work", thread_id="e2"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="rv2"))


def _prompts_by_label(backend: MemoryBackend) -> dict[str, list[str]]:
    prompts: dict[str, list[str]] = {}
    for label, prompt, _options in backend.history:
        prompts.setdefault(label, []).append(prompt)
    return prompts


def test_reviewer_sees_provider_text_as_raw_evidence_and_engineer_gets_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[RoundEvidenceRequest] = []

    def fake_provider(request: RoundEvidenceRequest) -> RoundEvidence:
        requests.append(request)
        seen = request.previous_state.get("seen", 0) + 1
        return RoundEvidence(
            reviewer_text=(
                f"## Host observation (round {request.round_index})\n"
                f"probe-{request.round_index} fired; {_NOT_A_GATE}."
            ),
            engineer_note=f"## Host observation from your previous round\nprobe-{request.round_index} fired.",
            state={"seen": seen},
        )

    monkeypatch.setattr(registry, "_PROVIDERS", [fake_provider])
    backend = MemoryBackend()
    _queue_two_rounds(backend)

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful

    prompts = _prompts_by_label(backend)
    reviewer_prompts = prompts["reviewer"]
    assert len(reviewer_prompts) == 2
    for index, prompt in enumerate(reviewer_prompts, start=1):
        assert "Raw verification evidence:" in prompt
        assert f"## Host observation (round {index})" in prompt
        assert f"probe-{index} fired" in prompt
        assert _NOT_A_GATE in prompt
        # Evidence, not subagent chatter: it must not ride in the background block.
        assert prompt.index("Raw verification evidence:") > prompt.index(
            "## Engineer's account of this round"
        )
    # Each review sees only its own round's evidence; it is consumed once read.
    assert "probe-2" not in reviewer_prompts[0]
    assert "probe-1 fired" not in reviewer_prompts[1]

    assert "Host observation" not in prompts["engineer-r1"][0]
    assert "## Host observation from your previous round" in prompts["engineer-r2"][0]
    assert "probe-1 fired" in prompts["engineer-r2"][0]

    # The provider saw the workdir, a life dir under it, and its own state back.
    assert [request.round_index for request in requests] == [1, 2]
    assert all(request.workdir == tmp_path for request in requests)
    assert requests[0].life_dir == tmp_path / ".argus" / "life"
    assert requests[0].previous_state == {}
    assert requests[1].previous_state == {"seen": 1}


def test_silent_provider_adds_no_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_PROVIDERS", [lambda request: None])
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="rv1"))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful
    reviewer_prompt = next(p for label, p, _o in backend.history if label == "reviewer")
    assert "Raw verification evidence:" not in reviewer_prompt


def test_raising_provider_does_not_break_the_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: RoundEvidenceRequest) -> RoundEvidence:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(registry, "_PROVIDERS", [boom])
    backend = MemoryBackend()
    backend.queue("matcher", CannedResponse(message='{"matched": []}'))
    backend.queue("distiller", CannedResponse(message=SKILL_MD))
    backend.queue("engineer-r1", CannedResponse(message="r1 work", thread_id="e1"))
    backend.queue("reviewer", CannedResponse(review_action=_review("done"), thread_id="rv1"))

    out = _loop(backend, tmp_path / "skills").run("task", workdir=tmp_path)
    assert out.successful

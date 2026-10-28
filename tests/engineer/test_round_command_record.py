"""The round hands the Reviewer what the host saw of the Engineer's commands.

The calls are captured in host memory while the Engineer's turn runs and
reach the round-evidence providers with the exact path of the host's own
event log; no file near the mission packet is involved.
"""
from __future__ import annotations

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig
from tests.hand_off import hand_off


def _review_prompt(engineer_backend: str, tmp_path) -> str:
    backend = MemoryBackend()
    engineer_runner = backend.fork()
    engineer_runner.backend = engineer_backend
    backend.queue("engineer-r1", CannedResponse(action=hand_off("reviewer", "ran the tests")))
    backend.queue("reviewer", CannedResponse(action=hand_off("done", "Done.")))
    engineer = SupervisedEngineer(
        engineer_runner=engineer_runner, reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"), reviewer_config=ReviewerConfig(model="m"),
    )
    engineer.run(
        objective="Fix the parser.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=1),
        workdir=tmp_path,
    )
    return next(prompt for label, prompt, _options in backend.history if label == "reviewer")


def test_the_reviewer_reruns_the_check_whatever_the_engineer_backend_reports(tmp_path) -> None:
    from argus.core.model_visible_text import REVIEW_EVIDENCE_RULE

    assert REVIEW_EVIDENCE_RULE in _review_prompt("copilot", tmp_path / "a")
    assert REVIEW_EVIDENCE_RULE in _review_prompt("claude", tmp_path / "b")

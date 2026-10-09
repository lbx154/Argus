"""The round hands the Reviewer what the host saw of the Engineer's commands.

The calls are captured in host memory while the Engineer's turn runs and
reach the round-evidence providers with the exact path of the host's own
event log; no file near the mission packet is involved.
"""
from __future__ import annotations

import json

from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.adapters.stream_progress import make_stream_progress_callback
from argus.engineer import round_evidence
from argus.engineer.runner import EngineerConfig, SupervisedConfig, SupervisedEngineer
from argus.reviewer import Reviewer, ReviewerConfig


class _Sink:
    def handle_event(self, event: dict) -> None:
        pass

    def handle_stream_line(self, stream: str, line: str) -> None:
        pass


def test_the_engineer_turn_captures_its_commands_for_the_providers(tmp_path, monkeypatch) -> None:
    backend = MemoryBackend()
    backend.queue("engineer-r1", CannedResponse(message="ran the tests"))
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "Done."})))
    engineer = SupervisedEngineer(
        engineer_runner=backend, reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"), reviewer_config=ReviewerConfig(model="m"),
    )
    stream = make_stream_progress_callback(_Sink())
    original = SupervisedEngineer._run_engineer

    def run_engineer(self, **kwargs):
        label = kwargs["run_label"]
        stream(f"{label}.stdout", json.dumps({"type": "tool.execution_start", "data": {
            "toolCallId": "c1", "toolName": "bash", "arguments": {"command": "pytest -q"}}}))
        stream(f"{label}.stdout", json.dumps({"type": "tool.execution_complete", "data": {
            "toolCallId": "c1", "success": True, "shellExecution": {"exitCode": 0},
            "result": {"content": "14 passed in 0.31s"}}}))
        return original(self, **kwargs)

    monkeypatch.setattr(SupervisedEngineer, "_run_engineer", run_engineer)
    seen: list[round_evidence.RoundEvidenceRequest] = []

    def provider(request):
        seen.append(request)
        return None

    monkeypatch.setattr(round_evidence, "_PROVIDERS", [provider])
    log_path = tmp_path / "project" / "events.jsonl"

    engineer.run(
        objective="Fix the parser.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=1, engineer_log_path=str(log_path),
                                           decision_progress_timeout_seconds=0),
        workdir=tmp_path,
    )

    assert seen, "the providers ran after the Engineer's turn"
    [run] = seen[0].command_runs
    assert run.text == "pytest -q" and run.exit_code == 0 and "14 passed" in run.output
    assert seen[0].events_path == log_path


def _review_prompt(engineer_backend: str, tmp_path) -> str:
    backend = MemoryBackend()
    engineer_runner = backend.fork()
    engineer_runner.backend = engineer_backend
    backend.queue("engineer-r1", CannedResponse(message="ran the tests"))
    backend.queue("reviewer", CannedResponse(review_action=("approve_review", {"review": "Done."})))
    engineer = SupervisedEngineer(
        engineer_runner=engineer_runner, reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"), reviewer_config=ReviewerConfig(model="m"),
    )
    engineer.run(
        objective="Fix the parser.",
        engineer_prompt_builder=lambda _next, _static=True: "Do the task.",
        supervised_config=SupervisedConfig(max_rounds=1, decision_progress_timeout_seconds=0),
        workdir=tmp_path,
    )
    return next(prompt for label, prompt, _options in backend.history if label == "reviewer")


def test_the_reviewer_relies_on_host_records_only_when_the_engineer_backend_reports_them(tmp_path) -> None:
    from argus.core.model_visible_text import (
        REVIEW_EVIDENCE_RULE_READ_ONLY,
        REVIEW_EVIDENCE_RULE_UNRECORDED,
    )

    assert REVIEW_EVIDENCE_RULE_READ_ONLY in _review_prompt("copilot", tmp_path / "a")
    assert REVIEW_EVIDENCE_RULE_UNRECORDED in _review_prompt("claude", tmp_path / "b")

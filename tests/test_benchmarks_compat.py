from __future__ import annotations

import asyncio
import json
import logging
import sys
import types
from pathlib import Path
from typing import Callable, cast

from argus_skill.core.models import RunnerResult
from benchmarks.swebench_pro.docker_env import ExecResult, MinimalDockerEnvironment


class _FakeHarborAdapter(types.ModuleType):
    _AUGMENTED_MAX_CHARS: int
    _extract_thread_id_from_jsonl: Callable[[str], str | None]
    _parse_agent_messages_from_jsonl: Callable[[str], list[str]]

    def __init__(self) -> None:
        super().__init__("benchmarks.harbor_adapter")
        self._AUGMENTED_MAX_CHARS = 1024
        self._extract_thread_id_from_jsonl = self._extract_thread_id_from_jsonl_impl
        self._parse_agent_messages_from_jsonl = (
            self._parse_agent_messages_from_jsonl_impl
        )

    @staticmethod
    def _extract_thread_id_from_jsonl_impl(text: str) -> str | None:
        return "thread-1" if text else None

    @staticmethod
    def _parse_agent_messages_from_jsonl_impl(text: str) -> list[str]:
        return [line for line in text.splitlines() if line.strip()]


class _FakeEnvironment(MinimalDockerEnvironment):
    def __init__(self) -> None:
        super().__init__("fake-container")
        self.default_workdir = "/app"

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        return ExecResult(
            return_code=0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"round one"}}\n',
            stderr="",
        )

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        return None


class _CapturingReviewerBackend:
    instances: list["_CapturingReviewerBackend"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.prompts: list[str] = []
        type(self).instances.append(self)

    def run_exec(
        self,
        *,
        prompt: str,
        options: object,
        run_label: str,
        resume_thread_id: str | None = None,
    ) -> RunnerResult:
        self.prompts.append(prompt)
        return RunnerResult(
            exit_code=0,
            agent_messages=[
                json.dumps(
                    {
                        "status": "done",
                        "confidence": 1.0,
                        "reason": "verified",
                        "next_action": "none",
                        "round_summary_markdown": "done",
                        "completion_summary_markdown": "done",
                    }
                )
            ],
            stdout_lines=[],
            stderr_lines=[],
            thread_id="thread-1",
            fatal_error=None,
        )


class _FakeMissionLoopEngine:
    def __init__(self, *, runner, reviewer, planner, config, state_store, event_sink=None) -> None:
        self.reviewer = reviewer
        self.config = config

    def run(self) -> types.SimpleNamespace:
        from argus_skill.engineer.reviewer import ReviewerConfig

        review = self.reviewer.evaluate(
            objective=self.config.objective,
            round_index=1,
            session_id=self.config.mission_id or None,
            main_summary="round one",
            main_error=None,
            checks=[],
            config=ReviewerConfig(
                model=self.config.reviewer_model or None,
                reasoning_effort=self.config.reviewer_reasoning_effort,
                extra_args=[],
                full_auto=True,
                skip_git_repo_check=True,
                dangerous_yolo=False,
            ),
            engineer_reasoning_summary="round one",
            prev_review_summary="",
        )
        return types.SimpleNamespace(
            status=review.status,
            rounds=[],
            final_message="round one",
            reason=review.reason,
            last_thread_id="thread-1",
        )


class _FakeVerifier:
    def __init__(self, failing: list[str] | None) -> None:
        self.expected_to_pass = ["tests/test_suite.py::test_acceptance"]
        self.timeout_sec = 1
        self._failing = failing
        self.calls = 0

    async def run_and_get_failing(self) -> list[str] | None:
        self.calls += 1
        return self._failing


def test_benchmark_container_runner_imports() -> None:
    import benchmarks.container_runner  # noqa: F401


def test_lazy_container_runner_import_boundary(monkeypatch) -> None:
    from benchmarks.swebench_pro.runner import _run_mission_engine_in_container

    fake_harbor = _FakeHarborAdapter()
    monkeypatch.setitem(sys.modules, "benchmarks.harbor_adapter", fake_harbor)

    fake_environment = _FakeEnvironment()
    fake_logger = logging.getLogger("test")

    result = asyncio.run(
        _run_mission_engine_in_container(
            instruction="fix it",
            environment=fake_environment,
            engineer_model="gpt-5.4-mini",
            engineer_effort="high",
            reviewer_model="gpt-5.4",
            reviewer_effort="medium",
            skill_text="",
            skill_name=None,
            max_rounds=1,
            round_timeout=1,
            mission_id="mission-1",
            no_reviewer=True,
            logger=fake_logger,
        )
    )

    assert result is not None


def test_verifier_evidence_reaches_reviewer_prompt(monkeypatch) -> None:
    import argus_skill.mission.engine as mission_engine_module
    import argus_skill.runners.container as container_module
    from benchmarks.swebench_pro.runner import (
        InContainerVerifier,
        _run_mission_engine_in_container,
    )

    fake_harbor = _FakeHarborAdapter()
    monkeypatch.setitem(sys.modules, "benchmarks.harbor_adapter", fake_harbor)
    monkeypatch.setattr(container_module, "ContainerReviewerBackend", _CapturingReviewerBackend)
    monkeypatch.setattr(mission_engine_module, "MissionLoopEngine", _FakeMissionLoopEngine)
    _CapturingReviewerBackend.instances.clear()

    fake_environment = _FakeEnvironment()
    fake_logger = logging.getLogger("test")
    fake_verifier = _FakeVerifier(["tests/test_suite.py::test_acceptance"])

    result = asyncio.run(
        _run_mission_engine_in_container(
            instruction="fix it",
            environment=fake_environment,
            engineer_model="gpt-5.4-mini",
            engineer_effort="high",
            reviewer_model="gpt-5.4",
            reviewer_effort="medium",
            skill_text="",
            skill_name=None,
            max_rounds=1,
            round_timeout=1,
            mission_id="mission-1",
            no_reviewer=False,
            logger=fake_logger,
            verifier=cast(InContainerVerifier, fake_verifier),
        )
    )

    assert result is not None
    assert fake_verifier.calls == 1
    assert _CapturingReviewerBackend.instances
    prompt = _CapturingReviewerBackend.instances[0].prompts[0]
    assert "Raw verification evidence:" in prompt
    assert "official verifier (FAIL, ground truth):" in prompt
    assert "tests/test_suite.py::test_acceptance" in prompt

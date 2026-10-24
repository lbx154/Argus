from __future__ import annotations

from pathlib import Path

from argus.core.models import RunnerResult
from argus.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus.reviewer import Reviewer, ReviewerConfig


class _CaptureBackend:
    def __init__(self) -> None:
        self.options = None

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        self.options = options
        return RunnerResult(exit_code=0, agent_messages=["complete"])


def test_engineer_uses_transport_idle_not_semantic_progress_scanning(
    tmp_path: Path,
) -> None:
    backend = _CaptureBackend()
    engineer = SupervisedEngineer(
        engineer_runner=backend,
        reviewer=Reviewer(runner=backend),
        engineer_config=EngineerConfig(model="m"),
        reviewer_config=ReviewerConfig(model="m"),
    )
    config = SupervisedConfig(session_id="mission-1")

    _result, compactions = engineer._run_engineer(
        prompt="work",
        workdir=tmp_path,
        run_label="engineer-r1",
        supervised_config=config,
    )

    assert compactions == 0
    assert backend.options.external_interrupt_reason_provider is None
    assert backend.options.watchdog_hard_idle_seconds is None
    assert backend.options.extension_env == {
        "ARGUS_PLUGIN_PARENT_MISSION_ID": "mission-1"
    }

from __future__ import annotations

from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.engineer.runner import (
    EngineerConfig,
    SupervisedConfig,
    SupervisedEngineer,
)
from argus_skill.reviewer import Reviewer, ReviewerConfig


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
    config = SupervisedConfig(
        effective_progress_warning_seconds=1,
        effective_progress_stalled_seconds=2,
        effective_progress_timeout_seconds=3,
        effective_progress_check_interval_seconds=1,
        round_compaction_limit=1,
        runner_hard_idle_seconds=45,
    )

    _result, compactions = engineer._run_engineer(
        prompt="work",
        workdir=tmp_path,
        run_label="engineer-r1",
        supervised_config=config,
    )

    assert compactions == 0
    assert backend.options.external_interrupt_reason_provider is None
    assert backend.options.watchdog_hard_idle_seconds == 45


def test_semantic_progress_compatibility_knobs_are_inert_by_default() -> None:
    config = SupervisedConfig()

    assert config.effective_progress_warning_seconds == 0
    assert config.effective_progress_stalled_seconds == 0
    assert config.effective_progress_timeout_seconds == 0
    assert config.effective_progress_check_interval_seconds == 0
    assert config.round_compaction_limit == 0
    assert config.runner_hard_idle_seconds == 2700

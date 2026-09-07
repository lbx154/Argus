"""The Planner preserves infrastructure failures even during verdict repair."""
from __future__ import annotations

from argus_skill.core.models import RunnerOptions, RunnerResult
from argus_skill.planner import Planner


def test_planner_repair_does_not_hide_or_replay_execution_host_failure(monkeypatch):
    from argus_skill.planner import planner as module

    diagnostic = (
        "Code Mode is unavailable because failed to spawn code-mode host "
        "/codex/codex-code-mode-host: host executable was not found. "
        "Code mode will fail closed."
    )
    calls = []

    class FakeRunner:
        def run_exec(self, **kwargs):
            calls.append(kwargs)
            return RunnerResult(
                exit_code=0, fatal_error=diagnostic, stop_kind="backend_unavailable",
                agent_messages=["PROJECT_DONE=true\nREASON=Looks complete"],
            )

    monkeypatch.setattr(module, "_PLANNER_REPAIR_ATTEMPTS", 3)
    verdict = Planner(FakeRunner())._repair_no_task_verdict(
        previous_raw_text="invalid verdict", previous_error="invalid output",
        options=RunnerOptions(), planning_cycle=1, resume_thread_id="thread-1",
    )
    assert verdict.error == diagnostic
    assert not verdict.project_done
    assert not verdict.new_tasks
    assert len(calls) == 1

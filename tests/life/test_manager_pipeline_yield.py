from __future__ import annotations

from argus_skill.life.memory import LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _UnusedRunner:
    def execute(self, **_kwargs):
        raise AssertionError("runner must not execute while Manager config is pending")


class _Sink:
    def handle_event(self, _event):
        return None

    def handle_stream_line(self, _stream, _line):
        return None

    def close(self):
        return None


def test_manager_pipeline_yield_stops_before_tick(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_UnusedRunner(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="old objective",
            manager_pipeline_yield_provider=lambda: True,
        ),
    )

    result = supervisor.run()

    assert result["stopped_by"] == "manager_config_pending"
    assert result["missions_run"] == 0
    assert result["planning_cycles"] == 0

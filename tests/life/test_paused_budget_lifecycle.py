from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from argus.core.autonomy import RETRY_WINDOW_SPENT_REASON
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.runtime_failure_circuit import PROVIDER_OUTAGE, active_runtime_failure_circuit
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.webapi.routes.daemon import _resume_parks_after_start


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def handle_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)


@dataclass
class _Outcome:
    success: bool
    status: str
    stop_kind: str | None = None
    recoverable: bool = False
    stop_reason: str = ""
    rounds: int = 1
    park_reason: str = ""


class _CompleteRunner:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **_kwargs: Any) -> _Outcome:
        self.calls += 1
        return _Outcome(success=True, status="done")


class _TurnedAwayOnceRunner(_CompleteRunner):
    """The model service turns the first task away for good; then it is back."""

    def __init__(self, stop_reason: str) -> None:
        super().__init__()
        self.stop_reason = stop_reason

    def execute(self, **kwargs: Any) -> _Outcome:
        if self.calls:
            return super().execute(**kwargs)
        self.calls += 1
        return _Outcome(
            success=False,
            status="parked",
            park_reason="provider_fence",
            stop_kind="provider_fence",
            stop_reason=self.stop_reason,
        )


def test_a_task_out_of_automatic_retries_holds_new_tasks_until_run(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    held = memory.backlog.add(BacklogItem.new(
        title="outage held",
        objective="wait for recovery",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    ready = memory.backlog.add(BacklogItem.new(
        title="next",
        objective="next work",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    runner = _TurnedAwayOnceRunner(f"{RETRY_WINDOW_SPENT_REASON} (HTTP 503).")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )

    assert supervisor.run()["stopped_by"] == "parked"
    # The model service would turn the next task away too: the circuit the
    # fenced park opened holds it without a call.
    for _ in range(3):
        assert supervisor.run()["stopped_by"] == "runtime_block"
    assert runner.calls == 1
    assert active_runtime_failure_circuit(tmp_path / "life")["fingerprint"] == PROVIDER_OUTAGE

    # The operator's explicit Run releases the fence and the held work.
    _resume_parks_after_start(tmp_path / "life", {"rc": 0})
    supervisor.run()

    stored = {item.id: item for item in memory.backlog.all()}
    assert stored[held.id].status == "done"
    assert stored[ready.id].status == "done"
    assert runner.calls == 3
    assert active_runtime_failure_circuit(tmp_path / "life") is None


def test_a_canary_the_model_service_answers_ends_the_outage_whatever_its_review(
    tmp_path,
) -> None:
    import json
    import time

    from argus.life.runtime_failure_circuit import CIRCUIT_FILENAME

    life = tmp_path / "life"
    memory = LifeMemory.open(life)
    held = memory.backlog.add(BacklogItem.new(
        title="outage held",
        objective="wait for recovery",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    canary = memory.backlog.add(BacklogItem.new(
        title="next",
        objective="next work",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    outcomes = [
        _Outcome(
            success=False, status="parked", park_reason="provider_fence",
            stop_kind="provider_fence", stop_reason=f"{RETRY_WINDOW_SPENT_REASON} (HTTP 503).",
        ),
        # The Reviewer turned the work down: the service answered all the same.
        _Outcome(success=False, status="failed", stop_reason="review: not done"),
        _Outcome(success=True, status="done"),
    ]
    runner = SimpleNamespace(execute=lambda **_kwargs: outcomes.pop(0))
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )
    assert supervisor.run()["stopped_by"] == "parked"
    assert supervisor.run()["stopped_by"] == "runtime_block"

    state = json.loads((life / CIRCUIT_FILENAME).read_text())
    state["canary_due_at"] = time.time() - 1
    (life / CIRCUIT_FILENAME).write_text(json.dumps(state))
    supervisor.run()

    stored = {item.id: item for item in memory.backlog.all()}
    assert stored[canary.id].status == "failed"
    assert (stored[held.id].status, stored[held.id].attempt) == ("done", 2)
    assert outcomes == []
    assert active_runtime_failure_circuit(life) is None


def test_a_spent_trial_allowance_parks_without_calling_the_model_service_down(tmp_path) -> None:
    """The hosted meter's 402 is the allowance, not an outage: no outage
    circuit opens, the record names the allowance, and the receipt says so
    plainly without sending the person to an administrator."""
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(BacklogItem.new(
        title="CartPole 三算法样本效率与稳定性研究",
        objective="比较三种算法",
        manager_decision={"routed": True, "vertical": "software"},
    ))
    stop = (
        "审阅者的会话在得出结论前就结束了，这一轮没有评审意见。 技术记录：exit=1; "
        "fatal_error=query: HTTP 402: 402 试用额度不足，任务已暂停。请联系试用版管理员。"
    )
    sink = _Sink()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_TurnedAwayOnceRunner(stop),
        sink=sink,
        config=LifeSupervisorConfig(poll_interval_seconds=0.0),
    )

    assert supervisor.run()["stopped_by"] == "parked"

    assert active_runtime_failure_circuit(tmp_path / "life") is None
    completed = next(event for event in sink.events if event.get("type") == "life.mission.completed")
    assert completed["park_reason"] == "provider_fence"
    assert completed["failure_kind"] == "allowance_exhausted"
    from argus.core.transcript import read_turns

    receipt = next(turn for turn in read_turns(tmp_path / "life") if turn.get("mission_result"))
    # No operator turn here, so the receipt is in English.
    assert receipt["text"].startswith("The trial allowance is used up, so the task is paused; all files are kept.")
    assert "click Resume" in receipt["text"]
    for absent in ("administrator", "model service", "Technical record", "402"):
        assert absent not in receipt["text"]

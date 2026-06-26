"""Integration test for the Manager stage-decision hook in the mission runner.

After each mission round, ``_SkillLoopRunner._decide_stage_transition`` hands the
final reviewer verdict to the Manager (the sole post-bootstrap writer of the
pipeline stage), which judges advance / hold / rollback and writes
``PIPELINE_STATE.json``. These tests drive that hook directly with a
``__new__``-built runner (no full ``__init__``) + a stub manager backend.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.apps._runtime import _SkillLoopRunner
from argus_skill.core.models import ReviewDecision


class _Result:
    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0


class _StubRunner:
    def __init__(self, verdict: dict) -> None:
        self._text = json.dumps(verdict)

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        return _Result(self._text)


class _BoomRunner:
    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        raise RuntimeError("backend down")


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)


class _Round:
    def __init__(self, review) -> None:  # noqa: ANN001
        self.review = review


def _runner_with(backend) -> _SkillLoopRunner:  # noqa: ANN001
    r = _SkillLoopRunner.__new__(_SkillLoopRunner)
    r.manager_backend = backend
    r._backend = backend
    return r


def _review(status: str = "done") -> ReviewDecision:
    return ReviewDecision(
        status=status,  # type: ignore[arg-type]
        reason="checklist satisfied",
        next_action="advance",
        planner_report={"forward_progress": True, "headline": "done"},
    )


def _project(tmp_path: Path, *, current: str) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": current}), encoding="utf-8"
    )
    return tmp_path


def _stage(root: Path) -> str:
    return json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )["current_stage"]


def test_hook_advances_stage_and_emits_event(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done", "confidence": 0.9}
    ))
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    assert decision["action"] == "advance"
    assert _stage(root) == "plan"
    assert any(e.get("type") == "life.manager.stage_decision" for e in sink.events)


def test_hook_no_review_holds_and_does_not_write(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "x", "confidence": 1.0}
    ))
    sink = _Sink()

    # empty rounds_list → no final review → Manager HOLDs, writes nothing.
    decision = runner._decide_stage_transition(rounds_list=[], workdir=root, sink=sink)

    assert decision["action"] == "hold"
    assert decision["source"] == "no_review_hold"
    assert _stage(root) == "research"


def test_hook_backend_error_holds_and_never_raises(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_BoomRunner())
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    # Manager swallows the LLM error → fail-safe HOLD; stage untouched.
    assert decision["action"] == "hold"
    assert _stage(root) == "research"

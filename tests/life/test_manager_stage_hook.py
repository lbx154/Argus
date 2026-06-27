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
    def __init__(self, verdict: dict | str) -> None:
        self._text = verdict if isinstance(verdict, str) else json.dumps(verdict)

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        return _Result(self._text)


class _BoomRunner:
    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        raise RuntimeError("backend down")


class _EmptyThenRunner:
    """Returns ``empties`` empty turns (the gpt-5.5/fnyweg flake) then the real
    verdict — exercises decide_stage_transition's empty-output retry."""

    def __init__(self, verdict: dict, *, empties: int = 1) -> None:
        self._text = json.dumps(verdict)
        self._empties_left = empties
        self.calls = 0

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        self.calls += 1
        if self._empties_left > 0:
            self._empties_left -= 1
            return _Result("")
        return _Result(self._text)


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
        {"action": "advance", "target_stage": "plan", "reason": "done"}
    ))
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    assert decision["action"] == "advance"
    assert decision["diagnostic"] == "valid_target"
    assert _stage(root) == "plan"
    assert any(e.get("type") == "life.manager.stage_decision" for e in sink.events)
    # The retired self-reported confidence must not leak into the event payload.
    assert "confidence" not in decision


def test_hook_retries_on_empty_output_then_advances(tmp_path: Path, monkeypatch) -> None:
    # An empty manager turn (gpt-5.5/fnyweg flake) must NOT silently default-HOLD
    # and wedge the stage — it retries and picks up the real advance verdict.
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _EmptyThenRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}, empties=1
    )
    runner = _runner_with(backend)
    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=_Sink()
    )
    assert backend.calls == 2  # one empty, then retried into the real verdict
    assert decision["action"] == "advance"
    assert _stage(root) == "plan"


def test_hook_persistent_empty_falls_back_to_safe_hold(tmp_path: Path, monkeypatch) -> None:
    # If every turn is empty, retries exhaust to a safe hold — no deadlock,
    # stage unchanged, nothing wrongly advanced.
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _EmptyThenRunner({}, empties=99)
    runner = _runner_with(backend)
    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=_Sink()
    )
    assert decision["action"] == "hold"
    assert _stage(root) == "research"


def test_hook_no_review_holds_and_does_not_write(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "x"}
    ))
    sink = _Sink()

    # empty rounds_list → no final review → Manager HOLDs, writes nothing.
    decision = runner._decide_stage_transition(rounds_list=[], workdir=root, sink=sink)

    assert decision["action"] == "hold"
    assert decision["source"] == "no_review_hold"
    assert decision["diagnostic"] == ""
    assert _stage(root) == "research"


def test_hook_parse_hold_event_carries_diagnostic(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    runner = _runner_with(_StubRunner("not json at all"))
    sink = _Sink()

    decision = runner._decide_stage_transition(
        rounds_list=[_Round(_review())], workdir=root, sink=sink
    )

    assert decision["action"] == "hold"
    assert decision["source"] == "manager_llm"
    assert decision["diagnostic"] == "no_json_object"
    assert _stage(root) == "research"
    event = next(e for e in sink.events if e.get("type") == "life.manager.stage_decision")
    assert event["diagnostic"] == "no_json_object"


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

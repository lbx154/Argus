"""Unit tests for the Manager's stage-transition authority.

The Manager is the SOLE post-bootstrap writer of ``current_stage``: it makes its
own LLM judgment from the reviewer's feedback and the current-stage checklist,
then advances / holds / rolls back. These tests drive ``decide_stage_transition``
with a stub runner returning canned JSON verdicts, plus the strict parser.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.manager import Manager, StageTransition
from argus_skill.manager.stage_decider import (
    fallback_empty_stage_decision,
    parse_stage_decision,
)


class _Result:
    """Minimal RunnerResult shape (last_agent_message + exit_code)."""

    def __init__(self, msg: str) -> None:
        self.last_agent_message = msg
        self.exit_code = 0


class _StubRunner:
    """A runner whose run_exec returns a fixed JSON verdict."""

    def __init__(self, verdict: dict | str) -> None:
        self._text = verdict if isinstance(verdict, str) else json.dumps(verdict)
        self.calls = 0

    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        self.calls += 1
        return _Result(self._text)


class _BoomRunner:
    def run_exec(self, *, prompt: str, options, run_label: str):  # noqa: ANN001
        raise RuntimeError("backend down")


def _review(
    status: str = "done",
    *,
    checklist: list[dict] | None = None,
    forward_progress: bool | None = True,
):
    """A minimal ReviewDecision-shaped object the decider reads."""
    from argus_skill.core.models import ReviewDecision

    report = {"headline": "done", "blocker": ""}
    if forward_progress is not None:
        report["forward_progress"] = forward_progress
    return ReviewDecision(
        status=status,  # type: ignore[arg-type]
        reason="checklist satisfied",
        next_action="advance",
        checklist=(
            checklist
            if checklist is not None
            else [
                {
                    "item": "research.first_score_plan",
                    "satisfied": True,
                    "evidence": "X",
                }
            ]
        ),
        planner_report=report,
    )


def _project(tmp_path: Path, *, current: str) -> Path:
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "PIPELINE_STATE.json").write_text(
        json.dumps({"current_stage": current}), encoding="utf-8"
    )
    return tmp_path


def _read_stage(root: Path) -> str:
    return json.loads(
        (root / "research" / "PIPELINE_STATE.json").read_text(encoding="utf-8")
    )["current_stage"]


# --- decide_stage_transition: writes -------------------------------------


def test_decide_advance_writes_state(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": "plan", "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert isinstance(st, StageTransition)
    assert st.action == "advance"
    assert _read_stage(root) == "plan"
    # The self-reported confidence field is gone from the verdict dataclass.
    import dataclasses
    assert "confidence" not in [f.name for f in dataclasses.fields(StageTransition)]


@pytest.mark.parametrize("target", ["`plan`", "plan stage"])
def test_decide_advance_accepts_harmless_target_formatting(
    tmp_path: Path, target: str
) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": target, "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "advance"
    assert st.target_stage == "plan"
    assert st.diagnostic == "normalized_target_stage"
    assert _read_stage(root) == "plan"


def test_decide_advance_infers_unique_next_stage_when_target_omitted(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "reason": "done"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "advance"
    assert st.target_stage == "plan"
    assert st.diagnostic == "inferred_next_stage"
    assert _read_stage(root) == "plan"


def test_decide_hold_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "hold", "target_stage": "research", "reason": "more work"}
    ))
    st = mgr.decide_stage_transition(review=_review(status="continue"), project_root=root)
    assert st.action == "hold"
    assert st.diagnostic == "intentional_hold"
    assert _read_stage(root) == "research"  # untouched


def test_decide_rollback_writes_state(tmp_path: Path) -> None:
    root = _project(tmp_path, current="run")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "rollback", "target_stage": "benchmark", "reason": "stub evaluator"}
    ))
    st = mgr.decide_stage_transition(review=_review(status="continue"), project_root=root)
    assert st.action == "rollback"
    assert _read_stage(root) == "benchmark"


# --- decide_stage_transition: fail-safe HOLDs ----------------------------


def test_decide_no_runner_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=None).decide_stage_transition(
        review=_review(), project_root=root
    )
    assert st.action == "hold"
    assert st.source == "no_runner_hold"
    assert _read_stage(root) == "research"


def test_decide_review_none_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=_StubRunner({"action": "advance", "target_stage": "plan", "reason": "x"})).decide_stage_transition(
        review=None, project_root=root
    )
    assert st.action == "hold"
    assert st.source == "no_review_hold"
    assert _read_stage(root) == "research"


def test_decide_llm_error_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=_BoomRunner()).decide_stage_transition(
        review=_review(), project_root=root
    )
    assert st.action == "hold"
    assert st.source == "failsafe_hold"
    assert _read_stage(root) == "research"


def test_decide_illegal_skip_target_holds(tmp_path: Path) -> None:
    # advance to a non-immediate stage → parser fails closed to HOLD, no write.
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner(
        {"action": "advance", "target_stage": "run", "reason": "skip"}
    ))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "hold"
    assert st.diagnostic == "illegal_advance_target"
    assert _read_stage(root) == "research"


def test_decide_garbage_output_holds(tmp_path: Path) -> None:
    root = _project(tmp_path, current="research")
    mgr = Manager(project_root=root, runner=_StubRunner("not json at all"))
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert st.action == "hold"
    assert st.diagnostic == "no_json_object"
    assert _read_stage(root) == "research"


def test_decide_persistent_empty_done_satisfied_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _StubRunner("")
    mgr = Manager(project_root=root, runner=backend)
    st = mgr.decide_stage_transition(review=_review(), project_root=root)
    assert backend.calls == 3
    assert st.action == "advance"
    assert st.target_stage == "plan"
    assert st.diagnostic == "empty_output_certified_advance"
    assert _read_stage(root) == "plan"


def test_decide_persistent_empty_unsatisfied_checklist_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    backend = _StubRunner("")
    review = _review(
        checklist=[
            {"item": "research.first_score_plan", "satisfied": False, "evidence": ""}
        ]
    )
    st = Manager(project_root=root, runner=backend).decide_stage_transition(
        review=review, project_root=root
    )
    assert st.action == "hold"
    assert st.diagnostic == "empty_output_unsatisfied_checklist"
    assert _read_stage(root) == "research"


@pytest.mark.parametrize("status", ["continue", "blocked"])
def test_decide_persistent_empty_reviewer_not_done_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr("argus_skill.manager._core.time.sleep", lambda *_a, **_k: None)
    root = _project(tmp_path, current="research")
    st = Manager(project_root=root, runner=_StubRunner("")).decide_stage_transition(
        review=_review(status=status), project_root=root
    )
    assert st.action == "hold"
    assert st.diagnostic == "empty_output_review_not_done"
    assert _read_stage(root) == "research"


# --- parse_stage_decision (pure, fail-closed) ----------------------------

ORDER = ("research", "plan", "benchmark", "run", "analysis", "draft", "review", "submission")


def test_parse_advance_immediate_ok() -> None:
    d = parse_stage_decision(
        '{"action":"advance","target_stage":"plan","reason":"ok"}',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance" and d.target_stage == "plan"
    assert d.diagnostic == "valid_target"
    import dataclasses
    assert "confidence" not in [f.name for f in dataclasses.fields(d)]


@pytest.mark.parametrize("target", ["`plan`", "plan stage"])
def test_parse_advance_harmless_target_formatting_ok(target: str) -> None:
    d = parse_stage_decision(
        json.dumps({"action": "advance", "target_stage": target, "reason": "ok"}),
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance"
    assert d.target_stage == "plan"
    assert d.diagnostic == "normalized_target_stage"


def test_parse_advance_missing_target_infers_unique_next_stage() -> None:
    d = parse_stage_decision(
        '{"action":"advance","reason":"ok"}',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance"
    assert d.target_stage == "plan"
    assert d.diagnostic == "inferred_next_stage"


def test_parse_advance_skip_holds() -> None:
    d = parse_stage_decision(
        '{"action":"advance","target_stage":"benchmark","reason":"skip"}',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "illegal_advance_target"


def test_parse_rollback_must_be_earlier() -> None:
    ok = parse_stage_decision(
        '{"action":"rollback","target_stage":"plan"}',
        current_stage="run", stage_order=ORDER,
    )
    assert ok.action == "rollback" and ok.target_stage == "plan"
    bad = parse_stage_decision(
        '{"action":"rollback","target_stage":"draft"}',  # later than run
        current_stage="run", stage_order=ORDER,
    )
    assert bad.action == "hold"
    assert bad.diagnostic == "illegal_rollback_target"


def test_parse_rollback_missing_target_holds() -> None:
    d = parse_stage_decision(
        '{"action":"rollback","reason":"unclear"}',
        current_stage="run", stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "missing_rollback_target"


def test_parse_json_in_code_fence() -> None:
    d = parse_stage_decision(
        '```json\n{"action":"advance","target_stage":"plan"}\n```',
        current_stage="research", stage_order=ORDER,
    )
    assert d.action == "advance"


def test_parse_unknown_action_holds() -> None:
    d = parse_stage_decision('{"action":"yolo"}', current_stage="research", stage_order=ORDER)
    assert d.action == "hold"
    assert d.diagnostic == "unknown_action"


def test_fallback_empty_stage_decision_missing_evidence_holds() -> None:
    d = fallback_empty_stage_decision(
        _review(checklist=[{"item": "x", "satisfied": True, "evidence": " "}]),
        current_stage="research",
        stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "empty_output_missing_checklist_evidence"


def test_fallback_empty_stage_decision_unknown_current_holds() -> None:
    d = fallback_empty_stage_decision(
        _review(),
        current_stage="unknown",
        stage_order=ORDER,
    )
    assert d.action == "hold"
    assert d.diagnostic == "empty_output_unknown_current_stage"

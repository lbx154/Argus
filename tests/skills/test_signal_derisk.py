"""Tests for the default measured-signal evidence validator.

The validator is fail-closed against missing, degenerate, over-budget,
wrong-direction, or fabricated evidence. The Reviewer decides whether that
diagnostic satisfies the active Planner-authored checklist.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.signal_derisk import (
    load_signal_derisk,
    main,
    validate_for_gate,
)


def _good(**over) -> dict:
    """A genuinely-passing de-risk: a defense drives ASR 0.62 -> 0.31 (lower is
    better), in budget, with a real log."""
    base = dict(
        schema_version=1,
        idea_id="tts-safety-defense",
        metric_name="attack_success_rate",
        success_direction="lower",
        model_id="gpt-5.5",
        model_source="vault:coproxy",
        data_source="advbench_40.jsonl",
        n_examples=40,
        baseline_metric=0.62,
        proposed_metric=0.31,
        delta=-0.31,
        min_meaningful_delta=0.1,
        signal_moved=True,
        cost_usd=0.18,
        duration_s=220.0,
        log_path="research/SIGNAL_DERISK_LOG.txt",
        commands=[".venv/bin/python experiments/derisk/run.py --n 40"],
        verdict="pass",
        pivoted=False,
        smoke_only=False,
        notes="ok",
    )
    base.update(over)
    return base


def _write(tmp_path: Path, data: dict, *, log: str | None = "real run output\n") -> Path:
    research = tmp_path / "research"
    research.mkdir(parents=True, exist_ok=True)
    if log is not None:
        (research / "SIGNAL_DERISK_LOG.txt").write_text(log, encoding="utf-8")
    p = research / "SIGNAL_DERISK.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _gate(tmp_path: Path, data: dict, **kw) -> tuple[bool, str]:
    p = _write(tmp_path, data, **kw)
    return validate_for_gate(tmp_path, p)


# --- the happy path ---------------------------------------------------------


def test_good_derisk_passes(tmp_path):
    reject, concern = _gate(tmp_path, _good())
    assert reject is False
    assert concern == ""


def test_good_higher_direction_passes(tmp_path):
    # pass@1 should go UP: baseline 0.40 -> proposed 0.55.
    reject, _ = _gate(
        tmp_path,
        _good(
            metric_name="pass_at_1",
            success_direction="higher",
            baseline_metric=0.40,
            proposed_metric=0.55,
            delta=0.15,
        ),
    )
    assert reject is False


# --- missing / malformed ----------------------------------------------------


def test_missing_file_rejects(tmp_path):
    reject, concern = validate_for_gate(tmp_path, tmp_path / "research" / "nope.json")
    assert reject is True
    assert "idea-feasibility-derisk" in concern


def test_incomplete_rejects(tmp_path):
    loaded, issues = load_signal_derisk(_write(tmp_path, {"idea_id": "x"}))
    assert loaded is None
    assert any(i.code == "derisk_incomplete" for i in issues)


def test_commands_not_a_list_rejects(tmp_path):
    loaded, issues = load_signal_derisk(_write(tmp_path, _good(commands="oops")))
    assert loaded is None
    assert any(i.code == "derisk_malformed" for i in issues)


# --- degeneracy (dead idea) -------------------------------------------------


def test_baseline_equals_proposed_rejects(tmp_path):
    reject, concern = _gate(tmp_path, _good(proposed_metric=0.62, delta=0.0))
    assert reject is True
    assert "baseline_equals_proposed" in concern


def test_signal_unmoved_rejects(tmp_path):
    reject, concern = _gate(tmp_path, _good(proposed_metric=0.60, delta=-0.02))
    assert reject is True
    assert "signal_unmoved" in concern


def test_wrong_direction_rejects(tmp_path):
    # success_direction=lower but the metric went UP -> the idea hurt it.
    reject, concern = _gate(tmp_path, _good(proposed_metric=0.80, delta=0.18))
    assert reject is True
    assert "wrong_direction" in concern


# --- tamper / provenance ----------------------------------------------------


def test_delta_inconsistent_rejects(tmp_path):
    # delta hand-edited away from proposed - baseline.
    reject, concern = _gate(tmp_path, _good(delta=-0.99))
    assert reject is True
    assert "delta_inconsistent" in concern


def test_log_missing_rejects(tmp_path):
    reject, concern = _gate(tmp_path, _good(), log=None)
    assert reject is True
    assert "log_missing" in concern


def test_log_empty_rejects(tmp_path):
    reject, concern = _gate(tmp_path, _good(), log="")
    assert reject is True
    assert "log_empty" in concern


def test_signal_moved_overclaim_rejects(tmp_path):
    # claims moved but the delta is below the bar (and we keep it consistent).
    reject, concern = _gate(tmp_path, _good(proposed_metric=0.60, delta=-0.02, signal_moved=True))
    # signal_unmoved fires first (both are blocking); either way it's rejected.
    assert reject is True


# --- pivot / verdict rule ---------------------------------------------------


def test_verdict_fail_blocks_for_pivot(tmp_path):
    # A clean, in-budget run that simply showed no movement: verdict=fail.
    reject, concern = _gate(
        tmp_path,
        _good(
            verdict="fail",
            signal_moved=False,
            proposed_metric=0.61,
            delta=-0.01,
            notes="signal flat",
        ),
    )
    assert reject is True
    # signal_unmoved or verdict_fail — both diagnose evidence that needs a pivot.
    assert "PIVOT" in concern or "pivot" in concern or "signal_unmoved" in concern


def test_pass_while_pivoted_rejects(tmp_path):
    reject, concern = _gate(tmp_path, _good(pivoted=True))
    assert reject is True
    assert "pass_while_pivoted" in concern or "pivoted" in concern


# --- smoke_only exemption ---------------------------------------------------


def test_smoke_only_waives_movement(tmp_path):
    # smoke_only run: no metric movement required, but budget + log still hold.
    reject, _ = _gate(
        tmp_path,
        _good(
            smoke_only=True,
            proposed_metric=0.62,
            delta=0.0,
            signal_moved=False,
            notes="wiring-only smoke",
        ),
    )
    assert reject is False


def test_smoke_only_false_string_fails_closed(tmp_path):
    # "false" must NOT be read as truthy and silently exempt a dead idea.
    data = _good(proposed_metric=0.62, delta=0.0)
    data["smoke_only"] = "false"
    reject, concern = _gate(tmp_path, data)
    assert reject is True
    assert "baseline_equals_proposed" in concern


# --- CLI --------------------------------------------------------------------


def test_cli_validate_good(tmp_path, capsys):
    _write(tmp_path, _good())
    rc = main(
        ["validate", "--project-root", str(tmp_path), "--derisk", "research/SIGNAL_DERISK.json"]
    )
    assert rc == 0


def test_cli_validate_degenerate(tmp_path, capsys):
    _write(tmp_path, _good(proposed_metric=0.62, delta=0.0))
    rc = main(
        ["validate", "--project-root", str(tmp_path), "--derisk", "research/SIGNAL_DERISK.json"]
    )
    assert rc == 1


# --- the stage keeps quality judgment with the Reviewer ---------------------


def test_research_checklist_does_not_dispatch_task_specific_derisk():
    from argus_skill.verticals.research import stages

    rendered = " ".join(item.statement for item in stages.STAGE_CHECKLISTS["research"])
    assert "mechanical routing decision" in rendered
    assert "theorem_derisk" not in rendered


def test_research_reviewer_checklist_has_selected_derisk_dimension():
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(
        item for item in STAGE_CHECKLISTS["research"] if item.id == "research.signal_derisk"
    )
    assert "Planner authors the evidence contract" in item.statement
    assert "default scalar-comparison shape" in item.statement
    assert "research/SIGNAL_DERISK.json" in item.evidence_hint
    assert "THEOREM_DERISK" not in item.evidence_hint


def test_research_checklist_item_present():
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    ids = [it.id for it in STAGE_CHECKLISTS["research"]]
    assert "research.signal_derisk" in ids

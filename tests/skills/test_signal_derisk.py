"""Legacy signal artifacts remain auditable but no longer gate idea selection."""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.signal_derisk import (
    load_signal_derisk,
    validate_signal_derisk,
)


def _good(**over) -> dict:
    payload = {
        "schema_version": 1,
        "idea_id": "tts-safety-defense",
        "metric_name": "attack_success_rate",
        "success_direction": "lower",
        "model_id": "gpt-5.5",
        "model_source": "vault:coproxy",
        "data_source": "advbench_40.jsonl",
        "n_examples": 40,
        "baseline_metric": 0.62,
        "proposed_metric": 0.31,
        "delta": -0.31,
        "min_meaningful_delta": 0.1,
        "signal_moved": True,
        "cost_usd": 0.18,
        "duration_s": 220.0,
        "log_path": "research/SIGNAL_DERISK_LOG.txt",
        "commands": [".venv/bin/python experiments/derisk/run.py --n 40"],
        "verdict": "pass",
        "pivoted": False,
        "smoke_only": False,
        "notes": "historical artifact",
    }
    payload.update(over)
    return payload


def _write(
    root: Path,
    payload: dict,
    *,
    log: str | None = "historical run output\n",
) -> Path:
    research = root / "research"
    research.mkdir(parents=True, exist_ok=True)
    if log is not None:
        (research / "SIGNAL_DERISK_LOG.txt").write_text(log, encoding="utf-8")
    path = research / "SIGNAL_DERISK.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _issues(root: Path, payload: dict, *, log: str | None = "historical run output\n"):
    artifact, load_issues = load_signal_derisk(_write(root, payload, log=log))
    assert artifact is not None, load_issues
    return validate_signal_derisk(artifact, project_root=root)


def test_historical_artifact_remains_auditable(tmp_path: Path) -> None:
    assert _issues(tmp_path, _good()) == []


def test_higher_direction_remains_auditable(tmp_path: Path) -> None:
    assert _issues(
        tmp_path,
        _good(
            metric_name="pass_at_1",
            success_direction="higher",
            baseline_metric=0.40,
            proposed_metric=0.55,
            delta=0.15,
        ),
    ) == []


def test_missing_and_incomplete_artifacts_are_reported(tmp_path: Path) -> None:
    missing, issues = load_signal_derisk(tmp_path / "research" / "missing.json")
    assert missing is None
    assert issues

    incomplete, issues = load_signal_derisk(_write(tmp_path, {"idea_id": "x"}))
    assert incomplete is None
    assert any(issue.code == "derisk_incomplete" for issue in issues)


def test_malformed_commands_are_reported(tmp_path: Path) -> None:
    artifact, issues = load_signal_derisk(_write(tmp_path, _good(commands="oops")))
    assert artifact is None
    assert any(issue.code == "derisk_malformed" for issue in issues)


def test_historical_numeric_and_provenance_defects_remain_visible(
    tmp_path: Path,
) -> None:
    cases = (
        (_good(proposed_metric=0.62, delta=0.0), "baseline_equals_proposed", "log"),
        (_good(proposed_metric=0.80, delta=0.18), "wrong_direction", "log"),
        (_good(delta=-0.99), "delta_inconsistent", "log"),
        (_good(), "log_missing", None),
        (_good(), "log_empty", ""),
    )
    for payload, expected, log in cases:
        issues = _issues(
            tmp_path / expected,
            payload,
            log=("historical run output\n" if log == "log" else log),
        )
        assert any(issue.code == expected for issue in issues)


def test_research_checklist_is_source_only() -> None:
    from argus_skill.verticals.research.stages import STAGE_CHECKLISTS

    item = next(
        entry
        for entry in STAGE_CHECKLISTS["research"]
        if entry.id == "research.signal_derisk"
    )
    statement = " ".join(item.statement.split())
    assert "contains no candidate execution or experimental outcomes" in statement
    assert "probe experiments belong to neither route ranking nor selection" in statement
    assert "SIGNAL_DERISK" not in item.evidence_hint

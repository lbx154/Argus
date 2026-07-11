from __future__ import annotations

import json
from pathlib import Path

from argus_skill.apps.cli._core import main
from argus_skill.core.mission_view import load_mission_view


def test_report_metric_writes_structured_event_and_projection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    session = home / "projects" / "s-report"
    workspace = tmp_path / "workspace"
    evidence = workspace / "experiments" / "run-v7" / "result.json"
    session.mkdir(parents=True)
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"sol": 61.8}\n', encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.setenv("ARGUS_SKILL_SESSION_ID", "s-report")
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(workspace))

    rc = main([
        "report",
        "metric",
        "--name", "sol_percent",
        "--baseline", "49.4",
        "--value", "61.8",
        "--unit", "%",
        "--direction", "maximize",
        "--evidence", "experiments/run-v7/result.json",
        "--experiment-id", "exp-v7",
        "--round", "7",
        "--primary",
    ])

    assert rc == 0
    output = json.loads(capsys.readouterr().out)
    assert output["type"] == "research.metric.reported"
    assert output["evidence"] == "experiments/run-v7/result.json"
    rows = [json.loads(line) for line in (session / "events.jsonl").read_text().splitlines()]
    assert rows[-1]["metric_id"] == output["metric_id"]
    assert load_mission_view(session)["primary_metric"]["value"] == 61.8


def test_report_rejects_evidence_outside_workspace(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    session = home / "projects" / "s-report"
    workspace = tmp_path / "workspace"
    session.mkdir(parents=True)
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(home))
    monkeypatch.setenv("ARGUS_SKILL_SESSION_ID", "s-report")
    monkeypatch.setenv("ARGUS_SKILL_PROJECT_ROOT", str(workspace))

    rc = main([
        "report", "metric",
        "--name", "score",
        "--value", "1",
        "--evidence", str(outside),
    ])

    assert rc == 2
    assert "evidence must stay inside project workspace" in capsys.readouterr().err
    assert not (session / "events.jsonl").exists()

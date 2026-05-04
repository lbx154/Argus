"""Tests for ``argus_skill.apps.mission_app``."""
from __future__ import annotations

import json
from pathlib import Path

from argus_skill.apps.cli import build_parser
from argus_skill.apps.mission_app import (
    cmd_mission_start,
    cmd_mission_status,
    cmd_mission_stop,
)


def _ns(parser, argv):
    return parser.parse_args(argv)


# ---------------------------------------------------------------------
# argparse plumbing
# ---------------------------------------------------------------------


def test_argparse_mission_start_parses_all_flags(tmp_path):
    parser = build_parser()
    ns = parser.parse_args(
        [
            "mission",
            "start",
            "build a tiny CLI",
            "--state-dir",
            str(tmp_path),
            "--workdir",
            "/tmp/wd",
            "--check",
            "pytest tests/",
            "--check",
            "ruff check .",
            "--max-rounds",
            "30",
            "--plan-mode",
            "auto",
            "--main-model",
            "gpt-5.4-mini",
            "--reviewer-model",
            "gpt-5.4-mini",
            "--plan-model",
            "gpt-5.4",
        ]
    )
    assert ns.cmd == "mission"
    assert ns.mission_cmd == "start"
    assert ns.objective == "build a tiny CLI"
    assert ns.state_dir == str(tmp_path)
    assert ns.workdir == "/tmp/wd"
    assert ns.check == ["pytest tests/", "ruff check ."]
    assert ns.max_rounds == 30
    assert ns.plan_mode == "auto"


def test_argparse_mission_start_rejects_invalid_plan_mode(tmp_path):
    parser = build_parser()
    import pytest

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "mission",
                "start",
                "obj",
                "--state-dir",
                str(tmp_path),
                "--plan-mode",
                "yolo",
            ]
        )


def test_argparse_mission_status_parses(tmp_path):
    parser = build_parser()
    ns = parser.parse_args(["mission", "status", "--state-dir", str(tmp_path)])
    assert ns.mission_cmd == "status"


def test_argparse_mission_stop_parses(tmp_path):
    parser = build_parser()
    ns = parser.parse_args(["mission", "stop", "--state-dir", str(tmp_path)])
    assert ns.mission_cmd == "stop"


# ---------------------------------------------------------------------
# mission start
# ---------------------------------------------------------------------


def test_mission_start_writes_mission_json_and_active_pointer(tmp_path):
    parser = build_parser()
    ns = parser.parse_args(
        [
            "mission",
            "start",
            "set up a venv",
            "--state-dir",
            str(tmp_path),
            "--check",
            "pytest -q",
            "--max-rounds",
            "25",
            "--plan-mode",
            "auto",
        ]
    )
    rc = cmd_mission_start(ns)
    assert rc == 0

    missions_dir = tmp_path / "missions"
    assert missions_dir.is_dir()
    active = json.loads((missions_dir / "active.json").read_text())
    mid = active["mission_id"]
    assert mid.startswith("mission_")

    mfile = missions_dir / mid / "mission.json"
    assert mfile.is_file()
    payload = json.loads(mfile.read_text())
    assert payload["objective"] == "set up a venv"
    assert payload["check_commands"] == ["pytest -q"]
    assert payload["max_rounds"] == 25
    assert payload["plan_mode"] == "auto"
    # loop_state subdir is pre-created so LoopStateStore can drop files there.
    assert (missions_dir / mid / "loop_state").is_dir()


def test_mission_start_workdir_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parser = build_parser()
    ns = parser.parse_args(
        ["mission", "start", "anything", "--state-dir", str(tmp_path)]
    )
    cmd_mission_start(ns)
    payload = json.loads(
        next((tmp_path / "missions").glob("mission_*/mission.json")).read_text()
    )
    assert Path(payload["workdir"]).resolve() == tmp_path.resolve()


def test_mission_start_overwrites_active_pointer(tmp_path):
    parser = build_parser()

    rc = cmd_mission_start(
        parser.parse_args(["mission", "start", "first", "--state-dir", str(tmp_path)])
    )
    assert rc == 0
    first = json.loads((tmp_path / "missions" / "active.json").read_text())["mission_id"]

    # Wait a smidge so the timestamp changes (mission IDs use second precision).
    import time as _t

    _t.sleep(1.1)

    rc = cmd_mission_start(
        parser.parse_args(["mission", "start", "second", "--state-dir", str(tmp_path)])
    )
    assert rc == 0
    second = json.loads((tmp_path / "missions" / "active.json").read_text())["mission_id"]
    assert second != first
    # And both mission dirs are still on disk (we don't delete the old one).
    assert (tmp_path / "missions" / first / "mission.json").exists()
    assert (tmp_path / "missions" / second / "mission.json").exists()


def test_mission_start_rejects_empty_objective(tmp_path, capsys):
    parser = build_parser()
    ns = parser.parse_args(["mission", "start", "   ", "--state-dir", str(tmp_path)])
    rc = cmd_mission_start(ns)
    assert rc == 2
    err = capsys.readouterr().err
    assert "objective" in err


# ---------------------------------------------------------------------
# mission status
# ---------------------------------------------------------------------


def test_mission_status_no_active(tmp_path, capsys):
    parser = build_parser()
    ns = parser.parse_args(["mission", "status", "--state-dir", str(tmp_path)])
    rc = cmd_mission_status(ns)
    assert rc == 1
    assert "no active mission" in capsys.readouterr().err


def test_mission_status_reads_active_and_daemon(tmp_path, capsys):
    parser = build_parser()
    cmd_mission_start(
        parser.parse_args(["mission", "start", "x", "--state-dir", str(tmp_path)])
    )
    capsys.readouterr()  # discard mission-start's stdout
    # Synthesize a daemon status.json next to missions/.
    (tmp_path / "status.json").write_text(json.dumps({"pid": 4321, "current": "idle"}))

    rc = cmd_mission_status(
        parser.parse_args(["mission", "status", "--state-dir", str(tmp_path)])
    )
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["active_mission"]["objective"] == "x"
    assert payload["daemon_status"]["pid"] == 4321


# ---------------------------------------------------------------------
# mission stop
# ---------------------------------------------------------------------


def test_mission_stop_publishes_stop_command(tmp_path, capsys):
    parser = build_parser()
    rc = cmd_mission_stop(
        parser.parse_args(["mission", "stop", "--state-dir", str(tmp_path)])
    )
    assert rc == 0
    inbox = tmp_path / "inbox.jsonl"
    assert inbox.is_file()
    line = inbox.read_text().strip()
    rec = json.loads(line)
    assert rec["kind"] == "stop"
    assert rec["source"] == "mission-stop"

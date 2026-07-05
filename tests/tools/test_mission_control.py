"""Round-trip tests for the REPL<->daemon mission-abort signal file.

See ``argus_skill.tools.mission_control`` for the full rationale: the
Manager runs in the operator's REPL process, the mission it may need to
abort runs in the daemon's separate process, and this file (dropped into
their shared ``life_dir``) is the only channel between them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.tools.mission_control import (
    main,
    pop_pending_mission_abort,
    request_mission_abort,
)


def test_pop_pending_returns_none_when_nothing_written(tmp_path: Path) -> None:
    assert pop_pending_mission_abort(tmp_path) is None


def test_pop_pending_returns_none_for_falsy_life_dir() -> None:
    assert pop_pending_mission_abort(None) is None
    assert pop_pending_mission_abort("") is None


def test_request_then_pop_round_trips_reason_and_consumes_file(tmp_path: Path) -> None:
    path = request_mission_abort(
        tmp_path, reason="operator asked to stop", requested_by="manager"
    )
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["reason"] == "operator asked to stop"
    assert payload["requested_by"] == "manager"

    reason = pop_pending_mission_abort(tmp_path)
    assert reason == "operator asked to stop"
    # One-shot: consumed (deleted), so a second pop sees nothing pending.
    assert not path.exists()
    assert pop_pending_mission_abort(tmp_path) is None


def test_request_with_blank_reason_falls_back_to_default_text(tmp_path: Path) -> None:
    request_mission_abort(tmp_path, reason="   ")
    assert pop_pending_mission_abort(tmp_path) == "operator requested abort"


def test_pop_pending_tolerates_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "mission_abort_request.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    assert pop_pending_mission_abort(tmp_path) is None
    # Still cleaned up so a corrupt file doesn't wedge future requests.
    assert not path.exists()


def test_pop_pending_tolerates_non_dict_json(tmp_path: Path) -> None:
    path = tmp_path / "mission_abort_request.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert pop_pending_mission_abort(tmp_path) is None


def test_repeated_requests_overwrite_not_accumulate(tmp_path: Path) -> None:
    request_mission_abort(tmp_path, reason="first")
    request_mission_abort(tmp_path, reason="second")
    # Only the latest request should be pending.
    assert pop_pending_mission_abort(tmp_path) == "second"
    assert pop_pending_mission_abort(tmp_path) is None


def test_cli_abort_writes_request_readable_by_pop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["abort", "--life-dir", str(tmp_path), "--reason", "stop it now"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mission abort requested" in out
    assert pop_pending_mission_abort(tmp_path) == "stop it now"


def test_cli_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        main([])

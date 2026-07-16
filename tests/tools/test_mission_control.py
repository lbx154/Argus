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

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.tools.mission_control import (
    main,
    pop_pending_mission_abort,
    request_current_mission_abort,
    request_mission_abort,
)


def _running_item(root: Path) -> BacklogItem:
    backlog = LifeMemory.open(root).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)
    return item


def test_pop_pending_returns_none_when_nothing_written(tmp_path: Path) -> None:
    assert pop_pending_mission_abort(tmp_path) is None


def test_pop_pending_returns_none_for_falsy_life_dir() -> None:
    assert pop_pending_mission_abort(None) is None
    assert pop_pending_mission_abort("") is None


def test_request_then_pop_round_trips_reason_and_consumes_file(tmp_path: Path) -> None:
    item = _running_item(tmp_path)
    path = request_mission_abort(
        tmp_path,
        reason="operator asked to stop",
        requested_by="manager",
        target_item_id=item.id,
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
    item = _running_item(tmp_path)
    request_mission_abort(tmp_path, reason="   ", target_item_id=item.id)
    assert pop_pending_mission_abort(tmp_path) == "operator requested abort"


def test_targetless_legacy_request_is_discarded(tmp_path: Path) -> None:
    _running_item(tmp_path)
    path = request_mission_abort(tmp_path, reason="stale targetless request")
    assert pop_pending_mission_abort(tmp_path) is None
    assert not path.exists()


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
    item = _running_item(tmp_path)
    request_mission_abort(tmp_path, reason="first", target_item_id=item.id)
    request_mission_abort(tmp_path, reason="second", target_item_id=item.id)
    # Only the latest request should be pending.
    assert pop_pending_mission_abort(tmp_path) == "second"
    assert pop_pending_mission_abort(tmp_path) is None


def test_current_abort_never_leaves_signal_while_idle(tmp_path: Path) -> None:
    requested, item_id = request_current_mission_abort(
        tmp_path,
        reason="stop",
    )
    assert requested is False
    assert item_id is None
    assert not (tmp_path / "mission_abort_request.json").exists()


def test_current_abort_targets_existing_running_item(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)

    requested, item_id = request_current_mission_abort(
        tmp_path,
        reason="operator stop",
    )

    assert requested is True
    assert item_id == item.id
    assert pop_pending_mission_abort(tmp_path) == "operator stop"


def test_current_abort_reports_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.tools import mission_control

    item = _running_item(tmp_path)
    monkeypatch.setattr(
        mission_control.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    requested, item_id = request_current_mission_abort(tmp_path, reason="stop")

    assert requested is False
    assert item_id == item.id
    assert not (tmp_path / "mission_abort_request.json").exists()
    assert list(tmp_path.glob("mission_abort_request.*.tmp")) == []


def test_targeted_abort_cannot_kill_a_later_mission(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    first = backlog.add(BacklogItem.new(title="first", objective="first"))
    backlog.mark_running(first.id)
    requested, _ = request_current_mission_abort(tmp_path, reason="stop first")
    assert requested is True
    backlog.update(first.id, status="failed", finished_ts=1.0)
    second = backlog.add(BacklogItem.new(title="second", objective="second"))
    backlog.mark_running(second.id)

    assert pop_pending_mission_abort(tmp_path) is None
    assert backlog.all()[-1].status == "running"


def test_cli_abort_writes_request_readable_by_pop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)

    rc = main(["abort", "--life-dir", str(tmp_path), "--reason", "stop it now"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "mission abort requested" in out
    assert pop_pending_mission_abort(tmp_path) == "stop it now"


def test_cli_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        main([])

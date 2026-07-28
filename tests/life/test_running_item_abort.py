"""Round-trip tests for the operator/API -> daemon running-item abort mailbox."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus_skill.life.memory import (
    BacklogItem,
    LifeMemory,
    consume_running_item_abort,
    request_running_item_abort,
)


def _running_item(root: Path) -> BacklogItem:
    backlog = LifeMemory.open(root).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)
    return item


def test_consume_returns_none_when_nothing_written(tmp_path: Path) -> None:
    assert consume_running_item_abort(tmp_path) is None


def test_consume_returns_none_for_falsy_life_dir() -> None:
    assert consume_running_item_abort(None) is None
    assert consume_running_item_abort("") is None


def test_request_then_consume_round_trips_reason_and_consumes_file(
    tmp_path: Path,
) -> None:
    item = _running_item(tmp_path)
    requested, item_id = request_running_item_abort(
        tmp_path,
        reason="operator asked to stop",
        requested_by="manager",
    )
    path = tmp_path / "running_item_abort.json"
    legacy_path = tmp_path / "mission_abort_request.json"
    assert requested is True
    assert item_id == item.id
    assert path.exists()
    assert legacy_path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["reason"] == "operator asked to stop"
    assert payload["requested_by"] == "manager"

    reason = consume_running_item_abort(tmp_path)
    assert reason == "operator asked to stop"
    # One-shot: consumed (deleted), so a second pop sees nothing pending.
    assert not path.exists()
    assert not legacy_path.exists()
    assert consume_running_item_abort(tmp_path) is None


def test_request_with_blank_reason_falls_back_to_default_text(tmp_path: Path) -> None:
    item = _running_item(tmp_path)
    requested, item_id = request_running_item_abort(tmp_path, reason="   ")
    assert requested is True
    assert item_id == item.id
    assert consume_running_item_abort(tmp_path) == "operator requested abort"


def test_targetless_legacy_request_is_discarded(tmp_path: Path) -> None:
    _running_item(tmp_path)
    path = tmp_path / "running_item_abort.json"
    path.write_text(
        json.dumps({"reason": "stale targetless request"}),
        encoding="utf-8",
    )
    assert consume_running_item_abort(tmp_path) is None
    assert not path.exists()


def test_consume_accepts_targeted_legacy_mailbox(tmp_path: Path) -> None:
    item = _running_item(tmp_path)
    path = tmp_path / "mission_abort_request.json"
    path.write_text(
        json.dumps(
            {
                "target_item_id": item.id,
                "reason": "old manager request",
            }
        ),
        encoding="utf-8",
    )

    assert consume_running_item_abort(tmp_path) == "old manager request"
    assert not path.exists()


def test_consume_tolerates_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "running_item_abort.json"
    path.write_text("not valid json{{{", encoding="utf-8")
    assert consume_running_item_abort(tmp_path) is None
    # Still cleaned up so a corrupt file doesn't wedge future requests.
    assert not path.exists()


def test_consume_tolerates_non_dict_json(tmp_path: Path) -> None:
    path = tmp_path / "running_item_abort.json"
    path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert consume_running_item_abort(tmp_path) is None


def test_repeated_requests_overwrite_not_accumulate(tmp_path: Path) -> None:
    _running_item(tmp_path)
    request_running_item_abort(tmp_path, reason="first")
    request_running_item_abort(tmp_path, reason="second")
    # Only the latest request should be pending.
    assert consume_running_item_abort(tmp_path) == "second"
    assert consume_running_item_abort(tmp_path) is None


def test_consume_does_not_delete_a_newer_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _running_item(tmp_path)
    request_running_item_abort(tmp_path, reason="first")
    original_read_text = Path.read_text
    injected = False

    def read_text_with_new_request(path: Path, *args, **kwargs) -> str:
        nonlocal injected
        text = original_read_text(path, *args, **kwargs)
        if path.name.endswith(".claimed") and not injected:
            injected = True
            request_running_item_abort(tmp_path, reason="second")
        return text

    monkeypatch.setattr(Path, "read_text", read_text_with_new_request)

    assert consume_running_item_abort(tmp_path) == "first"
    assert (tmp_path / "running_item_abort.json").exists()
    assert consume_running_item_abort(tmp_path) == "second"


def test_current_abort_never_leaves_signal_while_idle(tmp_path: Path) -> None:
    requested, item_id = request_running_item_abort(
        tmp_path,
        reason="stop",
    )
    assert requested is False
    assert item_id is None
    assert not (tmp_path / "running_item_abort.json").exists()


def test_current_abort_targets_existing_running_item(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    item = backlog.add(BacklogItem.new(title="task", objective="work"))
    backlog.mark_running(item.id)

    requested, item_id = request_running_item_abort(
        tmp_path,
        reason="operator stop",
    )

    assert requested is True
    assert item_id == item.id
    assert consume_running_item_abort(tmp_path) == "operator stop"


def test_current_abort_reports_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.life.memory as memory

    item = _running_item(tmp_path)
    monkeypatch.setattr(
        memory.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("disk full")),
    )

    requested, item_id = request_running_item_abort(tmp_path, reason="stop")

    assert requested is False
    assert item_id == item.id
    assert not (tmp_path / "running_item_abort.json").exists()
    assert list(tmp_path.glob("running_item_abort.*.tmp")) == []


def test_concurrent_writes_use_unique_temporary_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argus_skill.life.memory as memory

    _running_item(tmp_path)
    sources: list[str] = []

    def fail_after_capture(source, _destination) -> None:
        sources.append(str(source))
        raise OSError("disk full")

    monkeypatch.setattr(memory.os, "replace", fail_after_capture)

    request_running_item_abort(tmp_path, reason="first")
    request_running_item_abort(tmp_path, reason="second")

    assert len(sources) == 2
    assert sources[0] != sources[1]


def test_targeted_abort_cannot_kill_a_later_mission(tmp_path: Path) -> None:
    backlog = LifeMemory.open(tmp_path).backlog
    first = backlog.add(BacklogItem.new(title="first", objective="first"))
    backlog.mark_running(first.id)
    requested, _ = request_running_item_abort(tmp_path, reason="stop first")
    assert requested is True
    backlog.update(first.id, status="failed", finished_ts=1.0)
    second = backlog.add(BacklogItem.new(title="second", objective="second"))
    backlog.mark_running(second.id)

    assert consume_running_item_abort(tmp_path) is None
    assert backlog.all()[-1].status == "running"

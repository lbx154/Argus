"""Regression tests for the ``argus-skill --status`` command."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from argus_skill.apps.cli import _cmd_status


def test_status_uses_journal_tail_instead_of_full_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class _FakeEntry:
        def __init__(self, kind: str, summary: str) -> None:
            self.kind = kind
            self.summary = summary

    class _FakeJournal:
        def __init__(self) -> None:
            self.tail_calls: list[int] = []

        def tail(self, n: int = 20):
            self.tail_calls.append(n)
            if n != 3:
                raise AssertionError(f"expected tail(3), got tail({n})")
            return [
                _FakeEntry("mission_complete", "finished"),
                _FakeEntry("mission_failed", "oops"),
            ]

        def all(self):
            raise AssertionError("status must not read the full journal")

    class _FakeBacklog:
        def all(self):
            return [
                Namespace(status="pending"),
                Namespace(status="running"),
                Namespace(status="done"),
                Namespace(status="failed"),
                Namespace(status="skipped"),
            ]

    class _FakeMemory:
        def __init__(self) -> None:
            self.journal = _FakeJournal()
            self.backlog = _FakeBacklog()

    fake_mem = _FakeMemory()

    monkeypatch.setattr("argus_skill.life.memory.LifeMemory.open", lambda root: fake_mem)
    monkeypatch.setattr("argus_skill.daemon.life_worker.read_daemon_status", lambda life_dir: Namespace(
        alive=True,
        pid=4321,
        uptime_seconds=12.0,
        backend="memory",
    ))
    monkeypatch.setattr("argus_skill.apps.cli._check_logout_survival", lambda status: None)

    (tmp_path / "continuous.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "objective": "hardening objective",
                "done_reason": "planner declared project done",
                "done_at": "2026-05-12T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    rc = _cmd_status(Namespace(life_dir=str(tmp_path)))
    out = capsys.readouterr().out

    assert rc == 0
    assert fake_mem.journal.tail_calls == [3]
    assert "1 pending" in out
    assert "1 running" in out
    assert "2 done" not in out
    assert "mission_failed" in out or "oops" in out
    assert "continuous: on" in out
    assert "objective: hardening objective" in out
    assert "done_reason: planner declared project done" in out
    assert "done_at: 2026-05-12T00:00:00Z" in out

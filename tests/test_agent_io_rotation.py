"""``agent_io.jsonl`` must stay bounded on disk.

``events.jsonl`` is the authoritative history and already rotates; this
verbatim provider transcript did not, so a long-lived daemon grew it without
limit — 6.1 GiB in a single session and 33 GiB across sessions were measured on
one box before this guard existed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import _io_log


def _append(path: Path, payload: str, times: int = 1) -> None:
    lock = threading.Lock()
    for _ in range(times):
        _io_log._jsonl_append_lines(path, [payload], lock)


def test_transcript_rotates_once_it_exceeds_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_io_log._AGENT_IO_MAX_BYTES_ENV, "500")
    monkeypatch.setenv(_io_log._AGENT_IO_KEEP_ENV, "2")
    log = tmp_path / "agent_io.jsonl"

    _append(log, "x" * 200, times=6)

    assert log.exists()
    assert log.with_name("agent_io.jsonl.1").exists()
    # The ring keeps at most `keep` generations; nothing beyond it survives.
    assert not log.with_name("agent_io.jsonl.3").exists()


def test_total_disk_stays_within_cap_times_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cap, keep = 400, 2
    monkeypatch.setenv(_io_log._AGENT_IO_MAX_BYTES_ENV, str(cap))
    monkeypatch.setenv(_io_log._AGENT_IO_KEEP_ENV, str(keep))
    log = tmp_path / "agent_io.jsonl"

    # Far more data than the cap: without rotation this would grow unbounded.
    _append(log, "y" * 100, times=400)

    total = sum(p.stat().st_size for p in tmp_path.glob("agent_io.jsonl*"))
    # Each generation is rolled at the cap, so the ring holds (keep + 1) of
    # them plus at most one in-flight append.
    assert total <= cap * (keep + 1) + 200, total


def test_rotation_can_be_disabled_for_full_forensic_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_io_log._AGENT_IO_MAX_BYTES_ENV, "0")
    log = tmp_path / "agent_io.jsonl"

    _append(log, "z" * 100, times=50)

    assert not log.with_name("agent_io.jsonl.1").exists()
    assert log.stat().st_size > 4000


def test_append_still_works_and_keeps_the_newest_lines(tmp_path: Path) -> None:
    log = tmp_path / "agent_io.jsonl"
    _append(log, '{"a": 1}')
    _append(log, '{"a": 2}')
    assert log.read_text().splitlines() == ['{"a": 1}', '{"a": 2}']

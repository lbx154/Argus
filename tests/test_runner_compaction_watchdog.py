"""In-round auto-compaction amnesia-loop detection in the runner watchdog.

The engineer round watchdog (``_EffectiveProgressWatchdog``) is wired as
Codex's ``external_interrupt_reason_provider``. A single unreturned round can
fall into a tight auto-compaction loop (compact -> amnesia -> re-read skills ->
re-emit preamble -> compact again) that the idle-timeout path never catches,
because the churn keeps emitting real session events. These tests pin the
compaction-thrash detector and its supporting invariants.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import argus_skill.engineer.runner as runner_module
from argus_skill.engineer.runner import (
    _EffectiveProgressWatchdog,
    _is_project_progress_ignored_dir,
    _is_codex_compaction_line,
    _is_effective_codex_session_line,
    fatal_error_looks_like_compaction_thrash,
    should_clear_thread_id_after_outcome,
)

_COMPACTED = json.dumps({"timestamp": "t", "type": "compacted", "payload": {}})
_RESPONSE = json.dumps(
    {"timestamp": "t", "type": "response_item", "payload": {"type": "message"}}
)
_TOKEN_COUNT = json.dumps({"timestamp": "t", "type": "token_count", "payload": {}})


def _session_meta(workdir: Path) -> str:
    return json.dumps(
        {"type": "session_meta", "payload": {"cwd": str(workdir)}}
    )


def _make_session(root: Path, workdir: Path, *lines: str) -> Path:
    sessions = root / "sessions" / "2026" / "06" / "04"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / "rollout-test.jsonl"
    body = "\n".join([_session_meta(workdir), *lines])
    path.write_text(body + ("\n" if lines else "\n"), encoding="utf-8")
    return path


def _watchdog(workdir: Path, *, compaction_limit: int = 3) -> tuple[
    _EffectiveProgressWatchdog, list[dict]
]:
    events: list[dict] = []
    wd = _EffectiveProgressWatchdog(
        workdir=workdir,
        timeout_seconds=3600,
        check_interval_seconds=1.0,
        on_event=events.append,
        run_label="test",
        compaction_limit=compaction_limit,
    )
    return wd, events


# --- line classifiers ------------------------------------------------------


def test_compaction_line_recognised():
    assert _is_codex_compaction_line(_COMPACTED) is True
    assert _is_codex_compaction_line(_RESPONSE) is False
    assert _is_codex_compaction_line("not json") is False


def test_compaction_line_never_counts_as_effective_progress():
    # The whole bug was compaction events resetting the progress timer.
    assert _is_effective_codex_session_line(_COMPACTED) is False
    assert _is_effective_codex_session_line(_TOKEN_COUNT) is False
    assert _is_effective_codex_session_line(_RESPONSE) is True


# --- detector integration --------------------------------------------------


def test_compaction_thrash_interrupts_round(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = _make_session(home, workdir, _RESPONSE)

    wd, events = _watchdog(workdir, compaction_limit=3)
    with path.open("a", encoding="utf-8") as fh:
        for _ in range(3):
            fh.write(_COMPACTED + "\n")

    reason = wd.interrupt_reason()
    assert reason is not None
    assert fatal_error_looks_like_compaction_thrash(reason)
    assert should_clear_thread_id_after_outcome(status="continue", fatal_error=reason)
    assert any(e["type"] == "round.watchdog.compaction_thrash" for e in events)
    assert wd._compaction_count >= 3


def test_below_limit_does_not_interrupt(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = _make_session(home, workdir, _RESPONSE)

    wd, events = _watchdog(workdir, compaction_limit=3)
    with path.open("a", encoding="utf-8") as fh:
        for _ in range(2):
            fh.write(_COMPACTED + "\n")

    # idle timer has not elapsed and only 2 < 3 compactions occurred.
    assert wd.interrupt_reason() is None
    assert wd._compaction_count == 2
    assert not any(
        e["type"] == "round.watchdog.compaction_thrash" for e in events
    )


def test_disabled_when_limit_zero(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = _make_session(home, workdir, _RESPONSE)

    wd, _ = _watchdog(workdir, compaction_limit=0)
    with path.open("a", encoding="utf-8") as fh:
        for _ in range(10):
            fh.write(_COMPACTED + "\n")

    assert wd.interrupt_reason() is None


def test_partial_trailing_line_is_not_skipped(tmp_path, monkeypatch):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    home = tmp_path / "codex_home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = _make_session(home, workdir, _RESPONSE)

    wd, _ = _watchdog(workdir, compaction_limit=3)
    offset = wd._session_offsets[path]

    # Write a compaction line WITHOUT a trailing newline (live writer mid-flush).
    with path.open("a", encoding="utf-8") as fh:
        fh.write(_COMPACTED)
    first = wd._read_effective_session_events(path, offset)
    assert first.compactions == 0  # incomplete line not yet consumed
    assert first.consumed_bytes == 0

    # Complete that line and append another, both now terminated.
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n" + _COMPACTED + "\n")
    second = wd._read_effective_session_events(path, offset)
    assert second.compactions == 2  # the previously-partial line is not lost


def test_custom_named_virtualenv_does_not_hide_experiment_log_growth(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "proj"
    env_dir = workdir / ".venv-b200-tilelang"
    env_dir.mkdir(parents=True)
    (env_dir / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    for index in range(4):
        (env_dir / f"generated-{index}.py").write_text("x = 1\n", encoding="utf-8")

    log_path = workdir / "research" / "raw" / "full-gate.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("PASSED [ 51%]\n", encoding="utf-8")

    assert _is_project_progress_ignored_dir(workdir, env_dir.name)
    monkeypatch.setattr(runner_module, "_PROJECT_PROGRESS_MAX_FILES", 2)
    wd, _ = _watchdog(workdir)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("PASSED [ 52%]\n")

    assert wd._project_changed() is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

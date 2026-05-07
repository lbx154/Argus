"""Tests for the per-mission failed-tool ledger and its integration
with the codex stream-json progress callback."""

from __future__ import annotations

import json

from argus_skill.adapters.stream_progress import make_stream_progress_callback
from argus_skill.engineer.failed_tool_ledger import FailedToolLedger


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, event: dict) -> None:
        self.events.append(event)

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        return None


def _stream_event(sink, ledger, item: dict) -> None:
    cb = make_stream_progress_callback(sink, ledger=ledger)
    payload = json.dumps({"type": "item.completed", "item": item})
    cb("main.stdout", payload)


# ---- ledger basics --------------------------------------------------------

def test_ledger_records_and_thresholds() -> None:
    ledger = FailedToolLedger(nudge_threshold=2)
    assert ledger.repeated_failures() == {}

    ledger.record("apply_patch", "sandbox mismatch", detail="add foo.py")
    assert ledger.count("apply_patch") == 1
    assert ledger.repeated_failures() == {}, "below threshold should not surface"

    ledger.record("apply_patch", "sandbox mismatch", detail="add bar.py")
    repeated = ledger.repeated_failures()
    assert "apply_patch" in repeated
    assert len(repeated["apply_patch"]) == 2


def test_ledger_advisory_renders_once_then_silent() -> None:
    ledger = FailedToolLedger(nudge_threshold=2)
    ledger.record("apply_patch", "sandbox mismatch", detail="add foo.py")
    ledger.record("apply_patch", "sandbox mismatch", detail="add bar.py")

    advisory = ledger.render_advisory()
    assert "apply_patch" in advisory
    assert "Repeated tool failures" in advisory
    assert "investigate" in advisory.lower()

    # Subsequent renders return empty until a new tool reaches threshold.
    assert ledger.render_advisory() == ""

    # A fresh tool that crosses threshold surfaces independently.
    ledger.record("shell:git", "permission denied")
    assert ledger.render_advisory() == ""  # still 1× for git
    ledger.record("shell:git", "permission denied")
    advisory2 = ledger.render_advisory()
    assert "shell:git" in advisory2
    assert "apply_patch" not in advisory2  # already nudged previously


def test_ledger_clear_resets_state() -> None:
    ledger = FailedToolLedger(nudge_threshold=1)
    ledger.record("a", "boom")
    assert ledger.repeated_failures()
    ledger.clear()
    assert ledger.repeated_failures() == {}


def test_ledger_truncates_long_errors() -> None:
    ledger = FailedToolLedger()
    ledger.record("x", "z" * 5000)
    rec = ledger._records["x"][0]
    assert len(rec.error) <= 600
    assert rec.error.endswith("…")


# ---- stream-progress integration -----------------------------------------

def test_failed_command_execution_records_in_ledger() -> None:
    sink = _RecordingSink()
    ledger = FailedToolLedger(nudge_threshold=2)
    item = {
        "id": "item_1",
        "type": "command_execution",
        "command": "/bin/bash -lc 'git status --short'",
        "aggregated_output": "fatal: not a git repository\n",
        "exit_code": 128,
        "status": "failed",
    }
    _stream_event(sink, ledger, item)
    assert ledger.count("shell:git") == 1
    rec = ledger._records["shell:git"][0]
    assert "not a git repository" in rec.error
    assert "git status" in rec.detail


def test_successful_command_execution_does_not_record() -> None:
    sink = _RecordingSink()
    ledger = FailedToolLedger()
    item = {
        "id": "item_2",
        "type": "command_execution",
        "command": "/bin/bash -lc 'ls'",
        "aggregated_output": "foo.py\n",
        "exit_code": 0,
        "status": "completed",
    }
    _stream_event(sink, ledger, item)
    assert ledger.repeated_failures() == {}
    assert ledger._records == {}


def test_failed_file_change_records_apply_patch_bucket() -> None:
    sink = _RecordingSink()
    ledger = FailedToolLedger()
    item = {
        "id": "item_3",
        "type": "file_change",
        "changes": [{"path": "/tmp/foo.py", "kind": "add"}],
        "status": "failed",
    }
    _stream_event(sink, ledger, item)
    assert ledger.count("apply_patch") == 1
    rec = ledger._records["apply_patch"][0]
    assert "foo.py" in rec.detail


def test_shell_bucket_groups_same_binary() -> None:
    sink = _RecordingSink()
    ledger = FailedToolLedger(nudge_threshold=2)
    for cmd in [
        "/bin/bash -lc 'pytest -q test_a.py'",
        "/bin/bash -lc 'pytest -q test_b.py'",
    ]:
        _stream_event(sink, ledger, {
            "id": "x",
            "type": "command_execution",
            "command": cmd,
            "aggregated_output": "1 failed",
            "exit_code": 1,
            "status": "failed",
        })
    assert ledger.count("shell:pytest") == 2
    assert "shell:pytest" in ledger.repeated_failures()


def test_callback_without_ledger_is_noop() -> None:
    """Backwards-compat: existing callers don't pass a ledger and must
    not crash on failed beats."""
    sink = _RecordingSink()
    cb = make_stream_progress_callback(sink)  # no ledger=
    payload = json.dumps({
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": "/bin/bash -lc 'false'",
            "exit_code": 1,
            "status": "failed",
            "aggregated_output": "boom",
        },
    })
    cb("main.stdout", payload)
    # Smoke: no crash. Progress events still emit normally.
    assert any(e.get("type") == "engineer.progress" for e in sink.events)

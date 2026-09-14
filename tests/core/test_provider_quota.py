from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import portalocker

from argus.core.provider_quota import (
    acquire_codex_permit,
    codex_quota_snapshot,
    provider_usage_snapshot,
)


def test_codex_daily_cap_blocks_after_counted_start(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "1")

    first = acquire_codex_permit("engineer-r1")
    assert first.allowed
    assert first.daily_calls == 1
    first.finish(success=True)

    blocked = acquire_codex_permit("reviewer")
    assert not blocked.allowed
    assert "daily call cap 1 reached" in blocked.reason
    snapshot = codex_quota_snapshot()
    assert snapshot["daily_calls"] == 1
    assert snapshot["completed_calls"] == 1
    assert snapshot["remaining"] == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "codex-usage.jsonl").read_text().splitlines()
    ]
    assert [row["type"] for row in rows] == [
        "provider.request.started",
        "provider.request.completed",
        "provider.request.denied",
    ]
    assert all(row["event_schema_version"] == 1 for row in rows)
    assert all("event_validation" not in row for row in rows)


def test_provider_snapshot_combines_codex_and_copilot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "5")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_CALL_CAP", "7")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP", "11")

    permit = acquire_codex_permit("matcher")
    permit.finish(success=False, error_text="test failure")
    snapshot = provider_usage_snapshot()

    assert snapshot["codex"]["daily_calls"] == 1
    assert snapshot["codex"]["daily_cap"] == 5
    assert snapshot["codex"]["failed_calls"] == 1
    assert snapshot["copilot"]["daily_cap"] == 7
    assert snapshot["copilot"]["premium_cap"] == 11


def test_codex_daily_cap_is_atomic_across_processes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_CODEX_GUARD", "1")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "3")
    env = os.environ.copy()
    script = (
        "from argus.core.provider_quota import acquire_codex_permit; "
        "permit = acquire_codex_permit('parallel-test'); "
        "print('allowed' if permit.allowed else 'blocked', flush=True)"
    )

    # Hold the shared lock until every child has started. On Windows the old
    # fcntl-only implementation ignored this lock, so all children passed the
    # cap concurrently and overwrote one another's state.
    lock_path = tmp_path / "codex-quota.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as holder:
        portalocker.lock(holder, portalocker.LOCK_EX)
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(8)
        ]
        time.sleep(0.5)
        portalocker.unlock(holder)

    outputs = [process.communicate(timeout=20) for process in processes]
    assert all(process.returncode == 0 for process in processes), outputs
    assert sum(stdout.strip() == "allowed" for stdout, _stderr in outputs) == 3
    assert codex_quota_snapshot()["daily_calls"] == 3

"""Codex-specific per-request usage from the CLI's own session files.

``turn.completed`` reports one total over the thread's whole life and leaves
out the subagents a turn spawns (``spawn_agent``): on 2026-10-26 they used
11.8M input tokens beside one Engineer turn's own 6.0M. Codex also writes a
``token_usage_record`` for every model request to the thread's session file,
a subagent's under its parent's session id in the subagent's own file, which
the parent's ``SubAgentActivity`` event names. Read after a call, those rows
are the call's usage: no baseline is needed and each request has its own
long-context tier.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ..core.sandbox import private_path
from ..core.token_usage import TokenUsage, extract_token_usage


def _session_files(sessions: Path, thread_id: str) -> list[Path]:
    """Codex keeps a thread in ``YYYY/MM/DD/rollout-<time>-<id>.jsonl`` of the
    moment it began, which the thread's UUIDv7 id carries; the day is local,
    or UTC's where codex cannot tell the local offset."""
    try:
        began = int(thread_id.replace("-", "")[:12], 16) / 1000
        days = {datetime.fromtimestamp(began, zone).strftime("%Y/%m/%d") for zone in (None, UTC)}
    except (ValueError, OverflowError, OSError):
        return []
    return [path for day in sorted(days) for path in (sessions / day).glob(f"rollout-*-{thread_id}.jsonl")]


def read_codex_requests(
    *, session_id: str | None, since: float, working_dir: str | Path | None,
) -> tuple[TokenUsage, ...]:
    """Every model request codex recorded for ``session_id`` from ``since``
    (epoch seconds) on, its subagents' included; () when it recorded none."""
    if not session_id:
        return ()
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    sessions = private_path(home / "sessions", working_dir)
    requests: list[TokenUsage] = []
    threads = [session_id]
    for thread_id in threads:  # grows by the subagents each file names
        for path in _session_files(sessions, thread_id):
            try:
                with path.open(encoding="utf-8", errors="replace") as handle:
                    lines = [line for line in handle if '"token_usage_record"' in line or '"agent_thread_id"' in line]
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                    payload = record["payload"]
                    if datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp() < since:
                        continue
                    if record["type"] == "token_usage_record":
                        if payload["session_id"] == session_id:
                            requests.append(extract_token_usage([{"usage": payload["usage"]}]))
                    elif isinstance(agent := payload["item"]["agent_thread_id"], str) and agent not in threads:
                        threads.append(agent)
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # a torn or foreign line is no request
    return tuple(request for request in requests if request.observed)


__all__ = ["read_codex_requests"]

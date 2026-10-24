"""A fresh session must prefer an existing live-daemon session."""

from __future__ import annotations

import os
from pathlib import Path

from argus.core.session import (
    resolve_session,
)


def _make_session_with_daemon(gr: Path, sid: str, now: float, *, pid: int | None):
    # Create a deterministic session dir directly (no resolve_session — that
    # would mint extra random sessions and pollute the most-recent ordering).
    d = gr / "projects" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "session.json").write_text(
        f'{{"id": "{sid}", "created": {now}, "last_active": {now}}}',
        encoding="utf-8",
    )
    if pid is not None:
        (d / "daemon.pid").write_text(str(pid), encoding="utf-8")
    os.utime(d, (now, now))
    return d


def test_continue_falls_back_to_most_recent_when_none_live(tmp_path):
    _make_session_with_daemon(tmp_path, "s-old0001", 100, pid=None)
    _make_session_with_daemon(tmp_path, "s-new0002", 500, pid=None)
    sid, _ = resolve_session(global_root=tmp_path, mode="continue")
    assert sid == "s-new0002"  # plain most-recent when nothing is live

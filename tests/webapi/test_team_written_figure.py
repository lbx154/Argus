"""A figure the Engineer's tools recorded writing can be shown in the conversation.

The conversation's milestones name the files a round wrote; a picture among
them shows as a thumbnail, read through the same guarded artifact path as a
delivered result. A file nobody recorded writing stays unreadable there.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from argus.core.session import SessionMeta, write_session_meta
from argus.webapi.artifacts import get_project_artifact


def _project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    workspace = root / "workspace" / sid
    life.mkdir(parents=True)
    (workspace / "figures").mkdir(parents=True)
    now = time.time()
    write_session_meta(root, SessionMeta(id=sid, created=now, last_active=now, cwd=str(life),
                                         workdir=str(workspace), launch_cwd=str(workspace)))
    events = [
        {"type": "round.start", "item_id": "t", "round_index": 1, "ts": now - 5, "event_id": "r1"},
        {"type": "engineer.progress", "kind": "file_change", "agent_layer": "engineer", "item_id": "t",
         "changes": [str(workspace / "figures" / "curve.png"), "figures/table.csv"], "ts": now - 4, "event_id": "w1"},
    ]
    (life / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    for name in ("curve.png", "table.csv", "other.png"):
        (workspace / "figures" / name).write_bytes(b"\x89PNG")
    return workspace


def test_a_figure_the_engineer_wrote_is_readable_and_another_is_not(tmp_path: Path) -> None:
    _project(tmp_path, "s-figure")

    written = get_project_artifact("s-figure", "figures/curve.png", global_root=tmp_path, preview_bytes=0)
    assert written is not None and written["kind"] == "image" and written["source"] == "team_written"
    assert get_project_artifact("s-figure", "figures/table.csv", global_root=tmp_path, preview_bytes=0) is not None
    assert get_project_artifact("s-figure", "figures/other.png", global_root=tmp_path, preview_bytes=0) is None

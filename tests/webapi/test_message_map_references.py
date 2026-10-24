"""Card references quoted from the Atlas map, through the Manager front door.

F1: an operator message containing ``[[Argus引用 {...}]]`` lines reaches the
Manager with a bounded, secret-redacted context block appended after the
operator's own words, while the persisted/visible operator turn carries a
readable inline line instead of raw JSON.

F3: when such a message is routed to TASK creation, the created backlog item
depends on the referenced tasks that can still satisfy a dependency (done or
live). Referenced tasks in a terminal-not-done state are never attached —
``memory._cascade_blocked`` would immediately skip the new item.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import dispatch, front_door

pytest.importorskip("fastapi")


def _make_project(root: Path, sid: str) -> Path:
    life = root / "projects" / sid
    life.mkdir(parents=True)
    (life / "events.jsonl").write_text(
        json.dumps({"type": "mission.started", "text": "hi", "ts": time.time()})
        + "\n",
        encoding="utf-8",
    )
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    return life


def _seed_tasks(life: Path) -> None:
    memory = LifeMemory.open(life)
    memory.backlog.add(
        BacklogItem(
            id="task-done",
            ts=1,
            title="Compare coverage",
            objective="Run 100 seeds",
            status="done",
        )
    )
    memory.backlog.add(
        BacklogItem(
            id="task-bad",
            ts=2,
            title="Broken step",
            objective="obj",
            status="failed",
        )
    )


# ---------------------------------------------------------------------------
# F1 — the chat path sees the expanded block; the journal stays readable
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# F3 — task creation attaches the referenced tasks as dependencies
# ---------------------------------------------------------------------------


def test_direct_dispatch_attaches_reference_deps(
    tmp_path: Path, monkeypatch,
) -> None:
    """The direct (stage-closing) persist path also attaches deps."""
    from argus.life.memory import MemoryBundle

    sid = "s-ref-direct"
    life = _make_project(tmp_path, sid)
    _seed_tasks(life)
    mem = MemoryBundle.for_cwd(fingerprint=sid, global_root=tmp_path)
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    monkeypatch.setattr(dispatch, "_resolve_manager_workdir", lambda _mem: workdir)

    def handoff(mem_arg, body, chat_state, *, persist, **_kwargs):
        persist(body, None)
        return body

    monkeypatch.setattr(front_door, "manager_continuous_handoff", handoff)

    prepared = SimpleNamespace(
        decision=SimpleNamespace(workflow_mode="direct", assumptions=()),
        lifetime="bounded",
        continuous=False,
        open_ended=False,
        public_objective=None,
        failed=lambda exc: None,
    )
    item, _alive, _pid = dispatch.enqueue_mission(
        mem,
        "扩展这个实验",
        {"config": {"continuous": False}},
        root_task_id="new-item-1",
        prepared_handoff=prepared,
        reference_deps=["task-done", "task-bad", "missing-id", "new-item-1"],
    )

    assert item.id == "new-item-1"
    assert item.deps == ["task-done"]

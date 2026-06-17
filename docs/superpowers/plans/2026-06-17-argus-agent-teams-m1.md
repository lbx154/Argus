# Argus Agent Teams (M1 core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the M1 "笨管道" core of Argus Agent Teams — a shared claimable task board, per-recipient mailbox, team roster, per-teammate git-worktree isolation, an agent-facing `tools/team.py` CLI, and a per-team scoping fix for the subagent discussion-block deadlock — all with passing unit tests. Plus the agent-judgment skill docs (`agent-team-lead.md` + teammate prompt contract).

**Architecture:** New harness package `argus_skill/team/` holds domain-agnostic plumbing (atomic+flock store, task_board, mailbox, roster, worktree). `argus_skill/tools/team.py` is the agent-facing CLI (verbs: form / spawn / status / wait / send / drain / claim / reassign / dissolve), mirroring the existing `tools/subagent.py` CLI shape. Teammates are launched as detached bounded Argus missions (`python -m argus_skill --objective … --bounded`) inside private worktrees. The "decide to form a team / how to split files / synthesize" judgment lives only in `builtin_skills/engineer/agent-team-lead.md`. M2 (real GPU-lease scheduling) and M3 (supervisor-level concurrency, dashboard, heterogeneous runtimes) are out of scope.

**Tech Stack:** Python 3 (stdlib only: `json`, `os`, `fcntl`, `tempfile`, `subprocess`, `argparse`, `pathlib`), pytest. Reuses existing patterns: atomic `tmp+os.replace` (`tools/subagent.py:810`), `fcntl.flock` (`tools/gpu_lease.py:98`), offset-tracked JSONL inbox (`apps/_inbox.py`).

**Spec:** `docs/superpowers/specs/2026-06-17-argus-agent-teams-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `argus_skill/team/__init__.py` | package marker + public re-exports |
| `argus_skill/team/_store.py` | DRY: `atomic_write_json`, `read_json`, `locked()` flock ctx |
| `argus_skill/team/task_board.py` | shared claimable task list: form / claim (CAS) / heartbeat / complete / fail / reassign_stale / all_done / snapshot |
| `argus_skill/team/mailbox.py` | per-recipient messaging: send / drain / count_pending / broadcast |
| `argus_skill/team/roster.py` | team manifest + member lifecycle + stale detection + reattach |
| `argus_skill/team/worktree.py` | per-teammate git worktree create / remove |
| `argus_skill/tools/team.py` | agent-facing CLI wrapping the above + detached teammate spawn |
| `argus_skill/tools/subagent.py` | MODIFY: scope `_open_discussion_blockers` to a `lane` (per-team) |
| `argus_skill/builtin_skills/engineer/agent-team-lead.md` | lead role contract (judgment layer) |
| `argus_skill/builtin_skills/engineer/argus-engineer-role.md` | MODIFY: add "when to form a team / solo is default" note |
| `tests/team/test_store.py` | unit: atomic write + flock |
| `tests/team/test_task_board.py` | unit: claim CAS, deps, reassign, all_done |
| `tests/team/test_mailbox.py` | unit: send/drain/offset/broadcast |
| `tests/team/test_roster.py` | unit: lifecycle, stale, reattach |
| `tests/team/test_worktree.py` | unit: worktree create/remove in temp git repo |
| `tests/tools/test_team_cli.py` | integration: form→spawn(stub)→status→wait→dissolve |
| `tests/tools/test_subagent_lane_scope.py` | unit: parked lane A does not block submit in lane B |

**Layout convention:** team data lives under `<root>/` where `<root> = <project>/.argus_team/<team_id>/`:
```
.argus_team/<team_id>/
  roster.json              .roster.lock
  tasks/<task_id>.json     .tasks.lock
  mailbox/<member>/inbox.jsonl  mailbox/<member>/inbox.offset
  shards/<task_id>.jsonl
  wt/<member>/             # git worktree
```

---

## Task 1: `team/_store.py` — atomic write + flock helper (DRY)

**Files:**
- Create: `argus_skill/team/__init__.py`
- Create: `argus_skill/team/_store.py`
- Test: `tests/team/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/team/test_store.py
from __future__ import annotations
import json
import multiprocessing as mp
from pathlib import Path

from argus_skill.team import _store


def test_atomic_write_then_read_roundtrips(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "data.json"
    _store.atomic_write_json(p, {"a": 1, "b": ["x"]})
    assert _store.read_json(p) == {"a": 1, "b": ["x"]}


def test_read_json_missing_returns_default(tmp_path: Path) -> None:
    assert _store.read_json(tmp_path / "nope.json", default={"d": True}) == {"d": True}


def test_atomic_write_no_partial_on_crash_leaves_old(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    _store.atomic_write_json(p, {"v": 1})
    # tmp files must not linger
    _store.atomic_write_json(p, {"v": 2})
    assert _store.read_json(p) == {"v": 2}
    assert list(p.parent.glob(".tmp-*")) == []


def _locked_incr(lock: str, counter: str) -> None:
    from argus_skill.team import _store as s
    from pathlib import Path as P
    for _ in range(50):
        with s.locked(P(lock)):
            cur = s.read_json(P(counter), default={"n": 0})
            cur["n"] += 1
            s.atomic_write_json(P(counter), cur)


def test_locked_serializes_concurrent_writers(tmp_path: Path) -> None:
    lock = str(tmp_path / ".lock")
    counter = str(tmp_path / "counter.json")
    procs = [mp.Process(target=_locked_incr, args=(lock, counter)) for _ in range(4)]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join()
    assert _store.read_json(Path(counter)) == {"n": 200}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/team/test_store.py -q`
Expected: FAIL — `ModuleNotFoundError: argus_skill.team`

- [ ] **Step 3: Write minimal implementation**

```python
# argus_skill/team/__init__.py
"""Argus Agent Teams — domain-agnostic team plumbing (harness layer)."""
from __future__ import annotations
```

```python
# argus_skill/team/_store.py
"""Atomic JSON writes + flock helper, shared across the team package.

Mirrors the patterns already used in tools/subagent.py (tmp+os.replace)
and tools/gpu_lease.py (fcntl.flock); centralised here so task_board,
mailbox, and roster don't each re-roll them.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


@contextlib.contextmanager
def locked(lock_path: Path) -> Iterator[None]:
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/team/test_store.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/__init__.py argus_skill/team/_store.py tests/team/test_store.py
git commit -m "feat(team): atomic-write + flock store helper (M1)"
```

---

## Task 2: `team/task_board.py` — shared claimable task list

**Files:**
- Create: `argus_skill/team/task_board.py`
- Test: `tests/team/test_task_board.py`

**Interface (lock in):**
- `form(root, tasks: list[dict]) -> None` — write `tasks/<task_id>.json` for each; fields: `task_id, title, objective, owns_paths, deps, state="pending", owner="", result_shard, claim_ts=0, heartbeat_ts=0, attempts=0`.
- `claim(root, member_id, *, now) -> dict | None` — under `.tasks.lock`: pick first task (sorted by id) with `state=="pending"` and every dep `done`; set `state="claimed"`, `owner=member_id`, `claim_ts=now`, `heartbeat_ts=now`; persist; return it. Else `None`.
- `heartbeat(root, task_id, *, now) -> None` — set `heartbeat_ts=now`; if `state=="claimed"` → `"running"`.
- `complete(root, task_id, *, shard="") -> None` — `state="done"`, `result_shard=shard`.
- `fail(root, task_id, *, reason="") -> None` — `state="failed"`, store `reason`.
- `reassign_stale(root, *, ttl, now) -> list[str]` — under lock: for tasks in `{claimed,running}` with `now-heartbeat_ts > ttl`: reset `state="pending"`, `owner=""`, `attempts+=1`; return reassigned ids.
- `all_done(root) -> bool` — every task `state=="done"`.
- `snapshot(root) -> list[dict]` — all task dicts sorted by id.

- [ ] **Step 1: Write the failing test**

```python
# tests/team/test_task_board.py
from __future__ import annotations
from pathlib import Path

from argus_skill.team import task_board as tb


def _form(root: Path) -> None:
    tb.form(root, [
        {"task_id": "a", "title": "A", "objective": "do a", "owns_paths": ["a/**"]},
        {"task_id": "b", "title": "B", "objective": "do b", "owns_paths": ["b/**"], "deps": ["a"]},
    ])


def test_claim_returns_pending_and_flips_state(tmp_path: Path) -> None:
    _form(tmp_path)
    got = tb.claim(tmp_path, "tm-1", now=100.0)
    assert got is not None and got["task_id"] == "a"
    assert got["owner"] == "tm-1" and got["state"] == "claimed"


def test_dep_blocks_claim_until_done(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim(tmp_path, "tm-1", now=1.0)            # claims "a"
    # "b" depends on "a" which is not done -> only nothing else claimable
    assert tb.claim(tmp_path, "tm-2", now=2.0) is None
    tb.complete(tmp_path, "a", shard="shards/a.jsonl")
    got = tb.claim(tmp_path, "tm-2", now=3.0)
    assert got is not None and got["task_id"] == "b"


def test_no_double_claim(tmp_path: Path) -> None:
    _form(tmp_path)
    first = tb.claim(tmp_path, "tm-1", now=1.0)
    second = tb.claim(tmp_path, "tm-2", now=1.0)
    assert first["task_id"] == "a"
    assert second is None or second["task_id"] != "a"


def test_reassign_stale_returns_to_pending(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim(tmp_path, "tm-1", now=1.0)
    tb.heartbeat(tmp_path, "a", now=1.0)
    reassigned = tb.reassign_stale(tmp_path, ttl=10.0, now=100.0)
    assert reassigned == ["a"]
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["state"] == "pending" and snap["a"]["attempts"] == 1
    # fresh heartbeat is NOT reassigned
    tb.claim(tmp_path, "tm-2", now=200.0)
    tb.heartbeat(tmp_path, "a", now=205.0)
    assert tb.reassign_stale(tmp_path, ttl=100.0, now=210.0) == []


def test_all_done(tmp_path: Path) -> None:
    _form(tmp_path)
    tb.claim(tmp_path, "tm-1", now=1.0); tb.complete(tmp_path, "a")
    assert tb.all_done(tmp_path) is False
    tb.claim(tmp_path, "tm-2", now=2.0); tb.complete(tmp_path, "b")
    assert tb.all_done(tmp_path) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/team/test_task_board.py -q`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError`

- [ ] **Step 3: Write minimal implementation**

```python
# argus_skill/team/task_board.py
"""Shared, concurrently-claimable task list for an agent team.

All mutating ops take an exclusive flock on ``.tasks.lock`` and persist
each task as ``tasks/<task_id>.json`` via atomic write. Claiming is a
compare-and-set (state must be ``pending`` and deps ``done``) so two
teammates can never own the same task.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _store

_STATES = ("pending", "claimed", "running", "done", "failed")


def _tasks_dir(root: Path) -> Path:
    return Path(root) / "tasks"


def _lock(root: Path) -> Path:
    return Path(root) / ".tasks.lock"


def _path(root: Path, task_id: str) -> Path:
    return _tasks_dir(root) / f"{task_id}.json"


def _load_all(root: Path) -> list[dict[str, Any]]:
    d = _tasks_dir(root)
    if not d.exists():
        return []
    out = [_store.read_json(p, default=None) for p in sorted(d.glob("*.json"))]
    return [t for t in out if isinstance(t, dict)]


def form(root: Path, tasks: list[dict[str, Any]]) -> None:
    for spec in tasks:
        task = {
            "task_id": spec["task_id"],
            "title": spec.get("title", ""),
            "objective": spec.get("objective", ""),
            "owns_paths": list(spec.get("owns_paths", [])),
            "deps": list(spec.get("deps", [])),
            "state": "pending",
            "owner": "",
            "result_shard": spec.get("result_shard", ""),
            "reason": "",
            "claim_ts": 0.0,
            "heartbeat_ts": 0.0,
            "attempts": 0,
        }
        _store.atomic_write_json(_path(root, task["task_id"]), task)


def _done_ids(tasks: list[dict[str, Any]]) -> set[str]:
    return {t["task_id"] for t in tasks if t["state"] == "done"}


def claim(root: Path, member_id: str, *, now: float) -> dict[str, Any] | None:
    with _store.locked(_lock(root)):
        tasks = _load_all(root)
        done = _done_ids(tasks)
        for task in sorted(tasks, key=lambda t: t["task_id"]):
            if task["state"] != "pending":
                continue
            if not all(dep in done for dep in task["deps"]):
                continue
            task["state"] = "claimed"
            task["owner"] = member_id
            task["claim_ts"] = now
            task["heartbeat_ts"] = now
            _store.atomic_write_json(_path(root, task["task_id"]), task)
            return task
    return None


def _mutate(root: Path, task_id: str, **changes: Any) -> None:
    with _store.locked(_lock(root)):
        task = _store.read_json(_path(root, task_id), default=None)
        if not isinstance(task, dict):
            return
        task.update(changes)
        _store.atomic_write_json(_path(root, task_id), task)


def heartbeat(root: Path, task_id: str, *, now: float) -> None:
    with _store.locked(_lock(root)):
        task = _store.read_json(_path(root, task_id), default=None)
        if not isinstance(task, dict):
            return
        task["heartbeat_ts"] = now
        if task["state"] == "claimed":
            task["state"] = "running"
        _store.atomic_write_json(_path(root, task_id), task)


def complete(root: Path, task_id: str, *, shard: str = "") -> None:
    _mutate(root, task_id, state="done", result_shard=shard)


def fail(root: Path, task_id: str, *, reason: str = "") -> None:
    _mutate(root, task_id, state="failed", reason=reason)


def reassign_stale(root: Path, *, ttl: float, now: float) -> list[str]:
    reassigned: list[str] = []
    with _store.locked(_lock(root)):
        for task in _load_all(root):
            if task["state"] in ("claimed", "running") and now - task["heartbeat_ts"] > ttl:
                task["state"] = "pending"
                task["owner"] = ""
                task["attempts"] = int(task.get("attempts", 0)) + 1
                _store.atomic_write_json(_path(root, task["task_id"]), task)
                reassigned.append(task["task_id"])
    return reassigned


def all_done(root: Path) -> bool:
    tasks = _load_all(root)
    return bool(tasks) and all(t["state"] == "done" for t in tasks)


def snapshot(root: Path) -> list[dict[str, Any]]:
    return _load_all(root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/team/test_task_board.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/task_board.py tests/team/test_task_board.py
git commit -m "feat(team): shared claimable task board with deps + reassign (M1)"
```

---

## Task 3: `team/mailbox.py` — per-recipient messaging

**Files:**
- Create: `argus_skill/team/mailbox.py`
- Test: `tests/team/test_mailbox.py`

**Interface:** `send(root, to, frm, text, *, now)`, `drain(root, member) -> list[dict]` (advances offset), `count_pending(root, member) -> int` (no advance), `broadcast(root, members, frm, text, *, now)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/team/test_mailbox.py
from __future__ import annotations
from pathlib import Path

from argus_skill.team import mailbox as mb


def test_send_and_drain(tmp_path: Path) -> None:
    mb.send(tmp_path, to="tm-2", frm="tm-1", text="hi", now=1.0)
    mb.send(tmp_path, to="tm-2", frm="lead", text="status?", now=2.0)
    msgs = mb.drain(tmp_path, "tm-2")
    assert [m["text"] for m in msgs] == ["hi", "status?"]
    assert msgs[0]["from"] == "tm-1"
    # drained once -> empty next time
    assert mb.drain(tmp_path, "tm-2") == []


def test_count_pending_does_not_advance(tmp_path: Path) -> None:
    mb.send(tmp_path, to="tm-1", frm="lead", text="x", now=1.0)
    assert mb.count_pending(tmp_path, "tm-1") == 1
    assert mb.count_pending(tmp_path, "tm-1") == 1   # still 1
    assert mb.drain(tmp_path, "tm-1")[0]["text"] == "x"


def test_broadcast_one_copy_each(tmp_path: Path) -> None:
    mb.broadcast(tmp_path, ["a", "b"], frm="lead", text="go", now=1.0)
    assert mb.drain(tmp_path, "a")[0]["text"] == "go"
    assert mb.drain(tmp_path, "b")[0]["text"] == "go"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/team/test_mailbox.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# argus_skill/team/mailbox.py
"""Per-recipient mailbox — generalises apps/_inbox.py (append + offset)
to one inbox per team member so teammates can message each other directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _box(root: Path, member: str) -> Path:
    return Path(root) / "mailbox" / member / "inbox.jsonl"


def _offset_path(root: Path, member: str) -> Path:
    return Path(root) / "mailbox" / member / "inbox.offset"


def _read_offset(p: Path) -> int:
    try:
        return max(0, int(p.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def send(root: Path, *, to: str, frm: str, text: str, now: float) -> None:
    box = _box(root, to)
    box.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": now, "from": frm, "text": text}
    with box.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read(root: Path, member: str, *, advance: bool) -> list[dict[str, Any]]:
    box = _box(root, member)
    if not box.exists():
        return []
    offset = _read_offset(_offset_path(root, member))
    out: list[dict[str, Any]] = []
    try:
        with box.open("rb") as fh:
            fh.seek(offset)
            while True:
                raw = fh.readline()
                if not raw:
                    break
                new_offset = fh.tell()
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    if advance:
                        _offset_path(root, member).write_text(str(new_offset), encoding="utf-8")
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                    out.append(obj)
                if advance:
                    _offset_path(root, member).write_text(str(new_offset), encoding="utf-8")
    except OSError:
        return []
    return out


def drain(root: Path, member: str) -> list[dict[str, Any]]:
    return _read(root, member, advance=True)


def count_pending(root: Path, member: str) -> int:
    return len(_read(root, member, advance=False))


def broadcast(root: Path, members: list[str], *, frm: str, text: str, now: float) -> None:
    for m in members:
        send(root, to=m, frm=frm, text=text, now=now)
```

> NOTE: test calls use keyword args (`to=`, `frm=`, `text=`, `now=`). The `send` signature is keyword-only after `root`. Adjust the Step-1 test calls to keyword form when running (they already pass `to=`/`frm=`/`text=`/`now=`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/team/test_mailbox.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/mailbox.py tests/team/test_mailbox.py
git commit -m "feat(team): per-recipient mailbox (M1)"
```

---

## Task 4: `team/roster.py` — team manifest + lifecycle

**Files:**
- Create: `argus_skill/team/roster.py`
- Test: `tests/team/test_roster.py`

**Interface:** `create(root, *, team_id, mission, lead, now)`, `load(root) -> dict`, `add_member(root, member: dict)`, `mark(root, member_id, *, status, now)`, `set_state(root, state)`, `members(root) -> list[dict]`, `stale_members(root, *, ttl, now) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/team/test_roster.py
from __future__ import annotations
from pathlib import Path

from argus_skill.team import roster as rs


def test_create_and_add_member(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="optimize kernels", lead="lead", now=1.0)
    rs.add_member(tmp_path, {"id": "tm-1", "pid": 111, "worktree": "wt/tm-1",
                             "task_id": "a", "status": "running", "heartbeat_ts": 1.0})
    doc = rs.load(tmp_path)
    assert doc["team_id"] == "t1" and doc["lead"] == "lead"
    assert [m["id"] for m in rs.members(tmp_path)] == ["tm-1"]


def test_mark_updates_status_and_heartbeat(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="m", lead="lead", now=1.0)
    rs.add_member(tmp_path, {"id": "tm-1", "status": "running", "heartbeat_ts": 1.0})
    rs.mark(tmp_path, "tm-1", status="idle", now=9.0)
    m = rs.members(tmp_path)[0]
    assert m["status"] == "idle" and m["heartbeat_ts"] == 9.0


def test_stale_members(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="m", lead="lead", now=1.0)
    rs.add_member(tmp_path, {"id": "tm-1", "status": "running", "heartbeat_ts": 1.0})
    rs.add_member(tmp_path, {"id": "tm-2", "status": "running", "heartbeat_ts": 100.0})
    assert rs.stale_members(tmp_path, ttl=10.0, now=50.0) == ["tm-1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/team/test_roster.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# argus_skill/team/roster.py
"""Team manifest: who is in the team, what they own, and their liveness.
Persisted atomically under an exclusive lock; the restart-resume anchor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _store


def _path(root: Path) -> Path:
    return Path(root) / "roster.json"


def _lock(root: Path) -> Path:
    return Path(root) / ".roster.lock"


def create(root: Path, *, team_id: str, mission: str, lead: str, now: float) -> None:
    with _store.locked(_lock(root)):
        _store.atomic_write_json(_path(root), {
            "team_id": team_id,
            "mission_objective": mission,
            "lead": lead,
            "created_ts": now,
            "state": "forming",
            "members": [],
        })


def load(root: Path) -> dict[str, Any]:
    return _store.read_json(_path(root), default={}) or {}


def members(root: Path) -> list[dict[str, Any]]:
    return list(load(root).get("members", []))


def add_member(root: Path, member: dict[str, Any]) -> None:
    with _store.locked(_lock(root)):
        doc = load(root)
        doc.setdefault("members", [])
        doc["members"] = [m for m in doc["members"] if m.get("id") != member.get("id")]
        doc["members"].append(member)
        _store.atomic_write_json(_path(root), doc)


def mark(root: Path, member_id: str, *, status: str, now: float) -> None:
    with _store.locked(_lock(root)):
        doc = load(root)
        for m in doc.get("members", []):
            if m.get("id") == member_id:
                m["status"] = status
                m["heartbeat_ts"] = now
        _store.atomic_write_json(_path(root), doc)


def set_state(root: Path, state: str) -> None:
    with _store.locked(_lock(root)):
        doc = load(root)
        doc["state"] = state
        _store.atomic_write_json(_path(root), doc)


def stale_members(root: Path, *, ttl: float, now: float) -> list[str]:
    out = []
    for m in members(root):
        if m.get("status") in ("running", "idle") and now - float(m.get("heartbeat_ts", 0)) > ttl:
            out.append(m["id"])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/team/test_roster.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/roster.py tests/team/test_roster.py
git commit -m "feat(team): roster manifest + lifecycle + stale detection (M1)"
```

---

## Task 5: `team/worktree.py` — per-teammate git worktree

**Files:**
- Create: `argus_skill/team/worktree.py`
- Test: `tests/team/test_worktree.py`

**Interface:** `create(repo_root, *, team_id, member_id, base_ref="HEAD") -> Path`, `remove(repo_root, path) -> None`, `path_for(repo_root, team_id, member_id) -> Path`.

- [ ] **Step 1: Write the failing test**

```python
# tests/team/test_worktree.py
from __future__ import annotations
import subprocess
from pathlib import Path

import pytest

from argus_skill.team import worktree as wt


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_create_makes_isolated_worktree(repo: Path) -> None:
    p = wt.create(repo, team_id="t1", member_id="tm-1")
    assert p.exists() and (p / "README.md").exists()
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo,
                             capture_output=True, text=True).stdout
    assert str(p) in listing
    # writing in one worktree does not touch the main tree
    (p / "only_here.txt").write_text("hi", encoding="utf-8")
    assert not (repo / "only_here.txt").exists()


def test_remove_cleans_up(repo: Path) -> None:
    p = wt.create(repo, team_id="t1", member_id="tm-1")
    wt.remove(repo, p)
    listing = subprocess.run(["git", "worktree", "list"], cwd=repo,
                             capture_output=True, text=True).stdout
    assert str(p) not in listing
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/team/test_worktree.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# argus_skill/team/worktree.py
"""Per-teammate git worktree — the physical-isolation primitive that makes
concurrent teammates shared-nothing on the filesystem.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def path_for(repo_root: Path, team_id: str, member_id: str) -> Path:
    return Path(repo_root) / ".argus_team" / team_id / "wt" / member_id


def create(repo_root: Path, *, team_id: str, member_id: str, base_ref: str = "HEAD") -> Path:
    dest = path_for(repo_root, team_id, member_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    branch = f"argus-team/{team_id}/{member_id}"
    subprocess.run(
        ["git", "worktree", "add", "-B", branch, str(dest), base_ref],
        cwd=repo_root, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    return dest


def remove(repo_root: Path, path: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=repo_root, check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/team/test_worktree.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/worktree.py tests/team/test_worktree.py
git commit -m "feat(team): per-teammate git worktree isolation (M1)"
```

---

## Task 6: `tools/team.py` — agent-facing CLI + detached spawn

**Files:**
- Create: `argus_skill/tools/team.py`
- Test: `tests/tools/test_team_cli.py`

**Verbs:** `form --team-id --root --tasks <jsonl>`, `spawn --team-id --root --member-id --task-id --repo <repo> [--exec-cmd <cmd>]`, `status --team-id --root`, `wait --team-id --root [--timeout]`, `send/drain --root --member-id [--to --from --text]`, `claim --root --member-id`, `reassign --root [--ttl]`, `dissolve --root [--keep-worktrees]`.

**Spawn detail:** builds the teammate command (default `python -m argus_skill --objective <task.objective> --bounded`, cwd = the member worktree); `--exec-cmd` overrides for tests. Launches detached via `subprocess.Popen(..., start_new_session=True)`, records `{id,pid,worktree,task_id,status:"running",heartbeat_ts}` in roster, and claims the task on the board.

- [ ] **Step 1: Write the failing test**

```python
# tests/tools/test_team_cli.py
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "argus_skill.tools.team", *args],
                          cwd=cwd, capture_output=True, text=True)


def test_form_spawn_status_wait_dissolve(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(
        {"task_id": "a", "title": "A", "objective": "echo hello", "owns_paths": ["a/**"]}
    ) + "\n", encoding="utf-8")

    out = _run("form", "--root", str(root), "--tasks", str(tasks), cwd=tmp_path)
    assert out.returncode == 0, out.stderr

    # spawn a stub teammate that finishes quickly (no real codex)
    out = _run("spawn", "--root", str(root), "--team-id", "t1", "--member-id", "tm-1",
               "--task-id", "a", "--exec-cmd", "true", cwd=tmp_path)
    assert out.returncode == 0, out.stderr

    out = _run("status", "--root", str(root), cwd=tmp_path)
    assert out.returncode == 0
    status = json.loads(out.stdout)
    assert any(m["id"] == "tm-1" for m in status["members"])
    assert any(t["task_id"] == "a" for t in status["tasks"])


def test_send_and_drain_cli(tmp_path: Path) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _run("send", "--root", str(root), "--to", "tm-1", "--from", "lead",
         "--text", "ping", cwd=tmp_path)
    out = _run("drain", "--root", str(root), "--member-id", "tm-1", cwd=tmp_path)
    msgs = json.loads(out.stdout)
    assert msgs[0]["text"] == "ping"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/tools/test_team_cli.py -q`
Expected: FAIL — module `argus_skill.tools.team` has no `__main__` / not found

- [ ] **Step 3: Write minimal implementation**

```python
# argus_skill/tools/team.py
"""Agent-facing CLI for Argus Agent Teams (the lead/teammates call this).

Thin wrapper over argus_skill.team.{task_board,mailbox,roster,worktree}.
Mirrors tools/subagent.py's CLI shape. Spawn launches a teammate as a
detached bounded Argus mission inside a private worktree; tests override
the launched command with --exec-cmd.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ..team import mailbox, roster, task_board, worktree


def _load_tasks(path: Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def cmd_form(a: argparse.Namespace) -> int:
    task_board.form(Path(a.root), _load_tasks(Path(a.tasks)))
    return 0


def cmd_spawn(a: argparse.Namespace) -> int:
    root = Path(a.root)
    snap = {t["task_id"]: t for t in task_board.snapshot(root)}
    task = snap.get(a.task_id, {})
    cwd = Path.cwd()
    if a.repo:
        try:
            cwd = worktree.create(Path(a.repo), team_id=a.team_id, member_id=a.member_id)
        except Exception as exc:  # worktree optional in stub/test mode
            print(f"team: worktree skipped: {exc}", file=sys.stderr)
    if a.exec_cmd:
        argv = shlex.split(a.exec_cmd)
    else:
        argv = [sys.executable, "-m", "argus_skill",
                "--objective", task.get("objective", ""), "--bounded"]
    proc = subprocess.Popen(argv, cwd=str(cwd), start_new_session=True)
    now = time.time()
    task_board.claim(root, a.member_id, now=now)  # best-effort claim of next pending
    roster.add_member(root, {
        "id": a.member_id, "pid": proc.pid, "worktree": str(cwd),
        "task_id": a.task_id, "status": "running", "heartbeat_ts": now,
    })
    print(json.dumps({"member_id": a.member_id, "pid": proc.pid}))
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    root = Path(a.root)
    print(json.dumps({
        "roster": roster.load(root),
        "members": roster.members(root),
        "tasks": task_board.snapshot(root),
    }, ensure_ascii=False))
    return 0


def cmd_wait(a: argparse.Namespace) -> int:
    root = Path(a.root)
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        if task_board.all_done(root):
            print(json.dumps({"done": True}))
            return 0
        time.sleep(a.poll)
    print(json.dumps({"done": task_board.all_done(root)}))
    return 0


def cmd_send(a: argparse.Namespace) -> int:
    mailbox.send(Path(a.root), to=a.to, frm=getattr(a, "from"), text=a.text, now=time.time())
    return 0


def cmd_drain(a: argparse.Namespace) -> int:
    print(json.dumps(mailbox.drain(Path(a.root), a.member_id), ensure_ascii=False))
    return 0


def cmd_claim(a: argparse.Namespace) -> int:
    got = task_board.claim(Path(a.root), a.member_id, now=time.time())
    print(json.dumps(got, ensure_ascii=False))
    return 0


def cmd_reassign(a: argparse.Namespace) -> int:
    ids = task_board.reassign_stale(Path(a.root), ttl=a.ttl, now=time.time())
    print(json.dumps({"reassigned": ids}))
    return 0


def cmd_dissolve(a: argparse.Namespace) -> int:
    roster.set_state(Path(a.root), "dissolved")
    if not a.keep_worktrees and a.repo:
        for m in roster.members(Path(a.root)):
            wt = m.get("worktree")
            if wt:
                worktree.remove(Path(a.repo), Path(wt))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="argus_skill.tools.team")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("form"); f.add_argument("--root", required=True)
    f.add_argument("--team-id", default=""); f.add_argument("--tasks", required=True)
    f.set_defaults(fn=cmd_form)

    s = sub.add_parser("spawn"); s.add_argument("--root", required=True)
    s.add_argument("--team-id", required=True); s.add_argument("--member-id", required=True)
    s.add_argument("--task-id", required=True); s.add_argument("--repo", default="")
    s.add_argument("--exec-cmd", default=""); s.set_defaults(fn=cmd_spawn)

    st = sub.add_parser("status"); st.add_argument("--root", required=True)
    st.set_defaults(fn=cmd_status)

    w = sub.add_parser("wait"); w.add_argument("--root", required=True)
    w.add_argument("--timeout", type=float, default=3600.0)
    w.add_argument("--poll", type=float, default=2.0); w.set_defaults(fn=cmd_wait)

    sd = sub.add_parser("send"); sd.add_argument("--root", required=True)
    sd.add_argument("--to", required=True); sd.add_argument("--from", required=True)
    sd.add_argument("--text", required=True); sd.set_defaults(fn=cmd_send)

    dr = sub.add_parser("drain"); dr.add_argument("--root", required=True)
    dr.add_argument("--member-id", required=True); dr.set_defaults(fn=cmd_drain)

    cl = sub.add_parser("claim"); cl.add_argument("--root", required=True)
    cl.add_argument("--member-id", required=True); cl.set_defaults(fn=cmd_claim)

    ra = sub.add_parser("reassign"); ra.add_argument("--root", required=True)
    ra.add_argument("--ttl", type=float, default=120.0); ra.set_defaults(fn=cmd_reassign)

    ds = sub.add_parser("dissolve"); ds.add_argument("--root", required=True)
    ds.add_argument("--repo", default=""); ds.add_argument("--keep-worktrees", action="store_true")
    ds.set_defaults(fn=cmd_dissolve)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/tools/test_team_cli.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add argus_skill/tools/team.py tests/tools/test_team_cli.py
git commit -m "feat(team): agent-facing team CLI with detached teammate spawn (M1)"
```

---

## Task 7: subagent — scope discussion-block to a lane (deadlock fix)

**Context:** `tools/subagent.py::_open_discussion_blockers` (≈:1231) makes any one parked supervised subagent block **all** new submits (enforced at submit ≈:2035–2041). A team of N supervised teammates deadlocks the instant one parks. Fix: tasks carry an optional `lane` (the team id); a parked task only blocks submits **in the same lane**. Tasks with no lane keep today's global behavior (back-compat).

**Files:**
- Modify: `argus_skill/tools/subagent.py` (`_open_discussion_blockers`, submit guard, task record write)
- Test: `tests/tools/test_subagent_lane_scope.py`

- [ ] **Step 1: Read the current code**

Run: `python - <<'PY'` to print the regions, or open the file at the two ranges:
`sed -n '1225,1260p;2025,2055p' argus_skill/tools/subagent.py` (read-only inspection step).

- [ ] **Step 2: Write the failing test**

```python
# tests/tools/test_subagent_lane_scope.py
from __future__ import annotations
import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def sa(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mod = importlib.import_module("argus_skill.tools.subagent")
    importlib.reload(mod)
    return mod


def _write_parked(mod, task_id: str, lane: str) -> None:
    # Minimal task record with an OPEN discussion in the given lane.
    rec = {"task_id": task_id, "status": "running", "lane": lane,
           "supervised": True, "discussion_open": True}
    mod._write_task(task_id, rec)              # type: ignore[attr-defined]


def test_parked_lane_blocks_same_lane_only(sa) -> None:
    _write_parked(sa, "t1-w1", lane="t1")
    # same lane -> blocked
    assert sa._open_discussion_blockers(lane="t1")      # non-empty
    # different lane -> not blocked
    assert sa._open_discussion_blockers(lane="t2") == []
    # legacy no-lane caller still sees the blocker (global back-compat)
    assert sa._open_discussion_blockers(lane=None)
```

> The exact field used to mark "open discussion" must match the current code (`_discussion_path(task_id)` existence, per subagent.py:431). In Step 4 adapt `_write_parked` to create the real discussion file the code checks, rather than a `discussion_open` flag, if that is how the current predicate works.

- [ ] **Step 3: Run to verify it fails**

Run: `python -m pytest tests/tools/test_subagent_lane_scope.py -q`
Expected: FAIL — `_open_discussion_blockers()` takes no `lane` kwarg.

- [ ] **Step 4: Implement the lane scoping**

In `_open_discussion_blockers`, add `lane: str | None = None` param. When `lane` is not None, only consider task records whose `lane` equals `lane`. When `lane is None`, keep scanning all (legacy). At the submit guard (≈:2041), pass the submitting task's lane (new `--lane` arg on `submit`, default `""` → treat as legacy global by passing `None`). Persist `lane` into the task record in the submit/`_write_task` path.

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/tools/test_subagent_lane_scope.py tests/tools/test_subagent_status.py tests/tools/test_subagent_supervisor.py -q`
Expected: PASS (no regressions in the existing two subagent test files)

- [ ] **Step 6: Commit**

```bash
git add argus_skill/tools/subagent.py tests/tools/test_subagent_lane_scope.py
git commit -m "fix(subagent): scope discussion-block to a lane so teams don't deadlock (M1)"
```

---

## Task 8: skill docs — lead role + teammate prompt contract (judgment layer)

**Files:**
- Create: `argus_skill/builtin_skills/engineer/agent-team-lead.md`
- Modify: `argus_skill/builtin_skills/engineer/argus-engineer-role.md` (add a short "forming a team" subsection that points to the lead skill and stresses *solo is the default; only form a team when subtasks are independent and own disjoint files*)

**Content for `agent-team-lead.md`** (no code; it is an instruction contract). Must cover, per spec §5.6–5.7:
1. When to form a team vs solo (independent, file-ownership-disjoint, separately-completable subtasks only).
2. How to split work and assign disjoint `owns_paths`.
3. How to construct each teammate's system prompt — MUST include: identity; task + `owns_paths` boundary; **"keep `<worktree>/TEAMMATE_STATUS.md` updated promptly — it is your continuity record"**; mailbox protocol; pass your own reviewer gate (layer 1); anti-fraud guardrails; GPU/`CUDA_VISIBLE_DEVICES` boundary; self-claim next task when idle.
4. The tool calls: `tools/team.py form` → `spawn ×N` → `wait` → read shards → synthesise → mission-level L2 reviewer (layer 2) → HANDOFF; `reassign` on stale; `dissolve` at the end.
5. Concurrency rules: shared-nothing work product; coordination single-writer-or-locked; only the lead writes the merged canonical artifact.

- [ ] **Step 1: Write `agent-team-lead.md`** with the five sections above (prose, headings, an explicit teammate-prompt template block).

- [ ] **Step 2: Add the "forming a team" subsection to `argus-engineer-role.md`** (≤12 lines; default solo, opt-in team, links to the lead skill).

- [ ] **Step 3: Sanity-check the docs are discoverable** — confirm they sit in `builtin_skills/engineer/` next to the other engineer skills:

Run: `ls argus_skill/builtin_skills/engineer/agent-team-lead.md && grep -c "TEAMMATE_STATUS.md" argus_skill/builtin_skills/engineer/agent-team-lead.md`
Expected: path prints; grep count ≥ 1

- [ ] **Step 4: Commit**

```bash
git add argus_skill/builtin_skills/engineer/agent-team-lead.md argus_skill/builtin_skills/engineer/argus-engineer-role.md
git commit -m "docs(team): lead role contract + teammate sys-prompt mandate (M1)"
```

---

## Task 9: full suite green + package wiring

**Files:**
- Create: `tests/team/__init__.py` (if the suite needs package dirs — match `tests/tools/` convention; only add if collection requires it)
- Verify: `argus_skill/team/` is importable as a package

- [ ] **Step 1: Run the whole team + tools suite**

Run: `python -m pytest tests/team tests/tools -q`
Expected: PASS (all team tests + existing tool tests)

- [ ] **Step 2: Run the broader suite to catch regressions**

Run: `python -m pytest -q -x`
Expected: PASS (or only pre-existing unrelated failures; if a failure is unrelated to team/subagent, note it but do not let team work introduce new failures)

- [ ] **Step 3: Lint the new modules** (match repo tooling if configured)

Run: `python -m pyflakes argus_skill/team argus_skill/tools/team.py` (or `ruff check` if present)
Expected: clean

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "test(team): M1 agent-teams core green end-to-end"
```

---

## Self-Review (against spec)

**Spec coverage:**
- §5.1 task_board → Task 2 ✓ · §5.2 mailbox → Task 3 ✓ · §5.3 roster → Task 4 ✓ · §5.4 worktree → Task 5 ✓ (GPU lease = M2, out of scope) · §5.5 tools/team.py incl. `wait` → Task 6 ✓ · §5.6/§5.7 lead + teammate prompt → Task 8 ✓ · §6 concurrency (shared-nothing + flock + single-writer) → Tasks 1,2,5 ✓ · §6 subagent deadlock → Task 7 ✓ · §8 two-layer acceptance → encoded in Task 8 lead contract (layer-1 teammate reviewer is the existing engineer loop; layer-2 is the existing mission reviewer — no harness change) ✓ · §9 continuity (living doc + roster) → Tasks 4 + 8 ✓.
- Out of scope by spec phasing: real GPU-lease scheduling (M2), supervisor-level concurrency / dashboard / heterogeneous runtimes (M3). The teammate spawn uses the existing bounded-mission CLI; wiring it to live codex end-to-end is exercised by `--exec-cmd` stubs in tests (a real run needs live codex+GPU, an integration smoke left for M2).

**Placeholder scan:** no TBD/TODO; every code step has complete code. Task 7 intentionally has a read-first step because it edits existing code whose exact predicate (`_discussion_path` existence vs a flag) must be matched at edit time — the test note flags the adaptation.

**Type consistency:** `root: Path` first positional across task_board/mailbox/roster; mailbox/roster use keyword-only args after `root`; `tools/team.py` passes `now=time.time()`. `claim(root, member_id, *, now)` signature consistent between Task 2 def and Task 6 caller.

**Known adaptation risk:** Task 7's `_write_task`/discussion predicate and the submit guard line numbers are approximate; the read-first step de-risks. Mailbox Task-3 NOTE: tests already use keyword args matching the keyword-only signature.

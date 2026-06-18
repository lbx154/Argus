# Argus Teams Rolling Pool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the team's per-batch `form→wait→synthesize→next-batch` barrier with a **rolling pool**: a dumb coordinator process keeps N teammates always in flight from a priority backlog the lead maintains, so slots refill the instant they free instead of waiting on the lead's reasoning turn.

**Architecture:** Producer/consumer. The **lead** (LLM) owns a priority backlog (the existing file-locked task board) and reads result shards to accept/reject — pure judgment. A **coordinator** (no LLM, a detached `team coordinate` loop) enforces the invariant "N teammates in flight": each tick it reassigns stale teammates, then claims the highest-priority pending tasks and spawns a fresh teammate on each until full. The coordinator is the sole spawner (eliminating the M1 claim-race), is scoped to one lead mission, and self-exits on drain / stale-lead-heartbeat / max-wall. A tiny `pool.json` control file (`{width,state,lead_heartbeat_ts}`) is the lead→coordinator channel.

**Tech Stack:** Python 3, stdlib only (`argparse`, `subprocess`, `threading`, `fcntl` via existing `team/_store.py`). Tests are `pytest` (`tmp_path`, `capsys`). Canonical checkout: `/data/yijia/argus-merge` (== `origin/main`).

**Spec:** `docs/superpowers/specs/2026-06-18-argus-teams-rolling-pool-design.md`

**Commit identity:** the repo is already configured as `waltstephen <1016013662@qq.com>`. End every commit body with the trailer:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

**Run all tests with:** `cd /data/yijia/argus-merge && python -m pytest <paths> -q`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `argus_skill/team/task_board.py` | shared work queue | **modify**: `priority` field, `claim_top`, `count_in_flight` |
| `argus_skill/team/roster.py` | team manifest + member identity | **modify**: `next_member_id` monotonic allocator |
| `argus_skill/team/pool.py` | coordinator control plane (`pool.json`) | **create** |
| `argus_skill/tools/team.py` | agent-facing CLI | **modify**: extract `_spawn_teammate`, add `refill_once`, `_should_stop`, `coordinate` + `pool-set` verbs |
| `argus_skill/builtin_skills/engineer/agent-team-lead.md` | lead contract (prose the LLM reads) | **modify**: rolling-pool run model |
| `argus_skill/builtin_skills/engineer/agent-research-benchmark-runner.md` | benchmark fan-out guidance | **modify**: one line |
| `tests/team/test_task_board.py`, `tests/team/test_roster.py`, `tests/team/test_pool.py`, `tests/tools/test_team_cli.py` | tests | **modify/create** |

Teammate body (`teammate_entry.py`), time-box, shards, two-layer acceptance, worktree: **unchanged, reused.**

---

## Task 1: task_board — `priority` + `claim_top` + `count_in_flight`

**Files:**
- Modify: `argus_skill/team/task_board.py`
- Test: `tests/team/test_task_board.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/team/test_task_board.py`:

```python
def test_form_stores_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 5},
    ])
    snap = {t["task_id"]: t for t in tb.snapshot(tmp_path)}
    assert snap["a"]["priority"] == 100   # default
    assert snap["b"]["priority"] == 5


def test_claim_top_orders_by_priority(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"], "priority": 100},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 5},
        {"task_id": "c", "objective": "z", "owns_paths": ["c/**"], "priority": 5},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "b"   # lowest number first
    assert tb.claim_top(tmp_path, "w2", now=2.0)["task_id"] == "c"   # tie -> task_id order
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "a"
    assert tb.claim_top(tmp_path, "w4", now=4.0) is None             # backlog empty


def test_claim_top_respects_deps(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"], "priority": 100},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"], "priority": 1, "deps": ["a"]},
    ])
    assert tb.claim_top(tmp_path, "w1", now=1.0)["task_id"] == "a"   # b blocked despite priority 1
    assert tb.claim_top(tmp_path, "w2", now=2.0) is None
    tb.complete(tmp_path, "a")
    assert tb.claim_top(tmp_path, "w3", now=3.0)["task_id"] == "b"


def test_count_in_flight(tmp_path: Path) -> None:
    tb.form(tmp_path, [
        {"task_id": "a", "objective": "x", "owns_paths": ["a/**"]},
        {"task_id": "b", "objective": "y", "owns_paths": ["b/**"]},
    ])
    assert tb.count_in_flight(tmp_path) == 0
    tb.claim_top(tmp_path, "w1", now=1.0)     # a -> claimed
    assert tb.count_in_flight(tmp_path) == 1
    tb.heartbeat(tmp_path, "a", now=1.0)       # claimed -> running
    assert tb.count_in_flight(tmp_path) == 1
    tb.complete(tmp_path, "a")                 # running -> done
    assert tb.count_in_flight(tmp_path) == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/team/test_task_board.py -q`
Expected: FAIL — `AttributeError: module 'argus_skill.team.task_board' has no attribute 'claim_top'` (and `KeyError: 'priority'`).

- [ ] **Step 3: Implement** — in `argus_skill/team/task_board.py`:

(3a) In `form()`, add a `priority` field to the task dict (right after `"attempts": 0,`):

```python
            "attempts": 0,
            "priority": int(spec.get("priority", 100)),
```

(3b) Add these two functions after `claim_specific` (before `heartbeat`):

```python
def claim_top(root: Path, member_id: str, *, now: float) -> dict[str, Any] | None:
    """Atomically claim the highest-priority pending task whose deps are all done.

    Like ``claim()`` but orders eligible tasks by ``priority`` (lower number =
    higher priority), tie-broken by ``task_id``. This is what the coordinator
    uses to pull the top of the lead's backlog.
    """
    with _store.locked(_lock(root)):
        tasks = _load_all(root)
        done = _done_ids(tasks)
        eligible = [
            t for t in tasks
            if t["state"] == "pending" and all(dep in done for dep in t["deps"])
        ]
        if not eligible:
            return None
        eligible.sort(key=lambda t: (int(t.get("priority", 100)), t["task_id"]))
        task = eligible[0]
        task["state"] = "claimed"
        task["owner"] = member_id
        task["claim_ts"] = now
        task["heartbeat_ts"] = now
        _store.atomic_write_json(_path(root, task["task_id"]), task)
        return task


def count_in_flight(root: Path) -> int:
    """Number of tasks currently claimed or running (occupying a pool slot)."""
    return sum(1 for t in _load_all(root) if t["state"] in ("claimed", "running"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/team/test_task_board.py -q`
Expected: PASS (all task_board tests, including the M1 ones).

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/task_board.py tests/team/test_task_board.py
git commit -m "feat(team): task_board priority + claim_top + count_in_flight

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: roster — `next_member_id` monotonic allocator

**Files:**
- Modify: `argus_skill/team/roster.py`
- Test: `tests/team/test_roster.py`

- [ ] **Step 1: Write the failing test** — append to `tests/team/test_roster.py`:

```python
def test_next_member_id_monotonic_unique(tmp_path: Path) -> None:
    rs.create(tmp_path, team_id="t1", mission="m", lead="lead", now=1.0)
    ids = [rs.next_member_id(tmp_path, prefix="w") for _ in range(3)]
    assert ids == ["w1", "w2", "w3"]                 # monotonic, unique
    # works even without create() (fresh roster)
    assert rs.next_member_id(tmp_path / "fresh", prefix="k") == "k1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/team/test_roster.py -q`
Expected: FAIL — `AttributeError: module 'argus_skill.team.roster' has no attribute 'next_member_id'`.

- [ ] **Step 3: Implement** — add to `argus_skill/team/roster.py` (after `add_member`):

```python
def next_member_id(root: Path, *, prefix: str = "w") -> str:
    """Atomically allocate a unique, monotonic member id like ``w1``, ``w2``.

    The coordinator calls this for every teammate it spawns so ids never
    collide across the campaign. Works even if ``create()`` was never called.
    """
    with _store.locked(_lock(root)):
        doc = load(root)
        seq = int(doc.get("member_seq", 0)) + 1
        doc["member_seq"] = seq
        _store.atomic_write_json(_path(root), doc)
        return f"{prefix}{seq}"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/team/test_roster.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/roster.py tests/team/test_roster.py
git commit -m "feat(team): roster.next_member_id monotonic id allocator

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: team/pool.py — coordinator control plane

**Files:**
- Create: `argus_skill/team/pool.py`
- Test: `tests/team/test_pool.py`

- [ ] **Step 1: Write the failing test** — create `tests/team/test_pool.py`:

```python
from __future__ import annotations

from pathlib import Path

from argus_skill.team import pool


def test_read_default_when_missing(tmp_path: Path) -> None:
    assert pool.read(tmp_path) == {"width": 0, "state": "running", "lead_heartbeat_ts": 0.0}


def test_update_merges_and_stamps_heartbeat(tmp_path: Path) -> None:
    pool.update(tmp_path, width=8, state="running", now=10.0)
    p = pool.read(tmp_path)
    assert p["width"] == 8 and p["state"] == "running" and p["lead_heartbeat_ts"] == 10.0
    # partial update keeps width, flips state, refreshes heartbeat
    pool.update(tmp_path, state="draining", now=20.0)
    p = pool.read(tmp_path)
    assert p["width"] == 8 and p["state"] == "draining" and p["lead_heartbeat_ts"] == 20.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/team/test_pool.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'argus_skill.team.pool'`.

- [ ] **Step 3: Implement** — create `argus_skill/team/pool.py`:

```python
"""Coordinator control plane: a tiny shared file the lead writes and the
coordinator reads each tick.

``width`` is the target in-flight teammate count, ``state`` is
``running``/``draining``, and every ``update`` refreshes ``lead_heartbeat_ts``
so the coordinator can detect a dead lead and never orphan-spawn.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _store

_DEFAULT: dict[str, Any] = {"width": 0, "state": "running", "lead_heartbeat_ts": 0.0}


def _path(root: Path) -> Path:
    return Path(root) / "pool.json"


def _lock(root: Path) -> Path:
    return Path(root) / ".pool.lock"


def read(root: Path) -> dict[str, Any]:
    doc = _store.read_json(_path(root), default=None)
    merged = dict(_DEFAULT)
    if isinstance(doc, dict):
        merged.update(doc)
    return merged


def update(root: Path, *, width: int | None = None, state: str | None = None,
           now: float) -> dict[str, Any]:
    """Merge-write the control file; always refresh the lead heartbeat."""
    with _store.locked(_lock(root)):
        doc = read(root)
        if width is not None:
            doc["width"] = int(width)
        if state is not None:
            doc["state"] = state
        doc["lead_heartbeat_ts"] = now
        _store.atomic_write_json(_path(root), doc)
        return doc
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/team/test_pool.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add argus_skill/team/pool.py tests/team/test_pool.py
git commit -m "feat(team): pool.py coordinator control plane (width/state/heartbeat)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: tools/team.py — extract `_spawn_teammate`, add `refill_once` + `_should_stop`

**Files:**
- Modify: `argus_skill/tools/team.py:21` (import) and `cmd_spawn` (refactor) + new functions
- Test: `tests/tools/test_team_cli.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/tools/test_team_cli.py`:

```python
def test_refill_once_caps_and_idempotent(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tb.form(root, [{"task_id": f"k{i}", "objective": "opt", "owns_paths": [f"k{i}/**"]}
                   for i in range(5)])
    calls = []
    def fake_spawn(r, *, member_id, task_id, cwd, exec_cmd=""):
        calls.append((member_id, task_id)); return 4242
    res = teamcli.refill_once(root, width=3, cwd=tmp_path, ttl=180.0, now=1.0, spawn_fn=fake_spawn)
    assert len(res["spawned"]) == 3 and len(calls) == 3
    assert tb.count_in_flight(root) == 3
    res2 = teamcli.refill_once(root, width=3, cwd=tmp_path, ttl=180.0, now=2.0, spawn_fn=fake_spawn)
    assert res2["spawned"] == [] and len(calls) == 3       # idempotent when full


def test_refill_once_drains_short_backlog(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tb.form(root, [{"task_id": "k0", "objective": "opt", "owns_paths": ["k0/**"]},
                   {"task_id": "k1", "objective": "opt", "owns_paths": ["k1/**"]}])
    res = teamcli.refill_once(root, width=10, cwd=tmp_path, ttl=180.0, now=1.0,
                              spawn_fn=lambda r, **k: 1)
    assert len(res["spawned"]) == 2                          # only 2 tasks exist


def test_refill_once_reassigns_then_fills(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tb.form(root, [{"task_id": "k0", "objective": "opt", "owns_paths": ["k0/**"]}])
    tb.claim_top(root, "w0", now=1.0)                        # k0 claimed by a (now dead) teammate
    tb.heartbeat(root, "k0", now=1.0)
    # ttl small -> k0 is stale; refill should reassign it and re-spawn
    res = teamcli.refill_once(root, width=1, cwd=tmp_path, ttl=0.0, now=100.0,
                              spawn_fn=lambda r, **k: 1)
    assert res["reassigned"] == ["k0"] and len(res["spawned"]) == 1


def test_should_stop_conditions(tmp_path: Path) -> None:
    from argus_skill.tools import team as teamcli
    run = {"state": "running", "lead_heartbeat_ts": 100.0}
    drn = {"state": "draining", "lead_heartbeat_ts": 100.0}
    assert teamcli._should_stop(run, in_flight=3, elapsed=10, lead_ttl=300, max_wall=1000, now=110) is None
    assert teamcli._should_stop(drn, in_flight=0, elapsed=10, lead_ttl=300, max_wall=1000, now=110) == "drained"
    assert teamcli._should_stop(drn, in_flight=2, elapsed=10, lead_ttl=300, max_wall=1000, now=110) is None
    assert teamcli._should_stop(run, in_flight=3, elapsed=10, lead_ttl=300, max_wall=1000, now=500) == "lead-heartbeat-stale"
    assert teamcli._should_stop(run, in_flight=3, elapsed=2000, lead_ttl=300, max_wall=1000, now=110) == "max-wall"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/tools/test_team_cli.py -q`
Expected: FAIL — `AttributeError: module 'argus_skill.tools.team' has no attribute 'refill_once'`.

- [ ] **Step 3: Implement** — in `argus_skill/tools/team.py`:

(3a) Extend the team import at line 21 to include `pool`:

```python
from ..team import mailbox, pool, roster, task_board, worktree
```

(3b) Add `_spawn_teammate` (extracted launch logic) just above `cmd_spawn`:

```python
def _spawn_teammate(root: Path, *, member_id: str, task_id: str, cwd: Path,
                    exec_cmd: str = "") -> int:
    """Launch ONE detached headless teammate on ``task_id``, record it on the
    roster, return its pid. Claiming is the caller's job (cmd_spawn uses
    claim_specific; the coordinator uses claim_top)."""
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (member_id.replace(":", "_") + ".spawn.log")
    if exec_cmd:
        argv = shlex.split(exec_cmd)
    else:
        argv = [sys.executable, "-m", "argus_skill.team.teammate_entry",
                "--root", str(root), "--member-id", member_id,
                "--task-id", task_id, "--cwd", str(cwd)]
    with open(log_path, "ab") as log, open(os.devnull, "rb") as devnull:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=devnull, stdout=log,
                                stderr=log, start_new_session=True)
    roster.add_member(root, {
        "id": member_id, "pid": proc.pid, "worktree": str(cwd),
        "task_id": task_id, "status": "running", "heartbeat_ts": time.time(),
    })
    return proc.pid
```

(3c) Replace the body of `cmd_spawn` (from the `claimed = ...` line through the `print(...)`/`return 0`) so it reuses `_spawn_teammate`. The full new `cmd_spawn` is:

```python
def cmd_spawn(a: argparse.Namespace) -> int:
    root = Path(a.root)
    cwd = Path(a.cwd) if a.cwd else Path.cwd()
    if a.worktree and a.repo:
        try:
            cwd = worktree.create(Path(a.repo), team_id=a.team_id, member_id=a.member_id)
        except Exception as exc:  # worktree optional
            print(f"team: worktree skipped: {exc}", file=sys.stderr)
    now = time.time()
    claimed = task_board.claim_specific(root, a.task_id, a.member_id, now=now)
    task_id = claimed["task_id"] if claimed else a.task_id
    pid = _spawn_teammate(root, member_id=a.member_id, task_id=task_id,
                          cwd=cwd, exec_cmd=a.exec_cmd)
    print(json.dumps({"member_id": a.member_id, "pid": pid,
                      "task_id": task_id, "claimed": bool(claimed)}))
    return 0
```

(3d) Add `refill_once` and `_should_stop` after `cmd_spawn`:

```python
def refill_once(root: Path, *, width: int, cwd: Path, member_prefix: str = "w",
                ttl: float, now: float, exec_cmd: str = "", spawn_fn=None) -> dict:
    """Top the in-flight teammate count back up to ``width`` from the backlog.

    Reassign stale (dead) teammates' tasks first, then claim the highest-priority
    pending tasks and spawn a fresh teammate on each until the pool is full or the
    backlog is empty. Idempotent: a second call with the pool already full spawns
    nothing. ``spawn_fn`` is injectable for tests.
    """
    spawn_fn = spawn_fn or _spawn_teammate
    reassigned = task_board.reassign_stale(root, ttl=ttl, now=now)
    in_flight = task_board.count_in_flight(root)
    free = max(0, width - in_flight)
    spawned: list[dict] = []
    for _ in range(free):
        mid = roster.next_member_id(root, prefix=member_prefix)
        task = task_board.claim_top(root, mid, now=now)
        if task is None:
            break  # backlog empty
        spawn_fn(root, member_id=mid, task_id=task["task_id"], cwd=cwd, exec_cmd=exec_cmd)
        spawned.append({"member_id": mid, "task_id": task["task_id"]})
    return {"spawned": spawned, "in_flight": in_flight, "free": free,
            "reassigned": reassigned}


def _should_stop(pool_doc: dict, *, in_flight: int, elapsed: float,
                 lead_ttl: float, max_wall: float, now: float) -> str | None:
    """Return a stop reason, or None to keep coordinating. Never orphan-spawn."""
    if pool_doc.get("state") == "draining" and in_flight == 0:
        return "drained"
    hb = float(pool_doc.get("lead_heartbeat_ts", 0.0))
    if hb > 0 and now - hb > lead_ttl:
        return "lead-heartbeat-stale"
    if elapsed > max_wall:
        return "max-wall"
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/tools/test_team_cli.py -q`
Expected: PASS (new tests + the M1 spawn/claim tests still green, since `cmd_spawn` behavior is unchanged).

- [ ] **Step 5: Commit**

```bash
git add argus_skill/tools/team.py tests/tools/test_team_cli.py
git commit -m "feat(team): refill_once + _should_stop + _spawn_teammate extraction

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: tools/team.py — `coordinate` + `pool-set` CLI verbs

**Files:**
- Modify: `argus_skill/tools/team.py` (add `cmd_coordinate`, `cmd_pool_set`, parser entries)
- Test: `tests/tools/test_team_cli.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/tools/test_team_cli.py`:

```python
def test_pool_set_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / "t"
    rc, out = _call(capsys, "pool-set", "--root", str(root), "--width", "6", "--state", "running")
    doc = json.loads(out)
    assert rc == 0
    assert doc["width"] == 6 and doc["state"] == "running" and doc["lead_heartbeat_ts"] > 0


def test_coordinate_once_fills_to_width(tmp_path: Path, capsys) -> None:
    from argus_skill.team import task_board as tb
    root = tmp_path / "t"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text("".join(
        json.dumps({"task_id": f"k{i}", "objective": "opt", "owns_paths": [f"k{i}/**"]}) + "\n"
        for i in range(3)), encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--team-id", "t", "--tasks", str(tasks))
    # one tick, width 2, stub teammate that exits immediately (no real codex)
    rc, out = _call(capsys, "coordinate", "--root", str(root), "--team-id", "t",
                    "--cwd", str(tmp_path), "--width", "2", "--once", "--exec-cmd", "true")
    res = json.loads(out)
    assert rc == 0 and res["stopped"] == "once" and len(res["spawned"]) == 2
    assert sorted(s["member_id"] for s in res["spawned"]) == ["w1", "w2"]
    assert tb.count_in_flight(root) == 2          # the 2 claimed tasks occupy the pool


def test_coordinate_draining_does_not_spawn(tmp_path: Path, capsys) -> None:
    root = tmp_path / "t"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps({"task_id": "k0", "objective": "opt", "owns_paths": ["k0/**"]}) + "\n",
                     encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--team-id", "t", "--tasks", str(tasks))
    _call(capsys, "pool-set", "--root", str(root), "--state", "draining")
    # draining + nothing in flight -> stop immediately, spawn nothing
    rc, out = _call(capsys, "coordinate", "--root", str(root), "--team-id", "t",
                    "--cwd", str(tmp_path), "--width", "4", "--exec-cmd", "true")
    res = json.loads(out)
    assert rc == 0 and res["stopped"] == "drained"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/tools/test_team_cli.py -k "coordinate or pool_set" -q`
Expected: FAIL — `argparse` error / `invalid choice: 'coordinate'` (verbs not registered).

- [ ] **Step 3: Implement** — in `argus_skill/tools/team.py`:

(3a) Add the two command handlers after `cmd_dissolve`:

```python
def cmd_pool_set(a: argparse.Namespace) -> int:
    doc = pool.update(Path(a.root),
                      width=a.width if a.width is not None else None,
                      state=a.state or None, now=time.time())
    print(json.dumps(doc, ensure_ascii=False))
    return 0


def cmd_coordinate(a: argparse.Namespace) -> int:
    root = Path(a.root)
    cwd = Path(a.cwd) if a.cwd else Path.cwd()
    start = time.time()
    while True:
        now = time.time()
        p = pool.read(root)
        state = p.get("state", "running")
        # during draining, target width 0 so the pool empties instead of refilling
        width = 0 if state == "draining" else int(p.get("width") or a.width)
        in_flight = task_board.count_in_flight(root)
        reason = _should_stop(p, in_flight=in_flight, elapsed=now - start,
                              lead_ttl=a.lead_ttl, max_wall=a.max_wall, now=now)
        if reason:
            print(json.dumps({"stopped": reason, "in_flight": in_flight}))
            return 0
        res = refill_once(root, width=width, cwd=cwd, member_prefix=a.member_prefix,
                          ttl=a.ttl, now=now, exec_cmd=a.exec_cmd)
        if a.once:
            print(json.dumps({"stopped": "once", **res}))
            return 0
        time.sleep(a.poll)
```

(3b) Register both parsers inside `_build_parser` (after the `ds` / dissolve block, before `return p`):

```python
    co = sub.add_parser("coordinate", help="rolling pool: keep N teammates in flight until drained")
    co.add_argument("--root", required=True)
    co.add_argument("--team-id", default="")          # accepted for symmetry/logging
    co.add_argument("--cwd", default="")
    co.add_argument("--width", type=int, default=8)
    co.add_argument("--poll", type=float, default=5.0)
    co.add_argument("--ttl", type=float, default=180.0)         # teammate heartbeat stale
    co.add_argument("--lead-ttl", type=float, default=300.0)    # lead heartbeat stale
    co.add_argument("--max-wall", type=float, default=21600.0)  # 6h backstop
    co.add_argument("--member-prefix", default="w")
    co.add_argument("--once", action="store_true", help="run a single refill tick and exit")
    co.add_argument("--exec-cmd", default="")                   # test stub
    co.set_defaults(fn=cmd_coordinate)

    ps = sub.add_parser("pool-set", help="set coordinator width/state (+ lead heartbeat)")
    ps.add_argument("--root", required=True)
    ps.add_argument("--width", type=int, default=None)
    ps.add_argument("--state", default="", choices=["", "running", "draining"])
    ps.set_defaults(fn=cmd_pool_set)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/tools/test_team_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Run the FULL team suite (regression)**

Run: `python -m pytest tests/team tests/tools/test_team_cli.py -q`
Expected: PASS — all M1 + M2 team tests green.

- [ ] **Step 6: Commit**

```bash
git add argus_skill/tools/team.py tests/tools/test_team_cli.py
git commit -m "feat(team): coordinate (rolling pool loop) + pool-set CLI verbs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: skill docs — make the rolling pool the lead's default run model

**Files:**
- Modify: `argus_skill/builtin_skills/engineer/agent-team-lead.md` (the "How to run the team" section + two anti-pattern lines)
- Modify: `argus_skill/builtin_skills/engineer/agent-research-benchmark-runner.md` (one line)

No unit test (prose the LLM reads). Verification is a grep at the end.

- [ ] **Step 1: Replace the "How to run the team (tool calls)" section** in `agent-team-lead.md` (currently lines 30–37, the `## How to run the team (tool calls)` heading through the `dissolve` bullet) with:

````markdown
## How to run the team (rolling pool)
Use `python -m argus_skill.tools.team`. The model is a **rolling pool**, not a batch: a dumb **coordinator** keeps N teammates always in flight from a priority backlog you maintain. You never `spawn` teammates yourself and you never `wait` on a whole batch — that idle seam is exactly what this avoids.
1. `form --root <team_root> --team-id <tid> --mission "<objective>" --tasks tasks.jsonl` — write the initial backlog + roster. One JSON object per line: `{task_id, title, objective, owns_paths, deps?, priority?}` (lower `priority` = pulled first; default 100). **Lane-prefix `task_id`s as `<tid>::<name>`** so any subagent a teammate spawns stays lane-scoped. Bake the full teammate contract (below) into each task's `objective` — that text is what the teammate runs.
2. Launch ONE coordinator, detached:
   `nohup python -m argus_skill.tools.team coordinate --root <team_root> --team-id <tid> --cwd <workspace> --width <N> --poll 5 --ttl 180 --lead-ttl 300 --max-wall 21600 >coordinator.log 2>&1 &`
   It maintains exactly N teammates in flight (claims top-priority pending + spawns a fresh `w<k>` on each) and reassigns any stale teammate — on its own clock, independent of your reasoning.
3. Enter your **judgment loop** (you do NOT spawn and do NOT `wait` a barrier):
   - `pool-set --root <team_root> --width <N> --state running` each pass — sets the pool width AND beats your **lead heartbeat**. Keep doing this; if your heartbeat lapses past `--lead-ttl` the coordinator assumes you died and stops (so it never orphan-spawns).
   - Read newly-landed `shards/*.jsonl`; for each candidate compare its **MEASURED** metric against the current best and record only real improvements into your canonical artifact (you are its only writer).
   - Keep the backlog stocked with `form`: **breadth** (new untouched targets) and/or **depth** (re-`form` a promising target with a "try a new mechanism" objective at a lower `priority`).
   - Tune `--width` via `pool-set` if the route is saturated or idle.
4. Wind down: `pool-set --state draining` (keep beating the heartbeat while it drains) → the coordinator stops spawning and exits once nothing is in flight → synthesize the final canonical artifact → mission L2 reviewer → `dissolve --root <team_root> --repo <repo>`.
````

- [ ] **Step 2: Update the two stale anti-patterns** in `agent-team-lead.md`. Replace the line:

```
- Busy-polling instead of `wait`; leaving a stalled teammate's task claimed forever instead of `reassign`.
```

with:

```
- Spawning teammates yourself or `wait`-ing on a whole batch instead of running the coordinator + judgment loop; letting your lead heartbeat lapse (via missed `pool-set`) so the coordinator stops mid-campaign.
```

- [ ] **Step 3: Update the benchmark-runner doc.** In `argus_skill/builtin_skills/engineer/agent-research-benchmark-runner.md`, find the sentence describing the many-independent-target fan-out (the per-task subagents / teammate-engineer team line added in M1) and append:

```
For a many-target optimization benchmark, the DEFAULT is the **rolling teammate pool** (Agent Team Lead → "How to run the team (rolling pool)"): one coordinator keeps N kernel-engineers always in flight from a priority backlog; do not use per-task subagents for the cross-target fan-out, and do not run teams as fixed `wait`-ed batches.
```

- [ ] **Step 4: Verify the docs are consistent**

Run: `grep -n "coordinate\|rolling pool\|pool-set" argus_skill/builtin_skills/engineer/agent-team-lead.md`
Expected: matches present. And confirm the old barrier is gone from the lead's default path:
Run: `grep -n "wait --root" argus_skill/builtin_skills/engineer/agent-team-lead.md`
Expected: no match in the "How to run" section (the `wait` verb still exists in the CLI for fallback/tests, just not taught as the default).

- [ ] **Step 5: Commit**

```bash
git add argus_skill/builtin_skills/engineer/agent-team-lead.md argus_skill/builtin_skills/engineer/agent-research-benchmark-runner.md
git commit -m "skill(team): teach the rolling pool (coordinator + judgment loop) as the lead default

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Deploy to the live SOL daemon and restart

**This task touches the running daemon — do it with the user watching, not unattended.** No unit tests; verification is observing the live coordinator. The daemon project life dir is `/home/yifanyang/.argus-skill/projects/38e2f96b8ae8/`; the daemon checkout is `/data/yijia/argus-skill`; the SOL workspace + AGENTS.md is `/data/yijia/sol-execbench-argus/`.

- [ ] **Step 1: Full regression green in the canonical checkout**

Run: `cd /data/yijia/argus-merge && python -m pytest tests/team tests/tools/test_team_cli.py -q`
Expected: PASS. Do not proceed otherwise.

- [ ] **Step 2: Push the canonical branch to `main`**

```bash
cd /data/yijia/argus-merge
git log --oneline origin/main..HEAD          # sanity: only the M2 commits
git push origin HEAD:main
```
Expected: `main` advances by the Task 1–6 commits.

- [ ] **Step 3: Sync the daemon checkout to the new main**

```bash
cd /data/yijia/argus-skill
git stash list; git status --porcelain        # inspect the 2 dirty files first
git fetch origin main:refs/remotes/origin/main
git stash -u && git merge --ff-only origin/main || git rebase origin/main
```
Reconcile the 2 dirty files (the prior live-deployed `teammate_entry.py` time-box) — they should now be identical to committed `main`; if so, drop the stash. Confirm `python -c "import argus_skill.team.pool, argus_skill.tools.team as t; print(hasattr(t,'refill_once'))"` prints `True` against the daemon's interpreter (`/home/yifanyang/miniconda3/bin/python`).

- [ ] **Step 4: Rewrite the daemon objective + AGENTS.md to the rolling-pool contract**

Update `/tmp/argus_obj.txt` and the relevant section of `/data/yijia/sol-execbench-argus/AGENTS.md` so the LEAD instruction reads (per spec §9): default to the **rolling teammate pool** — `form` an initial backlog, launch ONE `team coordinate --width 8 ...`, then loop `pool-set` (heartbeat) + read shards (record only MEASURED-beats-best) + restock backlog (breadth/depth) + tune width; **never per-task subagents for fan-out, never `wait`-ed batches**. Keep all existing constraints verbatim (B200 cards 2,3,4,5 only; new-mechanism-only; never report an unmeasured SOL).

- [ ] **Step 5: Restart the daemon on SOL-ExecBench**

Stop the current daemon cleanly, then relaunch via the existing `/tmp/argus_relaunch_sol.sh` (caps=99999, the new objective). Confirm exactly one `--daemon-fg` process with the precise filter `[m]iniconda3/bin/python .*/bin/argus-skill --daemon-fg`.

- [ ] **Step 6: Verify the rolling pool is live**

- A `team coordinate` process is running (precise filter `[m]iniconda3/bin/.* -m argus_skill.tools.team coordinate`).
- `python -m argus_skill.tools.team status --root <active_team_root>` shows ~N tasks `claimed`/`running`.
- Watch one teammate finish (its task → `done`) and confirm a NEW `w<k>` member appears within ~`poll`+heartbeat seconds (slot refilled without a batch barrier).
- Over ~30 min: touched/scored kernel counts and mean SOL climb; GPUs cards 2–5 still the non-bottleneck.

- [ ] **Step 7: Record outcome** — note the new mean SOL / coverage and that the coordinator held N in flight, to the user.

---

## Self-Review (filled in by the plan author)

**Spec coverage:** task_board `priority`/`claim_top`/`count_in_flight` → Task 1 ✓. `roster.next_member_id` → Task 2 ✓. `pool.py` control plane → Task 3 ✓. `refill_once` + sole-spawner `_spawn_teammate` → Task 4 ✓. `coordinate` loop (drain/stale-lead/max-wall termination) + `pool-set` → Task 5 ✓. Objective/skill rewrite → Task 6 (+ deploy-time objective in Task 4-step-of-Task-7) ✓. Lifecycle & safety (sole spawner, orphan protection, draining→width 0) → Tasks 4–5 ✓. Rollout → Task 7 ✓.

**Placeholder scan:** none — every code/test step shows full content; the only `<...>` are CLI argument placeholders inside documentation prose (intended).

**Type/name consistency:** `claim_top`, `count_in_flight`, `next_member_id`, `pool.read`, `pool.update`, `refill_once(spawn_fn=...)`, `_spawn_teammate(root, *, member_id, task_id, cwd, exec_cmd)`, `_should_stop(pool_doc, *, in_flight, elapsed, lead_ttl, max_wall, now)`, verbs `coordinate`/`pool-set` — names match across tasks and the stub `fake_spawn` signature matches `_spawn_teammate`'s keyword args.

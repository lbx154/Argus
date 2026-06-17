from __future__ import annotations

import json
from pathlib import Path

from argus_skill.tools import team


def _call(capsys, *args: str) -> tuple[int, str]:
    rc = team.main(list(args))
    return rc, capsys.readouterr().out


def test_form_spawn_status_dissolve(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps(
        {"task_id": "a", "title": "A", "objective": "echo hello", "owns_paths": ["a/**"]}
    ) + "\n", encoding="utf-8")

    rc, _ = _call(capsys, "form", "--root", str(root), "--team-id", "t1", "--tasks", str(tasks))
    assert rc == 0

    # spawn a stub teammate that exits immediately (no real codex)
    rc, out = _call(capsys, "spawn", "--root", str(root), "--team-id", "t1",
                    "--member-id", "tm-1", "--task-id", "a", "--exec-cmd", "true")
    assert rc == 0
    assert json.loads(out)["task_id"] == "a"

    rc, out = _call(capsys, "status", "--root", str(root))
    status = json.loads(out)
    assert any(m["id"] == "tm-1" for m in status["members"])
    assert any(t["task_id"] == "a" for t in status["tasks"])

    rc, _ = _call(capsys, "dissolve", "--root", str(root))
    assert rc == 0


def test_send_and_drain_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    _call(capsys, "send", "--root", str(root), "--to", "tm-1", "--from", "lead", "--text", "ping")
    rc, out = _call(capsys, "drain", "--root", str(root), "--member-id", "tm-1")
    msgs = json.loads(out)
    assert msgs[0]["text"] == "ping" and msgs[0]["from"] == "lead"


def test_claim_and_reassign_cli(tmp_path: Path, capsys) -> None:
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(json.dumps({"task_id": "k0", "objective": "opt"}) + "\n", encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--tasks", str(tasks))
    rc, out = _call(capsys, "claim", "--root", str(root), "--member-id", "tm-1")
    assert json.loads(out)["task_id"] == "k0"
    # ttl=-1 -> any claimed task is stale regardless of sub-second timing
    rc, out = _call(capsys, "reassign", "--root", str(root), "--ttl", "-1")
    assert json.loads(out)["reassigned"] == ["k0"]


def test_spawn_claims_specific_task_no_crossing(tmp_path: Path, capsys) -> None:
    # the M1 bug: parallel spawn claimed next-pending, crossing member IDs.
    root = tmp_path / ".argus_team" / "t1"
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text(
        json.dumps({"task_id": "t1::a", "objective": "kernel a", "owns_paths": ["a/**"]}) + "\n"
        + json.dumps({"task_id": "t1::b", "objective": "kernel b", "owns_paths": ["b/**"]}) + "\n",
        encoding="utf-8")
    _call(capsys, "form", "--root", str(root), "--team-id", "t1", "--tasks", str(tasks))
    # spawn w2->b first, then w1->a (reverse) — old next-pending claim would cross
    _, out_b = _call(capsys, "spawn", "--root", str(root), "--team-id", "t1",
                     "--member-id", "t1::w2", "--task-id", "t1::b", "--exec-cmd", "true")
    _, out_a = _call(capsys, "spawn", "--root", str(root), "--team-id", "t1",
                     "--member-id", "t1::w1", "--task-id", "t1::a", "--exec-cmd", "true")
    assert json.loads(out_b)["task_id"] == "t1::b" and json.loads(out_b)["claimed"] is True
    assert json.loads(out_a)["task_id"] == "t1::a" and json.loads(out_a)["claimed"] is True
    _, out = _call(capsys, "status", "--root", str(root))
    by_id = {t["task_id"]: t for t in json.loads(out)["tasks"]}
    assert by_id["t1::a"]["owner"] == "t1::w1"
    assert by_id["t1::b"]["owner"] == "t1::w2"

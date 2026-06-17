from __future__ import annotations

from pathlib import Path

from argus_skill.team import mailbox as mb


def test_send_and_drain(tmp_path: Path) -> None:
    mb.send(tmp_path, to="tm-2", frm="tm-1", text="hi", now=1.0)
    mb.send(tmp_path, to="tm-2", frm="lead", text="status?", now=2.0)
    msgs = mb.drain(tmp_path, "tm-2")
    assert [m["text"] for m in msgs] == ["hi", "status?"]
    assert msgs[0]["from"] == "tm-1"
    assert mb.drain(tmp_path, "tm-2") == []   # drained once -> empty


def test_count_pending_does_not_advance(tmp_path: Path) -> None:
    mb.send(tmp_path, to="tm-1", frm="lead", text="x", now=1.0)
    assert mb.count_pending(tmp_path, "tm-1") == 1
    assert mb.count_pending(tmp_path, "tm-1") == 1   # still 1
    assert mb.drain(tmp_path, "tm-1")[0]["text"] == "x"


def test_broadcast_one_copy_each(tmp_path: Path) -> None:
    mb.broadcast(tmp_path, ["a", "b"], frm="lead", text="go", now=1.0)
    assert mb.drain(tmp_path, "a")[0]["text"] == "go"
    assert mb.drain(tmp_path, "b")[0]["text"] == "go"

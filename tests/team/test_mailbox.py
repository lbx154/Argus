from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("member", ["", ".", "..", "../escape", "../../escape", "a/b", "a\\b", "bad\x00id"])
def test_invalid_member_ids_do_not_escape_mailbox_on_send(tmp_path: Path, member: str) -> None:
    root = tmp_path / "team"
    with pytest.raises(ValueError):
        mb.send(root, to=member, frm="lead", text="x", now=1.0)

    assert not (tmp_path / "escape" / "inbox.jsonl").exists()
    assert not (root / "escape" / "inbox.jsonl").exists()


@pytest.mark.parametrize("op", [mb.drain, mb.count_pending])
def test_invalid_member_ids_do_not_escape_mailbox_on_read(tmp_path: Path, op) -> None:
    root = tmp_path / "team"
    escaped = tmp_path / "escape"
    escaped.mkdir()
    (escaped / "inbox.jsonl").write_text(
        json.dumps({"ts": 1.0, "from": "lead", "text": "outside"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        op(root, "../../escape")

    assert not (escaped / "inbox.offset").exists()


def test_broadcast_rejects_invalid_member_ids_without_escape(tmp_path: Path) -> None:
    root = tmp_path / "team"

    with pytest.raises(ValueError):
        mb.broadcast(root, ["../../escape", "safe"], frm="lead", text="go", now=1.0)

    assert not (tmp_path / "escape" / "inbox.jsonl").exists()
    assert not (root / "mailbox" / "safe" / "inbox.jsonl").exists()


@pytest.mark.parametrize("member", ["tm-1", "lead", "a", "b", "team::member"])
def test_valid_member_ids_send_count_and_drain(tmp_path: Path, member: str) -> None:
    mb.send(tmp_path, to=member, frm="lead", text="ok", now=1.0)

    assert mb.count_pending(tmp_path, member) == 1
    msgs = mb.drain(tmp_path, member)
    assert [m["text"] for m in msgs] == ["ok"]
    assert mb.count_pending(tmp_path, member) == 0

"""Every project gets a readable name at once; an Agent title replaces it later."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.session import (
    SessionMeta,
    provisional_session_name,
    read_session_meta,
    seed_provisional_session_name,
    write_session_meta,
)
from argus.manager.front_door import _maybe_name_session


def _session(root: Path, sid: str = "s-provisional", **fields) -> str:
    (root / "projects" / sid).mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, **fields))
    return sid


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("你好", "你好"),
        ("  帮我检查销售数据的重复订单。然后再看退款口径。", "帮我检查销售数据的重复订单"),
        ("Summarize this paper. Then list its claims.", "Summarize this paper"),
        ("first line\nsecond line", "first line"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_provisional_name_is_the_first_sentence(text, expected):
    assert provisional_session_name(text) == expected


def test_provisional_name_is_bounded_per_script():
    cjk = provisional_session_name("请" * 60)
    assert len(cjk) <= 24 and cjk.endswith("…")
    latin = provisional_session_name("please review " * 10)
    assert len(latin) <= 40 and latin.endswith("…")
    assert not latin[:-1].endswith(" ")


def test_agent_title_replaces_provisional_but_not_user_names(tmp_path):
    sid = _session(tmp_path)
    seed_provisional_session_name(tmp_path, sid, "帮我看看这个CSV")
    state = {"session_id": sid, "global_root": tmp_path}
    assert _maybe_name_session(state, "帮我看看这个CSV", suggested_name="销售数据核对")
    meta = read_session_meta(tmp_path, sid)
    assert (meta.display_name, meta.name_source) == ("销售数据核对", "agent")

    user = _session(tmp_path, "s-user", display_name="Operator title", name_source="user")
    state = {"session_id": user, "global_root": tmp_path}
    assert _maybe_name_session(state, "x", suggested_name="Other") == ""
    assert read_session_meta(tmp_path, user).display_name == "Operator title"


def test_web_message_never_names_the_project_with_a_pasted_credential(tmp_path, monkeypatch):
    from argus.webapi import manager_bridge, manager_state

    sid = _session(tmp_path, "s-credential-web", origin="web")
    manager_state._STATES.clear()
    secret = "sk-proj-A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"

    manager_bridge.manager_message(sid, secret, global_root=tmp_path)

    session = json.loads((tmp_path / "projects" / sid / "session.json").read_text())
    assert "A1b2C3d4" not in session.get("display_name", "")

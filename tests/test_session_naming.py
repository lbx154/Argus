"""Session auto-naming and mission-context rendering."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.apps.cli._follow import _format_follow_mission_context
from argus_skill.manager.config_intent import _front_door_classify
from argus_skill.manager.front_door import (
    _derive_session_name,
    _maybe_name_session,
)


def test_derive_session_name_first_line_then_truncate():
    assert _derive_session_name("优化 079 kernel\nsecond line") == "优化 079 kernel"
    assert _derive_session_name("   \n\n  real task here") == "real task here"
    assert _derive_session_name("“勾股定理简证。”") == "勾股定理简证"
    long = "a" * 80
    out = _derive_session_name(long)
    assert out.endswith("…") and len(out) == 48
    assert _derive_session_name("") == ""


def test_maybe_name_session_is_idempotent_and_failsoft():
    # already named -> no-op
    cs = {"session_named": True, "session_id": "s-y", "global_root": "/tmp"}
    _maybe_name_session(cs, "task")
    assert cs["session_named"] is True
    # missing global_root -> no crash, stays unnamed
    cs2 = {"session_named": False, "session_id": "s-x", "global_root": None}
    _maybe_name_session(cs2, "task")
    assert cs2["session_named"] is False


def test_maybe_name_session_names_a_fresh_session(tmp_path):
    from argus_skill.core.session import read_session_meta, resolve_session

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=1)
    cs = {"session_named": False, "session_id": sid, "global_root": tmp_path}
    _maybe_name_session(cs, "optimize the 079 kernel\nmore detail")
    assert cs["session_named"] is True
    assert read_session_meta(tmp_path, sid).display_name == "optimize the 079 kernel"


def test_maybe_name_session_uses_concise_manager_title(tmp_path):
    from argus_skill.core.session import read_session_meta, resolve_session

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=1)
    cs = {"session_named": False, "session_id": sid, "global_root": tmp_path}
    _maybe_name_session(
        cs,
        "帮我简单证明勾股定理，并且给出容易理解的说明",
        suggested_name="勾股定理简证",
    )
    assert read_session_meta(tmp_path, sid).display_name == "勾股定理简证"


def test_maybe_name_session_never_overwrites_persisted_name(tmp_path):
    from argus_skill.core.session import read_session_meta, resolve_session, touch_session

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=1)
    touch_session(tmp_path, sid, display_name="已有名称")
    cs = {"session_named": False, "session_id": sid, "global_root": tmp_path}
    _maybe_name_session(cs, "新任务", suggested_name="新名称")
    assert cs["session_named"] is True
    assert read_session_meta(tmp_path, sid).display_name == "已有名称"


@pytest.mark.parametrize("route", ["simple", "complex"])
def test_front_door_names_first_message_for_every_route(tmp_path, route):
    from argus_skill.core.session import read_session_meta, resolve_session

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=1)
    cs = {"session_named": False, "session_id": sid, "global_root": tmp_path}

    class _Manager:
        def classify_front_door(self, text, *, name_sink=None):
            assert text == "帮我简单证明勾股定理"
            assert name_sink is not None
            name_sink("勾股定理简证")
            return None, None, route

    result = _front_door_classify(
        object(),
        "帮我简单证明勾股定理",
        cs,
        ensure_runner=lambda *_: SimpleNamespace(manager=_Manager()),
    )

    assert result == (None, None, route)
    assert read_session_meta(tmp_path, sid).display_name == "勾股定理简证"


def test_front_door_names_first_message_when_classifier_is_unavailable(tmp_path):
    from argus_skill.core.session import read_session_meta, resolve_session

    sid, _ = resolve_session(global_root=tmp_path, mode="new", cwd=tmp_path, now=1)
    cs = {"session_named": False, "session_id": sid, "global_root": tmp_path}
    result = _front_door_classify(
        object(),
        "fallback task title",
        cs,
        ensure_runner=lambda *_: None,
    )
    assert result == (None, None, "complex")
    assert read_session_meta(tmp_path, sid).display_name == "fallback task title"


# ---- objective=- root-cause fix ------------------------------------------

def test_mission_context_renders_objective_when_event_carries_it():
    # The daemon now emits `objective` on life.mission.started, so the follow
    # mission-context line shows the real goal instead of "objective=-".
    ev = {"item_id": "it-1", "title": "kernel work", "objective": "hit SOL on 079"}
    bits = _format_follow_mission_context(ev)
    assert "objective=hit SOL on 079" in bits
    # ...and degrades to "-" only when genuinely absent.
    bits2 = _format_follow_mission_context({"item_id": "it-2"})
    assert "objective=-" in bits2

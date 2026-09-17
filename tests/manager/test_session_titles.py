import json
from types import SimpleNamespace

import pytest

from argus.core.session import SessionMeta, read_session_meta, write_session_meta
from argus.manager import config_intent
from argus.manager.domain_author import parse_fast_vertical_decision, parse_vertical_decision
from argus.manager.front_door import _maybe_name_session
from argus.webapi.project_crud import update_project


def session(tmp_path):
    sid = "s-agent-title"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "projects" / sid).mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id=sid, cwd=str(workspace), workdir=str(workspace)))
    return {"session_id": sid, "global_root": tmp_path}


def test_agent_titles_follow_topic_changes_across_restarts(tmp_path):
    state = session(tmp_path)
    inputs = []
    replies = iter(["销售数据核对", "NONE", "论文摘要润色"])

    class Manager:
        def classify_front_door(self, text, *, name_sink=None, **kwargs):
            inputs.append(text)
            name_sink(next(replies))
            return None, None, "simple"

    runner = SimpleNamespace(manager=Manager())
    def classify(text):
        # Recreate ephemeral chat state, as after reconnecting or restarting.
        chat = {**state}
        return config_intent._front_door_classify(
            SimpleNamespace(), text, chat, ensure_runner=lambda *_a: runner,
        )

    assert classify("17\n请检查这份销售CSV的重复订单和退款口径。") == (None, None, "simple")
    first = read_session_meta(tmp_path, state["session_id"])
    assert first.display_name == "销售数据核对" and first.name_source == "agent"
    assert classify("再详细一点") == (None, None, "simple")
    assert "销售数据核对" in inputs[-1]
    assert read_session_meta(tmp_path, state["session_id"]).display_name == first.display_name
    assert classify("换个话题，帮我润色这段论文摘要。") == (None, None, "simple")
    assert read_session_meta(tmp_path, state["session_id"]).display_name == "论文摘要润色"
    assert len(inputs) == 3, "Naming reuses classification; it must not add a model call"


@pytest.mark.parametrize("title", ["", "NONE", "KEEP", "17"])
def test_missing_or_placeholder_titles_do_not_freeze_raw_input(tmp_path, title):
    state = session(tmp_path)
    assert _maybe_name_session(state, "17\nLong raw user instruction", suggested_name=title) == ""
    assert read_session_meta(tmp_path, state["session_id"]).display_name == ""
    _maybe_name_session(state, "next turn", suggested_name="订单去重")
    assert read_session_meta(tmp_path, state["session_id"]).display_name == "订单去重"


def test_manual_rename_wins_over_an_inflight_agent_and_survives_replacement(tmp_path):
    state = session(tmp_path)
    _maybe_name_session(state, "task", suggested_name="Agent title")

    class Manager:
        def classify_front_door(self, text, *, name_sink=None, **kwargs):
            name_sink("Another Agent title")
            update_project(state["session_id"], name="My chosen title", global_root=tmp_path)
            return None, None, "simple"

    config_intent._front_door_classify(
        SimpleNamespace(), "new task", state,
        ensure_runner=lambda *_a: SimpleNamespace(manager=Manager()),
    )
    _maybe_name_session(state, "replacement", suggested_name="Overwritten?", replacing=True)
    meta = read_session_meta(tmp_path, state["session_id"])
    assert meta.display_name == "My chosen title" and meta.name_source == "user"
    update_project(state["session_id"], name="", global_root=tmp_path)
    _maybe_name_session(state, "next task", suggested_name="Automatic again")
    assert read_session_meta(tmp_path, state["session_id"]).name_source == "agent"


@pytest.mark.parametrize("fast", [False, True])
@pytest.mark.parametrize("structured", [False, True])
def test_existing_handoff_carries_an_agent_title_without_an_extra_call(fast, structured):
    fields = dict(choice="existing", vertical="software", workflow_mode="direct",
                  confidence=0.9, rationale="A small repository change",
                  session_title="Refund calculation", execution_task="Fix the refund formula")
    raw = json.dumps(fields) if structured else "\n".join(f"{key.upper()}={value}" for key, value in fields.items())
    parser = parse_fast_vertical_decision if fast else parse_vertical_decision
    decision = parser(raw, known_verticals=("software",))
    assert decision is not None
    assert decision.session_title == "Refund calculation"
    assert decision.vertical == "software"

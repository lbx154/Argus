from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import append_turn
from argus.life.memory import BacklogItem, LifeMemory
from argus.manager import config_intent, front_door
from argus.webapi import manager_bridge, manager_state
from argus.webapi.manager_session_intent import contextualize_operator_turn


def _session(tmp_path: Path, sid: str) -> tuple[Path, Path]:
    life = tmp_path / "projects" / sid
    life.mkdir(parents=True)
    (life / "backlog.jsonl").write_text("", encoding="utf-8")
    workspace = tmp_path / "workspaces" / sid
    workspace.mkdir(parents=True)
    write_session_meta(
        tmp_path,
        SessionMeta(
            id=sid,
            display_name="test",
            cwd=str(life),
            workdir=str(workspace),
            launch_cwd=str(tmp_path),
        ),
    )
    manager_state._STATES.clear()
    return life, workspace


def test_directory_question_reaches_llm_front_door_classifier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-path-info"
    life, _workspace = _session(tmp_path, sid)
    seen: list[str] = []

    def classify(_mem, text, chat_state, **_kwargs):
        seen.append(text)
        chat_state["_frontdoor_self_mode"] = "reply"
        chat_state["_frontdoor_fast_reply"] = "LLM path answer"
        return None, "no_dispatch", "simple"

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *_a, **_k: None)

    result = manager_bridge.manager_message(
        sid,
        "那么你的项目目录呢？",
        global_root=tmp_path,
    )

    assert result["kind"] == "chat"
    assert result["reply"] == "LLM path answer"
    assert seen == ["那么你的项目目录呢？"]
    assert LifeMemory.open(life).backlog.all() == []


def test_current_turn_is_classified_raw_and_dispatched_with_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sid = "s-contextual-task"
    life, _workspace = _session(tmp_path, sid)
    memory = LifeMemory.open(life)
    previous = memory.backlog.add(
        BacklogItem.new(
            title="repair parser",
            objective="请修复解析器的转义错误。",
            original_objective="请修复解析器的转义错误。",
            manager_decision={"routed": True},
        )
    )
    memory.backlog.mark_done(previous.id)
    append_turn(life, "operator", "请修复解析器的转义错误。")
    append_turn(life, "argus", "我会保留现有 API，并补充回归测试。")
    seen: dict[str, str] = {}

    def classify(_mem, text, chat_state, **_kwargs):
        seen["classify"] = str(
            chat_state.get("_frontdoor_contextual_text") or text
        )
        chat_state["_frontdoor_lifetime"] = "bounded"
        return None, None, "complex"

    def dispatch(
        _mem,
        body,
        _state,
        _root_task_id,
        _cancelled,
        _emitter,
        **_kwargs,
    ):
        seen["dispatch"] = body
        return SimpleNamespace(id="task-1", title="repair parser", status="pending"), False, None

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(front_door, "manager_triage", lambda *_a, **_k: None)
    monkeypatch.setattr(manager_bridge, "_dispatch_team_mission", dispatch)

    result = manager_bridge.manager_message(
        sid,
        "那就按刚才说的做",
        global_root=tmp_path,
    )

    assert result["kind"] == "task"
    assert seen["classify"] == "那就按刚才说的做"
    assert "last_team_task: 请修复解析器的转义错误。" in seen["dispatch"]
    assert "[CURRENT OPERATOR MESSAGE]\n那就按刚才说的做" in seen["dispatch"]


def test_contextualization_is_bounded_but_keeps_long_corrections() -> None:
    prior = [
        {"role": "operator", "text": "discarded old task"},
        {"role": "argus", "text": "discarded old reply"},
        {"role": "operator", "text": "relevant task"},
        {"role": "argus", "text": "relevant reply"},
        {"role": "operator", "text": "newer task"},
        {"role": "argus", "text": "x" * 400},
    ]

    enriched = contextualize_operator_turn(
        "那就列出修改",
        prior,
        last_team_task="把所有 commit 作者改成 lbx154。",
    )
    assert "last_team_task: 把所有 commit 作者改成 lbx154。" in enriched
    assert "discarded old task" not in enriched
    assert "relevant task" in enriched
    assert ("x" * 300) in enriched
    assert ("x" * 301) not in enriched
    assert "[CURRENT OPERATOR MESSAGE]\n那就列出修改" in enriched

    long_text = "这些 commit 的作者仍然没有改：" + ("x" * 200)
    long_context = contextualize_operator_turn(
        long_text,
        prior,
        last_team_task="把所有 commit 作者改成 lbx154。",
    )
    assert "relevant task" in long_context
    assert f"[CURRENT OPERATOR MESSAGE]\n{long_text}" in long_context

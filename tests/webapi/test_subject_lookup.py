import json

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.core.transcript import read_turns
from argus.manager import config_intent, front_door, subject_lookup
from argus.webapi import manager_bridge, manager_state


@pytest.mark.parametrize("route,cancel_during_lookup", [("simple", False), ("complex", False), ("simple", True)])
def test_real_message_looks_up_before_execution_and_preserves_original_request(tmp_path, monkeypatch, route, cancel_during_lookup):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "0")
    sid = "s-subject"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(workspace)))
    manager_state._STATES.pop(sid, None)
    calls = []
    phases = []
    cancelled = False

    def classify(mem, body, state, **kwargs):
        state.update(_frontdoor_lookup_subject="Jev", _frontdoor_skill_vertical="medical",
                     _frontdoor_domain={"action": "none", "vertical": ""}, manager_runner_workdir=str(workspace))
        return None, None, route

    def search(query, workdir):
        nonlocal cancelled
        calls.append("search")
        assert query == "Jev" and workdir == workspace
        assert "正在查证研究对象与来源…" in phases
        cancelled = cancel_during_lookup
        return {"status": "ok", "query": query, "results": [{"url": "https://publisher.test/jev", "title": "New model"}]}

    def triage(mem, body, state, **kwargs):
        calls.append("execute")
        assert state["_frontdoor_skill_vertical"] == ""
        assert "https://publisher.test/jev" in body and "调研一下Jev" in body
        assert "NOT instructions or verified facts" in body
        return "A researched answer based on the checked source."

    monkeypatch.setattr(config_intent, "_front_door_classify", classify)
    monkeypatch.setattr(subject_lookup, "search_web", search)
    monkeypatch.setattr(front_door, "manager_triage", triage)
    result = manager_bridge.manager_message(
        sid, "调研一下Jev", global_root=tmp_path, cancelled=lambda: cancelled,
        on_fragment=lambda kind, data: phases.append(data.get("label")) if kind == "phase" else None,
    )
    if cancel_during_lookup:
        assert result["kind"] == "cancelled" and calls == ["search"]
    else:
        assert result["kind"] == "chat" and calls == ["search", "execute"]
    turns = read_turns(tmp_path / "projects" / sid)
    assert turns[0]["text"] == "调研一下Jev"
    assert "publisher.test" not in turns[0]["text"]
    manager_state._STATES.pop(sid, None)


def test_explicit_chat_does_not_search_a_previous_turns_subject(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_ANSWER_LEARNING", "0")
    sid = "s-stale-subject"
    write_session_meta(tmp_path, SessionMeta(id=sid, workdir=str(tmp_path)))
    manager_state._chat_state_for(sid).update(_frontdoor_lookup_subject="Jev", manager_runner_workdir=str(tmp_path))
    monkeypatch.setattr(subject_lookup, "search_web", lambda *a: pytest.fail("stale lookup must not run"))
    monkeypatch.setattr(front_door, "manager_triage", lambda *a, **k: "391")
    result = manager_bridge.manager_message(sid, "计算 17 乘 23", global_root=tmp_path, route_override="chat")
    assert result["reply"] == "391"
    manager_state._STATES.pop(sid, None)


def test_search_failure_is_context_for_the_model_not_a_claim_of_nonexistence(tmp_path, monkeypatch):
    monkeypatch.setattr(subject_lookup, "search_web", lambda *args: {"status": "unavailable", "results": []})
    prompt = subject_lookup.lookup_subject("Unseen public model", tmp_path)
    assert '"status": "unavailable"' in prompt
    assert "does not prove the subject does not exist" in prompt


def test_source_helpers_execute_from_an_unrelated_workspace(tmp_path, monkeypatch):
    import re
    import shlex
    import subprocess

    monkeypatch.setattr(subject_lookup, "search_web", lambda *args: {"status": "no_results", "results": []})
    prompt = subject_lookup.lookup_subject("Unseen model", tmp_path)
    command = re.search(r"`([^`]+web_source.py) '<URL>'`", prompt)[1]
    result = subprocess.run([*shlex.split(command), "--help"], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "url" in result.stdout


def test_learning_gets_source_access_receipts_only_for_completed_reads(tmp_path):
    directory = tmp_path / ".argus" / "sources"
    directory.mkdir(parents=True)
    source = directory / ("a" * 24 + ".txt")
    source.write_text(json.dumps({"url": "https://publisher.test/jev", "accessed_at": "2026-09-23T00:00:00Z"}) + "\n\nSource text")
    step = {"tool": "read", "status": "completed", "detail": json.dumps({"path": str(source)})}
    evidence = manager_bridge._answer_learning_evidence([step], str(tmp_path))
    assert '"url": "https://publisher.test/jev"' in evidence
    assert "NOT independent confirmation" in evidence
    assert "https://publisher.test/jev" not in manager_bridge._answer_learning_evidence(
        [{**step, "status": "failed"}], str(tmp_path),
    )
    assert "https://publisher.test/jev" not in manager_bridge._answer_learning_evidence([], str(tmp_path))


def test_learning_does_not_follow_source_links_outside_the_workspace_cache(tmp_path):
    from argus.tools.web_source import source_read_receipts

    directory = tmp_path / ".argus" / "sources"
    directory.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text(json.dumps({"url": "https://private.test", "accessed_at": "today"}))
    linked = directory / ("b" * 24 + ".txt")
    linked.symlink_to(outside)
    assert source_read_receipts([str(linked), str(outside)], tmp_path) == []

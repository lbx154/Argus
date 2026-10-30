"""The composer's gray text: one tool-less light-model turn that never waits on the Manager."""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.models import AgentRunResult
from argus.core import hosting
from argus.core.run_gateway import current_run_interrupt_reason
from argus.core.session import SessionMeta, write_session_meta
from argus.core.stop_kinds import stop_kind_from_external_interrupt
from argus.core.transcript import append_turn
from argus.core.usage import UsageLedger
from argus.webapi import manager_bridge, manager_state, map_model, server

CONFIG = map_model.MapModel("codex", "gpt-5.4-mini", "low", sys.executable)


def _project(root, sid="s-gray-text"):
    life = root / "projects" / sid
    life.mkdir(parents=True)
    write_session_meta(root, SessionMeta(id=sid, cwd=str(life), workdir=str(life), objective="Beat the baseline"))
    return sid, life


@pytest.fixture()
def model(monkeypatch):
    """Stands in for the light model: `answer` gets the prompt and the call's keywords."""
    calls = []

    def install(answer):
        def run(prompt, schema, config, **kwargs):
            calls.append({"prompt": prompt, "schema": schema, "config": config, **kwargs})
            return {"text": answer(prompt, kwargs)}

        monkeypatch.setattr(map_model, "run_map_model", run)
        return calls

    monkeypatch.setattr(map_model, "resolve_map_model", lambda: CONFIG)
    return install


@pytest.mark.parametrize(("draft", "anchor", "reply", "tail"), [
    ("I want to tra", "I want to tra", "I want to train the model", "in the model"),
    ("I want to ", "I want to", "I want to  train it", "train it"),
    ("我想比较两个", "我想比较两个", "我想比较两个基线的结果", "基线的结果"),
    ("Check the run on the GPU and com", "run on the GPU and com", "Check the run on the GPU and compare it", "pare it"),
    ("Compare the tw", "Compare the tw", "two baselines", ""),
    ("Compare the tw", "Compare the tw", "Compare the two\nbaselines", "o"),
    ("Compare the two", "Compare the two", "Compare the two", ""),
    ("Compare", "Compare", "Compare " + "x" * 200, " " + "x" * 119),
])
def test_tail_is_what_follows_the_restated_end_of_the_draft(draft, anchor, reply, tail):
    assert manager_bridge._completion_tail(draft, anchor, reply) == tail


def test_asks_the_light_model_once_while_the_manager_is_busy(tmp_path, model):
    sid, life = _project(tmp_path)
    append_turn(life, "operator", "Run the baseline")
    append_turn(life, "argus", "The baseline   finished.")
    calls = model(lambda prompt, kwargs: "Compare the two baselines on CIFAR")
    held, release = threading.Event(), threading.Event()

    def hold_manager():
        with manager_state._lock_for(sid):
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold_manager)
    holder.start()
    try:
        assert held.wait(1)
        assert manager_bridge.manager_complete(sid, "Compare the two ", global_root=tmp_path) == {
            "completion": "baselines on CIFAR"}
    finally:
        release.set()
        holder.join()
    (call,) = calls
    assert call["run_label"] == "prompt-complete"
    assert call["project_root"] == life and call["global_root"] == tmp_path
    assert list(call["schema"]["properties"]) == ["text"]
    assert "standing objective: Beat the baseline" in call["prompt"]
    assert "operator: Run the baseline\nargus: The baseline finished." in call["prompt"]
    assert call["prompt"].endswith('## Draft (a JSON string)\n"Compare the two "\n\n## Starts with\n"Compare the two"')


@pytest.mark.parametrize("case", ["policy", "no model", "secret", "no project", "failure"])
def test_any_obstacle_is_simply_no_suggestion(tmp_path, monkeypatch, model, case):
    sid, _ = _project(tmp_path)
    text = "Compare the two baselines and"

    def answer(prompt, kwargs):
        raise map_model.MapGenerationError("provider_error")

    calls = model(answer)
    if case == "policy":
        monkeypatch.setattr(hosting, "hosting", lambda: SimpleNamespace(prompt_completion_enabled=lambda: False))
    elif case == "no model":
        monkeypatch.setattr(map_model, "resolve_map_model", lambda: map_model.MapModel("memory", "", None, ""))
    elif case == "secret":
        text = "Use OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123 to"
    elif case == "no project":
        sid = "s-gone"
    assert manager_bridge.manager_complete(sid, text, global_root=tmp_path) == {"completion": ""}
    assert len(calls) == (case == "failure")


def _waits_for_a_stop(reasons):
    def answer(prompt, kwargs):
        if "second" in prompt:
            return "the second draft goes on"
        for _ in range(500):
            if reason := current_run_interrupt_reason():
                reasons.append(reason)
                raise map_model.MapGenerationError("provider_error")
            time.sleep(0.01)
        raise AssertionError("the call was never stopped")

    return answer


def test_a_newer_draft_stops_the_older_call_as_an_operator_abort(tmp_path, model):
    sid, _ = _project(tmp_path)
    reasons = []
    calls = model(_waits_for_a_stop(reasons))
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(manager_bridge.manager_complete, sid, "the first draft", global_root=tmp_path)
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager_bridge.manager_complete(sid, "the second draft", global_root=tmp_path) == {
            "completion": " goes on"}
        assert first.result(timeout=5) == {"completion": ""}
    assert len(reasons) == 1 and stop_kind_from_external_interrupt(reasons[0]) == "operator_abort"


def test_sending_the_message_stops_the_call_for_its_draft(tmp_path, monkeypatch, model):
    sid, _ = _project(tmp_path)
    reasons = []
    calls = model(_waits_for_a_stop(reasons))
    monkeypatch.setattr(manager_bridge, "_manager_message", lambda sid, text, **kwargs: {"kind": "chat"})
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(manager_bridge.manager_complete, sid, "the first draft", global_root=tmp_path)
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager_bridge.manager_message(sid, "the first draft", global_root=tmp_path) == {"kind": "chat"}
        assert pending.result(timeout=5) == {"completion": ""}
    assert len(reasons) == 1 and stop_kind_from_external_interrupt(reasons[0]) == "operator_abort"


def test_a_slow_call_stops_as_an_operator_abort(tmp_path, monkeypatch, model):
    sid, _ = _project(tmp_path)
    reasons = []
    model(_waits_for_a_stop(reasons))
    monkeypatch.setattr(manager_bridge, "_COMPLETION_SECONDS", 0.05)
    assert manager_bridge.manager_complete(sid, "the first draft", global_root=tmp_path) == {"completion": ""}
    assert len(reasons) == 1 and stop_kind_from_external_interrupt(reasons[0]) == "operator_abort"


def test_the_turn_is_read_only_tool_less_and_billed_to_the_project(tmp_path, monkeypatch):
    sid, life = _project(tmp_path)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setattr(map_model, "resolve_map_model", lambda: CONFIG)
    seen = []

    def run(self, **kwargs):
        seen.append(kwargs["options"])
        return AgentRunResult(
            command=[], exit_code=0, turn_completed=True,
            agent_messages=['{"text": "Compare the two baselines"}'], usage_model="gpt-5.4-mini",
            json_events=[{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 5}}],
        )

    monkeypatch.setattr(AgentCliRunner, "run_exec", run)
    assert manager_bridge.manager_complete(sid, "Compare the tw", global_root=tmp_path) == {"completion": "o baselines"}
    (options,) = seen
    assert options.disable_tools and options.force_safe_mode and options.sandbox_mode == "read-only"
    assert options.output_schema == manager_bridge._COMPLETION_SCHEMA
    assert options.external_interrupt_reason_provider() is None
    manager_bridge._LATEST_COMPLETION[sid] = object()
    assert options.external_interrupt_reason_provider().startswith("operator abort requested:")
    (row,) = UsageLedger(life).records()
    assert row.to_jsonable()["run_label"] == "prompt-complete"


def test_the_rewrite_route_hands_complete_mode_to_the_suggestion(tmp_path, monkeypatch):
    sid, _ = _project(tmp_path)
    calls = []
    monkeypatch.setattr(manager_bridge, "manager_complete",
                        lambda sid, text, **kwargs: calls.append(("complete", text)) or {"completion": "x"})
    monkeypatch.setattr(manager_bridge, "manager_rewrite",
                        lambda sid, text, **kwargs: calls.append(("rewrite", text)) or {"rewritten": "y"})
    client = TestClient(server.create_app(global_root=tmp_path))
    path = f"/api/projects/{sid}/prompt/rewrite"
    assert client.post(path, json={"text": "Compare the ", "mode": "complete"}).json() == {"completion": "x"}
    assert client.post(path, json={"text": " Tidy this "}).json() == {"rewritten": "y"}
    assert client.post(path, json={"text": "Compare", "mode": "guess"}).status_code == 422
    assert client.post(path, json={"text": "  ", "mode": "complete"}).status_code == 400
    assert calls == [("complete", "Compare the "), ("rewrite", " Tidy this ")]

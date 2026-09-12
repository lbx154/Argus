import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.core.knob_store import write_persisted_knobs
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.core.usage import UsageLedger, UsageRecord
from argus_skill.webapi import map_model
from argus_skill.webapi.server import create_app


def test_map_output_recovers_only_the_observed_premature_cards_root_closure():
    from argus_skill.webapi.map_narrative import schema

    card = {"title": "Check a boundary", "summary": "The result covers one case", "detail": "A literal } is part of the note.",
            "reader_brief": {"why": "Check the stated assumption", "concept": None,
                             "scope": "The general claim has not been established", "next": "No next step is recorded"}}
    expected = {"cards": {"task-a": card}, "relations": []}
    # This is the exact structural error observed in the first real Pi output:
    # the root closes after cards, before its relations property starts.
    malformed = json.dumps({"cards": expected["cards"]}) + ',"relations":[]}'
    with pytest.raises(json.JSONDecodeError, match="Extra data"):
        json.loads(malformed)
    assert map_model._parse_document(malformed, schema(["task-a"], ["task-a"])) == expected
    assert map_model._parse_document(json.dumps(expected), schema(["task-a"], ["task-a"])) == expected
    assert map_model._parse_document('```json\n' + json.dumps(expected) + '\n```', schema(["task-a"], ["task-a"])) == expected


@pytest.mark.parametrize("raw", [
    '{"cards":{}},"relations":[]} trailing explanation',
    '{"cards":{}},"other":[]}',
    '{"cards":{}},{"relations":[]}',
    '{"cards":{}},"relations":[],"extra":"do not accept"}',
    '{"cards":[]},"relations":[]}',
    '{"cards":{}},"relations":{}}',
    'preface {"cards":{},"relations":[]}',
    '{"cards":{}},"relations":[',
])
def test_map_output_recovery_rejects_trailing_prose_other_shapes_and_incomplete_output(raw):
    with pytest.raises(ValueError):
        map_model._parse_document(raw, {})


def test_recovered_output_still_requires_the_requested_schema_and_card_coverage():
    from argus_skill.webapi.map_narrative import schema

    with pytest.raises(ValueError, match="schema"):
        map_model._parse_document('{"cards":{}},"relations":[]}', schema(["required-task"], ["required-task"]))
    with pytest.raises(ValueError, match="schema"):
        map_model._parse_document('{"cards":{"a":{"title":"Missing brief"}}},"relations":[]}', schema(["a"], ["a"]))


def test_map_inherits_research_role_and_persisted_overrides(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("ARGUS_SKILL_MODEL", "gpt-5.5")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "medium")
    before = map_model.resolve_map_model()
    assert (before.backend, before.model, before.effort) == ("copilot", "gpt-5.4-mini", "medium")
    write_persisted_knobs({"ARGUS_SKILL_MAP_MODEL": "gpt-5.5", "ARGUS_SKILL_MAP_REASONING_EFFORT": "low"})
    changed = map_model.resolve_map_model()
    assert (changed.backend, changed.model, changed.effort) == ("copilot", "gpt-5.5", "low")
    assert changed.revision != before.revision
    write_persisted_knobs({"ARGUS_SKILL_MAP_MODEL": "auto", "ARGUS_SKILL_MAP_REASONING_EFFORT": "auto"})
    assert map_model.resolve_map_model() == before


def test_map_settings_use_existing_config_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MAP_MODEL", "auto")
    monkeypatch.setenv("ARGUS_SKILL_MAP_REASONING_EFFORT", "auto")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    write_session_meta(tmp_path, SessionMeta(id="s-settings", created=1, last_active=1))
    with TestClient(create_app(global_root=tmp_path, auth_token="test")) as client:
        path = "/api/projects/s-settings/config/set"
        request = {"name": "ARGUS_SKILL_MAP_MODEL", "value": "gpt-5.5"}
        assert client.post(path, json=request).status_code == 401
        headers = {"Authorization": "Bearer test"}
        changed = client.post(path, json=request, headers=headers)
        assert changed.status_code == 200 and not changed.json()["restart_required"]
        assert map_model.resolve_map_model().model == "gpt-5.5"
        config = client.get("/api/projects/s-settings/config", headers=headers).json()
        assert next(r for r in config["roles"] if r["role"] == "engineer")["model"] == "gpt-5.4-mini"
        assert client.post(path, json={**request, "value": "auto"}, headers=headers).status_code == 200
        assert map_model.resolve_map_model().model == "gpt-5.4-mini"
        assert client.post(path, json={**request, "value": "not a model"}, headers=headers).status_code == 400
        assert client.post(path, json={"name": "ARGUS_SKILL_MAP_REASONING_EFFORT", "value": "invalid"}, headers=headers).status_code == 400


def test_map_runner_uses_shared_usage_ledger_and_read_only_turn(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "100")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "100")

    def run(self, **kwargs):
        options = kwargs["options"]
        assert options.disable_tools and options.force_safe_mode
        assert options.sandbox_mode == "read-only"
        assert kwargs.get("resume_thread_id") is None
        observed.append(options)
        return AgentRunResult(
            command=[], exit_code=0, turn_completed=True,
            agent_messages=['{"cards":{},"relations":[]}'],
            usage_model="gpt-5.4-mini",
            json_events=[{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 20}}],
        )

    monkeypatch.setattr(AgentCliRunner, "run_exec", run)
    project = tmp_path / "projects/s-map"
    config = map_model.MapModel("codex", "gpt-5.4-mini", "low", sys.executable)
    result = map_model.run_map_model("Summarize these records", {}, config, project_root=project, global_root=tmp_path)
    assert result == {"cards": {}, "relations": []} and len(observed) == 1
    rows = UsageLedger(project).records()
    assert len(rows) == 1
    row = rows[0].to_jsonable()
    assert row["run_label"] == "map-summary"
    assert row["model"] == "gpt-5.4-mini" and row["input_tokens"] == 100
    assert row["cost_usd"] is not None
    assert not list((tmp_path / "map-presentation").glob("generation-*"))


def test_shared_budget_denies_map_before_provider_call(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "0.000001")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "100")

    def unexpected(*args, **kwargs):
        pytest.fail("An over-budget map request reached the provider")

    monkeypatch.setattr(AgentCliRunner, "run_exec", unexpected)
    UsageLedger(tmp_path / "projects/s-budget").append(UsageRecord.from_jsonable({
        "call_id": "earlier-research-call", "project_id": "s-budget", "status": "completed",
        "pricing_status": "priced", "cost_usd": 1, "completed_at": time.time(),
    }))
    with pytest.raises(OSError, match="did not complete"):
        map_model.run_map_model(
            "Summarize records", {}, map_model.MapModel("codex", "gpt-5.4-mini", "low", sys.executable),
            project_root=tmp_path / "projects/s-budget", global_root=tmp_path,
        )


def test_historical_generation_requires_an_owning_session(tmp_path, monkeypatch):
    folder = tmp_path / "datasets"
    folder.mkdir()
    (folder / "history.json").write_text(json.dumps({"id": "history", "read_only": True, "tasks": [], "events": []}))
    monkeypatch.setenv("ARGUS_MAP_DATASETS_DIR", str(folder))
    with TestClient(create_app(global_root=tmp_path)) as client:
        response = client.post("/api/map-copy/dataset/history", json={"cards": [{"key": "a", "task_id": "a", "kind": "task"}]})
        assert response.status_code == 422

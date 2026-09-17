from __future__ import annotations

import copy
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.models import AgentRunResult
from argus.core import cost_control
from argus.core.session import SessionMeta, write_session_meta
from argus.core.usage import UsageLedger, UsageRecord
from argus.webapi import map_model, map_narrative
from argus.webapi.server import create_app


def dataset(count=1):
    return {"id": "live:s-map", "tasks": [
        {"id": f"t{i}", "title": f"Task {i}", "objective": "Check evidence", "status": "running", "deps": []}
        for i in range(count)
    ], "events": []}


def requests(data, count=None):
    return [{"key": t["id"], "task_id": t["id"], "kind": "task", "event_ids": []}
            for t in data["tasks"][:count]]


def cards(documents, *args, **kwargs):
    return {"cards": [{"key": d["key"], "title": "Finding", "summary": "Evidence", "detail": "Limits"}
                      for d in documents], "relations": []}


def test_empty_cache_failure_has_durable_cooldown_and_no_paid_retry(tmp_path, monkeypatch):
    data = dataset()
    calls = []
    monkeypatch.setattr(map_narrative, "configured", lambda: True)

    def fail(*args, **kwargs):
        calls.append(1)
        raise map_model.MapGenerationError("map_timeout")

    monkeypatch.setattr(map_narrative, "generate", fail)
    first = map_narrative.enrich(tmp_path, data, requests(data), "en-US", project_root=tmp_path)
    again = map_narrative.enrich(tmp_path, data, requests(data), "en-US", project_root=tmp_path)
    assert first["cards"] == again["cards"] == {}
    assert first["generation_error"]["code"] == "map_timeout"
    assert again["retry_after"] > 0 and len(calls) == 1
    saved = map_narrative.read_cache(tmp_path, data["id"] + ":en-US")
    assert saved["generation_error"]["code"] == "map_timeout"

    # The cooldown is durable, not a per-process in-memory flag.
    saved["retry_at"] = 0
    map_narrative._write_cache(map_narrative.cache_path(tmp_path, data["id"] + ":en-US"), saved)
    monkeypatch.setattr(map_narrative, "generate", cards)
    recovered = map_narrative.enrich(tmp_path, data, requests(data), "en-US", project_root=tmp_path)
    assert recovered["cards"]["t0"]["summary"] == "Evidence"
    assert recovered["generation_error"] is None


def test_failed_refresh_preserves_published_cards_and_revisions(tmp_path, monkeypatch):
    data = dataset()
    monkeypatch.setattr(map_narrative, "configured", lambda: True)
    monkeypatch.setattr(map_narrative, "generate", cards)
    first = map_narrative.enrich(tmp_path, data, requests(data), "en-US", project_root=tmp_path)
    saved = copy.deepcopy(first["cards"])
    data["tasks"][0]["objective"] = "A different claim"
    path = map_narrative.cache_path(tmp_path, data["id"] + ":en-US")
    cache = json.loads(path.read_text())
    cache["attempt_at"] = 0
    map_narrative._write_cache(path, cache)

    def fail(*args, **kwargs):
        raise map_model.MapGenerationError("cost_unreconciled")

    monkeypatch.setattr(map_narrative, "generate", fail)
    result = map_narrative.enrich(tmp_path, data, requests(data), "en-US", project_root=tmp_path)
    assert result["cards"] == saved
    assert result["cache_revision"] == first["cache_revision"]
    assert result["generation_error"]["code"] == "cost_unreconciled"


def test_generation_batches_are_small_and_relation_context_is_bounded(tmp_path, monkeypatch):
    data = dataset(200)
    observed = []
    monkeypatch.setattr(map_narrative, "configured", lambda: True)

    def generate(documents, tasks, *args, **kwargs):
        observed.append((len(documents), len(tasks)))
        assert {d["task_id"] for d in documents} <= {t["id"] for t in tasks}
        return cards(documents)

    monkeypatch.setattr(map_narrative, "generate", generate)
    result = map_narrative.enrich(tmp_path, data, requests(data, 8), "en-US", project_root=tmp_path)
    assert len(observed) == 1
    assert observed[0][0] == 2
    # The current reader sends relevant neighbors, not 16 unrelated tasks.
    assert 2 <= observed[0][1] <= 16
    assert len(result["cards"]) == 2


def test_bounded_prompt_does_not_mutate_research_evidence_or_effort(tmp_path, monkeypatch):
    data = dataset()
    data["tasks"][0]["objective"] = "evidence " * 5000
    documents = map_narrative.card_evidence(data, requests(data))
    documents[0]["events"] = [{"id": str(i), "text": "observation " * 2000} for i in range(16)]
    original = copy.deepcopy(documents)
    config = map_model.MapModel("pi", "", "medium", sys.executable)

    def run(prompt, output_schema, selected, **kwargs):
        assert len(prompt) < 40000
        assert '"evidence_truncated": true' in prompt
        assert selected.effort == "medium"
        assert output_schema["properties"]["relations"]["maxItems"] == 8
        return {"cards": {"t0": {
            "title": "Finding", "summary": "Evidence", "detail": "Limits",
            "reader_brief": {"why": "Foundation", "concept": None,
                             "scope": "Recorded task", "next": "No next step recorded"},
        }}, "relations": []}

    # Teaching-review semantics have their own upstream suite. This test isolates
    # source bounding while still satisfying the current reader document schema.
    monkeypatch.setattr(map_narrative, "review_concepts", lambda *a, **kw: ({}, {}, {}))
    monkeypatch.setattr(map_narrative, "run_map_model", run)
    map_narrative.generate(documents, [], "en-US", config=config, project_root=tmp_path, global_root=tmp_path)
    assert documents == original


def test_configurable_deadline_preserves_unpriced_receipt_and_running_research(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_COST_CONTROL", "1")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "100")
    monkeypatch.setenv("ARGUS_SKILL_MAP_TIMEOUT_SECONDS", "600")
    project = tmp_path / "projects/s-map"
    running, _ = cost_control.reserve_call_budget(
        call_id="research", project_root=project, mission_id="research", provider="pi",
        model="test-model", run_label="engineer-r1", global_root=tmp_path,
    )
    assert running is not None
    clock = [0.0]
    # Do not change the shared time module used by lock timeout machinery.
    class Clock:
        @staticmethod
        def monotonic():
            return clock[0]
    monkeypatch.setattr(map_model, "time", Clock)

    def interrupted(self, **kwargs):
        options = kwargs["options"]
        assert options.reasoning_effort == "medium"
        clock[0] = 180
        assert options.external_interrupt_reason_provider() is None
        clock[0] = 601
        reason = options.external_interrupt_reason_provider()
        assert "Map text generation timed out" in reason
        return AgentRunResult(command=[], exit_code=-1, fatal_error="External interrupt: " + reason)

    monkeypatch.setattr(AgentCliRunner, "run_exec", interrupted)
    with pytest.raises(map_model.MapGenerationError) as error:
        map_model.run_map_model("Facts", {}, map_model.MapModel("pi", "", "medium", sys.executable),
                                project_root=project, global_root=tmp_path)
    assert error.value.code == "map_timeout"
    row = UsageLedger(project, migrate_legacy=False).records()[0]
    assert row.run_label == "map-summary" and row.cost_usd is None
    assert row.pricing_status == "unpriced"
    assert running.observe_cost(1) == ""
    assert cost_control.cost_control_snapshot(global_root=tmp_path)["blocking_unresolved_calls"] == 1
    assert not list((tmp_path / "map-presentation").glob("generation-*"))


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "3601", "1.5"])
def test_invalid_map_deadline_is_rejected_before_provider_construction(tmp_path, monkeypatch, value):
    monkeypatch.setenv("ARGUS_SKILL_MAP_TIMEOUT_SECONDS", value)
    monkeypatch.setattr(map_model, "AgentCliBackend", lambda **kw: pytest.fail("provider constructed"))
    with pytest.raises(ValueError):
        map_model.run_map_model("Facts", {}, map_model.MapModel("pi", "", "medium", sys.executable),
                                project_root=tmp_path, global_root=tmp_path)


def test_provider_output_length_is_validated_not_just_requested(tmp_path, monkeypatch):
    from argus.core.models import RunnerResult

    monkeypatch.setattr(map_model, "run_exec", lambda *args, **kwargs: RunnerResult(
        exit_code=0, agent_messages=['{"title":"a much too long title"}'],
    ))
    output_schema = {"type": "object", "properties": {"title": {"type": "string", "maxLength": 3}}}
    with pytest.raises(ValueError, match="bounded output schema"):
        map_model.run_map_model("Facts", output_schema, map_model.MapModel("pi", "", "medium", sys.executable),
                                project_root=tmp_path, global_root=tmp_path)
    assert not list((tmp_path / "map-presentation").glob("generation-*"))


def test_cost_acknowledgement_api_requires_auth_and_correct_project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    for sid in ["s-map", "s-other"]:
        write_session_meta(tmp_path, SessionMeta(id=sid, created=1, last_active=1))
    project = tmp_path / "projects/s-map"
    ledger = UsageLedger(project, migrate_legacy=False)
    ledger.append(UsageRecord.from_jsonable({
        "call_id": "map-timeout", "project_id": "s-map", "provider": "pi", "run_label": "map-summary",
        "status": "error", "cost_usd": None, "pricing_status": "unpriced", "completed_at": time.time(),
    }))
    before = ledger.path.read_bytes()
    body = {"call_id": "map-timeout", "liability_usd": 5.0, "reason": "Explicit operator approval"}
    headers = {"Authorization": "Bearer test"}
    with TestClient(create_app(global_root=tmp_path, auth_token="test")) as client:
        path = "/api/projects/s-map/cost-control/acknowledge"
        assert client.post(path, json=body).status_code == 401
        status_path = "/api/projects/s-map/cost-control"
        assert client.get(status_path).status_code == 401
        status = client.get(status_path, headers=headers).json()
        assert status["cost_control"]["unresolved"][0]["call_id"] == "map-timeout"
        assert status["admission_reason"].startswith("unresolved provider cost")
        assert client.post(path.replace("s-map", "s-other"), json=body, headers=headers).status_code == 404
        assert client.post(path, json={**body, "liability_usd": 0}, headers=headers).status_code == 422
        result = client.post(path, json=body, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["cost_control"]["pending_liability_usd"] == 5
        assert result.json()["cost_control"]["blocking_unresolved_calls"] == 0
        assert result.json()["admission_reason"] == ""
        repeated = client.post(path, json=body, headers=headers)
        assert repeated.json()["acknowledgement"] == result.json()["acknowledgement"]
    assert ledger.path.read_bytes() == before

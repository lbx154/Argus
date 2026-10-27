"""The model chooser shows recorded prices, speed and availability; only an explicit test calls a model."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from argus.core.models import RunnerResult
from argus.core.pricing import quote_token_usage
from argus.core.session import SessionMeta, write_session_meta
from argus.webapi import mission_items, model_choices
from argus.webapi.server import create_app


def _call(model, *, mission, at, duration_ms=20_000, error="", tokens=(100_000, 90_000, 2_000)):
    return {
        "model": model, "provider": "codex", "mission_id": mission, "completed_at": at,
        "duration_ms": duration_ms, "status": "error" if error else "completed", "error": error,
        "input_tokens": None if error else tokens[0],
        "cached_input_tokens": None if error else tokens[1],
        "output_tokens": None if error else tokens[2],
        "reasoning_output_tokens": None if error else 100,
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    pi = tmp_path / "pi"
    pi.mkdir()
    (pi / "models.json").write_text(json.dumps({"providers": {"p": {"models": [{"id": "pi-only-model"}, {"id": "gpt-5.6-luna"}]}}}))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_MODEL_CHOICES", "gpt-6.1-sol, gpt-5.6-luna, gpt-6-astra, brand-new")
    monkeypatch.delenv("ARGUS_SKILL_MODEL", raising=False)
    life = tmp_path / "projects" / "one"
    life.mkdir(parents=True)
    write_session_meta(tmp_path, SessionMeta(id="one", cwd=str(life), workdir=str(life)))
    now = time.time()
    rows = [
        _call("gpt-6.1-sol", mission="m1", at=now - 300, duration_ms=10_000),
        _call("gpt-6.1-sol", mission="m1", at=now - 200, duration_ms=30_000, tokens=(300_000, 200_000, 5_000)),
        _call("gpt-6.1-sol", mission="m2", at=now - 100, duration_ms=20_000),
        _call("gpt-6-astra", mission="m3", at=now - 50, error="503 quota exceeded for this deployment\nretry later"),
    ]
    (life / "usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    with TestClient(create_app(global_root=tmp_path, auth_token="choices-test")) as web:
        web.headers["Authorization"] = "Bearer choices-test"
        yield web, life


def _expected_task_cost(model):
    def cost(tokens):
        return quote_token_usage(
            model, input_tokens=tokens[0], cached_input_tokens=tokens[1],
            output_tokens=tokens[2], reasoning_output_tokens=100, cache_write_tokens=None,
        ).cost_usd
    m1 = cost((100_000, 90_000, 2_000)) + cost((300_000, 200_000, 5_000))
    m2 = cost((100_000, 90_000, 2_000))
    return (m1 + m2) / 2


def test_choices_come_from_the_price_table_and_recorded_calls_without_calling_a_model(client, monkeypatch):
    web, life = client
    called = []
    monkeypatch.setattr(model_choices, "probe_model", lambda *a, **k: called.append(a))
    before = (life / "usage.jsonl").read_text()

    body = web.get("/api/projects/one/model-choices").json()

    assert called == [] and (life / "usage.jsonl").read_text() == before
    assert body["backend"] == "codex" and body["billing"] == "token"
    assert body["task_sample"] == 2  # m3 recorded no tokens
    rows = {row["model"]: row for row in body["options"]}
    assert {"gpt-6.1-sol", "gpt-5.6-luna", "gpt-6-astra", "brand-new"} <= set(rows)
    # Pi's harness catalog is not what a Codex provider serves.
    assert "pi-only-model" not in rows

    sol = rows["gpt-6.1-sol"]
    assert sol["price"] == {"input": 2.0, "cached_input": pytest.approx(sol["price"]["cached_input"]), "output": 10.0}
    assert sol["task_estimate_usd"] == pytest.approx(_expected_task_cost("gpt-6.1-sol"))
    assert sol["latency_ms"] == 20_000 and sol["latency_samples"] == 3
    assert sol["availability"]["state"] == "ok"

    # The same recorded tasks, re-priced for a model that never ran them.
    luna = rows["gpt-5.6-luna"]
    assert luna["task_estimate_usd"] == pytest.approx(_expected_task_cost("gpt-5.6-luna"))
    assert luna["task_estimate_usd"] < sol["task_estimate_usd"]
    assert luna["latency_ms"] is None and luna["availability"]["state"] == "unknown"

    astra = rows["gpt-6-astra"]
    assert astra["price"] is None and astra["task_estimate_usd"] is None
    assert astra["availability"]["state"] == "failed"
    assert astra["availability"]["reason"] == "503 quota exceeded for this deployment"

    assert rows["brand-new"]["price"] is None and rows["brand-new"]["availability"]["state"] == "unknown"


def test_no_task_estimate_without_recorded_tasks(client):
    web, life = client
    (life / "usage.jsonl").write_text("")
    body = web.get("/api/projects/one/model-choices").json()
    assert body["task_sample"] == 0
    assert all(row["task_estimate_usd"] is None for row in body["options"])


def test_probe_makes_one_low_effort_call_billed_to_the_project(client, monkeypatch):
    web, life = client
    seen = {}

    def fake_run_exec(runner, *, prompt, options, run_label, resume_thread_id):
        seen.update(model=options.model, effort=options.reasoning_effort, tools=options.disable_tools,
                    label=run_label, usage_root=runner._usage_project_root)
        return RunnerResult(exit_code=0, agent_messages=["OK"])

    monkeypatch.setattr("argus.core.run_gateway.run_exec", fake_run_exec)
    monkeypatch.setattr(mission_items, "_active_backend", lambda backend=None: ("codex", "/bin/true"))
    response = web.post("/api/projects/one/model-choices/probe", json={"model": "gpt-6.1-sol"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen == {"model": "gpt-6.1-sol", "effort": "low", "tools": True,
                    "label": "model-probe", "usage_root": life}


def test_probe_reports_the_failure_and_refuses_a_second_test_in_flight(client, monkeypatch):
    web, _life = client
    monkeypatch.setattr(
        "argus.core.run_gateway.run_exec",
        lambda *a, **k: RunnerResult(exit_code=1, fatal_error="503 quota exceeded\ndetails"),
    )
    monkeypatch.setattr(mission_items, "_active_backend", lambda backend=None: ("codex", "/bin/true"))
    failed = web.post("/api/projects/one/model-choices/probe", json={"model": "gpt-6-astra"}).json()
    assert failed["ok"] is False and failed["error"] == "503 quota exceeded"

    model_choices._probing.add("gpt-6-astra")
    try:
        busy = web.post("/api/projects/one/model-choices/probe", json={"model": "gpt-6-astra"})
    finally:
        model_choices._probing.discard("gpt-6-astra")
    assert busy.status_code == 409


def test_a_per_role_choice_survives_a_restart_under_a_deployment_model(tmp_path, monkeypatch):
    """Saved role pins outrank a deployment ARGUS_SKILL_MODEL after a restart, as they did when saved."""
    from argus.core.knob_store import write_persisted_knobs
    from argus.core.knobs import resolve_role_model, resolve_role_reasoning_effort

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    assert write_persisted_knobs({
        "ARGUS_SKILL_REVIEWER_MODEL": "gpt-5.6-luna",
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT": "medium",
        "ARGUS_SKILL_ENGINEER_MODEL": "",  # released by "one model for every role"
    })
    env = {"ARGUS_SKILL_MODEL": "gpt-6.1-sol", "ARGUS_SKILL_PLAN_MODEL": "deployment-planner"}

    mission_items.prefer_saved_over_deployment_env(env)

    assert resolve_role_model("reviewer", role_env="ARGUS_SKILL_REVIEWER_MODEL", env=env) == "gpt-5.6-luna"
    assert resolve_role_reasoning_effort("ARGUS_SKILL_REVIEWER_REASONING_EFFORT", env=env) == "medium"
    # Roles with nothing saved keep the deployment model; a deployment role value stays.
    assert resolve_role_model("engineer", role_env="ARGUS_SKILL_ENGINEER_MODEL", env=env) == "gpt-6.1-sol"
    assert env["ARGUS_SKILL_PLAN_MODEL"] == "deployment-planner"

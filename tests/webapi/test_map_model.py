import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from argus.agent_cli.agent_cli_runner import AgentCliRunner
from argus.agent_cli.models import AgentRunResult
from argus.core.knob_store import write_persisted_knobs
from argus.core.models import RunnerResult
from argus.core.session import SessionMeta, write_session_meta
from argus.core.usage import UsageLedger
from argus.webapi import map_model
from argus.webapi.server import create_app


def schema(keys: list[str], task_ids: list[str]) -> dict:
    """A cards-and-relations output schema: one required card per key."""
    def obj(props):
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    text = {"type": "string", "minLength": 1}
    brief = obj({"why": text, "concept": {"anyOf": [{"type": "object"}, {"type": "null"}]}, "scope": text, "next": text})
    card = obj({"reader_brief": brief, "title": text, "summary": text, "detail": text})
    relation = obj({key: {"type": "string", "enum": task_ids} for key in ("source", "target")} | {"label": {"type": "string"}})
    return obj({"cards": obj({key: card for key in keys}), "relations": {"type": "array", "items": relation}})


def test_map_output_recovers_only_the_observed_premature_cards_root_closure():
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


def test_map_output_keeps_valid_json_escapes_and_does_not_guess_unicode():
    raw = r'{"detail":"quote \" slash \\ solidus \/ controls \b\f\n\r\t unicode \u03c1"}'
    assert map_model._parse_document(raw, {}) == json.loads(raw)
    # An unknown escape elsewhere must not change already valid escapes.
    mixed = raw.replace('quote ', r'\le quote ')
    assert map_model._parse_document(mixed, {})['detail'] == '\\le ' + json.loads(raw)['detail']
    for malformed in (r'{"detail":"\u12 \le"}', r'{"detail":"\le"', r'{"detail":"\le"} trailing'):
        with pytest.raises(ValueError):
            map_model._parse_document(malformed, {})


def reading_schema():
    from argus.webapi.reader_foundation_prompt import foundation_request

    return foundation_request("Explain a comparison", "en-US")[1]


@pytest.mark.parametrize("raw", [
    "No heading\n\nAn answer", "## A subheading\n\nAn answer", "# Only a title", "# Empty\n\n",
    "#   \n\nAn answer", "```markdown\n# A title\n\nAn answer\n```",
    '{"title":"A title","markdown":"An answer"}',
    "# " + "t" * 161 + "\n\nAn answer", "# Title\n\n" + "x" * 32_000,
])
def test_markdown_requires_the_actual_first_H1_body_and_existing_limits(raw):
    with pytest.raises(ValueError):
        map_model._parse_markdown_document(raw, reading_schema())


def test_map_runs_the_light_model_on_the_shared_runner(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_MANAGER_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.5")
    # Retired map-only settings are ignored.
    write_persisted_knobs({"ARGUS_SKILL_MAP_MODEL": "retired", "ARGUS_SKILL_MAP_REASONING_EFFORT": "max"})
    before = map_model.resolve_map_model()
    assert (before.backend, before.model, before.effort) == ("codex", "gpt-5.4-mini", "low")
    monkeypatch.setenv("ARGUS_SKILL_LIGHT_MODEL", "gpt-5.5")
    monkeypatch.setenv("ARGUS_SKILL_LIGHT_REASONING_EFFORT", "high")
    changed = map_model.resolve_map_model()
    assert (changed.backend, changed.model, changed.effort) == ("codex", "gpt-5.5", "high")
    assert changed.revision != before.revision


def test_light_model_settings_use_existing_config_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_MODEL", "gpt-5.4-mini")
    write_session_meta(tmp_path, SessionMeta(id="s-settings", created=1, last_active=1))
    with TestClient(create_app(global_root=tmp_path, auth_token="test")) as client:
        path = "/api/projects/s-settings/config/set"
        request = {"name": "ARGUS_SKILL_LIGHT_MODEL", "value": "gpt-5.5"}
        assert client.post(path, json=request).status_code == 401
        headers = {"Authorization": "Bearer test"}
        changed = client.post(path, json=request, headers=headers)
        assert changed.status_code == 200 and not changed.json()["restart_required"]
        assert map_model.resolve_map_model().model == "gpt-5.5"
        config = client.get("/api/projects/s-settings/config", headers=headers).json()
        assert next(r for r in config["roles"] if r["role"] == "engineer")["model"] == "gpt-5.4-mini"
        assert client.post(path, json={**request, "value": "auto"}, headers=headers).status_code == 200
        assert map_model.resolve_map_model().model == map_model.resolve_role_config("manager").model
        assert client.post(path, json={**request, "value": "not a model"}, headers=headers).status_code == 400
        effort = {"name": "ARGUS_SKILL_LIGHT_REASONING_EFFORT", "value": "high"}
        changed = client.post(path, json=effort, headers=headers)
        assert changed.status_code == 200 and not changed.json()["restart_required"]
        assert map_model.resolve_map_model().effort == "high"
        assert client.post(path, json={**effort, "value": "invalid"}, headers=headers).status_code == 400


@pytest.mark.parametrize("runner", ["codex", "pi"])
@pytest.mark.parametrize("run_label", [None, "reader-foundation", "reader-clarification"])
@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_map_runner_uses_shared_usage_ledger_and_read_only_turn(tmp_path, monkeypatch, runner, run_label, output_format):
    observed = []
    phases = []
    receipts = []
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    def run(self, **kwargs):
        assert phases == ["planning"]
        options = kwargs["options"]
        assert options.disable_tools and options.force_safe_mode
        assert options.sandbox_mode == "read-only"
        assert options.output_schema == ({} if output_format == "json" else None)
        assert kwargs.get("resume_thread_id") is None
        observed.append(options)
        return AgentRunResult(
            command=[], exit_code=0, turn_completed=True,
            agent_messages=['{"cards":{},"relations":[]}' if output_format == "json" else "# A reading\n\nLiteral " + r"\frac{10}{7}"],
            usage_model="gpt-5.4-mini",
            json_events=[{"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 20}}],
        )

    monkeypatch.setattr(AgentCliRunner, "run_exec", run)
    project = tmp_path / "projects/s-map"
    config = map_model.MapModel(runner, "gpt-5.4-mini", "low", sys.executable)
    result = map_model.run_map_model("Summarize these records", {}, config, project_root=project, global_root=tmp_path,
                                     on_progress=phases.append, phase="planning", on_result=receipts.append,
                                     **({"output_format": output_format} if output_format == "markdown" else {}),
                                     **({"run_label": run_label} if run_label is not None else {}))
    expected = {"cards": {}, "relations": []} if output_format == "json" else {
        "title": "A reading", "markdown": "# A reading\n\nLiteral " + r"\frac{10}{7}"}
    assert result == expected and len(observed) == 1
    assert phases == ["planning"]
    rows = UsageLedger(project).records()
    assert len(rows) == 1
    row = rows[0].to_jsonable()
    assert row["run_label"] == (run_label or "map-summary")
    assert row["mission_id"] is None
    assert len(receipts) == 1
    assert receipts[0].call_id == row["call_id"]
    assert receipts[0].call_id_log_correlated is True
    assert row["model"] == "gpt-5.4-mini" and row["input_tokens"] == 100
    assert row["cost_usd"] is not None
    assert not list((tmp_path / "map-presentation").glob("generation-*"))


@pytest.mark.parametrize(("result_options", "raw", "error"), [
    ({}, '{"markdown":"A saved explanation"}', None),
    ({"exit_code": 1}, '{"markdown":"Unfinished"}', OSError),
    ({"fatal_error": "runner failed"}, '{"markdown":"Unfinished"}', OSError),
    ({"tool_activity_observed": True}, '{"markdown":"Unexpected tool use"}', ValueError),
    ({}, 'not JSON', ValueError),
    ({}, '{}', ValueError),
])
def test_map_result_receipt_survives_execution_and_output_failures(tmp_path, monkeypatch, result_options, raw, error):
    receipt = RunnerResult(
        **{"exit_code": 0, **result_options}, agent_messages=[raw],
        call_id="actual-runtime-call", call_id_log_correlated=True,
    )
    received = []
    monkeypatch.setattr(map_model, "run_exec", lambda *args, **kwargs: receipt)
    schema = {"type": "object", "properties": {"markdown": {"type": "string"}}, "required": ["markdown"]}

    def generate():
        return map_model.run_map_model(
            "Explain the selected question", schema,
            map_model.MapModel("pi", "gpt-5.4-mini", "low", sys.executable),
            project_root=tmp_path / "projects/s-foundation", global_root=tmp_path,
            run_label="reader-foundation", on_result=received.append,
        )

    if error is None:
        assert generate() == {"markdown": "A saved explanation"}
    else:
        with pytest.raises(error):
            generate()
    assert len(received) == 1 and received[0] is receipt
    assert not list((tmp_path / "map-presentation").glob("generation-*"))


def test_unknown_output_format_fails_before_runner_or_phase(tmp_path, monkeypatch):
    phases, receipts = [], []
    monkeypatch.setattr(map_model, "_run_map_turn", lambda *a, **k: pytest.fail("Unknown format ran a model"))
    with pytest.raises(ValueError, match="unsupported map output format"):
        map_model.run_map_model("Question", {}, map_model.MapModel("pi", "model", "low", sys.executable),
            project_root=tmp_path, global_root=tmp_path, output_format="xml",
            on_progress=phases.append, on_result=receipts.append)
    assert phases == receipts == []
    assert not (tmp_path / "map-presentation").exists()


@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_expired_map_deadline_does_not_report_a_model_phase(tmp_path, monkeypatch, output_format):
    phases = []
    receipts = []
    monkeypatch.setattr(map_model, "run_exec", lambda *args, **kwargs: pytest.fail("Expired request ran a model"))
    with pytest.raises(map_model.MapGenerationError) as failure:
        map_model.run_map_model(
            "Expired request", {}, map_model.MapModel("pi", "gpt-5.5", "medium", sys.executable),
            project_root=tmp_path, global_root=tmp_path, deadline=time.monotonic() - 1,
            on_progress=phases.append, phase="writing", on_result=receipts.append,
            output_format=output_format,
        )
    assert failure.value.code == "map_timeout"
    assert phases == []
    assert receipts == []
    assert not (tmp_path / "map-presentation").exists()

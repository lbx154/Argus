import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from argus_skill.agent_cli.agent_cli_runner import AgentCliRunner
from argus_skill.agent_cli.models import AgentRunResult
from argus_skill.core.knob_store import write_persisted_knobs
from argus_skill.core.models import RunnerResult
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


@pytest.mark.parametrize(("raw", "expected"), [
    (r'{"detail":"\\rho(X) \le 15"}', r'\rho(X) \le 15'),
    (r'{"detail":"C:\work\project\main.cpp"}', r'C:\work\project\main.cpp'),
    (r'{"detail":"a \"quote\" then \le; newline \n; pair \\\\"}', 'a "quote" then \\le; newline \n; pair \\\\'),
    (r'{"detail":"odd \\\le"}', r'odd \\le'),
])
def test_map_output_preserves_literal_unknown_escapes(raw, expected):
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)
    assert map_model._parse_document(raw, {}) == {"detail": expected}


def test_map_output_keeps_valid_json_escapes_and_does_not_guess_unicode():
    raw = r'{"detail":"quote \" slash \\ solidus \/ controls \b\f\n\r\t unicode \u03c1"}'
    assert map_model._parse_document(raw, {}) == json.loads(raw)
    # An unknown escape elsewhere must not change already valid escapes.
    mixed = raw.replace('quote ', r'\le quote ')
    assert map_model._parse_document(mixed, {})['detail'] == '\\le ' + json.loads(raw)['detail']
    for malformed in (r'{"detail":"\u12 \le"}', r'{"detail":"\le"', r'{"detail":"\le"} trailing'):
        with pytest.raises(ValueError):
            map_model._parse_document(malformed, {})


def test_unknown_escape_recovery_reuses_card_boundary_and_schema_validation():
    raw = r'{"cards":{"a":"literal \le"}},"relations":[]}'
    assert map_model._parse_document(raw, {}) == {'cards': {'a': r'literal \le'}, 'relations': []}
    with pytest.raises(ValueError, match='schema'):
        map_model._parse_document(raw, {'required': ['missing']})


def reading_schema():
    from argus_skill.webapi.reader_foundation_prompt import foundation_request

    return foundation_request("Explain a comparison", "en-US")[1]


def test_markdown_preserves_the_entire_document_and_literal_math_backslashes(monkeypatch):
    raw = "# A comparison\n\n" + r"\[\frac{10}{7}=\lambda.\] Literal \u0005 stays literal."
    monkeypatch.setattr(map_model, "_document_value", lambda *a: pytest.fail("Markdown was JSON-decoded"))
    monkeypatch.setattr(map_model, "_literal_unknown_escapes", lambda *a: pytest.fail("Markdown was rewritten"))
    result = map_model._parse_markdown_document(raw, reading_schema())
    assert result == {"title": "A comparison", "markdown": raw}
    assert "\f" not in result["markdown"] and "\x05" not in result["markdown"]


@pytest.mark.parametrize("raw", [
    "No heading\n\nAn answer", "## A subheading\n\nAn answer", "# Only a title", "# Empty\n\n",
    "#   \n\nAn answer", "```markdown\n# A title\n\nAn answer\n```",
    '{"title":"A title","markdown":"An answer"}',
    "# " + "t" * 161 + "\n\nAn answer", "# Title\n\n" + "x" * 32_000,
])
def test_markdown_requires_the_actual_first_H1_body_and_existing_limits(raw):
    with pytest.raises(ValueError):
        map_model._parse_markdown_document(raw, reading_schema())


def test_markdown_accepts_the_complete_local_limit_without_truncation():
    prefix = "# " + "t" * 160 + "\n\n"
    raw = prefix + "x" * (32_000 - len(prefix))
    result = map_model._parse_markdown_document(raw, reading_schema())
    assert len(result["title"]) == 160 and result["markdown"] == raw


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


def test_review_override_changes_only_the_checker_and_its_effective_revision(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_ENGINEER_REASONING_EFFORT", "high")
    monkeypatch.setenv("ARGUS_SKILL_MAP_REASONING_EFFORT", "medium")
    monkeypatch.setenv("ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "auto")
    inherited = map_model.resolve_map_model()
    assert inherited.for_review().effort == "medium"
    monkeypatch.setenv("ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "high")
    changed = map_model.resolve_map_model()
    review = changed.for_review()
    assert changed.effort == inherited.effort == "medium"
    assert review.effort == "high"
    assert (review.backend, review.model, review.runner_bin, review.extra_args) == (
        inherited.backend, inherited.model, inherited.runner_bin, inherited.extra_args)
    assert changed.revision != inherited.revision
    assert review.revision != inherited.for_review().revision
    assert map_model.resolve_role_config("engineer").effort == "high"


def test_map_settings_use_existing_config_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_MAP_MODEL", "auto")
    monkeypatch.setenv("ARGUS_SKILL_MAP_REASONING_EFFORT", "auto")
    monkeypatch.setenv("ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "auto")
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
        review_request = {"name": "ARGUS_SKILL_MAP_REVIEW_REASONING_EFFORT", "value": "high"}
        changed = client.post(path, json=review_request, headers=headers)
        assert changed.status_code == 200 and not changed.json()["restart_required"]
        assert map_model.resolve_map_model().for_review().effort == "high"
        assert client.post(path, json={**review_request, "value": "invalid"}, headers=headers).status_code == 400
        assert client.post(path, json={**review_request, "value": "auto"}, headers=headers).status_code == 200
        assert map_model.resolve_map_model().for_review().effort == map_model.resolve_map_model().effort


@pytest.mark.parametrize("runner", ["codex", "pi"])
@pytest.mark.parametrize("run_label", [None, "reader-foundation", "reader-clarification"])
@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_map_runner_uses_shared_usage_ledger_and_read_only_turn(tmp_path, monkeypatch, runner, run_label, output_format):
    observed = []
    phases = []
    receipts = []
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "100")
    monkeypatch.setenv("ARGUS_SKILL_CODEX_DAILY_CALL_CAP", "100")

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


@pytest.mark.parametrize("result_options,raw,error", [
    ({}, "# A reading\n\nA complete answer.", None),
    ({"exit_code": 1}, "# Interrupted\n\nPartial answer.", OSError),
    ({"fatal_error": "runner failed"}, "# Interrupted\n\nPartial answer.", OSError),
    ({"tool_activity_observed": True}, "# Unexpected tools\n\nAnswer.", ValueError),
    ({}, "An answer without its title", ValueError),
    ({}, "# Empty answer", ValueError),
])
def test_markdown_receipt_precedes_validation_and_failures_never_retry(tmp_path, monkeypatch, result_options, raw, error):
    receipt = RunnerResult(**{"exit_code": 0, **result_options}, agent_messages=[raw],
                           call_id="actual-markdown-call", call_id_log_correlated=True)
    received, calls = [], []

    def run(*args, **kwargs):
        assert kwargs["options"].output_schema is None
        assert kwargs["options"].disable_tools is True
        calls.append(kwargs)
        return receipt

    monkeypatch.setattr(map_model, "run_exec", run)
    original = map_model._parse_markdown_document

    def parse(*args):
        assert received == [receipt]
        return original(*args)

    monkeypatch.setattr(map_model, "_parse_markdown_document", parse)

    def generate():
        return map_model.run_map_model("Explain the selected reading", reading_schema(),
            map_model.MapModel("pi", "gpt-5.4-mini", "low", sys.executable),
            project_root=tmp_path / "project", global_root=tmp_path,
            run_label="reader-clarification", on_result=received.append, output_format="markdown")

    if error is None:
        assert generate() == {"title": "A reading", "markdown": raw}
    else:
        with pytest.raises(error):
            generate()
    assert len(calls) == 1 and received == [receipt]
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
    with pytest.raises(OSError, match="timed out"):
        map_model.run_map_model(
            "Expired request", {}, map_model.MapModel("pi", "gpt-5.5", "medium", sys.executable),
            project_root=tmp_path, global_root=tmp_path, deadline=time.monotonic() - 1,
            on_progress=phases.append, phase="writing", on_result=receipts.append,
            output_format=output_format,
        )
    assert phases == []
    assert receipts == []
    assert not (tmp_path / "map-presentation").exists()


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

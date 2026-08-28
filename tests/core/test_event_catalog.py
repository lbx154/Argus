from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from argus_skill.core.event_catalog import (
    CALL_SCOPED_EVENT_TYPES,
    EVENT_ENVELOPE_VERSION,
    EVENT_PAYLOAD_SCHEMA_VERSION,
    EVENT_PAYLOAD_SCHEMAS,
    EVENT_SPECS,
    SIGNAL_EVENT_TYPES,
    EventType,
    canonical_event_type,
    new_event,
    normalize_event_envelope,
    validate_event_envelope,
)
from argus_skill.life.event_log import JsonlEventSink


def test_catalog_names_are_unique_valid_and_fully_specified() -> None:
    values = [event_type.value for event_type in EventType]
    assert len(values) == len(set(values))
    assert set(EVENT_SPECS) == set(EventType)
    for event_type in EventType:
        result = validate_event_envelope({"type": event_type.value})
        if EVENT_SPECS[event_type].required_fields:
            assert result.valid is False
            assert "missing required fields" in result.errors[0]
        else:
            assert result.valid is True


def test_envelope_normalization_versions_events_and_marks_invalid_known_rows() -> None:
    valid = new_event(
        EventType.AGENT_IO_START,
        call_id="call-1",
        run_label="manager",
    )
    assert valid["type"] == "agent.io.start"
    assert valid["event_schema_version"] == EVENT_ENVELOPE_VERSION
    assert valid["payload_schema_version"] == 1
    assert isinstance(valid["ts"], float)
    assert "event_validation" not in valid

    invalid = normalize_event_envelope({
        "type": EventType.AGENT_IO_START,
        "call_id": "call-1",
    })
    assert invalid["event_validation"]["status"] == "invalid"
    assert invalid["event_validation"]["errors"] == [
        "missing required fields: run_label"
    ]


def test_daemon_manager_intent_legacy_fields_are_normalized() -> None:
    event = normalize_event_envelope({
        "type": EventType.LIFE_MANAGER_INTENT_COMPLETED,
        "intent_id": "intent-daemon-1",
        "execution_task": "Optimize the kernel",
        "vertical": "software",
        "kind": "software",
        "stages": ["delivery"],
        "event_validation": {
            "status": "invalid",
            "errors": ["missing required fields: item_id, objective"],
        },
    })

    assert event["item_id"] == "intent-daemon-1"
    assert event["objective"] == "Optimize the kernel"
    assert "event_validation" not in event


def test_manager_intent_legacy_event_without_ids_gets_local_correlation() -> None:
    event = normalize_event_envelope({
        "type": EventType.LIFE_MANAGER_INTENT_COMPLETED,
        "execution_task": "Audit the runtime",
        "vertical": "software",
        "kind": "software",
        "stages": ["delivery"],
    })

    assert event["intent_id"] == event["item_id"]
    assert event["intent_id"].startswith("legacy-")
    assert event["objective"] == "Audit the runtime"
    assert "event_validation" not in event


def test_payload_schema_validates_types_and_payload_versions() -> None:
    invalid = normalize_event_envelope({
        "type": EventType.AGENT_IO_COMPLETE,
        "call_id": "call-1",
        "run_label": "engineer-r1",
        "input_tokens": "100",
    })
    assert "field input_tokens must be integer" in invalid["event_validation"]["errors"]

    usage = normalize_event_envelope({
        "type": EventType.USAGE_RECORDED,
        "call_id": "call-2",
        "schema_version": 2,
        "provider": "codex",
        "status": "completed",
        "usage": {},
        "pricing": {},
    })
    assert usage["payload_schema_version"] == 2
    assert "event_validation" not in usage

    unknown_resumed_premium = normalize_event_envelope({
        "type": EventType.AGENT_IO_COMPLETE,
        "call_id": "call-3",
        "run_label": "manager-frontdoor-classify",
        "premium_requests": None,
        "premium_requests_present": False,
    })
    assert "event_validation" not in unknown_resumed_premium


def test_project_completion_events_are_typed_cross_component_signals() -> None:
    completed = new_event(
        EventType.PROJECT_COMPLETED,
        vertical="software",
        source="vertical_completion_certificate",
        required_gate="metric",
        evidence_refs=["certificate:delivery"],
        from_state="active",
    )
    assert completed["type"] == "project.completed"
    assert "event_validation" not in completed
    assert EventType.PROJECT_COMPLETED.value in SIGNAL_EVENT_TYPES

    refused = new_event(
        EventType.PROJECT_COMPLETION_REFUSED,
        vertical="research",
        source="planner_verdict",
        required_gate="certified",
        reason="source is weaker than the declared gate",
    )
    assert refused["type"] == "project.completion_refused"
    assert "event_validation" not in refused
    assert EventType.PROJECT_COMPLETION_REFUSED.value in SIGNAL_EVENT_TYPES


def test_unknown_vertical_events_remain_extensible_and_legacy_aliases_are_explicit() -> None:
    unknown = validate_event_envelope({"type": "research.custom_evidence.ready"})
    assert unknown.valid is True
    assert unknown.known is False
    assert canonical_event_type("mission.started") == "life.mission.started"
    assert canonical_event_type("life.team.waiting") == "life.planner.waiting"
    aliased = normalize_event_envelope({"type": "mission.started"})
    assert aliased["canonical_type"] == "life.mission.started"


def test_event_sink_persists_versioned_envelopes_and_validation_evidence(
    tmp_path: Path,
) -> None:
    sink = JsonlEventSink(None, life_dir=tmp_path, verbosity="full")
    sink.append({
        "type": EventType.AGENT_IO_START,
        "call_id": "call-1",
        "run_label": "manager",
    })
    sink.append({"type": EventType.AGENT_IO_ERROR, "call_id": "call-2"})

    rows = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["event_schema_version"] == EVENT_ENVELOPE_VERSION
    assert "event_validation" not in rows[0]
    assert rows[1]["event_validation"]["errors"] == [
        "missing required fields: error"
    ]
    metrics = [
        json.loads(line)
        for line in (tmp_path / "metrics.jsonl").read_text().splitlines()
    ]
    assert metrics[-1]["name"] == "event.validation_failure"
    assert metrics[-1]["labels"]["type"] == "agent.io.error"


def test_frontend_event_catalog_matches_python_catalog_and_groups() -> None:
    source = (
        Path(__file__).parents[2] / "frontend" / "core" / "src" / "eventCatalog.ts"
    ).read_text(encoding="utf-8")
    object_block = source.split("EVENT_TYPES = {", 1)[1].split("} as const", 1)[0]
    frontend = dict(re.findall(r"^\s+([A-Z0-9_]+): '([^']+)',?$", object_block, re.MULTILINE))
    assert frontend == {item.name: item.value for item in EventType}

    signal_block = source.split("SIGNAL_EVENT_TYPES", 1)[1].split("]);", 1)[0]
    signal_names = set(re.findall(r"EVENT_TYPES\.([A-Z0-9_]+)", signal_block))
    assert {EventType[name].value for name in signal_names} == SIGNAL_EVENT_TYPES

    call_block = source.split("CALL_SCOPED_EVENT_TYPES", 1)[1].split("]);", 1)[0]
    call_names = set(re.findall(r"EVENT_TYPES\.([A-Z0-9_]+)", call_block))
    assert {EventType[name].value for name in call_names} == CALL_SCOPED_EVENT_TYPES


def test_payload_schema_is_standard_json_schema_and_generated_types_are_current() -> None:
    schema_path = (
        Path(__file__).parents[2]
        / "argus_skill"
        / "core"
        / "event_payload_schemas.json"
    )
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(payload)
    for event_schema in payload["events"].values():
        jsonschema.Draft202012Validator.check_schema({
            "type": "object",
            **event_schema,
        })
    assert payload["schema_version"] == EVENT_PAYLOAD_SCHEMA_VERSION
    assert set(payload["events"]) == set(EVENT_PAYLOAD_SCHEMAS)
    assert set(payload["events"]) == {event.value for event in EventType}

    generated = (
        Path(__file__).parents[2]
        / "frontend"
        / "core"
        / "src"
        / "eventPayloads.generated.ts"
    ).read_text(encoding="utf-8")
    assert f"EVENT_PAYLOAD_SCHEMA_VERSION = {EVENT_PAYLOAD_SCHEMA_VERSION}" in generated
    assert all(event_type in generated for event_type in payload["events"])


def test_generated_event_renderer_corpus_and_coverage_are_current() -> None:
    from argus_skill.release_tools import generate_event_fixtures

    corpus = json.loads(generate_event_fixtures.CORPUS_PATH.read_text(encoding="utf-8"))
    report = json.loads(generate_event_fixtures.COVERAGE_PATH.read_text(encoding="utf-8"))
    expected_types = {event.value for event in EventType}

    assert {row["event"]["type"] for row in corpus["fixtures"]} == expected_types
    assert all(validate_event_envelope(row["event"]).valid for row in corpus["fixtures"])
    assert set(report["event_types"]) == expected_types
    assert generate_event_fixtures.CORPUS_PATH.read_text(encoding="utf-8") == (
        generate_event_fixtures._json_text(generate_event_fixtures._corpus())
    )
    assert generate_event_fixtures.COVERAGE_PATH.read_text(encoding="utf-8") == (
        generate_event_fixtures._json_text(generate_event_fixtures._coverage())
    )
    assert generate_event_fixtures.TYPES_PATH.read_text(encoding="utf-8") == (
        generate_event_fixtures._types()
    )


def test_resource_status_schema_and_generated_contracts_are_current() -> None:
    from argus_skill.release_tools import generate_resource_status

    schema = json.loads(generate_resource_status.SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert generate_resource_status.OUTPUT_PATH.read_text(encoding="utf-8") == (
        generate_resource_status.render()
    )
    assert generate_resource_status.PYTHON_OUTPUT_PATH.read_text(encoding="utf-8") == (
        generate_resource_status.render_python()
    )

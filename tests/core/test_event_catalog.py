from __future__ import annotations

import json
import re
from pathlib import Path

from argus_skill.core.event_catalog import (
    CALL_SCOPED_EVENT_TYPES,
    EVENT_ENVELOPE_VERSION,
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


def test_unknown_vertical_events_remain_extensible_and_legacy_aliases_are_explicit() -> None:
    unknown = validate_event_envelope({"type": "research.custom_evidence.ready"})
    assert unknown.valid is True
    assert unknown.known is False
    assert canonical_event_type("mission.started") == "life.mission.started"
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

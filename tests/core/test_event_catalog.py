from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import jsonschema

from argus.core.event_catalog import (
    CALL_SCOPED_EVENT_TYPES,
    EVENT_PAYLOAD_SCHEMA_VERSION,
    EVENT_PAYLOAD_SCHEMAS,
    EVENT_TYPE_RE,
    LEGACY_EVENT_ALIASES,
    SIGNAL_EVENT_TYPES,
    EventType,
    normalize_event_envelope,
    validate_event_envelope,
)


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


def test_planner_idle_family_events_accept_cycle_zero_from_a_fresh_boot() -> None:
    # A daemon that skips an unchanged Planner turn, or restores an already
    # delivered terminal conclusion from the outbox, emits these before any
    # planning cycle has run in the new process. There, cycle == 0 is the
    # honest count of cycles completed so far -- the same convention
    # life.planner.start already allows.
    idle = normalize_event_envelope({
        "type": EventType.LIFE_PLANNER_IDLE,
        "cycle": 0,
        "reason": "nothing the Planner reads has changed",
        "model_call_skipped": True,
    })
    assert "event_validation" not in idle

    terminal_idle = normalize_event_envelope({
        "type": EventType.LIFE_PLANNER_TERMINAL_IDLE,
        "cycle": 0,
        "reason": "open-ended project_done unchanged since last planner run",
    })
    assert "event_validation" not in terminal_idle

    # A negative cycle is still a defect, never a boot-time value.
    negative = validate_event_envelope({
        "type": "life.planner.idle",
        "cycle": -1,
    })
    assert negative.valid is False
    assert "field cycle must be >= 0" in negative.errors


def test_legacy_validation_only_relaxes_missing_payload_fields() -> None:
    legacy = {"type": "round.review.completed", "status": "continue"}
    assert validate_event_envelope(legacy).valid is False
    assert validate_event_envelope(legacy, allow_missing_fields=True).valid is True
    for fields in ({"ts": "unparseable"}, {"round_index": []}, {"status": "invalid-status"}):
        assert validate_event_envelope(
            {**legacy, **fields}, allow_missing_fields=True,
        ).valid is False
    assert validate_event_envelope({}, allow_missing_fields=True).valid is False


def test_frontend_event_catalog_matches_python_catalog_and_groups() -> None:
    source = (
        Path(__file__).parents[2] / "packages" / "contracts" / "src" / "eventCatalog.ts"
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
        / "packages"
        / "contracts"
        / "schemas"
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
        / "packages"
        / "contracts"
        / "src"
        / "eventPayloads.generated.ts"
    ).read_text(encoding="utf-8")
    assert f"EVENT_PAYLOAD_SCHEMA_VERSION = {EVENT_PAYLOAD_SCHEMA_VERSION}" in generated
    assert all(event_type in generated for event_type in payload["events"])


def test_generated_event_renderer_corpus_covers_every_event() -> None:
    root = Path(__file__).parents[2]
    corpus = json.loads((root / "frontend/core/fixtures/eventCorpus.generated.json").read_text(encoding="utf-8"))

    assert {row["event"]["type"] for row in corpus["fixtures"]} == {event.value for event in EventType}
    assert all(validate_event_envelope(row["event"]).valid for row in corpus["fixtures"])


def test_resource_status_schema_is_standard_json_schema() -> None:
    schema_path = Path(__file__).parents[2] / "packages/contracts/schemas/resource_status.json"
    jsonschema.Draft202012Validator.check_schema(json.loads(schema_path.read_text(encoding="utf-8")))


# Modules whose dotted ``type`` strings are not events.jsonl rows: each writes
# its own ledger or speaks another protocol, so the event catalog does not own
# that vocabulary.
_NON_EVENT_MODULES = frozenset({
    # repairs/history.jsonl, the doctor's own audit ledger.
    "maintenance/repair.py",
    # Map observation nodes projected from events for the web map.
    "webapi/map_view.py",
})


def _type_literals(node: ast.expr) -> list[str] | None:
    """Every string a ``type`` expression can evaluate to; None when dynamic."""
    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        body = _type_literals(node.body)
        orelse = _type_literals(node.orelse)
        return None if body is None or orelse is None else body + orelse
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "EventType"
    ):
        return [EventType[node.attr].value]
    return None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def test_every_emitted_event_type_literal_is_in_the_catalog() -> None:
    """The catalog is the vocabulary: no emitter may invent a dotted type.

    Scans ``{"type": ...}`` literals, ``type=`` / ``event_type=`` keywords and
    the positional type argument of ``emit`` helpers. Legacy aliases count as
    catalogued because ``canonical_event_type`` resolves them.
    """
    root = Path(__file__).parents[2] / "argus"
    known = {event.value for event in EventType} | set(LEGACY_EVENT_ALIASES)
    unknown: list[str] = []
    dynamic: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if path.name == "event_catalog.py" or relative in _NON_EVENT_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            strict: list[ast.expr] = []
            loose: list[ast.expr] = []
            if isinstance(node, ast.Dict):
                strict.extend(
                    value
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and key.value == "type"
                )
            elif isinstance(node, ast.Call):
                strict.extend(
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in {"type", "event_type"}
                )
                if _call_name(node) in {"emit", "_emit"}:
                    loose.extend(
                        arg for arg in node.args if isinstance(arg, ast.Constant)
                    )
            for value in strict:
                if isinstance(value, ast.JoinedStr):
                    dynamic.append(f"{relative}:{value.lineno}")
            for value in strict + loose:
                for literal in _type_literals(value) or []:
                    if "." not in literal or EVENT_TYPE_RE.fullmatch(literal) is None:
                        continue
                    if literal not in known:
                        unknown.append(f"{relative}:{value.lineno}:{literal}")

    assert unknown == [], (
        "event types emitted outside the catalog; add them to EventType, "
        "event_payload_schemas.json, eventCatalog.ts and the renderers:\n"
        + "\n".join(unknown)
    )
    assert dynamic == [], (
        "event types built from f-strings cannot be checked against the "
        "catalog; pick the member per branch instead:\n" + "\n".join(dynamic)
    )

#!/usr/bin/env python3
"""Generate the cross-language event renderer fixture corpus and coverage report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from argus_skill.core.event_catalog import (
    EVENT_PAYLOAD_SCHEMA_VERSION,
    EVENT_PAYLOAD_SCHEMAS,
    EventType,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "frontend" / "core" / "fixtures"
CORPUS_PATH = FIXTURE_DIR / "eventCorpus.generated.json"
COVERAGE_PATH = FIXTURE_DIR / "eventRendererCoverage.generated.json"
TYPES_PATH = ROOT / "frontend" / "core" / "src" / "eventCorpus.generated.ts"

_STRING_VALUES = {
    "action": "advance",
    "agent_layer": "engineer",
    "backend": "codex",
    "call_id": "call-1",
    "cause": "provider rejected the request",
    "command_id": "command-1",
    "contract_field": "research_target_level",
    "diagnostic": "removed duplicate planner task",
    "error": "provider request failed",
    "from_state": "active",
    "intent_id": "intent-1",
    "item_id": "task-1",
    "kind": "agent_message",
    "label": "implementation",
    "lifetime": "bounded",
    "manager_action": "keep",
    "model": "gpt-5",
    "next_step": "run the focused verification",
    "objective": "Implement the shared semantic event renderer",
    "operation": "replace",
    "phase": "backend",
    "plan_id": "plan-1",
    "pricing_status": "priced",
    "provider": "openai",
    "reason": "the current operation needs attention",
    "reservation_id": "reservation-1",
    "role": "engineer",
    "route": "team",
    "run_id": "run-1",
    "run_label": "Engineer",
    "scope": "frontend/core",
    "source": "daemon",
    "stage": "implementation",
    "status": "completed",
    "stream": "stdout",
    "summary": "Implemented and verified the requested change.",
    "target_stage": "review",
    "text": "Renderer event detail",
    "title": "Shared event renderer",
    "to_state": "complete",
    "usage_scope": "mission",
    "vertical": "software",
    "workflow_mode": "staged",
}


def _example_value(schema: dict[str, Any], field: str) -> Any:
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list):
        return next((value for value in enum if value is not None), None)
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        raw_type = next((item for item in raw_type if item != "null"), "null")
    if raw_type == "string":
        return _STRING_VALUES.get(field, field.replace("_", " "))
    if raw_type == "integer":
        return max(1, int(schema.get("minimum") or 0))
    if raw_type == "number":
        return max(1.25, float(schema.get("minimum") or 0))
    if raw_type == "boolean":
        return True
    if raw_type == "array":
        return [_example_value(schema.get("items") or {}, field.rstrip("s"))]
    if raw_type == "object":
        properties = schema.get("properties") or {}
        if properties:
            return {
                key: _example_value(value, key)
                for key, value in properties.items()
            }
        if field == "usage":
            return {"input_tokens": 120, "output_tokens": 40}
        if field == "pricing":
            return {"cost_usd": 0.0025, "status": "priced"}
        return {"value": "recorded"}
    return None


def _base_event(event_type: EventType, index: int) -> dict[str, Any]:
    schema = EVENT_PAYLOAD_SCHEMAS[event_type.value]
    event = {
        "type": event_type.value,
        "ts": 1_700_000_000 + index,
        "payload_schema_version": int(schema.get("version") or 1),
    }
    event.update({
        field: _example_value(field_schema, field)
        for field, field_schema in (schema.get("properties") or {}).items()
    })
    if event_type == EventType.LIFE_MANAGER_INTENT_COMPLETED:
        event.update({"route": "team", "continuous": True, "open_ended": False})
    elif event_type == EventType.LIFE_MANAGER_INTENT_FAILED:
        event.update({
            "attempts": 2,
            "cause": "401 Missing bearer",
            "error": "VerticalDecisionError: routing failed",
            "backend_error": "401 Missing bearer",
            "model_reply_snippet": "invalid routing response",
            "answer_preserved": True,
        })
    elif event_type == EventType.LIFE_MISSION_COMPLETED:
        event.update({"status": "done", "success": True, "outcome_class": "completed"})
    elif event_type == EventType.LIFE_PLANNER_VERDICT:
        event.update({"status": "completed", "success": True})
    elif event_type == EventType.ROUND_REVIEW_COMPLETED:
        event.update({"status": "done", "reason": "focused checks passed"})
    elif event_type == EventType.ENGINEER_PROGRESS:
        event.update({"kind": "agent_message", "text": "Implemented the renderer.", "agent_layer": "engineer"})
    return event


def _fixture(fixture_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {"id": fixture_id, "event": event}


def _corpus() -> dict[str, Any]:
    fixtures = [
        _fixture(event_type.value, _base_event(event_type, index))
        for index, event_type in enumerate(EventType)
    ]
    extras = [
        _fixture("engineer.progress.reasoning", {
            "type": "engineer.progress", "kind": "reasoning", "agent_layer": "planner",
            "text": "Compare the new semantic output with both current surfaces.",
        }),
        _fixture("engineer.progress.secret-redaction", {
            "type": "engineer.progress", "kind": "agent_message", "agent_layer": "engineer",
            "text": "using token ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        }),
        _fixture("engineer.progress.handoff-fields", {
            "type": "engineer.progress", "kind": "agent_message", "agent_layer": "engineer",
            "text": "Artifact complete.\nNEXT_OWNER=reviewer\nOPERATOR_QUESTION=none",
        }),
        _fixture("engineer.progress.failed-command", {
            "type": "engineer.progress", "kind": "command_execution", "agent_layer": "engineer",
            "status": "failed", "text": "npm test", "action_summary": "run tests",
        }),
        _fixture("life.planner.task_skipped.review-purchase-deferred", {
            "type": "life.planner.task_skipped", "title": "Purchase another paper review",
            "objective": "Repeat certification review", "skip_category": "paper_review_purchase_deferred",
            "reason": "the current manuscript already has a completed independent review",
        }),
        _fixture("life.planner.waiting.waiting-resource", {
            "type": "life.planner.waiting", "cycle": 2,
            "reason": "subagent state waiting_resource is a healthy resource wait",
            "waiting_contract": {"wait_id": "subagent-1", "recheck_condition": "state leaves waiting_resource", "wait_mode": "event", "wake_on": ["subagent_state"], "observed_revision": "waiting_resource"},
        }),
    ]
    fixtures.extend(extras)
    return {
        "schema_version": 1,
        "event_payload_schema_version": EVENT_PAYLOAD_SCHEMA_VERSION,
        "fixtures": fixtures,
    }


def _function_block(source: str, marker: str) -> str:
    start = source.index(marker)
    match = re.search(r"\n(?:export )?(?:async )?(?:function |def )", source[start + len(marker):])
    return source[start:] if match is None else source[start:start + len(marker) + match.start()]


def _event_type_references(source: str) -> set[str]:
    values = {match.group(1) for match in re.finditer(r"['\"]([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*|operator_alert)['\"]", source)}
    for name in re.findall(r"EventType\.([A-Z0-9_]+)", source):
        try:
            values.add(EventType[name].value)
        except KeyError:
            pass
    return values


def _coverage() -> dict[str, Any]:
    catalog = {item.value for item in EventType}
    web_source = (ROOT / "frontend/web/src/lib/eventRender.ts").read_text(encoding="utf-8")
    tui_source = (ROOT / "frontend/tui/src/eventRender.ts").read_text(encoding="utf-8")
    follow_source = (ROOT / "argus_skill/apps/cli/_follow.py").read_text(encoding="utf-8")
    format_source = (ROOT / "argus_skill/cli/event_format.py").read_text(encoding="utf-8")
    web_block = _function_block(web_source, "export function renderEvent")
    tui_block = _function_block(tui_source, "export function renderEvent")
    follow_block = _function_block(follow_source, "def _format_follow_event_body")

    explicit_web_hidden = set(re.findall(
        r"if \(t === ['\"]([^'\"]+)['\"]\) return null", web_block,
    )) & catalog
    renderers = {
        "web": (_event_type_references(web_block) & catalog, explicit_web_hidden, "hide"),
        "tui": (_event_type_references(tui_block) & catalog, set(), "hide"),
        "python_cli": (
            (_event_type_references(follow_block) | _event_type_references(format_source)) & catalog,
            set(),
            "greppable",
        ),
    }
    report: dict[str, Any] = {"schema_version": 1, "event_types": sorted(catalog), "renderers": {}}
    for name, (referenced, hidden, fallback) in renderers.items():
        handled = referenced - hidden
        missing = catalog - handled - hidden
        report["renderers"][name] = {
            "handled": sorted(handled),
            "hidden": sorted(hidden),
            "missing": sorted(missing),
            "fallback": fallback,
            "counts": {"handled": len(handled), "hidden": len(hidden), "missing": len(missing)},
        }
    return report


def _types() -> str:
    return """// Generated by argus_skill.release_tools.generate_event_fixtures. Do not edit.
import corpus from '../fixtures/eventCorpus.generated.json' with { type: 'json' };
import type { EventPayloadByType, TypedArgusEvent } from './eventPayloads.generated.js';

export type EventCorpusFixture = {
  [Type in keyof EventPayloadByType]: {
    readonly id: string;
    readonly event: EventPayloadByType[Type];
  };
}[keyof EventPayloadByType];

export interface EventCorpus {
  readonly schema_version: 1;
  readonly event_payload_schema_version: number;
  readonly fixtures: ReadonlyArray<EventCorpusFixture>;
}

export const EVENT_CORPUS = corpus as unknown as EventCorpus;
export type CorpusEvent = TypedArgusEvent;
"""


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = {item.value for item in EventType}
    schemas = set(EVENT_PAYLOAD_SCHEMAS)
    if catalog != schemas:
        raise SystemExit(f"event catalog/schema mismatch: missing={sorted(catalog - schemas)}, extra={sorted(schemas - catalog)}")
    artifacts = {
        CORPUS_PATH: _json_text(_corpus()),
        COVERAGE_PATH: _json_text(_coverage()),
        TYPES_PATH: _types(),
    }
    if args.check:
        stale = [path for path, expected in artifacts.items() if not path.exists() or path.read_text(encoding="utf-8") != expected]
        if stale:
            raise SystemExit("generated event fixtures are stale; run python -m argus_skill.release_tools.generate_event_fixtures")
        return 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for path, rendered in artifacts.items():
        path.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

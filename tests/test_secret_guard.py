from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core import secret_guard
from argus.core.secret_guard import (
    redact_secrets_record,
    redact_secrets_text,
)
from argus.life.event_log import JsonlEventSink

# Masking is off by default (ARGUS_SKILL_REDACT_SECRETS); this module tests the
# masking itself, so it opts in. Default-off behavior is tested at the end.
pytestmark = pytest.mark.usefixtures("redact_secrets_on")


def test_structured_json_redaction_preserves_valid_json() -> None:
    redacted = redact_secrets_text(
        json.dumps(
            {
                "api_key": "json-secret-value",
                "reason": "api_key=inline-secret-value was exposed",
            }
        )
    )
    parsed = json.loads(redacted)

    assert parsed["api_key"] == "<REDACTED:secret>"
    assert "inline-secret-value" not in parsed["reason"]


def test_jsonl_redaction_preserves_each_record() -> None:
    redacted = redact_secrets_text('{"api_key":"response-secret-value"}\n{"status":"ok"}\n')
    records = [json.loads(line) for line in redacted.splitlines()]

    assert records == [
        {"api_key": "<REDACTED:secret>"},
        {"status": "ok"},
    ]


def test_header_redaction_handles_crlf_and_is_idempotent() -> None:
    redacted = redact_secrets_text(
        "Cookie: response-secret-value\r\nstatus: 200\r\n"
    )
    assert "response-secret-value" not in redacted
    assert "status: 200" in redacted
    assert "\r\nstatus: 200\r\n" in redacted
    assert redact_secrets_text(redacted) == redacted


def test_jsonl_event_sink_redacts_downstream_and_disk(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVICE_API_KEY", "live-secret-value-123")

    class Downstream:
        def __init__(self) -> None:
            self.events: list[dict] = []
            self.lines: list[str] = []

        def handle_event(self, event: dict) -> None:
            self.events.append(event)

        def handle_stream_line(self, _stream: str, line: str) -> None:
            self.lines.append(line)

    downstream = Downstream()

    class SecretRepr:
        def __repr__(self) -> str:
            return "api_key=opaque-secret-123"

    sink = JsonlEventSink(
        downstream,
        life_dir=tmp_path,
        verbosity="full",
    )
    sink.handle_event(
        {
            "type": "round.main.completed",
            "round_index": 1,
            "fatal_error": "api_key=live-secret-value-123",
            "next_step": "api_key=live-secret-value-123",
            "tuple_payload": ("api_key=opaque-secret-123",),
            "custom_payload": SecretRepr(),
        }
    )
    sink.handle_stream_line(
        "stdout",
        "api_key=live-secret-value-123",
    )

    rendered = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "live-secret-value-123" not in rendered
    assert "live-secret-value-123" not in json.dumps(downstream.events)
    assert "live-secret-value-123" not in json.dumps(downstream.lines)


@pytest.fixture
def redaction_default(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_REDACT_SECRETS", raising=False)


def test_redaction_is_off_by_default(redaction_default) -> None:
    assert secret_guard.secret_redaction_enabled() is False
    line = "Authorization: Bearer abcdefghijklmnopqrstu"
    record = {"token": "live-abcdef1234567890", "nested": [line]}

    assert redact_secrets_text(line, known_values=("abcdefghijklmnopqrstu",)) == line
    assert redact_secrets_record(record) is record

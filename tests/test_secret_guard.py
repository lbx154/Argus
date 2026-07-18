from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from argus_skill.adapters.agent_cli_backend import AgentCliBackend
from argus_skill.core import secret_guard
from argus_skill.core.models import ReviewDecision
from argus_skill.core.secret_guard import (
    ArtifactChangedDuringScrubError,
    _write_redacted,
    known_secret_values,
    redact_secrets_record,
    redact_secrets_text,
    redact_secrets_text_with_count,
    scrub_recent_text_artifacts,
)
from argus_skill.engineer.background_subagents import parse_wait_sentinel
from argus_skill.engineer.runner import (
    _apply_round_secret_guard,
    _review_event_payload,
    parse_continue_work_request,
)
from argus_skill.life.event_log import JsonlEventSink


def test_redacts_sensitive_headers_and_known_environment_values() -> None:
    env = {
        "SERVICE_API_KEY": "live-secret-value-123",
        "PATH": "/usr/bin",
    }
    known = known_secret_values(env)
    text = (
        "x-api-key: response-secret-value\n"
        "payload=live-secret-value-123\n"
        "ordinary research text"
    )

    redacted = redact_secrets_text(text, known_values=known)

    assert "response-secret-value" not in redacted
    assert "live-secret-value-123" not in redacted
    assert "ordinary research text" in redacted
    assert redact_secrets_text(redacted, known_values=known) == redacted


def test_structured_json_redaction_preserves_valid_json() -> None:
    redacted = redact_secrets_text(
        json.dumps({
            "api_key": "json-secret-value",
            "reason": "api_key=inline-secret-value was exposed",
        })
    )
    parsed = json.loads(redacted)

    assert parsed["api_key"] == "<REDACTED:secret>"
    assert "inline-secret-value" not in parsed["reason"]


def test_jsonl_redaction_preserves_each_record() -> None:
    redacted = redact_secrets_text(
        '{"api_key":"response-secret-value"}\n'
        '{"status":"ok"}\n'
    )
    records = [json.loads(line) for line in redacted.splitlines()]

    assert records == [
        {"api_key": "<REDACTED:secret>"},
        {"status": "ok"},
    ]


def test_redacts_values_under_structured_sensitive_keys() -> None:
    redacted = redact_secrets_record({
        "api_key": "response-secret-value",
        "nested": {
            "authorization": "short",
            "clientSecret": "client-value",
            "refreshToken": "refresh-value",
            "auth_token": "auth-value",
            "clientToken": "client-token-value",
            "private_token": "private-value",
            "status": "ok",
        },
    })

    assert redacted["api_key"] == "<REDACTED:secret>"
    assert redacted["nested"]["authorization"] == "<REDACTED:secret>"
    assert redacted["nested"]["clientSecret"] == "<REDACTED:secret>"
    assert redacted["nested"]["refreshToken"] == "<REDACTED:secret>"
    assert redacted["nested"]["auth_token"] == "<REDACTED:secret>"
    assert redacted["nested"]["clientToken"] == "<REDACTED:secret>"
    assert redacted["nested"]["private_token"] == "<REDACTED:secret>"
    assert redacted["nested"]["status"] == "ok"


def test_header_redaction_handles_crlf_and_does_not_recount_placeholders() -> None:
    redacted, count = redact_secrets_text_with_count(
        "Cookie: response-secret-value\r\nstatus: 200\r\n"
    )
    assert "response-secret-value" not in redacted
    assert "status: 200" in redacted
    assert "\r\nstatus: 200\r\n" in redacted
    assert count == 1

    same, second_count = redact_secrets_text_with_count(redacted)
    assert same == redacted
    assert second_count == 0


def test_scrubs_recent_text_artifacts_and_preserves_source_fixtures(
    tmp_path: Path,
) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\nstatus: 200\n", encoding="utf-8")
    source = tmp_path / "fixture.py"
    source.write_text(
        'HEADER = "x-api-key: fake-test-value"\n',
        encoding="utf-8",
    )
    active = tmp_path / ".argus_subagents" / "task_logs"
    active.mkdir(parents=True)
    active_log = active / "stdout.log"
    active_log.write_text("x-api-key: active-secret-value\n", encoding="utf-8")

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert report.redacted_paths == ("response.headers",)
    assert "new-secret-value" not in recent.read_text(encoding="utf-8")
    assert "fake-test-value" in source.read_text(encoding="utf-8")
    assert "active-secret-value" in active_log.read_text(encoding="utf-8")


def test_scrub_preserves_cue_schema_token_labels(tmp_path: Path) -> None:
    schema = tmp_path / "flipt.schema.cue"
    schema.write_text(
        "#GitAuthentication: {\n"
        "  token: access_token: string\n"
        "}\n",
        encoding="utf-8",
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert not report.changed
    assert schema.read_text(encoding="utf-8") == (
        "#GitAuthentication: {\n"
        "  token: access_token: string\n"
        "}\n"
    )


def test_scrub_skips_vendored_code_references_clones(tmp_path: Path) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\nstatus: 200\n", encoding="utf-8")

    vendored_repo = tmp_path / "code" / "references" / "some-upstream-repo"
    vendored_repo.mkdir(parents=True)
    vendored_file = vendored_repo / "fixture.json"
    vendored_file.write_text(
        '{"x-api-key": "vendored-fixture-secret"}\n', encoding="utf-8"
    )
    # Give the vendored clone a fresh mtime so the only reason it would be
    # excluded is the vendored-directory skip, not the modified_since filter.
    now = time.time()
    (vendored_repo / "fixture.json").touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ("response.headers",)
    assert "vendored-fixture-secret" in vendored_file.read_text(encoding="utf-8")
    # The vendored tree must not even be walked/counted.
    assert report.scanned_files == 1


def test_scrub_skips_project_huggingface_cache(tmp_path: Path) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\n", encoding="utf-8")

    cache_file = (
        tmp_path
        / "models"
        / "huggingface"
        / "hub"
        / "models--example--model"
        / "blobs"
        / "upstream.json"
    )
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text(
        '{"token": "public-tokenizer-schema-value"}\n',
        encoding="utf-8",
    )
    now = time.time()
    cache_file.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ("response.headers",)
    assert cache_file.read_text(encoding="utf-8") == (
        '{"token": "public-tokenizer-schema-value"}\n'
    )
    assert report.scanned_files == 1


def test_round_guard_surfaces_scrub_to_reviewer_context(tmp_path: Path) -> None:
    artifact = tmp_path / "response.txt"
    artifact.write_text("Authorization: Bearer live-token-value-123\n", encoding="utf-8")
    events: list[dict] = []

    report, reviewer_note = _apply_round_secret_guard(
        workdir=tmp_path,
        modified_since=time.time() - 5,
        round_index=2,
        round_max=10,
        on_event=events.append,
    )

    assert report.changed
    assert "live-token-value-123" not in artifact.read_text(encoding="utf-8")
    assert "SECURITY GUARD" in reviewer_note
    assert events[0]["type"] == "round.secret_redacted"
    assert events[0]["redacted_paths"] == ["response.txt"]


def test_round_guard_keeps_engineer_control_sentinels_pristine(
    tmp_path: Path,
) -> None:
    (tmp_path / "response.headers").write_text(
        "x-api-key: response-secret-value\n",
        encoding="utf-8",
    )
    _report, reviewer_note = _apply_round_secret_guard(
        workdir=tmp_path,
        modified_since=time.time() - 5,
        round_index=1,
        round_max=10,
        on_event=None,
    )
    wait_message = "WAIT_FOR_SUBAGENT: task-123"
    continue_message = "work completed\nCONTINUE_WORK: rebuild the hash chain"

    assert reviewer_note
    assert parse_wait_sentinel(wait_message) == "task-123"
    assert (
        parse_continue_work_request(continue_message)
        == "rebuild the hash chain"
    )


def test_agent_io_persistence_and_stream_callback_are_redacted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERVICE_API_KEY", "live-secret-value-123")
    streamed: list[tuple[str, str]] = []
    backend = AgentCliBackend(
        backend="copilot",
        runner_bin="copilot",
        event_callback=lambda stream, line: streamed.append((stream, line)),
    )
    path = tmp_path / "events.jsonl"
    backend._log_agent_io(path, {
        "type": "agent.io.complete",
        "stdout_lines": ["api_key=live-secret-value-123"],
    })
    context = {
        "log_path": str(path),
        "call_id": "call",
        "run_label": "engineer-r1",
        "model": "test",
        "mode": "full",
        "buffer": [],
        "buffer_bytes": 0,
        "last_flush": 0.0,
    }
    with backend._io_context_lock:
        backend._io_context = context
    backend._stream_event_callback(
        "copilot.stdout",
        "Authorization: Bearer live-secret-value-123",
    )
    backend._close_io_context("call")

    rendered = path.read_text(encoding="utf-8")
    assert "live-secret-value-123" not in rendered
    assert "live-secret-value-123" not in json.dumps(streamed)


def test_review_event_payload_redacts_reviewer_echoes(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_API_KEY", "live-secret-value-123")
    review = ReviewDecision(
        status="continue",
        reason="api_key=live-secret-value-123",
        next_action="repair the artifact",
        round_summary_markdown="credential was exposed",
        completion_summary_markdown="",
    )

    payload = _review_event_payload(
        review,
        round_index=1,
        round_max=10,
        text="review completed",
    )

    assert "live-secret-value-123" not in json.dumps(payload)


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
    sink.handle_event({
        "type": "round.main.completed",
        "round_index": 1,
        "fatal_error": "api_key=live-secret-value-123",
        "next_step": "api_key=live-secret-value-123",
        "tuple_payload": ("api_key=opaque-secret-123",),
        "custom_payload": SecretRepr(),
    })
    sink.handle_stream_line(
        "stdout",
        "api_key=live-secret-value-123",
    )

    rendered = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "live-secret-value-123" not in rendered
    assert "live-secret-value-123" not in json.dumps(downstream.events)
    assert "live-secret-value-123" not in json.dumps(downstream.lines)


def test_large_recent_text_artifact_surfaces_incomplete_coverage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 8)
    (tmp_path / "large.txt").write_text(
        "x-api-key: response-secret-value\n",
        encoding="utf-8",
    )

    report, reviewer_note = _apply_round_secret_guard(
        workdir=tmp_path,
        modified_since=time.time() - 5,
        round_index=1,
        round_max=10,
        on_event=None,
    )

    assert report.truncated is True
    assert "Coverage incomplete" in reviewer_note


def test_atomic_scrub_refuses_to_overwrite_concurrent_change(
    tmp_path: Path,
) -> None:
    path = tmp_path / "response.headers"
    original = b"x-api-key: first-secret-value\n"
    path.write_bytes(original)
    mode = path.stat().st_mode
    path.write_bytes(b"x-api-key: concurrent-secret-value\n")

    with pytest.raises(ArtifactChangedDuringScrubError):
        _write_redacted(
            path,
            "x-api-key: <REDACTED:secret>\n",
            mode,
            expected_raw=original,
        )

    assert b"concurrent-secret-value" in path.read_bytes()

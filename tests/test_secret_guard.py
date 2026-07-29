from __future__ import annotations

import json
import subprocess
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
from argus_skill.engineer.external_work import parse_external_wait_request
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
    text = "x-api-key: response-secret-value\npayload=live-secret-value-123\nordinary research text"

    redacted = redact_secrets_text(text, known_values=known)

    assert "response-secret-value" not in redacted
    assert "live-secret-value-123" not in redacted
    assert "ordinary research text" in redacted
    assert redact_secrets_text(redacted, known_values=known) == redacted


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


def test_redacts_values_under_structured_sensitive_keys() -> None:
    redacted = redact_secrets_record(
        {
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
        }
    )

    assert redacted["api_key"] == "<REDACTED:secret>"
    assert redacted["nested"]["authorization"] == "<REDACTED:secret>"
    assert redacted["nested"]["clientSecret"] == "<REDACTED:secret>"
    assert redacted["nested"]["refreshToken"] == "<REDACTED:secret>"
    assert redacted["nested"]["auth_token"] == "<REDACTED:secret>"
    assert redacted["nested"]["clientToken"] == "<REDACTED:secret>"
    assert redacted["nested"]["private_token"] == "<REDACTED:secret>"
    assert redacted["nested"]["status"] == "ok"


def test_preserves_structured_tokenizer_metadata() -> None:
    tokenizer_config = {
        "bos_token": "<|im_start|>",
        "eos_token": "<|im_end|>",
        "pad_token": "<|endoftext|>",
        "unk_token": "<unk>",
        "mask_token": "<mask>",
        "additional_special_tokens": ["<image>", "<video>"],
    }

    assert redact_secrets_record(tokenizer_config) == tokenizer_config


def test_scrub_does_not_mutate_tokenizer_config_file(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint" / "tokenizer_config.json"
    path.parent.mkdir(parents=True)
    payload = {
        "eos_token": "<|im_end|>",
        "pad_token": "<|endoftext|>",
        "tokenizer_class": "Qwen2Tokenizer",
    }
    original = json.dumps(payload, indent=2) + "\n"
    path.write_text(original, encoding="utf-8")
    now = time.time()
    path.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ()
    assert path.read_text(encoding="utf-8") == original


def test_git_scrub_ignores_recent_but_unchanged_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "examples" / "config.yml"
    changed = tmp_path / "artifact.yml"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        "client_secret: benchmark-fixture-secret\n",
        encoding="utf-8",
    )
    changed.write_text("status: clean\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "base"],
        check=True,
    )

    now = time.time()
    fixture.touch()
    changed.write_text(
        "client_secret: newly-written-secret\n",
        encoding="utf-8",
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ("artifact.yml",)
    assert fixture.read_text(encoding="utf-8") == (
        "client_secret: benchmark-fixture-secret\n"
    )
    assert "<REDACTED:secret>" in changed.read_text(encoding="utf-8")


def test_still_redacts_explicit_provider_token_keys() -> None:
    redacted = redact_secrets_record(
        {
            "github_token": "github-secret-value",
            "hf_token": "huggingface-secret-value",
            "session_token": "session-secret-value",
        }
    )

    assert redacted == {
        "github_token": "<REDACTED:secret>",
        "hf_token": "<REDACTED:secret>",
        "session_token": "<REDACTED:secret>",
    }


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
        "#GitAuthentication: {\n  token: access_token: string\n}\n",
        encoding="utf-8",
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert not report.changed
    assert schema.read_text(encoding="utf-8") == (
        "#GitAuthentication: {\n  token: access_token: string\n}\n"
    )


def test_scrub_skips_vendored_code_references_clones(tmp_path: Path) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\nstatus: 200\n", encoding="utf-8")

    vendored_repo = tmp_path / "code" / "references" / "some-upstream-repo"
    vendored_repo.mkdir(parents=True)
    vendored_file = vendored_repo / "fixture.json"
    vendored_file.write_text('{"x-api-key": "vendored-fixture-secret"}\n', encoding="utf-8")
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


def test_scrub_only_matches_known_secrets_in_project_huggingface_cache(
    tmp_path: Path,
) -> None:
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
        '{"token": "public-tokenizer-schema-value", "download_auth": "live-cache-secret-value"}\n',
        encoding="utf-8",
    )
    now = time.time()
    cache_file.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
        known_values=("live-cache-secret-value",),
    )

    assert report.redacted_paths == (
        "response.headers",
        str(cache_file.relative_to(tmp_path)),
    )
    assert cache_file.read_text(encoding="utf-8") == (
        '{"token": "public-tokenizer-schema-value", "download_auth": "<REDACTED:known-secret>"}\n'
    )
    assert report.scanned_files == 2


def test_scrub_skips_project_third_party_runtime_trees(tmp_path: Path) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\n", encoding="utf-8")
    runtime_payload = (
        tmp_path / "third_party" / "runtime_deps" / "huggingface_hub-0.34.4.dist-info" / "METADATA"
    )
    reference_payload = (
        tmp_path / "third_party" / "reference_sources" / "transformers" / "tokenizer_config.json"
    )
    runtime_payload.parent.mkdir(parents=True)
    reference_payload.parent.mkdir(parents=True)
    runtime_payload.write_text(
        "client_secret: synthetic-wheel-fixture\n",
        encoding="utf-8",
    )
    reference_payload.write_text(
        '{"access_token":"synthetic-upstream-fixture"}\n',
        encoding="utf-8",
    )
    now = time.time()
    runtime_payload.touch()
    reference_payload.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ("response.headers",)
    assert runtime_payload.read_text(encoding="utf-8") == (
        "client_secret: synthetic-wheel-fixture\n"
    )
    assert reference_payload.read_text(encoding="utf-8") == (
        '{"access_token":"synthetic-upstream-fixture"}\n'
    )
    assert report.scanned_files == 1


def test_scrub_skips_comparator_worker_runtime_overlay(tmp_path: Path) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\n", encoding="utf-8")
    metadata = (
        tmp_path
        / "experiments"
        / "comparator_worker_env"
        / "site"
        / "huggingface_hub-0.36.0.dist-info"
        / "METADATA"
    )
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        "Description: example client_secret: synthetic-package-text\n",
        encoding="utf-8",
    )
    now = time.time()
    metadata.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ("response.headers",)
    assert metadata.read_text(encoding="utf-8") == (
        "Description: example client_secret: synthetic-package-text\n"
    )
    assert report.scanned_files == 1


def test_scrub_skips_immutable_acquisition_anchor_bodies(tmp_path: Path) -> None:
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\n", encoding="utf-8")
    body = (
        tmp_path
        / "experiments"
        / "runs"
        / "frozen-run"
        / "acquisition"
        / "anchors"
        / "publisher.body"
    )
    body.parent.mkdir(parents=True)
    body.write_text(
        "public documentation example client_secret=synthetic-page-value\n",
        encoding="utf-8",
    )
    now = time.time()
    body.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=now - 5,
    )

    assert report.redacted_paths == ("response.headers",)
    assert body.read_text(encoding="utf-8") == (
        "public documentation example client_secret=synthetic-page-value\n"
    )
    assert report.scanned_files == 1


def test_artifact_scrub_preserves_synthetic_task_tokens(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "research" / "runs" / "RAW_TRAJECTORIES.jsonl"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "task_id": "synthetic-auth-task",
                "arguments": {"access_token": "access_token_abc123"},
                "raw_output": (
                    '<tool_call>{"username":"mzhang","password":"SecurePass123"}</tool_call>'
                ),
                "executed_call": (
                    "trading_login(username='your_username',password='your_password')"
                ),
            }
        )
        + "\n"
        + json.dumps(
            {
                "task_id": "provider-credential-leak",
                "github_token": "github-secret-value",
            }
        )
        + "\n"
        + json.dumps(
            {
                "task_id": "known-secret-leak",
                "arguments": {"access_token": "live-environment-secret-123"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
        known_values=("live-environment-secret-123",),
    )

    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    assert rows[0]["arguments"]["access_token"] == "access_token_abc123"
    assert "SecurePass123" in rows[0]["raw_output"]
    assert "your_password" in rows[0]["executed_call"]
    assert rows[1]["github_token"] == "<REDACTED:secret>"
    assert rows[2]["arguments"]["access_token"] == ("<REDACTED:known-secret>")
    assert report.redacted_paths == ("research/runs/RAW_TRAJECTORIES.jsonl",)


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
    wait_message = '{"wait_for": "subagent", "wait_id": "task-123"}'
    continue_message = "work completed\nCONTINUE_WORK: rebuild the hash chain"

    assert reviewer_note
    assert parse_external_wait_request(wait_message) == ("subagent", "task-123")
    assert parse_continue_work_request(continue_message) == "rebuild the hash chain"


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
    backend._log_agent_io(
        path,
        {
            "type": "agent.io.complete",
            "stdout_lines": ["api_key=live-secret-value-123"],
        },
    )
    context = {
        "log_path": str(path),
        "raw_log_path": str(path.with_name("agent_io.jsonl")),
        "call_id": "call",
        "run_label": "engineer-r1",
        "model": "test",
        "mode": "full",
        "buffer": [],
        "buffer_bytes": 0,
        "last_flush": 0.0,
    }
    with backend._io_logger.io_context_lock:
        backend._io_logger.io_context = context
    backend._stream_event_callback(
        "copilot.stdout",
        "Authorization: Bearer live-secret-value-123",
    )
    backend._close_io_context("call")

    rendered = path.read_text(encoding="utf-8")
    raw_rendered = path.with_name("agent_io.jsonl").read_text(encoding="utf-8")
    assert "live-secret-value-123" not in rendered
    assert "live-secret-value-123" not in raw_rendered
    assert "live-secret-value-123" not in json.dumps(streamed)


def test_review_event_payload_redacts_reviewer_echoes(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_API_KEY", "live-secret-value-123")
    review = ReviewDecision(
        status="continue",
        reason="api_key=live-secret-value-123",
        next_action="repair the artifact",
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

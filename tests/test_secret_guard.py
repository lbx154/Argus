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


def test_known_secret_redaction_does_not_guess_from_task_identifier_shape() -> None:
    secret = "sk-example-secret-value-123456"
    text = (
        "TASK_KEY=risk-kv-offline-evaluator\n"
        f"payload={secret}\n"
    )

    redacted = redact_secrets_text(text, known_values=(secret,))

    assert "TASK_KEY=risk-kv-offline-evaluator" in redacted
    assert secret not in redacted


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

    # ``blobs/`` under a hub-layout repo dir is now excluded outright as a
    # content-addressed cache; ``snapshots/`` stays on the known-secret-only
    # path this test covers.
    cache_file = (
        tmp_path
        / "models"
        / "huggingface"
        / "hub"
        / "models--example--model"
        / "snapshots"
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
        cache_file.relative_to(tmp_path).as_posix(),
    )
    assert cache_file.read_text(encoding="utf-8") == (
        '{"token": "public-tokenizer-schema-value", "download_auth": "<REDACTED:known-secret>"}\n'
    )
    assert report.scanned_files == 2


def test_scrub_preserves_crlf_without_inserting_blank_lines(tmp_path: Path) -> None:
    artifact = tmp_path / "response.headers"
    artifact.write_bytes(
        b"x-api-key: live-cache-secret-value\r\ncontent-type: text/plain\r\n"
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
        known_values=("live-cache-secret-value",),
    )

    assert report.redacted_paths == ("response.headers",)
    assert artifact.read_bytes() == (
        b"x-api-key: <REDACTED:known-secret>\r\ncontent-type: text/plain\r\n"
    )


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
    monkeypatch.setattr(secret_guard, "_HARD_MAX_ARTIFACT_BYTES", 8)
    payload = "x-api-key: response-secret-value\n"
    (tmp_path / "large.txt").write_text(payload, encoding="utf-8")
    events: list[dict] = []

    report, reviewer_note = _apply_round_secret_guard(
        workdir=tmp_path,
        modified_since=time.time() - 5,
        round_index=1,
        round_max=10,
        on_event=events.append,
    )

    assert report.skipped_paths == (("large.txt", len(payload)),)
    assert report.truncated is True
    assert "Coverage incomplete" in reviewer_note
    assert f"large.txt ({len(payload) / (1024 * 1024):.1f} MiB)" in reviewer_note
    assert events[0]["skipped_paths"] == [
        {"path": "large.txt", "bytes": len(payload)}
    ]
    assert events[0]["operator_alert"] is True
    # The guardrail skip must leave the artifact untouched.
    assert (tmp_path / "large.txt").read_text(encoding="utf-8") == payload


def test_oversized_ipynb_notebook_secret_is_scrubbed(tmp_path: Path) -> None:
    # Regression: >32 MiB non-whitelisted suffixes (.ipynb) used to be skipped
    # with zero trace. The streamed scan must now cover them at real size.
    notebook = tmp_path / "analysis.ipynb"
    secret = "ghp_" + "a" * 36
    pad_line = '    "pad": "' + "x" * 118 + '",\n'
    target = 33 * 1024 * 1024
    with notebook.open("w", encoding="utf-8") as handle:
        handle.write("{\n")
        written = 0
        while written < target:
            handle.write(pad_line)
            written += len(pad_line)
        handle.write(f'    "leak": "{secret}"\n}}\n')

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert report.redacted_paths == ("analysis.ipynb",)
    assert report.skipped_paths == ()
    assert report.truncated is False
    rendered = notebook.read_text(encoding="utf-8")
    assert secret not in rendered
    assert "<REDACTED:github-token>" in rendered


def test_streaming_scrub_redacts_secret_across_chunk_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 64)
    monkeypatch.setattr(secret_guard, "_STREAM_CHUNK_BYTES", 64)
    artifact = tmp_path / "trace.ipynb"
    content = (
        "padding-line\n" * 4
        + "x-api-key: chunk-straddling-secret-value\n"
        + "trailer: ok\n"
    )
    artifact.write_text(content, encoding="utf-8")

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    rendered = artifact.read_text(encoding="utf-8")
    assert report.redacted_paths == ("trace.ipynb",)
    assert "chunk-straddling-secret-value" not in rendered
    assert "x-api-key: <REDACTED:secret>" in rendered
    assert rendered.startswith("padding-line\n" * 4)
    assert rendered.endswith("trailer: ok\n")


def test_streaming_scrub_preserves_crlf_without_blank_lines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 64)
    monkeypatch.setattr(secret_guard, "_STREAM_CHUNK_BYTES", 64)
    artifact = tmp_path / "response.html"
    padding = b"<p>padding padding padding padding</p>\r\n" * 3
    artifact.write_bytes(
        padding
        + b"x-api-key: live-crlf-secret-value\r\ncontent-type: text/plain\r\n"
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
        known_values=("live-crlf-secret-value",),
    )

    assert report.redacted_paths == ("response.html",)
    assert artifact.read_bytes() == (
        padding
        + b"x-api-key: <REDACTED:known-secret>\r\ncontent-type: text/plain\r\n"
    )


def test_streaming_scrub_refuses_to_overwrite_concurrent_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "large.log"
    artifact.write_text(
        "x-api-key: streamed-secret-value\nstatus: 200\n",
        encoding="utf-8",
    )
    mode = artifact.stat().st_mode
    real_chmod = secret_guard.os.chmod

    def mutate_then_chmod(target, bits):
        # Runs between the scanning pass and the recheck pass.
        with artifact.open("ab") as handle:
            handle.write(b"appended-after-scan\n")
        real_chmod(target, bits)

    monkeypatch.setattr(secret_guard.os, "chmod", mutate_then_chmod)

    with pytest.raises(ArtifactChangedDuringScrubError):
        secret_guard._scrub_streaming(artifact, mode)

    assert b"streamed-secret-value" in artifact.read_bytes()


def test_oversized_binary_artifact_is_sniffed_and_skipped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 16)
    weights = tmp_path / "model.bin"
    payload = b"\x00\x01\x02" * 64
    weights.write_bytes(payload)

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    # A NUL in the head marks a binary artifact: quietly excluded from the
    # text scrub, exactly like the in-memory whole-file NUL check.
    assert report.redacted_paths == ()
    assert report.skipped_paths == ()
    assert report.errors == ()
    assert weights.read_bytes() == payload


def test_oversized_artifact_turning_binary_mid_stream_leaves_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 1024)
    mixed = tmp_path / "mixed.log"
    payload = (
        b"x-api-key: buried-secret-value\n"
        + b"text line\n" * 900
        + b"\x00binary tail"
    )
    assert b"\0" not in payload[: secret_guard._TEXT_SNIFF_BYTES]
    mixed.write_bytes(payload)

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert report.skipped_paths == (("mixed.log", len(payload)),)
    assert report.truncated is True
    assert mixed.read_bytes() == payload


def test_scan_file_budget_exhaustion_is_reported_not_silent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_SCANNED_FILES", 1)
    (tmp_path / "a.txt").write_text("alpha: ok\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta: ok\n", encoding="utf-8")

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert report.scanned_files == 1
    assert len(report.skipped_paths) == 1
    skipped_path, skipped_size = report.skipped_paths[0]
    assert skipped_path in {"a.txt", "b.txt"}
    assert skipped_size > 0
    assert report.truncated is True


def test_single_line_above_carry_cap_is_skipped_with_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A newline-free oversized artifact must not OOM the streamed scan.

    The carry used to be rebuilt as ``carry + chunk`` bytes objects, so a
    single huge line was O(n^2) in memcpy and held twice its size in memory.
    A line above the cap now aborts that file with a skipped_paths trace.
    """
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 1024)
    monkeypatch.setattr(secret_guard, "_STREAM_CHUNK_BYTES", 1024)
    monkeypatch.setattr(secret_guard, "_MAX_STREAM_LINE_BYTES", 4096)
    artifact = tmp_path / "single_line.json"
    payload = b'{"blob":"' + b"a" * (64 * 1024) + b'"}'  # 64 KiB, no newline
    artifact.write_bytes(payload)

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    assert report.skipped_paths == (
        ("single_line.json (oversized line)", len(payload)),
    )
    assert report.truncated is True
    assert report.redacted_paths == ()
    assert artifact.read_bytes() == payload


def test_scrub_excludes_hf_content_addressed_cache_trees(tmp_path: Path) -> None:
    """HuggingFace content-addressed caches are immutable upstream bytes.

    run-08's workspace carried 4.5 GiB of datasets blobs under
    ``results/*/cache/huggingface/*/blobs/*``; scanning them burns the file
    and time budgets for nothing. Both spellings — an adjacent
    ``cache/huggingface`` pair at any depth and a ``blobs`` dir under a
    hub-layout repo dir — are excluded outright, not surfaced as skips.
    """
    recent = tmp_path / "response.headers"
    recent.write_text("x-api-key: new-secret-value\n", encoding="utf-8")
    run_cache_blob = (
        tmp_path / "results" / "run-08" / "cache" / "huggingface"
        / "datasets" / "downloads" / "0a1b2c3d"
    )
    hub_blob = (
        tmp_path / "outputs" / "hf_home" / "hub" / "datasets--org--corpus"
        / "blobs" / "9f8e7d6c"
    )
    for blob in (run_cache_blob, hub_blob):
        blob.parent.mkdir(parents=True)
        blob.write_text(
            "x-api-key: upstream-cache-header\n"
            "download_auth=live-cache-secret-value\n",
            encoding="utf-8",
        )
        blob.touch()

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
        known_values=("live-cache-secret-value",),
    )

    assert report.redacted_paths == ("response.headers",)
    # Excluded rather than skipped: the cache is not a coverage gap to alert on.
    assert report.skipped_paths == ()
    assert report.scanned_files == 1
    for blob in (run_cache_blob, hub_blob):
        assert "live-cache-secret-value" in blob.read_text(encoding="utf-8")


def test_streaming_time_budget_exhaustion_skips_remaining_large_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 64)
    monkeypatch.setattr(
        secret_guard, "_STREAMING_SCAN_TIME_BUDGET_SECONDS", 0.0
    )
    payload = "padding line of text\n" * 8
    for name in ("large_a.log", "large_b.log"):
        (tmp_path / name).write_text(payload, encoding="utf-8")

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    # The spent time is compared strictly, so a zero budget still admits the
    # first streamed scan; every further oversized artifact is surfaced.
    assert report.scanned_files == 1
    assert len(report.skipped_paths) == 1
    skipped_path, skipped_size = report.skipped_paths[0]
    assert skipped_path in {"large_a.log", "large_b.log"}
    assert skipped_size == len(payload)
    assert report.truncated is True


def test_file_budget_exhaustion_enumerates_remaining_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Budget exhaustion used to record only the one file it stopped at."""
    monkeypatch.setattr(secret_guard, "_MAX_SCANNED_FILES", 1)
    line = "status: ok\n"
    for index in range(60):
        (tmp_path / f"file_{index:02d}.txt").write_text(line, encoding="utf-8")

    report, reviewer_note = _apply_round_secret_guard(
        workdir=tmp_path,
        modified_since=time.time() - 5,
        round_index=1,
        round_max=10,
        on_event=None,
    )

    assert report.scanned_files == 1
    # 59 unscanned candidates: 50 enumerated individually, 9 summarized.
    assert len(report.skipped_paths) == 51
    for path, size in report.skipped_paths[:-1]:
        assert path.startswith("file_")
        assert size == len(line)
    assert report.skipped_paths[-1] == ("+9 more files", 0)
    assert report.truncated is True
    assert "+9 more files" in reviewer_note
    # The summary entry names a count, not a file; no "0.0 MiB" suffix.
    assert "+9 more files (0.0" not in reviewer_note


def test_bearer_redaction_is_line_local_and_stream_consistent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The bearer pattern must not span a newline (red before the fix).

    ``bearer\\s+`` let the token half of a match start on the next line, so
    the in-memory path redacted "bearer\\n<token>" while the newline-aligned
    streamed path silently missed it whenever a segment boundary fell between
    the two lines. All artifact patterns now match within a single line, so
    both paths agree by construction.
    """
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 64)
    monkeypatch.setattr(secret_guard, "_STREAM_CHUNK_BYTES", 64)
    cross_line_token = "A" * 70
    same_line_token = "B" * 24
    content = (
        "seed bearer\n"
        + cross_line_token + "\n"
        + f"auth bearer {same_line_token}\n"
    )
    artifact = tmp_path / "trace.log"
    artifact.write_text(content, encoding="utf-8")

    scrub_recent_text_artifacts(tmp_path, modified_since=time.time() - 5)
    rendered = artifact.read_text(encoding="utf-8")

    # The streamed result equals the in-memory redaction of the same text.
    assert rendered == redact_secrets_text(content)
    # Same-line bearer tokens are still redacted...
    assert same_line_token not in rendered
    assert "auth <REDACTED:token>\n" in rendered
    # ...and a bare "bearer" line no longer swallows the following line.
    assert cross_line_token + "\n" in rendered


def test_streaming_scrub_restores_crlf_on_whole_json_document_segment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A segment that is exactly one JSON document must keep its CRLF ending."""
    monkeypatch.setattr(secret_guard, "_MAX_ARTIFACT_BYTES", 64)
    artifact = tmp_path / "payload.json"
    secret = "streamed-crlf-json-secret-value"
    artifact.write_bytes(
        json.dumps({"api_key": secret, "pad": "x" * 64}).encode("utf-8")
        + b"\r\n"
    )

    report = scrub_recent_text_artifacts(
        tmp_path,
        modified_since=time.time() - 5,
    )

    raw = artifact.read_bytes()
    assert report.redacted_paths == ("payload.json",)
    assert secret.encode("utf-8") not in raw
    assert raw.endswith(b"\r\n")
    assert not raw.endswith(b"\r\r\n")
    assert json.loads(raw.decode("utf-8"))["api_key"] == "<REDACTED:secret>"


def test_streaming_scrub_skips_rewrite_pass_for_clean_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Two-pass streaming: a clean file costs one read and zero tmp writes.

    ``time.time_ns`` is only consulted to name the rewrite temp file, so the
    spy proves the rewrite pass never started for a hit-free artifact.
    """
    calls: list[int] = []
    real_time_ns = secret_guard.time.time_ns

    def spy() -> int:
        calls.append(1)
        return real_time_ns()

    monkeypatch.setattr(secret_guard.time, "time_ns", spy)
    clean = tmp_path / "clean.log"
    clean.write_text("status: ok\n" * 100, encoding="utf-8")
    assert secret_guard._scrub_streaming(clean, clean.stat().st_mode) == 0
    assert calls == []

    dirty = tmp_path / "dirty.log"
    dirty.write_text("x-api-key: streamed-secret-value\n", encoding="utf-8")
    assert secret_guard._scrub_streaming(dirty, dirty.stat().st_mode) == 1
    assert calls == [1]
    assert "<REDACTED:secret>" in dirty.read_text(encoding="utf-8")


def test_vault_known_values_exclude_multiline_strings(tmp_path: Path) -> None:
    """Vault strings follow the env-source contract: no newlines in a secret.

    The streamed scrub's newline-aligned coverage proof assumes known secret
    values never contain a newline; a multi-line vault "value" is
    configuration prose, and replacing it would rewrite line structure.
    """
    vault = tmp_path / "model_api.json"
    vault.write_text(
        json.dumps({
            "api_key": "vault-secret-value-123",
            "backup_token": "first-line-secret\nsecond-line",
        }),
        encoding="utf-8",
    )

    values = known_secret_values({
        "ARGUS_SKILL_CAPABILITY_VAULT": str(vault),
    })

    assert "vault-secret-value-123" in values
    assert all("\n" not in value for value in values)


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

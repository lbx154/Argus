from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from argus.core import secret_guard
from argus.core.secret_guard import (
    ArtifactChangedDuringScrubError,
    _write_redacted,
    redact_secrets_record,
    redact_secrets_text,
    redact_secrets_text_with_count,
    scrub_recent_text_artifacts,
)
from argus.engineer.runner import (
    _apply_round_secret_guard,
    parse_continue_work_request,
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
    continue_message = "work completed\nCONTINUE_WORK: rebuild the hash chain"

    assert reviewer_note
    assert parse_continue_work_request(continue_message) == "rebuild the hash chain"


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
    (tmp_path / "large.txt").write_bytes(payload.encode("utf-8"))
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
        (tmp_path / name).write_bytes(payload.encode("utf-8"))

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
        (tmp_path / f"file_{index:02d}.txt").write_bytes(line.encode("utf-8"))

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


def test_scrub_without_git_executable_falls_back_to_mtime_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Minimal task containers ship no git binary at all; the round must still
    # scrub new artifacts instead of raising FileNotFoundError.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    artifact = tmp_path / "artifact.yml"
    now = time.time()
    artifact.write_text("client_secret: newly-written-secret\n", encoding="utf-8")

    report = scrub_recent_text_artifacts(tmp_path, modified_since=now - 5)

    assert report.redacted_paths == ("artifact.yml",)
    assert "<REDACTED:secret>" in artifact.read_text(encoding="utf-8")


# Credential-shaped values that masking (when on) and detection (always) must
# catch: literals, prefix-only references, calls on arbitrary names, header
# literals.
_MUST_STAY_MASKED = (
    'token = "live-abcdef1234567890"',
    "password=Summer2024(xyz)",
    "password=hunter2hunter2",
    "api_key: AbCdEf0123456789",
    "SERVICE_TOKEN=abcd1234efgh5678",
    "token=$ecretPass99",
    "auth=Abc(defghijk",
    "secret = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    "Authorization: Bearer abcdefghijklmnopqrstu",
    "Authorization: Basic dXNlcjpwYXNzd29yZA==",
    "Authorization: Bearer <sk_live_abcdef1234567890abcdef>",
    "Authorization: Bearer <real token value 1234567890 abcdef>",
    "Authorization: Token {a1b2c3d4e5f6a7b8c9d0}",
    "Authorization: Basic $dXNlcjpwYXNzd29yZA",
    "Authorization: token $hunter2password",
    "Authorization: Bearer ${abc123realtokenvalue}",
    "api_key={real}Xk9fooBarBazQux1234",
    "api_key={realsecretvalue1234}",
    "password=${Xk9fooBarBaz",
    "password=$(Xk9!fooBarBaz",
    "password={{Xk9fooBarBaz",
    "password=$SECRETPASSWORD9",
    "password=$ADMINPASS2024",
    "password=$UPER_S3CR3T",
    "api_key=sk_live_51HabcdefGHIJ123(x)",
    "api_key=sk_live_51HabcdefGHIJ123[0]",
    "password=supersecret(",
    "password=correcthorse[1]battery",
    "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123(",
    "token=f(abcdefghijklmnopqrstuvwxyz0123456789)",
    "token=a.b(REALSECRETVALUE1234567)",
    "OPENAI_API_KEY=os.environ_SECRETVALUE123",
    "token=process.envREALSECRET123",
    "token=os.environXYZREAL",
    "token=os.environ.get(REALSECRETVALUE1)x",
    "secret=my_s3cr3t_pass(word)",
    "client_secret=Ab_CdEfGh12345[",
)


# --- Default: Argus does not rewrite text --------------------------------
# Masked text cannot be verified; one task spent five extra rounds disputing
# a line neither role could read. Detection still works with masking off.


@pytest.fixture
def redaction_default(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_REDACT_SECRETS", raising=False)


def test_redaction_is_off_by_default(redaction_default) -> None:
    assert secret_guard.secret_redaction_enabled() is False
    line = "Authorization: Bearer abcdefghijklmnopqrstu"
    record = {"token": "live-abcdef1234567890", "nested": [line]}

    assert redact_secrets_text(line, known_values=("abcdefghijklmnopqrstu",)) == line
    assert redact_secrets_text_with_count(line) == (line, 0)
    assert redact_secrets_record(record) is record


@pytest.mark.parametrize("line", _MUST_STAY_MASKED)
def test_training_capture_still_flags_secrets_by_default(redaction_default, line: str) -> None:
    from argus.trial.training_capture import _content_diagnostic
    from argus.trial.training_data import _sensitive

    diagnostic = _content_diagnostic("message", {"content": line}, sid="s", mission_id="m")
    assert diagnostic is not None
    assert diagnostic["detector"] == "secret_redactor"
    assert _sensitive({"content": line})

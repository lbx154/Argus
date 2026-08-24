from __future__ import annotations

import hashlib
import json
import urllib.parse

import pytest
from foundry.integrations.argus_webapi import (
    ArgusDaemonCommandError,
    ArgusWebApiClient,
    ArgusWebApiError,
    ArtifactDigest,
    ArtifactDownload,
    argus_connection_metadata,
    assess_argus_connection,
)


def _doctor_report(*, backend_ok: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": backend_ok,
        "generated_at": "2026-08-24T00:00:00+00:00",
        "findings": [
            {
                "code": "ARGUS-BACKEND-001",
                "scope": "backend",
                "severity": "info" if backend_ok else "error",
                "ok": backend_ok,
                "status": "ready" if backend_ok else "not_ready",
            }
        ],
    }


def test_daemon_create_requires_applied_rc_and_activation_proof() -> None:
    responses = iter(
        [
            {
                "sid": "session-admission",
                "rc": 2,
                "spawned": False,
                "command_status": "failed",
                "start": {
                    "admission_required": True,
                    "error": "active daemon limit reached",
                },
            },
            {
                "sid": "session-unproven",
                "rc": 0,
                "spawned": False,
                "command_status": "applied",
            },
            {
                "sid": "session-running",
                "rc": 0,
                "spawned": True,
                "command_status": "applied",
            },
        ]
    )

    def transport(method, url, headers, body, timeout):
        return 200, json.dumps(next(responses)).encode()

    client = ArgusWebApiClient("https://argus.example", transport=transport)
    with pytest.raises(ArgusDaemonCommandError) as admission:
        client.create_daemon(name="paper", objective="research", workdir="/work")
    assert admission.value.outcome == "admission_required"
    assert admission.value.receipt.project_id == "session-admission"
    assert admission.value.receipt.admission_required is True

    with pytest.raises(ArgusDaemonCommandError) as unproven:
        client.create_daemon(name="paper", objective="research", workdir="/work")
    assert unproven.value.outcome == "inconclusive"
    assert "spawned=true" in str(unproven.value)

    applied = client.create_daemon(name="paper", objective="research", workdir="/work")
    assert applied["sid"] == "session-running"


@pytest.mark.parametrize("command_status", ["failed", "rejected"])
def test_daemon_stop_rejects_unsuccessful_http_2xx_receipt(command_status: str) -> None:
    def transport(method, url, headers, body, timeout):
        return 200, json.dumps(
            {
                "rc": 3,
                "command_status": command_status,
                "error": "stop refused",
            }
        ).encode()

    client = ArgusWebApiClient("https://argus.example", transport=transport)
    with pytest.raises(ArgusDaemonCommandError, match="stop refused") as raised:
        client.stop("project-1", drain=command_status == "failed")
    assert raised.value.outcome == command_status


def test_capability_negotiation_records_optional_commands_snapshot_and_usage() -> None:
    def transport(method, url, headers, body, timeout):
        assert headers["User-Agent"] == "Argus-Flywheel/1"
        if url.endswith("/api/system/doctor"):
            return 200, json.dumps(_doctor_report()).encode()
        return 200, json.dumps(
            {
                "authentication": {"required": True, "authenticated": True},
                "runtime": {"revision": "abc"},
                "protocol": {"name": "argus.webapi", "major": 1, "minor": 13},
                "snapshot_schema_version": 7,
                "capabilities": [
                    "daemon.admission.v1",
                    "mission.view.v1",
                    "research.events.v1",
                    "daemon.command.v1",
                    "snapshot.schema.v1",
                    "snapshot.budget.v1",
                    "usage.recorded.v2",
                ],
            }
        ).encode()

    tested = ArgusWebApiClient(
        "https://argus.example", token="secret", transport=transport
    ).test_connection()
    assessment = assess_argus_connection(tested)
    metadata = argus_connection_metadata(tested, assessment)

    assert assessment.launch_compatible is True
    assert tested.supports_feature("daemon_commands") is True
    assert tested.supports_feature("manager_sse") is False
    assert metadata["feature_support"]["daemon_commands"] is True
    assert metadata["snapshot_contract"] == {
        "advertised": True,
        "schema_version": 7,
        "schema_7_understood": True,
        "budget_fields_advertised": True,
        "usage_records_advertised": True,
    }


def test_missing_command_and_snapshot_contracts_block_launch() -> None:
    def transport(method, url, headers, body, timeout):
        if url.endswith("/api/system/doctor"):
            return 200, json.dumps(_doctor_report()).encode()
        return 200, json.dumps(
            {
                "authentication": {"required": False, "authenticated": True},
                "protocol": {"name": "argus.webapi", "major": 1, "minor": 13},
                "snapshot_schema_version": 7,
                "capabilities": [
                    "daemon.admission.v1",
                    "mission.view.v1",
                    "research.events.v1",
                ],
            }
        ).encode()

    tested = ArgusWebApiClient("https://argus.example", transport=transport).test_connection()
    assessed = assess_argus_connection(tested)
    assert assessed.launch_compatible is False
    assert assessed.missing_capabilities == (
        "daemon.command.v1",
        "snapshot.schema.v1",
    )
    assert tested.supports_feature("daemon_commands") is False


def test_backend_doctor_failure_blocks_launch_and_persists_only_summary() -> None:
    def transport(method, url, headers, body, timeout):
        if url.endswith("/api/system/doctor"):
            report = _doctor_report(backend_ok=False)
            report["findings"][0]["detail"] = "secret-looking provider detail"  # type: ignore[index]
            report["findings"][0]["evidence"] = {"credential": "never-persist"}  # type: ignore[index]
            return 200, json.dumps(report).encode()
        return 200, json.dumps(
            {
                "authentication": {"required": True, "authenticated": True},
                "protocol": {"name": "argus.webapi", "major": 1, "minor": 13},
                "snapshot_schema_version": 7,
                "capabilities": [
                    "daemon.admission.v1",
                    "daemon.command.v1",
                    "mission.view.v1",
                    "research.events.v1",
                    "snapshot.schema.v1",
                ],
            }
        ).encode()

    tested = ArgusWebApiClient(
        "https://argus.example", token="secret", transport=transport
    ).test_connection()
    assessed = assess_argus_connection(tested)
    metadata = argus_connection_metadata(tested, assessed)

    assert tested.ok is True
    assert tested.backend_ready is False
    assert assessed.launch_compatible is False
    assert assessed.status == "incompatible"
    assert "model backend/provider" in str(assessed.error)
    assert metadata["backend_ready"] is False
    assert metadata["system_doctor"] == {
        "status": "not_ready",
        "schema_version": 1,
        "report_ok": False,
        "backend_finding_count": 1,
        "blocking_codes": ["ARGUS-BACKEND-001"],
        "generated_at": "2026-08-24T00:00:00+00:00",
    }
    assert "never-persist" not in json.dumps(metadata)
    assert "secret-looking" not in json.dumps(metadata)


def test_missing_system_doctor_endpoint_is_explicitly_launch_incompatible() -> None:
    def transport(method, url, headers, body, timeout):
        if url.endswith("/api/system/doctor"):
            return 404, b'{"detail":"not found"}'
        return 200, json.dumps(
            {
                "authentication": {"required": False, "authenticated": True},
                "protocol": {"name": "argus.webapi", "major": 1, "minor": 13},
                "snapshot_schema_version": 7,
                "capabilities": [
                    "daemon.admission.v1",
                    "daemon.command.v1",
                    "mission.view.v1",
                    "research.events.v1",
                    "snapshot.schema.v1",
                ],
            }
        ).encode()

    tested = ArgusWebApiClient(
        "https://argus.example", transport=transport
    ).test_connection()
    assessed = assess_argus_connection(tested)

    assert tested.ok is True
    assert tested.doctor_summary["status"] == "endpoint_missing"
    assert tested.doctor_summary["http_status"] == 404
    assert assessed.status == "incompatible"
    assert assessed.launch_compatible is False


def test_json_transport_response_is_bounded_before_parsing() -> None:
    def oversized_transport(method, url, headers, body, timeout):
        return 200, b"{" + (b"x" * 64)

    client = ArgusWebApiClient(
        "https://argus.example",
        transport=oversized_transport,
        max_json_bytes=32,
    )
    with pytest.raises(ArgusWebApiError, match="exceeds the byte limit"):
        client.artifacts("project-1")


def test_typed_decision_and_legacy_backlog_routes_are_distinct() -> None:
    calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), json.loads(body)))
        return 200, b'{"resolved":true}'

    client = ArgusWebApiClient(
        "https://argus.example", token="secret", transport=transport
    )
    client.resolve_decision(
        "project-1", "decision:7", option_id="local-fallback", note="bounded"
    )
    client.answer_backlog_item("project-1", "item:9", text="Use the verified fallback")

    assert urllib.parse.urlparse(calls[0][1]).path == (
        "/api/projects/project-1/decisions/decision%3A7/resolve"
    )
    assert calls[0][3] == {"option_id": "local-fallback", "note": "bounded"}
    assert urllib.parse.urlparse(calls[1][1]).path == (
        "/api/projects/project-1/backlog/item%3A9/answer"
    )
    assert calls[1][3] == {"text": "Use the verified fallback"}
    with pytest.raises(ValueError, match="decision id"):
        client.resolve_decision("project-1", "../bad", option_id="x")


def test_raw_artifact_download_hashes_chunks_and_keeps_token_on_original_request() -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    def raw_transport(method, url, headers, timeout):
        seen.append((method, url, dict(headers)))
        return 200, {"Content-Type": "application/pdf", "Content-Length": "6"}, [
            b"ab",
            b"cd",
            b"ef",
        ]

    client = ArgusWebApiClient(
        "https://argus.example",
        token="secret",
        raw_transport=raw_transport,
        max_artifact_bytes=8,
        max_artifact_batch_bytes=16,
    )
    downloaded = client.download_artifact("p1", "paper/main.pdf")

    assert downloaded == ArtifactDownload(
        path="paper/main.pdf",
        size=6,
        sha256=hashlib.sha256(b"abcdef").hexdigest(),
        content_type="application/pdf",
        content=b"abcdef",
    )
    assert seen[0][0] == "GET"
    assert seen[0][2]["Authorization"] == "Bearer secret"
    assert seen[0][2]["User-Agent"] == "Argus-Flywheel/1"
    parsed = urllib.parse.urlparse(seen[0][1])
    assert parsed.path == "/api/projects/p1/artifact/raw"
    assert urllib.parse.parse_qs(parsed.query) == {
        "path": ["paper/main.pdf"],
        "download": ["true"],
    }


def test_raw_artifact_digest_does_not_retain_content_and_enforces_limits() -> None:
    def raw_transport(method, url, headers, timeout):
        return 200, {}, [b"123", b"456"]

    client = ArgusWebApiClient(
        "https://argus.example",
        raw_transport=raw_transport,
        max_artifact_bytes=6,
        max_artifact_batch_bytes=10,
    )
    digest = client.artifact_digest("p1", "results/data.bin")
    assert type(digest) is ArtifactDigest
    assert digest.size == 6
    assert digest.sha256 == hashlib.sha256(b"123456").hexdigest()
    with pytest.raises(ArgusWebApiError, match="byte limit"):
        client.download_artifact("p1", "results/data.bin", max_bytes=5)
    with pytest.raises(ArgusWebApiError, match="cumulative byte limit"):
        client.download_artifacts("p1", ["results/a.bin", "results/b.bin"])


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/absolute/file",
        "C:/secret",
        "paper\\main.pdf",
        "paper//main.pdf",
        "paper/./main.pdf",
    ],
)
def test_raw_artifact_rejects_non_normalized_paths_before_transport(path: str) -> None:
    called = False

    def raw_transport(method, url, headers, timeout):
        nonlocal called
        called = True
        return 200, {}, []

    client = ArgusWebApiClient("https://argus.example", raw_transport=raw_transport)
    with pytest.raises(ValueError, match="allowlist path"):
        client.download_artifact("p1", path)
    assert called is False


def test_raw_artifact_refuses_redirect_without_forwarding_to_new_host() -> None:
    calls = 0

    def raw_transport(method, url, headers, timeout):
        nonlocal calls
        calls += 1
        return 302, {"Location": "https://evil.example/steal"}, [b""]

    client = ArgusWebApiClient(
        "https://argus.example", token="secret", raw_transport=raw_transport
    )
    with pytest.raises(ArgusWebApiError) as raised:
        client.download_artifact("p1", "paper/main.pdf")
    assert raised.value.status == 302
    assert calls == 1


def test_event_poll_cursor_preserves_identical_events_and_detects_window_gap() -> None:
    repeated = {"type": "progress", "message": "same"}
    responses = iter(
        [
            [repeated, repeated],
            [repeated, repeated, {"type": "done", "seq": 3}],
            [{"type": "new-window", "seq": 99}],
        ]
    )

    def transport(method, url, headers, body, timeout):
        return 200, json.dumps({"events": next(responses)}).encode()

    client = ArgusWebApiClient("https://argus.example", transport=transport)
    first = client.poll_events("p1")
    second = client.poll_events("p1", cursor=first.cursor)
    third = client.poll_events("p1", cursor=second.cursor)

    assert len(first.events) == 2
    assert second.overlap_count == 2
    assert [event["type"] for event in second.events] == ["done"]
    assert second.gap_detected is False
    assert [event["type"] for event in third.events] == ["new-window"]
    assert third.gap_detected is True
    with pytest.raises(ValueError, match="different project"):
        client.poll_events("p2", cursor=third.cursor)


def test_base_url_and_token_cannot_smuggle_headers_or_query_credentials() -> None:
    with pytest.raises(ValueError, match="query or fragment"):
        ArgusWebApiClient("https://argus.example?token=secret")
    with pytest.raises(ValueError, match="control characters"):
        ArgusWebApiClient("https://argus.example", token="secret\r\nX-Evil: yes")

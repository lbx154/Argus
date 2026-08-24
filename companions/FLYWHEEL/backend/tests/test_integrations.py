from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import pytest
from foundry.integrations import (
    ArgusBackend,
    ArgusCliAdapter,
    ArgusWebApiClient,
    GitHubAdapter,
    NvidiaSmiProbe,
    OpenReviewAdapter,
    ReleaseMonitor,
)
from foundry.integrations.argus_webapi import ConnectionTest, assess_argus_connection
from foundry.integrations.sources import ArxivAdapter


def test_webapi_client_uses_bearer_and_real_argus_routes() -> None:
    calls: list[tuple[str, str, dict[str, str], dict | None]] = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), json.loads(body) if body else None))
        if url.endswith("/api/meta"):
            return 200, json.dumps({
                "authentication": {"required": True, "authenticated": True},
                "runtime": {"commit_sha": "abc"},
                "protocol": {"name": "argus.webapi", "major": 1, "minor": 13},
                "snapshot_schema_version": 7,
                "capabilities": [
                    "daemon.admission.v1", "daemon.command.v1", "mission.view.v1",
                    "research.events.v1", "snapshot.schema.v1",
                ],
            }).encode()
        if url.endswith("/api/system/doctor"):
            return 200, json.dumps({
                "schema_version": 1,
                "ok": True,
                "generated_at": "2026-08-24T00:00:00+00:00",
                "findings": [{
                    "code": "ARGUS-BACKEND-001", "scope": "backend",
                    "severity": "info", "ok": True, "status": "ready",
                }],
            }).encode()
        if "/api/projects?" in url:
            return 200, b'{"projects":[{"sid":"s-1"}]}'
        if "/events?" in url:
            return 200, b'{"events":[]}'
        if url.endswith("/artifacts"):
            return 200, b'{"artifacts":[{"path":"paper/main.pdf","exists":true}]}'
        if url.endswith("/api/daemons"):
            return 200, b'{"sid":"s-created","rc":0,"spawned":true,"command_status":"applied"}'
        if url.endswith("/daemon/start"):
            return 200, b'{"rc":0,"already_alive":true,"command_status":"applied"}'
        if url.endswith("/daemon/stop"):
            return 200, b'{"rc":0,"command_status":"applied"}'
        return 200, b'{"ok":true}'

    client = ArgusWebApiClient("https://argus.example", token="secret", transport=transport)
    tested = client.test_connection()
    assert tested.authenticated
    assert tested.protocol == {"name": "argus.webapi", "major": 1, "minor": 13}
    assert tested.snapshot_schema_version == 7
    assert "mission.view.v1" in tested.capabilities
    assert client.list_projects() == [{"sid": "s-1"}]
    client.create_daemon(name="paper", objective="bounded", workdir="/campaign")
    client.snapshot("s-1")
    client.events("s-1")
    assert client.artifacts("s-1") == [{"path": "paper/main.pdf", "exists": True}]
    client.start("s-1")
    client.stop("s-1", drain=True)
    assert all(call[2].get("Authorization") == "Bearer secret" for call in calls)
    assert any(call[1].endswith("/api/daemons") for call in calls)
    assert any("/api/projects/s-1/snapshot" in call[1] for call in calls)
    assert any("/api/projects/s-1/events" in call[1] for call in calls)
    assert any(call[1].endswith("/api/projects/s-1/artifacts") for call in calls)
    assert any("/api/projects/s-1/daemon/start" in call[1] for call in calls)
    assert any("/api/projects/s-1/daemon/stop" in call[1] for call in calls)


def test_webapi_rejects_remote_bearer_over_http() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ArgusWebApiClient("http://argus.example", token="secret")
    ArgusWebApiClient("http://127.0.0.1:8799", token="local-secret")


def test_webapi_artifact_detail_uses_encoded_allowlisted_query() -> None:
    seen: list[str] = []

    def transport(method, url, headers, body, timeout):
        seen.append(url)
        return 200, json.dumps({
            "path": "reports/table + μ.json",
            "exists": True,
            "kind": "json",
            "size": 8,
            "preview": "{\"ok\":1}",
            "truncated": False,
        }).encode()

    client = ArgusWebApiClient("https://argus.example", token="secret", transport=transport)
    detail = client.artifact("s-1", "reports/table + μ.json")

    assert detail["kind"] == "json"
    parsed = urllib.parse.urlparse(seen[0])
    assert parsed.path == "/api/projects/s-1/artifact"
    assert urllib.parse.parse_qs(parsed.query) == {"path": ["reports/table + μ.json"]}
    with pytest.raises(ValueError, match="non-empty"):
        client.artifact("s-1", "")


@pytest.mark.parametrize(
    ("protocol", "capabilities", "expected_missing"),
    [
        (
            {"name": "argus.webapi", "major": 2},
            (
                "daemon.admission.v1",
                "daemon.command.v1",
                "mission.view.v1",
                "research.events.v1",
                "snapshot.schema.v1",
            ),
            (),
        ),
        (
            {"name": "argus.webapi", "major": 1},
            (
                "daemon.admission.v1",
                "daemon.command.v1",
                "mission.view.v1",
                "snapshot.schema.v1",
            ),
            ("research.events.v1",),
        ),
    ],
)
def test_argus_launch_assessment_requires_exact_protocol_and_capabilities(
    protocol: dict[str, object],
    capabilities: tuple[str, ...],
    expected_missing: tuple[str, ...],
) -> None:
    tested = ConnectionTest(
        ok=True,
        authenticated=True,
        authentication_required=True,
        runtime={},
        protocol=protocol,
        capabilities=capabilities,
        snapshot_schema_version=7,
        backend_ready=True,
    )

    assessed = assess_argus_connection(tested)

    assert assessed.status == "incompatible"
    assert assessed.launch_compatible is False
    assert assessed.missing_capabilities == expected_missing
    assert assessed.error and "argus.webapi major 1" in assessed.error


def test_cli_plan_is_explicit_isolated_and_dry(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    objective = campaign / "OBJECTIVE.md"
    adapter = ArgusCliAdapter("argus-skill")
    plan = adapter.build_launch(
        campaign_root=campaign,
        objective_file=objective,
        backend=ArgusBackend.PI,
        dry_run=True,
    )
    assert plan.project_root == campaign.resolve() / "workspace"
    assert plan.life_dir == campaign.resolve() / "life"
    assert plan.argv[:3] == ("argus-skill", "--daemon", "--new")
    assert "--continuous" in plan.argv
    assert "--bounded" in plan.argv
    assert adapter.launch(plan) is plan


def test_arxiv_atom_parse_and_daily_cache(tmp_path: Path) -> None:
    calls = 0
    atom = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2608.12345v1</id><updated>2026-08-23T00:00:00Z</updated><title> A useful paper </title></entry></feed>'''

    def fetcher(url, headers, timeout):
        nonlocal calls
        calls += 1
        return 200, atom, {}

    ArxivAdapter._last_request = 0.0
    adapter = ArxivAdapter(cache_dir=tmp_path, fetcher=fetcher)
    first = adapter.refresh("all:argus")
    second = adapter.refresh("all:argus")
    assert first.status == "fresh"
    assert first.items[0].item_id == "2608.12345v1"
    assert second.status == "cache"
    assert calls == 1


def test_openreview_uses_api2_accepted_venue_id(tmp_path: Path) -> None:
    seen = ""

    def fetcher(url, headers, timeout):
        nonlocal seen
        seen = url
        return 200, json.dumps({"notes": [{"id": "n1", "forum": "f1", "content": {"title": {"value": "Accepted"}}}]}).encode(), {}

    result = OpenReviewAdapter(cache_dir=tmp_path, fetcher=fetcher).refresh("ICLR.cc/2027/Conference")
    assert result.items[0].title == "Accepted"
    assert seen.startswith("https://api2.openreview.net/notes?")
    assert urllib.parse.parse_qs(urllib.parse.urlparse(seen).query)["content.venueid"] == ["ICLR.cc/2027/Conference"]


def test_github_conditional_request_and_difference(tmp_path: Path) -> None:
    requests: list[dict[str, str]] = []

    def fetcher(url, headers, timeout):
        requests.append(dict(headers))
        if len(requests) == 1:
            body = [{"sha": "abc", "html_url": "https://github.test/c/abc", "commit": {"message": "first", "author": {"date": "now"}}}]
            return 200, json.dumps(body).encode(), {"ETag": '"v1"', "X-RateLimit-Remaining": "42"}
        return 304, b"", {"ETag": '"v1"'}

    adapter = GitHubAdapter(cache_dir=tmp_path, fetcher=fetcher)
    first = adapter.refresh("owner/repo", force=True)
    second = adapter.refresh("owner/repo", force=True)
    assert first.added_ids == ("abc",)
    assert second.status == "unchanged"
    assert requests[1]["If-None-Match"] == '"v1"'


def test_source_update_reports_exact_added_removed_and_changed_ids(tmp_path: Path) -> None:
    responses = [
        [
            {"sha": "a", "html_url": "https://github.test/c/a", "commit": {
                "message": "original", "author": {"date": "2026-08-22T00:00:00Z"}}},
            {"sha": "b", "html_url": "https://github.test/c/b", "commit": {
                "message": "will be removed", "author": {"date": "2026-08-22T00:00:00Z"}}},
        ],
        [
            {"sha": "a", "html_url": "https://github.test/c/a", "commit": {
                "message": "metadata changed", "author": {"date": "2026-08-23T00:00:00Z"}}},
            {"sha": "c", "html_url": "https://github.test/c/c", "commit": {
                "message": "actually added", "author": {"date": "2026-08-23T00:00:00Z"}}},
        ],
    ]

    def fetcher(url, headers, timeout):
        body = responses.pop(0)
        return 200, json.dumps(body).encode(), {"ETag": '"next"'}

    adapter = GitHubAdapter(cache_dir=tmp_path, fetcher=fetcher)
    adapter.refresh("owner/repo", force=True)
    update = adapter.refresh("owner/repo", force=True)

    assert update.added_ids == ("c",)
    assert update.removed_ids == ("b",)
    assert update.changed_ids == ("a",)
    assert update.difference_summary == "新增 1，移除 1，元数据变化 1"


def test_resource_probe_parses_nvidia_smi() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="0, GPU-1, NVIDIA RTX A6000, 49140, 1024, 87, 64\n",
        stderr="",
    )
    result = NvidiaSmiProbe(runner=lambda *args, **kwargs: completed).probe()
    assert result.available
    assert result.devices[0].memory_total_mib == 49140


def test_release_monitor_only_runs_ls_remote() -> None:
    calls = []
    runner_kwargs = []
    completed = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="c22de7c581a5577a01c00ca7c1bd17df8de2ebc4\trefs/heads/main\n",
        stderr="",
    )

    def runner(argv, **kwargs):
        calls.append(tuple(argv))
        runner_kwargs.append(kwargs)
        return completed

    status = ReleaseMonitor(runner=runner).inspect(
        "https://github.com/lbx154/Argus.git",
        reported_release={"commit_sha": "old"},
    )
    assert status.candidate_available
    assert status.staging == "isolated_stage_available_confirmation_required"
    assert calls == [("git", "ls-remote", "https://github.com/lbx154/Argus.git", "refs/heads/main")]
    environment = runner_kwargs[0]["env"]
    assert runner_kwargs[0]["shell"] is False
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GCM_INTERACTIVE"] == "Never"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull

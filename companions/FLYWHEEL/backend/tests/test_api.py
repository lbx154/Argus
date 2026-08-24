from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.argus_artifact_api import _client as artifact_api_client
from foundry.config import Settings
from foundry.integrations.argus_webapi import ArgusWebApiError

BASE_PREFLIGHT_ATTESTATIONS = {
    "compute_inventory_and_capacity_verified": True,
    "data_access_and_license_reviewed": True,
    "non_compute_prerequisites_reviewed": True,
}
HI_PREFLIGHT_ATTESTATIONS = {
    **BASE_PREFLIGHT_ATTESTATIONS,
    "human_subjects_and_ethics_path_reviewed": True,
}
START_APPROVAL = {
    "human_approved": True,
    "approval_reason": "Operator reviewed the immutable launch packet and resource contract.",
    "actor": "pytest-operator",
}
PPOPP_WALL_CLOCK = "2027-07-01T18:00:00+08:00"


def _applied_start_receipt(sid: str, **extra: object) -> dict[str, object]:
    return {
        "sid": sid,
        "rc": 0,
        "spawned": True,
        "command_status": "applied",
        **extra,
    }


def _applied_stop_receipt() -> dict[str, object]:
    return {"rc": 0, "command_status": "applied"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "foundry.db",
        data_dir=tmp_path / "data",
        seed_data_dir=seed_dir,
        cors_origins=("http://localhost:5173",),
        poll_interval_seconds=0,
        auto_seed=True,
        allow_unbound_campaign_launch_for_tests=True,
    )
    with TestClient(create_app(settings)) as value:
        yield value


@pytest.fixture()
def env_bound_client(tmp_path: Path) -> TestClient:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "env-bound.db",
        data_dir=tmp_path / "env-bound-data",
        seed_data_dir=seed_dir,
        cors_origins=("http://localhost:5173",),
        poll_interval_seconds=0,
        auto_seed=True,
        argus_base_url="http://127.0.0.1:8799",
        argus_token_env="ARGUS_SKILL_WEB_TOKEN",
    )
    with TestClient(create_app(settings)) as value:
        yield value


def _locked_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "primary_claim": "The frozen mechanism improves tail latency under held-out contention.",
        "primary_metric": "held-out p99 latency in milliseconds",
        "minimum_effect": "at least 8% lower p99 than every frozen baseline",
        "data_split": "public train/pilot split; untouched confirmatory test shard v1",
        "confirmatory_seeds": [101, 202, 303, 404, 505],
        "strongest_baselines": ["Baseline-A@abc123", "Baseline-B@def456"],
        "human_approved": True,
        "approval_reason": "Pilot falsifier passed; claim, metric, split, and budget reviewed.",
    }
    payload.update(overrides)
    return payload


def _mark_connection_launch_compatible(client: TestClient, connection_id: str) -> None:
    row = client.app.state.db.fetch_one(
        "SELECT metadata_json FROM connections WHERE id=?", (connection_id,)
    )
    assert row is not None
    metadata = json.loads(row["metadata_json"])
    metadata["launch_compatible"] = True
    client.app.state.db.execute(
        "UPDATE connections SET status='online',metadata_json=? WHERE id=?",
        (json.dumps(metadata), connection_id),
    )


def _create_contract_ready_campaign(
    client: TestClient, *, with_connection: bool = False, include_preflight: bool = True
) -> dict:
    resource = client.post(
        "/api/resources",
        json={
            "name": "Locked contract GPU",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 24,
                "api_budget": "none",
                "max_parallel_jobs": 1,
            },
        },
    ).json()
    connection_id = None
    if with_connection:
        connection_id = client.post(
            "/api/connections",
            json={
                "name": "Locked contract Argus",
                "kind": "local",
                "base_url": "http://127.0.0.1:8799",
            },
        ).json()["id"]
        _mark_connection_launch_compatible(client, connection_id)
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    deadline = client.get("/api/venues/PPOPP").json()["deadlines"][0]
    config: dict[str, object] = {"wall_clock_deadline": PPOPP_WALL_CLOCK}
    if include_preflight:
        config["preflight_attestations"] = BASE_PREFLIGHT_ATTESTATIONS
    return client.post(
        "/api/campaigns",
        json={
            "venue_key": "PPOPP",
            "idea_id": idea["id"],
            "deadline_id": deadline["id"],
            "connection_id": connection_id,
            "resource_id": resource["id"],
            "config": config,
        },
    ).json()


def _create_rolling_campaign(
    client: TestClient, *, wall_clock_deadline: str
) -> dict:
    connection = client.post(
        "/api/connections",
        json={
            "name": "Rolling Argus",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
        },
    ).json()
    _mark_connection_launch_compatible(client, connection["id"])
    resource = client.post(
        "/api/resources",
        json={
            "name": "Rolling campaign GPU",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 24,
                "api_budget": "none",
                "max_parallel_jobs": 1,
            },
        },
    ).json()
    idea = client.get("/api/ideas?venue_key=CSCW&limit=1").json()["items"][0]
    venue = client.get("/api/venues/CSCW").json()
    assert venue["deadlines"] == []
    return client.post(
        "/api/campaigns",
        json={
            "venue_key": "CSCW",
            "idea_id": idea["id"],
            "connection_id": connection["id"],
            "resource_id": resource["id"],
            "config": {
                "preflight_attestations": HI_PREFLIGHT_ATTESTATIONS,
                "wall_clock_deadline": wall_clock_deadline,
            },
        },
    ).json()


def test_seeded_dashboard_and_catalog(client: TestClient) -> None:
    dashboard = client.get("/api/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["counts"]["venues"] == 58
    assert dashboard.json()["counts"]["ideas"] == 290
    assert len(dashboard.json()["upcoming_deadlines"]) == 85
    venues = client.get("/api/venues").json()
    assert venues["total"] == 58
    deadlines = sum(item["deadline_count"] for item in venues["items"])
    assert deadlines == 85


def test_campaign_api_never_infers_release_pin_from_config_or_unverified_manifest(
    client: TestClient,
) -> None:
    campaign = _create_contract_ready_campaign(client)
    configured_sha = "a" * 40
    manifest_sha = "b" * 40
    config = {
        **campaign["config"],
        "argus_release_sha": configured_sha,
    }
    client.app.state.db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(config), campaign["id"]),
    )

    detail = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert detail["release_pinned"] is False
    assert detail["release_reference"] == configured_sha
    assert detail["release_reference_source"] == "campaign_config_reference"

    campaign_dir = client.app.state.settings.data_dir / "campaigns" / campaign["id"]
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "manifest.json").write_text(
        json.dumps(
            {
                "campaign_id": campaign["id"],
                "argus_release_sha": manifest_sha,
                "release_pinned": True,
            }
        ),
        encoding="utf-8",
    )
    client.app.state.db.execute(
        "UPDATE campaigns SET launch_command_id=? WHERE id=?",
        ("immutable-launch-receipt", campaign["id"]),
    )

    detail = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert detail["release_pinned"] is False
    assert detail["release_reference"] == configured_sha
    assert detail["release_reference_source"] == "campaign_config_reference"
    listed = client.get("/api/campaigns?limit=500").json()["items"]
    listed_campaign = next(item for item in listed if item["id"] == campaign["id"])
    assert listed_campaign["release_reference"] == configured_sha
    assert listed_campaign["release_pinned"] is False


def test_campaign_list_includes_latest_completed_viewer_feedback(client: TestClient) -> None:
    campaign = _create_contract_ready_campaign(client)
    scores = {
        "novelty": 8.0,
        "significance": 7.5,
        "technical_quality": 8.5,
        "empirical_rigor": 7.0,
        "clarity": 8.0,
        "reproducibility": 7.5,
        "venue_fit": 8.0,
    }
    feedback = {
        "state": "scored",
        "dimension_scores": scores,
        "overall": 7.9,
        "oral_readiness": "not_yet",
        "blockers": ["confirm the held-out split provenance"],
        "report": "Independent evaluator completed the frozen evidence review.",
    }
    now = datetime.now(UTC).isoformat()
    client.app.state.db.execute(
        "UPDATE campaigns SET viewer_score=?,reviewer_scores_json=? WHERE id=?",
        (7.9, json.dumps(scores), campaign["id"]),
    )
    client.app.state.db.execute(
        "INSERT INTO reviews(id,campaign_id,reviewer_kind,state,score,recommendation,feedback_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()), campaign["id"], "venue_reviewer", "scored", 7.9,
            "not_yet", json.dumps(feedback), now, now,
        ),
    )

    listed = client.get("/api/campaigns?limit=500").json()["items"]
    item = next(row for row in listed if row["id"] == campaign["id"])
    assert item["reviewer_scores"] == scores
    assert item["latest_review_feedback"]["blockers"] == feedback["blockers"]
    assert item["latest_review_recommendation"] == "not_yet"
    assert item["latest_review_updated_at"] == now


def test_forecast_countdown_uses_conservative_window_start(client: TestClient) -> None:
    deadlines = client.get("/api/dashboard").json()["upcoming_deadlines"]
    forecast = next(
        item
        for item in deadlines
        if item["evidence_status"] != "official_confirmed"
        and item.get("forecast_window_start")
        and item["forecast_window_start"] != item["deadline_date"]
    )
    expected = (date.fromisoformat(forecast["forecast_window_start"]) - date.today()).days
    assert forecast["days_remaining"] == expected


def test_seeded_reminders_use_conservative_forecast_bound_and_explicit_basis(
    client: TestClient,
) -> None:
    db = client.app.state.db
    forecast = db.fetch_one(
        "SELECT * FROM deadlines WHERE evidence_status='forecast' "
        "AND forecast_window_start IS NOT NULL AND forecast_window_start<>deadline_date "
        "ORDER BY id LIMIT 1"
    )
    official = db.fetch_one(
        "SELECT * FROM deadlines WHERE evidence_status='official_confirmed' ORDER BY id LIMIT 1"
    )
    assert forecast is not None and official is not None

    for deadline, expected_basis, expected_cutoff in (
        (forecast, "forecast_window_start", forecast["forecast_window_start"]),
        (official, "official_deadline_date", official["deadline_date"]),
    ):
        reminder_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"foundry:{deadline['id']}:30")
        )
        reminder = db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))
        assert reminder is not None
        payload = json.loads(reminder["payload_json"])
        assert payload["planning_basis"] == expected_basis
        assert payload["planning_cutoff_date"] == expected_cutoff
        assert payload["evidence_status"] == deadline["evidence_status"]
        assert payload["point_estimate_deadline_date"] == deadline["deadline_date"]
        assert expected_basis in reminder["title"]
        expected_local_day = date.fromisoformat(expected_cutoff) - timedelta(days=30)
        expected_trigger = datetime.combine(
            expected_local_day,
            time(hour=9),
            tzinfo=timezone(timedelta(hours=8)),
        ).astimezone(UTC).isoformat()
        assert reminder["trigger_at"] == expected_trigger


def test_connection_secret_is_never_persisted_or_returned(client: TestClient) -> None:
    created = client.post("/api/connections", json={
        "name": "Local Argus", "kind": "local", "base_url": "http://127.0.0.1:8799",
        "bearer_token": "do-not-leak",
    })
    assert created.status_code == 201
    body = created.json()
    assert body["has_token"] is True
    assert body["token_source"] == "memory"
    assert "do-not-leak" not in json.dumps(body)
    db = client.app.state.db
    row = db.fetch_one("SELECT * FROM connections WHERE id=?", (body["id"],))
    assert "token_secret" not in row
    assert row["token_ref"] == f"memory:{body['id']}"
    assert "do-not-leak" not in client.app.state.settings.database_path.read_bytes().decode(
        "utf-8", errors="ignore"
    )


def test_connection_env_reference_is_server_allowlisted_before_network(
    env_bound_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = env_bound_client
    calls: list[str] = []

    class Tested:
        ok = True
        authenticated = True
        authentication_required = True
        runtime = {"revision": "b4e4e721aa05", "worktree": {"dirty": False}}
        protocol = {"name": "argus.webapi", "major": 1, "minor": 13}
        capabilities = (
            "daemon.admission.v1",
            "daemon.command.v1",
            "mission.view.v1",
            "research.events.v1",
            "snapshot.schema.v1",
        )
        snapshot_schema_version = 7
        backend_ready = True
        doctor_generated_at = "2026-08-24T00:00:00+00:00"
        doctor_summary = {"status": "ready", "blocking_codes": []}

    def fake_test(self):
        calls.append("request")
        return Tested()

    monkeypatch.setattr(
        "foundry.api.ArgusWebApiClient.test_connection", fake_test
    )
    rejected_create = client.post(
        "/api/connections",
        json={
            "name": "Must not read GitHub token",
            "kind": "remote",
            "base_url": "https://attacker.example.invalid",
            "token_env": "GITHUB_TOKEN",
        },
    )
    assert rejected_create.status_code == 422
    assert calls == []

    ambiguous_create = client.post(
        "/api/connections",
        json={
            "name": "Ambiguous credentials",
            "kind": "remote",
            "base_url": "http://127.0.0.1:8799",
            "token_env": "ARGUS_SKILL_WEB_TOKEN",
            "bearer_token": "literal-one-run-token",
        },
    )
    assert ambiguous_create.status_code == 422
    assert calls == []

    rejected_endpoint = client.post(
        "/api/connections",
        json={
            "name": "Managed env at attacker endpoint",
            "kind": "remote",
            "base_url": "https://attacker.example.invalid",
            "token_env": "ARGUS_SKILL_WEB_TOKEN",
        },
    )
    assert rejected_endpoint.status_code == 422
    assert calls == []

    literal_remote = client.post(
        "/api/connections",
        json={
            "name": "Literal remote token",
            "kind": "remote",
            "base_url": "https://argus.example.invalid",
            "bearer_token": "literal-one-run-token",
        },
    )
    assert literal_remote.status_code == 201
    assert literal_remote.json()["token_source"] == "memory"

    connection = client.post(
        "/api/connections",
        json={
            "name": "Managed env",
            "kind": "remote",
            "base_url": "http://127.0.0.1:8799/",
            "token_env": "ARGUS_SKILL_WEB_TOKEN",
        },
    )
    assert connection.status_code == 201
    connection_id = connection.json()["id"]
    rejected_update = client.patch(
        f"/api/connections/{connection_id}", json={"token_env": "GITHUB_TOKEN"}
    )
    assert rejected_update.status_code == 422
    assert calls == []
    rejected_endpoint_update = client.patch(
        f"/api/connections/{connection_id}",
        json={"base_url": "https://attacker.example.invalid"},
    )
    assert rejected_endpoint_update.status_code == 422
    assert calls == []
    ambiguous_update = client.patch(
        f"/api/connections/{connection_id}",
        json={
            "token_env": "ARGUS_SKILL_WEB_TOKEN",
            "bearer_token": "literal-one-run-token",
        },
    )
    assert ambiguous_update.status_code == 422
    assert calls == []

    # A legacy/forged row must also be rejected by Test before constructing a
    # request, even if it bypassed the public create/update APIs.
    client.app.state.db.execute(
        "UPDATE connections SET token_ref='env:GITHUB_TOKEN' WHERE id=?",
        (connection_id,),
    )
    rejected_test = client.post(f"/api/connections/{connection_id}/test")
    assert rejected_test.status_code == 422
    assert calls == []

    client.app.state.db.execute(
        "UPDATE connections SET token_ref='env:ARGUS_SKILL_WEB_TOKEN',"
        "base_url='https://attacker.example.invalid' WHERE id=?",
        (connection_id,),
    )
    rejected_forged_endpoint = client.post(f"/api/connections/{connection_id}/test")
    assert rejected_forged_endpoint.status_code == 422
    assert calls == []

    client.app.state.db.execute(
        "UPDATE connections SET base_url='http://127.0.0.1:8799' WHERE id=?",
        (connection_id,),
    )
    accepted_test = client.post(f"/api/connections/{connection_id}/test")
    assert accepted_test.status_code == 200
    assert calls == ["request"]


def test_forged_env_endpoint_cannot_reach_coordinator_or_artifact_client(
    env_bound_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = env_bound_client
    db = client.app.state.db
    connection_id = "argus-managed-local"
    db.execute(
        "UPDATE connections SET base_url='https://attacker.example.invalid',"
        "token_ref='env:ARGUS_SKILL_WEB_TOKEN',status='unknown' WHERE id=?",
        (connection_id,),
    )
    constructed: list[str] = []

    class ForbiddenNetworkClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            constructed.append("constructed")
            raise AssertionError("endpoint binding must fail before a network client exists")

    monkeypatch.setattr(
        "foundry.services.coordinator.ArgusWebApiClient", ForbiddenNetworkClient
    )
    asyncio.run(client.app.state.coordinator.poll_connections())
    assert constructed == []
    row = db.fetch_one(
        "SELECT status,last_error FROM connections WHERE id=?", (connection_id,)
    )
    assert row["status"] == "offline"
    assert "endpoint" in row["last_error"].lower()

    monkeypatch.setattr(
        "foundry.argus_artifact_api.ArgusWebApiClient", ForbiddenNetworkClient
    )
    forged = db.fetch_one("SELECT * FROM connections WHERE id=?", (connection_id,))
    with pytest.raises(ValueError, match="endpoint"):
        artifact_api_client(SimpleNamespace(app=client.app), forged)
    assert constructed == []


def test_connection_probe_persists_argus_protocol_truth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = client.post(
        "/api/connections",
        json={
            "name": "Protocol-aware Argus",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
            "metadata": {"operator_note": "preserve me"},
        },
    ).json()

    class Tested:
        ok = True
        authenticated = True
        authentication_required = True
        runtime = {
            "revision": "b4e4e721aa05",
            "release_id": "0.1.2+88b1ce08ae0a04c9",
            "package_version": "0.1.2",
            "worktree": {"dirty": False},
        }
        protocol = {"name": "argus.webapi", "major": 1, "minor": 13}
        capabilities = (
            "daemon.admission.v1",
            "daemon.command.v1",
            "mission.view.v1",
            "research.events.v1",
            "snapshot.schema.v1",
        )
        snapshot_schema_version = 7
        backend_ready = True
        doctor_generated_at = "2026-08-24T00:00:00+00:00"
        doctor_summary = {
            "status": "ready",
            "backend_finding_count": 1,
            "blocking_codes": [],
        }

    class FakeClient:
        def test_connection(self):
            return Tested()

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    probed = client.post(f"/api/connections/{created['id']}/test")
    assert probed.status_code == 200
    body = probed.json()
    assert body["status"] == "online"
    assert body["metadata"]["operator_note"] == "preserve me"
    assert body["metadata"]["argus_revision"] == "b4e4e721aa05"
    assert body["metadata"]["launch_compatible"] is True
    assert body["metadata"]["backend_ready"] is True
    assert body["metadata"]["snapshot_schema_version"] == 7
    assert body["argus_meta"]["protocol"]["minor"] == 13


def test_prompt_and_calendar_feed(client: TestClient) -> None:
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    prompt = client.get(f"/api/ideas/{idea['id']}/prompt")
    assert prompt.status_code == 200
    result = prompt.json()
    assert result["prompt_sha256"]
    assert "Oral" in result["prompt"]
    assert "不得以获得正向结果为完成条件" in result["prompt"]
    calendar = client.get("/api/calendar.ics")
    assert calendar.status_code == 200
    assert calendar.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in calendar.text
    assert "[FORECAST — NOT ANNOUNCED]" in calendar.text
    assert "[OFFICIAL CONFIRMED]" in calendar.text
    assert "STATUS:TENTATIVE" in calendar.text
    assert "STATUS:CONFIRMED" in calendar.text
    assert all(len(line) <= 75 for line in calendar.content.split(b"\r\n") if line)
    assert b"\n" not in calendar.content.replace(b"\r\n", b"")
    pipeline = client.get("/api/pipeline?venue_key=PPOPP")
    assert pipeline.status_code == 200
    assert len(pipeline.json()["stages"]) == 11
    assert pipeline.json()["d30_is_review_sprint_not_research_start"] is True


def test_source_sync_preserves_adapter_delta_in_idea_and_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]

    def fake_sync(*args, **kwargs):
        return {
            "updates": [
                {
                    "source": "arxiv",
                    "query": "cat:cs.LG|50",
                    "status": "cache",
                    "items": [{
                        "item_id": "already-cached",
                        "title": "Existing cached evidence",
                        "url": "https://arxiv.org/abs/already-cached",
                    }],
                    "added_ids": [],
                    "removed_ids": [],
                    "changed_ids": [],
                    "difference_summary": "缓存未刷新；没有声称外部来源发生变化",
                },
                {
                    "source": "github",
                    "query": "owner/repo|30",
                    "status": "fresh",
                    "items": [{
                        "item_id": "metadata-updated",
                        "title": "Updated implementation evidence",
                        "url": "https://github.example/updated",
                    }],
                    "added_ids": ["actually-new"],
                    "removed_ids": ["no-longer-present"],
                    "changed_ids": ["metadata-updated"],
                    "difference_summary": "新增 1，移除 1，元数据变化 1",
                },
            ],
            "all_succeeded": True,
            "external_calls_are_not_implied_by_demo": True,
        }

    monkeypatch.setattr("foundry.api.sync_external_sources", fake_sync)
    response = client.post(
        "/api/sources/sync",
        json={"idea_id": idea["id"], "requests": [{"kind": "arxiv", "query": "x"}]},
    )

    assert response.status_code == 200
    delta = response.json()["idea_delta"]
    assert delta["changed_since_snapshot"] == {
        "added": ["actually-new"],
        "removed": ["no-longer-present"],
        "changed": ["metadata-updated"],
    }
    assert "already-cached" not in delta["changed_since_snapshot"]["added"]
    assert delta["change_basis"] == "adapter_source_updates"
    assert delta["source_deltas"][0]["status"] == "cache"
    assert delta["source_deltas"][0]["added"] == []
    assert delta["source_deltas"][1]["changed"] == ["metadata-updated"]

    stored = client.app.state.db.fetch_one(
        "SELECT differentiation FROM ideas WHERE id=?", (idea["id"],)
    )
    assert stored is not None
    assert json.loads(stored["differentiation"])["changed_since_snapshot"] == {
        "added": ["actually-new"],
        "removed": ["no-longer-present"],
        "changed": ["metadata-updated"],
    }
    events = client.get("/api/events?topic=sources&limit=1000").json()["items"]
    event = next(row for row in reversed(events) if row["event_type"] == "idea.differentiation_refreshed")
    assert event["payload"]["changed_since_snapshot"] == delta["changed_since_snapshot"]
    assert event["payload"]["change_basis"] == "adapter_source_updates"
    assert event["payload"]["source_deltas"] == delta["source_deltas"]


def test_campaign_start_freezes_packet_and_is_idempotent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = client.post("/api/connections", json={
        "name": "Local", "kind": "local", "base_url": "http://127.0.0.1:8799"
    }).json()
    _mark_connection_launch_compatible(client, connection["id"])
    resource = client.post("/api/resources", json={
        "name": "Test GPU", "resource_type": "gpu_pool",
        "capacity": {"configured": True, "gpu_count": 1, "gpu_model": "Mock GPU",
                     "gpu_hours": 24, "api_budget": "1M tokens hard cap",
                     "max_parallel_jobs": 1},
    }).json()
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    venue = client.get("/api/venues/PPOPP").json()

    class FakeClient:
        def create_daemon(self, **kwargs):
            assert Path(kwargs["workdir"]).name == "workspace"
            assert kwargs["command_id"]
            return _applied_start_receipt("argus-project-1")

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    campaign = client.post("/api/campaigns", json={
        "venue_key": "PPOPP", "idea_id": idea["id"],
        "deadline_id": venue["deadlines"][0]["id"], "connection_id": connection["id"],
        "resource_id": resource["id"],
        "config": {
            "preflight_attestations": BASE_PREFLIGHT_ATTESTATIONS,
            "wall_clock_deadline": PPOPP_WALL_CLOCK,
        },
    }).json()
    started = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 200
    assert started.json()["execution_state"] == "running"
    root = client.app.state.settings.data_dir / "campaigns" / campaign["id"]
    assert (root / "OBJECTIVE.md").is_file()
    assert (root / "SOURCE_SNAPSHOT.json").is_file()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_sha256"]
    assert hashlib.sha256((root / "OBJECTIVE.md").read_bytes()).hexdigest() == manifest[
        "prompt_sha256"
    ]
    assert manifest["resource_contract"]["gpu_model"] == "Mock GPU"
    assert manifest["resource_contract"]["planning_cutoff_basis"] == "forecast_window_start"
    assert manifest["resource_contract"]["planning_cutoff_date"] == "2027-07-04"
    assert manifest["preflight_attestations"] == BASE_PREFLIGHT_ATTESTATIONS
    duplicate = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert duplicate.status_code == 409


@pytest.mark.parametrize("connection_status", ["incompatible", "unauthorized", "offline"])
def test_campaign_start_rejects_known_unusable_connection_status(
    client: TestClient, connection_status: str
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    client.app.state.db.execute(
        "UPDATE connections SET status=? WHERE id=?",
        (connection_status, campaign["connection_id"]),
    )

    response = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )

    assert response.status_code == 409
    assert connection_status in response.json()["detail"]


def test_campaign_start_requires_successful_probe_and_keeps_campaign_idle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    client.app.state.db.execute(
        "UPDATE connections SET status='unknown',metadata_json='{}' WHERE id=?",
        (campaign["connection_id"],),
    )

    def forbidden_client(*args, **kwargs):
        raise AssertionError("an unprobed connection must never launch Argus")

    monkeypatch.setattr("foundry.api._client", forbidden_client)
    response = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )

    assert response.status_code == 409
    assert "Test connection" in response.json()["detail"]
    persisted = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert persisted["execution_state"] == "idle"
    assert persisted["argus_project_id"] is None
    assert persisted["launch_command_id"] is None


@pytest.mark.parametrize("compatibility_value", [False, "true"])
def test_campaign_start_requires_literal_true_compatibility_metadata(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    compatibility_value: object,
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    client.app.state.db.execute(
        "UPDATE connections SET status='online',metadata_json=? WHERE id=?",
        (
            json.dumps({"launch_compatible": compatibility_value}),
            campaign["connection_id"],
        ),
    )

    def forbidden_client(*args, **kwargs):
        raise AssertionError("non-boolean compatibility metadata must fail closed")

    monkeypatch.setattr("foundry.api._client", forbidden_client)
    response = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )

    assert response.status_code == 409
    assert "launch_compatible=False" in response.json()["detail"]


def test_campaign_start_rejects_unconfigured_resources(client: TestClient) -> None:
    connection = client.post("/api/connections", json={
        "name": "Local", "kind": "local", "base_url": "http://127.0.0.1:8799"
    }).json()
    _mark_connection_launch_compatible(client, connection["id"])
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    venue = client.get("/api/venues/PPOPP").json()
    campaign = client.post("/api/campaigns", json={
        "venue_key": "PPOPP", "idea_id": idea["id"],
        "deadline_id": venue["deadlines"][0]["id"], "connection_id": connection["id"],
        "resource_id": "resource-unconfigured",
    }).json()
    response = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert response.status_code == 409
    assert "resource profile is not available" in response.json()["detail"]


def test_campaign_start_rejects_missing_non_compute_preflight(client: TestClient) -> None:
    connection = client.post("/api/connections", json={
        "name": "Local", "kind": "local", "base_url": "http://127.0.0.1:8799"
    }).json()
    _mark_connection_launch_compatible(client, connection["id"])
    resource = client.post("/api/resources", json={
        "name": "Configured GPU", "resource_type": "gpu_pool",
        "capacity": {
            "configured": True, "gpu_count": 1, "gpu_model": "Mock GPU",
            "gpu_hours": 24, "api_budget": "1M tokens hard cap",
            "max_parallel_jobs": 1,
        },
    }).json()
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    venue = client.get("/api/venues/PPOPP").json()
    campaign = client.post("/api/campaigns", json={
        "venue_key": "PPOPP", "idea_id": idea["id"],
        "deadline_id": venue["deadlines"][0]["id"],
        "connection_id": connection["id"], "resource_id": resource["id"],
        "config": {"wall_clock_deadline": PPOPP_WALL_CLOCK},
    }).json()
    response = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert response.status_code == 409
    assert "Preflight attestations are incomplete" in response.json()["detail"]


@pytest.mark.parametrize("wall_clock", [None, "2027-07-01T18:00:00"])
def test_campaign_start_requires_explicit_wall_clock_offset(
    client: TestClient, wall_clock: str | None
) -> None:
    connection = client.post(
        "/api/connections",
        json={
            "name": f"Offset check {wall_clock}",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
        },
    ).json()
    _mark_connection_launch_compatible(client, connection["id"])
    resource = client.post(
        "/api/resources",
        json={
            "name": "Offset GPU",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 24,
                "api_budget": "none",
                "max_parallel_jobs": 1,
            },
        },
    ).json()
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    deadline = client.get("/api/venues/PPOPP").json()["deadlines"][0]
    config: dict[str, object] = {
        "preflight_attestations": BASE_PREFLIGHT_ATTESTATIONS,
    }
    if wall_clock is not None:
        config["wall_clock_deadline"] = wall_clock
    campaign = client.post(
        "/api/campaigns",
        json={
            "venue_key": "PPOPP",
            "idea_id": idea["id"],
            "deadline_id": deadline["id"],
            "connection_id": connection["id"],
            "resource_id": resource["id"],
            "config": config,
        },
    ).json()
    rejected = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "wall_clock_deadline" in rejected.json()["detail"]
    assert "offset" in rejected.json()["detail"].lower()


def test_campaign_start_rejects_forecast_cutoff_after_window_start(
    client: TestClient,
) -> None:
    connection = client.post(
        "/api/connections",
        json={
            "name": "Late cutoff connection",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
        },
    ).json()
    _mark_connection_launch_compatible(client, connection["id"])
    resource = client.post(
        "/api/resources",
        json={
            "name": "Late cutoff GPU",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 24,
                "api_budget": "none",
            },
        },
    ).json()
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    deadline = client.get("/api/venues/PPOPP").json()["deadlines"][0]
    campaign = client.post(
        "/api/campaigns",
        json={
            "venue_key": "PPOPP",
            "idea_id": idea["id"],
            "deadline_id": deadline["id"],
            "connection_id": connection["id"],
            "resource_id": resource["id"],
            "config": {
                "preflight_attestations": BASE_PREFLIGHT_ATTESTATIONS,
                "wall_clock_deadline": "2027-07-05T00:00:00+08:00",
            },
        },
    ).json()
    rejected = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    detail = rejected.json()["detail"]
    assert "forecast_window_start" in detail
    assert "2027-07-04" in detail


def test_locked_contract_is_immutable_versioned_and_idempotent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client)

    def forbidden_client(*args, **kwargs):
        raise AssertionError("locking must never contact Argus")

    monkeypatch.setattr("foundry.api._client", forbidden_client)
    payload = _locked_payload()
    first = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=payload
    )
    assert first.status_code == 201
    body = first.json()
    assert body["id"] == campaign["id"]
    assert body["target_campaign_id"] == campaign["id"]
    assert body["promoted_from_campaign_id"] is None
    assert body["science_state"] == "hypothesis_locked"
    assert body["schedule_state"] == "admitted"
    receipt = body["locked_contract"]
    assert receipt["version"] == 1
    assert receipt["idempotent"] is False
    assert "# ARGUS / FLYWHEEL · RESEARCH PROTOCOL v2 · LOCKED" in receipt["objective"]
    assert str(payload["primary_claim"]) in receipt["objective"]
    assert receipt["manifest"]["prompt_manifest"]["phase"] == "locked"
    assert receipt["manifest"]["human_science_gate"]["human_approved"] is True
    assert receipt["manifest"]["launch_triggered"] is False
    assert receipt["manifest"]["submission"] is False

    campaign_root = client.app.state.settings.data_dir / "campaigns" / campaign["id"]
    first_dir = campaign_root / receipt["directory"]
    first_objective = (first_dir / "OBJECTIVE.md").read_bytes()
    first_manifest = (first_dir / "MANIFEST.json").read_bytes()
    assert hashlib.sha256(first_objective).hexdigest() == receipt["prompt_sha256"]
    assert hashlib.sha256(first_objective).hexdigest() == receipt["manifest"][
        "prompt_sha256"
    ]
    assert receipt["contract_sha256"] in first_dir.name

    repeated = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=payload
    )
    assert repeated.status_code == 200
    assert repeated.json()["locked_contract"]["idempotent"] is True
    assert repeated.json()["locked_contract"]["directory"] == receipt["directory"]
    assert len(list((campaign_root / "contracts").glob("locked-v*-*"))) == 1
    lock_events = client.app.state.db.fetch_all(
        "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type='campaign.contract_locked'",
        (campaign["id"],),
    )
    assert len(lock_events) == 1
    event_payload = json.loads(lock_events[0]["payload_json"])
    assert event_payload["launch_triggered"] is False
    assert event_payload["submission"] is False

    revised_payload = _locked_payload(
        minimum_effect="at least 10% lower p99 than every frozen baseline",
        approval_reason="Human approved a new, stricter minimum-effect contract.",
    )
    revised = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=revised_payload
    )
    assert revised.status_code == 201
    assert revised.json()["locked_contract"]["version"] == 2
    assert revised.json()["locked_contract"]["directory"] != receipt["directory"]
    assert len(list((campaign_root / "contracts").glob("locked-v*-*"))) == 2
    assert (first_dir / "OBJECTIVE.md").read_bytes() == first_objective
    assert (first_dir / "MANIFEST.json").read_bytes() == first_manifest

    rejected_patch = client.patch(
        f"/api/campaigns/{campaign['id']}",
        json={"objective": "silently replace the frozen hypothesis"},
    )
    assert rejected_patch.status_code == 409
    assert "frozen" in rejected_patch.json()["detail"].lower()


def test_rolling_contract_and_launch_freeze_internal_cutoff_without_fake_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    internal_cutoff = "2026-09-30T18:00:00+08:00"
    campaign = _create_rolling_campaign(
        client, wall_clock_deadline=internal_cutoff
    )

    def forbidden_client(*args, **kwargs):
        raise AssertionError("locking a rolling contract must not contact Argus")

    monkeypatch.setattr("foundry.api._client", forbidden_client)
    locked = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=_locked_payload()
    )
    assert locked.status_code == 201
    receipt = locked.json()["locked_contract"]
    snapshot = receipt["manifest"]["deadline_snapshot"]
    assert snapshot == {
        "kind": "rolling_venue_internal_cutoff",
        "venue_key": "CSCW",
        "has_fixed_submission_deadline": False,
        "official_submission_deadline": None,
        "evidence_status": "official_rolling_no_fixed_deadline",
        "source_url": "https://cscw.acm.org/rolling.html",
        "official_model_reason": (
            "CSCW uses journal-style rolling submission; submit at any time and a "
            "submission is not guaranteed to appear at a particular conference edition."
        ),
        "operator_internal_cutoff": internal_cutoff,
        "internal_cutoff_is_official_deadline": False,
        "truth_notice": (
            "Operator cutoff for research planning only; this venue has no fixed official "
            "submission deadline and the cutoff must never be represented as one."
        ),
    }
    assert receipt["manifest"]["bindings"]["deadline_id"] is None
    objective_path = (
        client.app.state.settings.data_dir
        / "campaigns"
        / campaign["id"]
        / receipt["directory"]
        / "OBJECTIVE.md"
    )
    assert hashlib.sha256(objective_path.read_bytes()).hexdigest() == receipt[
        "prompt_sha256"
    ]
    assert internal_cutoff in receipt["objective"]
    assert "rolling/TBA; operator-supplied internal cutoff" in receipt["objective"]

    create_calls: list[dict] = []

    class FakeClient:
        def create_daemon(self, **kwargs):
            create_calls.append(dict(kwargs))
            return _applied_start_receipt("rolling-project-1")

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    started = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 200
    assert create_calls[0]["objective"] == receipt["objective"]
    campaign_root = client.app.state.settings.data_dir / "campaigns" / campaign["id"]
    launch_manifest = json.loads(
        (campaign_root / "manifest.json").read_text(encoding="utf-8")
    )
    assert launch_manifest["deadline"] is None
    assert launch_manifest["submission_snapshot"] == snapshot
    assert launch_manifest["resource_contract"]["wall_clock_deadline"] == internal_cutoff
    assert hashlib.sha256(
        (campaign_root / "OBJECTIVE.md").read_bytes()
    ).hexdigest() == launch_manifest["prompt_sha256"]


def test_rolling_contract_and_start_require_explicit_offset_cutoff(
    client: TestClient,
) -> None:
    campaign = _create_rolling_campaign(
        client, wall_clock_deadline="2026-09-30T18:00:00"
    )
    locked = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=_locked_payload()
    )
    assert locked.status_code == 409
    assert "explicit UTC offset" in locked.json()["detail"]
    started = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 409
    assert "explicit UTC offset" in started.json()["detail"]


def test_fixed_deadline_venue_still_rejects_missing_deadline(
    client: TestClient,
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    client.app.state.db.execute(
        "UPDATE campaigns SET deadline_id=NULL WHERE id=?", (campaign["id"],)
    )
    locked = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=_locked_payload()
    )
    assert locked.status_code == 409
    assert "fixed-deadline venue" in locked.json()["detail"]
    started = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 409
    assert "fixed-deadline venue" in started.json()["detail"]


def test_locked_contract_enforces_human_preflight_and_active_gates(
    client: TestClient,
) -> None:
    campaign = _create_contract_ready_campaign(client, include_preflight=False)
    no_preflight = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=_locked_payload()
    )
    assert no_preflight.status_code == 409
    assert "Preflight attestations" in no_preflight.json()["detail"]

    false_approval = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract",
        json=_locked_payload(human_approved=False),
    )
    assert false_approval.status_code == 422

    negative_seed = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract",
        json=_locked_payload(confirmatory_seeds=[13, -1]),
    )
    assert negative_seed.status_code == 422

    active = _create_contract_ready_campaign(client)
    client.app.state.db.execute(
        "UPDATE campaigns SET execution_state='running',argus_project_id='active-project' WHERE id=?",
        (active["id"],),
    )
    active_response = client.post(
        f"/api/campaigns/{active['id']}/locked-contract", json=_locked_payload()
    )
    assert active_response.status_code == 409
    assert "paused" in active_response.json()["detail"].lower()


def test_campaign_start_rejects_unapplied_backend_override(client: TestClient) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    config = dict(campaign["config"])
    config["backend"] = "pi"
    client.app.state.db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(config), campaign["id"]),
    )
    rejected = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "CreateDaemonIn" in rejected.json()["detail"]
    assert "target Argus instance" in rejected.json()["detail"]


def test_paused_portfolio_promotes_child_and_child_start_uses_locked_packet(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _create_contract_ready_campaign(client, with_connection=True)
    create_calls: list[dict] = []
    stop_calls: list[tuple[str, bool, bool]] = []

    class FakeClient:
        def create_daemon(self, **kwargs):
            create_calls.append(dict(kwargs))
            return _applied_start_receipt(f"argus-project-{len(create_calls)}")

        def stop(self, project_id: str, *, drain: bool, force: bool):
            stop_calls.append((project_id, drain, force))
            return _applied_stop_receipt()

    fake = FakeClient()
    monkeypatch.setattr("foundry.api._client", lambda request, row: fake)
    started_source = client.post(
        f"/api/campaigns/{source['id']}/start", json=START_APPROVAL
    )
    assert started_source.status_code == 200
    paused_source = client.post(
        f"/api/campaigns/{source['id']}/pause",
        json={"reason": "Portfolio evidence is ready for human winner promotion"},
    )
    assert paused_source.status_code == 200
    assert paused_source.json()["execution_state"] == "paused"
    assert stop_calls == [("argus-project-1", False, False)]

    db = client.app.state.db
    source_before = client.get(f"/api/campaigns/{source['id']}").json()
    source_root = client.app.state.settings.data_dir / "campaigns" / source["id"]
    source_objective_before = (source_root / "OBJECTIVE.md").read_bytes()
    source_manifest_before = (source_root / "manifest.json").read_bytes()
    payload = _locked_payload()
    promoted = client.post(
        f"/api/campaigns/{source['id']}/locked-contract", json=payload
    )
    assert promoted.status_code == 201
    child = promoted.json()
    assert child["id"] != source["id"]
    assert child["target_campaign_id"] == child["id"]
    assert child["promoted_from_campaign_id"] == source["id"]
    assert child["execution_state"] == "idle"
    assert child["science_state"] == "hypothesis_locked"
    assert len(create_calls) == 1

    source_after = client.get(f"/api/campaigns/{source['id']}").json()
    for key in (
        "objective",
        "config",
        "execution_state",
        "science_state",
        "argus_project_id",
        "launch_command_id",
        "started_at",
    ):
        assert source_after[key] == source_before[key]
    assert (source_root / "OBJECTIVE.md").read_bytes() == source_objective_before
    assert (source_root / "manifest.json").read_bytes() == source_manifest_before

    repeated = client.post(
        f"/api/campaigns/{source['id']}/locked-contract", json=payload
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == child["id"]
    assert repeated.json()["locked_contract"]["idempotent"] is True
    assert len(
        db.fetch_all(
            "SELECT id FROM events WHERE entity_type='campaign' AND entity_id=? "
            "AND event_type='campaign.contract_promoted'",
            (source["id"],),
        )
    ) == 1

    frozen_objective = child["locked_contract"]["objective"]
    db.execute(
        "UPDATE campaigns SET objective='tampered database draft' WHERE id=?",
        (child["id"],),
    )
    started_child = client.post(
        f"/api/campaigns/{child['id']}/start", json=START_APPROVAL
    )
    assert started_child.status_code == 200
    assert len(create_calls) == 2
    assert create_calls[1]["objective"] == frozen_objective
    child_root = client.app.state.settings.data_dir / "campaigns" / child["id"]
    launch_manifest = json.loads(
        (child_root / "manifest.json").read_text(encoding="utf-8")
    )
    assert launch_manifest["locked_contract"]["contract_sha256"] == child[
        "locked_contract"
    ]["contract_sha256"]


def test_human_approval_is_atomic_audited_and_idempotent(client: TestClient) -> None:
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    campaign = client.post(
        "/api/campaigns",
        json={"venue_key": "PPOPP", "idea_id": idea["id"]},
    ).json()
    db = client.app.state.db
    db.execute(
        "UPDATE campaigns SET schedule_state='awaiting_approval' WHERE id=?",
        (campaign["id"],),
    )
    approval_id = f"approval-{campaign['id']}"

    approved = client.post(
        f"/api/approvals/{approval_id}",
        json={"decision": "approve", "reason": "Resource and ethics checks passed"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["schedule_state"] == "admitted"
    assert body["execution_state"] == "idle"
    assert body["argus_project_id"] is None
    assert body["approval"]["idempotent"] is False
    events = db.fetch_all(
        "SELECT event_type,payload_json FROM events WHERE entity_type='campaign' "
        "AND entity_id=? AND event_type='campaign.approval_approved'",
        (campaign["id"],),
    )
    assert len(events) == 1
    payload = json.loads(events[0]["payload_json"])
    assert payload["launch_triggered"] is False
    assert payload["submission_triggered"] is False

    repeated = client.post(
        f"/api/approvals/{approval_id}",
        json={"decision": "approve", "reason": "Duplicate delivery"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["approval"]["idempotent"] is True
    assert len(
        db.fetch_all(
            "SELECT id FROM events WHERE entity_type='campaign' AND entity_id=? "
            "AND event_type='campaign.approval_approved'",
            (campaign["id"],),
        )
    ) == 1
    opposite = client.post(
        f"/api/approvals/{approval_id}",
        json={"decision": "reject", "reason": "Contradicts the recorded decision"},
    )
    assert opposite.status_code == 409


def test_human_review_rejection_defers_without_side_effects(client: TestClient) -> None:
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    campaign = client.post(
        "/api/campaigns",
        json={"venue_key": "PPOPP", "idea_id": idea["id"]},
    ).json()
    db = client.app.state.db
    db.execute(
        "UPDATE campaigns SET review_state='human_review' WHERE id=?",
        (campaign["id"],),
    )
    rejected = client.post(
        f"/api/approvals/approval-{campaign['id']}",
        json={"decision": "reject", "reason": "Evidence package is incomplete"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["schedule_state"] == "deferred"
    assert rejected.json()["review_state"] == "rejected"
    assert rejected.json()["execution_state"] == "idle"

    not_pending = client.post(
        "/api/campaigns", json={"venue_key": "PPOPP", "idea_id": idea["id"]}
    ).json()
    illegal = client.post(
        f"/api/approvals/approval-{not_pending['id']}",
        json={"decision": "approve", "reason": "Pending state is required"},
    )
    assert illegal.status_code == 409
    malformed = client.post(
        "/api/approvals/not-an-approval",
        json={"decision": "approve", "reason": "Malformed id must be rejected"},
    )
    assert malformed.status_code == 422


@pytest.mark.parametrize(
    ("initial_outcome", "expected_failed_state"),
    [("transport_error", "failed"), ("missing_receipt", "needs_attention")],
)
def test_failed_start_reconciles_same_receipt_and_frozen_packet(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    initial_outcome: str,
    expected_failed_state: str,
) -> None:
    connection = client.post(
        "/api/connections",
        json={"name": "Local", "kind": "local", "base_url": "http://127.0.0.1:8799"},
    ).json()
    _mark_connection_launch_compatible(client, connection["id"])
    resource = client.post(
        "/api/resources",
        json={
            "name": "Retry GPU",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 24,
                "api_budget": "none",
                "max_parallel_jobs": 1,
            },
        },
    ).json()
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    venue = client.get("/api/venues/PPOPP").json()
    calls: list[dict] = []

    class FakeClient:
        def create_daemon(self, **kwargs):
            calls.append(dict(kwargs))
            if len(calls) == 1:
                if initial_outcome == "transport_error":
                    raise ArgusWebApiError("response lost after dispatch")
                return {
                    "command_id": kwargs["command_id"],
                    "command_status": "applied",
                }
            assert kwargs["command_id"] == calls[0]["command_id"]
            assert kwargs["objective"] == calls[0]["objective"]
            assert kwargs["name"] == calls[0]["name"]
            assert kwargs["workdir"] == calls[0]["workdir"]
            return _applied_start_receipt("argus-reconciled-project")

    fake = FakeClient()
    monkeypatch.setattr("foundry.api._client", lambda request, row: fake)
    campaign = client.post(
        "/api/campaigns",
        json={
            "venue_key": "PPOPP",
            "idea_id": idea["id"],
            "deadline_id": venue["deadlines"][0]["id"],
            "connection_id": connection["id"],
            "resource_id": resource["id"],
            "config": {
                "preflight_attestations": BASE_PREFLIGHT_ATTESTATIONS,
                "wall_clock_deadline": PPOPP_WALL_CLOCK,
            },
        },
    ).json()

    first = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert first.status_code == 502
    failed = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert failed["execution_state"] == expected_failed_state
    command_id = failed["launch_command_id"]
    assert command_id == calls[0]["command_id"]
    root = client.app.state.settings.data_dir / "campaigns" / campaign["id"]
    objective_before = (root / "OBJECTIVE.md").read_bytes()
    manifest_before = (root / "manifest.json").read_bytes()
    manifest = json.loads(manifest_before.decode("utf-8"))
    assert manifest["launch_command_id"] == command_id

    changed_draft = client.patch(
        f"/api/campaigns/{campaign['id']}",
        json={"objective": "This later draft must not replace the frozen launch objective."},
    )
    assert changed_draft.status_code == 409
    assert "Launch receipt freezes" in changed_draft.json()["detail"]
    reconciled = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["execution_state"] == "running"
    assert reconciled.json()["argus_project_id"] == "argus-reconciled-project"
    assert reconciled.json()["launch_command_id"] == command_id
    assert (root / "OBJECTIVE.md").read_bytes() == objective_before
    assert (root / "manifest.json").read_bytes() == manifest_before
    event = client.app.state.db.fetch_one(
        "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type='campaign.start_reconciliation_attempted' ORDER BY id DESC LIMIT 1",
        (campaign["id"],),
    )
    assert event is not None
    assert json.loads(event["payload_json"])["launch_command_id"] == command_id
    already_active = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert already_active.status_code == 409


def test_failed_start_receipt_with_sid_is_never_marked_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)

    class AdmissionRequired:
        def create_daemon(self, **kwargs):
            return {
                "sid": "argus-awaiting-admission",
                "rc": 2,
                "spawned": False,
                "command_id": kwargs["command_id"],
                "command_status": "failed",
                "start": {
                    "admission_required": True,
                    "error": "active daemon limit reached",
                },
            }

    monkeypatch.setattr(
        "foundry.api._client", lambda request, row: AdmissionRequired()
    )
    response = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )

    assert response.status_code == 409
    assert "requires admission" in response.json()["detail"]
    persisted = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert persisted["execution_state"] == "needs_attention"
    assert persisted["argus_project_id"] == "argus-awaiting-admission"
    assert persisted["science_state"] != "researching"
    assert persisted["started_at"] is None
    event = client.app.state.db.fetch_one(
        "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type='campaign.start_admission_required' ORDER BY id DESC LIMIT 1",
        (campaign["id"],),
    )
    assert event is not None
    payload = json.loads(event["payload_json"])
    assert payload["argus_project_id"] == "argus-awaiting-admission"
    assert payload["spawned"] is False
    assert payload["admission_required"] is True


@pytest.mark.parametrize("action", ["pause", "drain"])
def test_rejected_stop_receipt_preserves_running_campaign(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    client.app.state.db.execute(
        "UPDATE campaigns SET execution_state='running',science_state='researching',"
        "argus_project_id='active-argus-project',last_summary='verified progress' WHERE id=?",
        (campaign["id"],),
    )

    class RejectedStop:
        def stop(self, project_id: str, *, drain: bool, force: bool):
            assert project_id == "active-argus-project"
            assert drain is (action == "drain")
            return {
                "rc": 3,
                "command_status": "rejected",
                "error": "revision conflict",
            }

    monkeypatch.setattr("foundry.api._client", lambda request, row: RejectedStop())
    response = client.post(
        f"/api/campaigns/{campaign['id']}/{action}",
        json={"reason": "operator requested a boundary"},
    )

    assert response.status_code == 502
    persisted = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert persisted["execution_state"] == "running"
    assert persisted["science_state"] == "researching"
    assert persisted["last_summary"] == "verified progress"
    event = client.app.state.db.fetch_one(
        "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type=? ORDER BY id DESC LIMIT 1",
        (campaign["id"], f"campaign.{action}_failed"),
    )
    assert event is not None
    assert json.loads(event["payload_json"])["command_status"] == "rejected"


def test_promotion_receipt_replays_exact_contract_version_not_child_latest(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _create_contract_ready_campaign(client, with_connection=True)

    class FakeClient:
        def create_daemon(self, **kwargs):
            return _applied_start_receipt("portfolio-before-promotion")

        def stop(self, project_id: str, *, drain: bool, force: bool):
            return _applied_stop_receipt()

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    assert client.post(
        f"/api/campaigns/{source['id']}/start", json=START_APPROVAL
    ).status_code == 200
    assert client.post(
        f"/api/campaigns/{source['id']}/pause", json={"reason": "freeze winner"}
    ).status_code == 200

    payload_a = _locked_payload(primary_claim="Frozen claim A")
    child_a = client.post(
        f"/api/campaigns/{source['id']}/locked-contract", json=payload_a
    ).json()
    receipt_a = child_a["locked_contract"]
    payload_b = _locked_payload(primary_claim="Frozen claim B")
    child_b = client.post(
        f"/api/campaigns/{child_a['id']}/locked-contract", json=payload_b
    ).json()
    assert child_b["locked_contract"]["version"] == 2
    assert child_b["locked_contract"]["request_sha256"] != receipt_a["request_sha256"]

    replay = client.post(
        f"/api/campaigns/{source['id']}/locked-contract", json=payload_a
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == child_a["id"]
    assert replay.json()["locked_contract"]["directory"] == receipt_a["directory"]
    assert replay.json()["locked_contract"]["request_sha256"] == receipt_a[
        "request_sha256"
    ]


def test_complete_locked_packet_is_reconciled_after_database_rollback(
    client: TestClient,
) -> None:
    campaign = _create_contract_ready_campaign(client)
    original_config = dict(campaign["config"])
    payload = _locked_payload()
    first = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=payload
    ).json()
    receipt = first["locked_contract"]
    packet_root = (
        client.app.state.settings.data_dir
        / "campaigns"
        / campaign["id"]
        / receipt["directory"]
    )
    objective_before = (packet_root / "OBJECTIVE.md").read_bytes()
    manifest_before = (packet_root / "MANIFEST.json").read_bytes()
    db = client.app.state.db
    db.execute(
        "UPDATE campaigns SET objective='',science_state='candidate',config_json=? WHERE id=?",
        (json.dumps(original_config), campaign["id"]),
    )
    db.execute(
        "DELETE FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type='campaign.contract_locked'",
        (campaign["id"],),
    )

    recovered = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=payload
    )
    assert recovered.status_code == 201, recovered.json()
    recovered_receipt = recovered.json()["locked_contract"]
    assert recovered_receipt["directory"] == receipt["directory"]
    assert recovered_receipt["contract_sha256"] == receipt["contract_sha256"]
    assert (packet_root / "OBJECTIVE.md").read_bytes() == objective_before
    assert (packet_root / "MANIFEST.json").read_bytes() == manifest_before


def test_orphan_locked_packet_with_wrong_binding_is_never_adopted(
    client: TestClient,
) -> None:
    campaign = _create_contract_ready_campaign(client)
    original_config = dict(campaign["config"])
    payload = _locked_payload()
    locked = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=payload
    ).json()["locked_contract"]
    manifest_path = (
        client.app.state.settings.data_dir
        / "campaigns"
        / campaign["id"]
        / locked["directory"]
        / "MANIFEST.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bindings"]["resource_id"] = "wrong-resource"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client.app.state.db.execute(
        "UPDATE campaigns SET objective='',science_state='candidate',config_json=? WHERE id=?",
        (json.dumps(original_config), campaign["id"]),
    )
    rejected = client.post(
        f"/api/campaigns/{campaign['id']}/locked-contract", json=payload
    )
    assert rejected.status_code == 409
    assert "bindings" in rejected.json()["detail"]


def test_promoted_packet_is_recoverable_after_child_transaction_rollback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _create_contract_ready_campaign(client, with_connection=True)

    class FakeClient:
        def create_daemon(self, **kwargs):
            return _applied_start_receipt("portfolio-before-crash")

        def stop(self, project_id: str, *, drain: bool, force: bool):
            return _applied_stop_receipt()

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    assert client.post(
        f"/api/campaigns/{source['id']}/start", json=START_APPROVAL
    ).status_code == 200
    assert client.post(
        f"/api/campaigns/{source['id']}/pause", json={"reason": "winner selected"}
    ).status_code == 200
    payload = _locked_payload()
    first = client.post(
        f"/api/campaigns/{source['id']}/locked-contract", json=payload
    ).json()
    child_id = first["id"]
    receipt = first["locked_contract"]
    packet_root = (
        client.app.state.settings.data_dir
        / "campaigns"
        / child_id
        / receipt["directory"]
    )
    packet_hash = hashlib.sha256((packet_root / "MANIFEST.json").read_bytes()).hexdigest()
    db = client.app.state.db
    db.execute(
        "DELETE FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type='campaign.contract_promoted'",
        (source["id"],),
    )
    db.execute("DELETE FROM campaigns WHERE id=?", (child_id,))

    # Recovery must adopt the timestamp authenticated from the immutable
    # packet, not require a retry to land in the same wall-clock second.
    monkeypatch.setattr("foundry.api.utc_now", lambda: "2099-01-01T00:00:00+00:00")
    recovered = client.post(
        f"/api/campaigns/{source['id']}/locked-contract", json=payload
    )
    assert recovered.status_code == 201, recovered.json()
    assert recovered.json()["id"] == child_id
    assert recovered.json()["locked_contract"]["directory"] == receipt["directory"]
    assert hashlib.sha256((packet_root / "MANIFEST.json").read_bytes()).hexdigest() == packet_hash


@pytest.mark.parametrize(
    "approval",
    [
        {"human_approved": 1, "approval_reason": "reviewed", "actor": "operator"},
        {"human_approved": False, "approval_reason": "reviewed", "actor": "operator"},
        {"human_approved": True, "approval_reason": "   ", "actor": "operator"},
        {"human_approved": True, "approval_reason": "reviewed", "actor": "   "},
    ],
)
def test_start_requires_strict_attributable_human_approval(
    client: TestClient, approval: dict[str, object]
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    response = client.post(f"/api/campaigns/{campaign['id']}/start", json=approval)
    assert response.status_code == 422
    assert client.get(f"/api/campaigns/{campaign['id']}").json()[
        "launch_command_id"
    ] is None


def test_launch_receipt_freezes_all_patchable_launch_critical_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)

    class LostResponse:
        def create_daemon(self, **kwargs):
            raise ArgusWebApiError("lost after dispatch")

    monkeypatch.setattr("foundry.api._client", lambda request, row: LostResponse())
    assert client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    ).status_code == 502
    mutations = [
        {"title": "changed launch name"},
        {"objective": "changed objective"},
        {"connection_id": None},
        {"resource_id": None},
        {"config": {**campaign["config"], "gpu_hours": 99}},
    ]
    for mutation in mutations:
        rejected = client.patch(
            f"/api/campaigns/{campaign['id']}", json=mutation
        )
        assert rejected.status_code == 409, mutation
        assert "Launch receipt freezes" in rejected.json()["detail"]


def test_connection_probe_truth_is_reserved_and_identity_change_invalidates_it(
    client: TestClient,
) -> None:
    forged = client.post(
        "/api/connections",
        json={
            "name": "Forged",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
            "metadata": {"launch_compatible": True},
        },
    )
    assert forged.status_code == 422
    connection = client.post(
        "/api/connections",
        json={
            "name": "Mutable idle connection",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
            "metadata": {"owner": "lab-a"},
        },
    ).json()
    _mark_connection_launch_compatible(client, connection["id"])
    forged_patch = client.patch(
        f"/api/connections/{connection['id']}",
        json={"metadata": {"protocol_compatible": True}},
    )
    assert forged_patch.status_code == 422

    changed = client.patch(
        f"/api/connections/{connection['id']}",
        json={"base_url": "http://127.0.0.1:8800"},
    )
    assert changed.status_code == 200
    body = changed.json()
    assert body["status"] == "unknown"
    assert body["metadata"] == {"owner": "lab-a"}
    assert body["last_checked_at"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"base_url": "http://127.0.0.1:8800"},
        {"kind": "remote"},
        {"bearer_token": "replacement-secret"},
        {"clear_bearer_token": True},
    ],
)
def test_active_campaign_freezes_connection_identity_and_credentials(
    client: TestClient, mutation: dict[str, object]
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    client.app.state.db.execute(
        "UPDATE campaigns SET execution_state='running' WHERE id=?", (campaign["id"],)
    )
    response = client.patch(
        f"/api/connections/{campaign['connection_id']}", json=mutation
    )
    assert response.status_code == 409
    assert "active campaign freezes" in response.json()["detail"]


def test_remote_launch_uses_target_workspace_without_sending_local_path(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client)
    remote = client.post(
        "/api/connections",
        json={
            "name": "Remote Argus",
            "kind": "remote",
            "base_url": "https://argus.example.invalid",
        },
    ).json()
    _mark_connection_launch_compatible(client, remote["id"])
    client.app.state.db.execute(
        "UPDATE campaigns SET connection_id=? WHERE id=?", (remote["id"], campaign["id"])
    )
    calls: list[dict[str, object]] = []

    class FakeRemote:
        def create_daemon(self, **kwargs):
            calls.append(dict(kwargs))
            return _applied_start_receipt(
                "remote-project", workdir="/argus/workspaces/remote-project"
            )

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeRemote())
    started = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 200
    assert calls[0]["workdir"] == ""
    assert calls[0]["launch_cwd"] == ""
    manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / campaign["id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["launch"]["workspace_mode"] == "target_argus_default"
    assert manifest["launch"]["workdir"] == ""


@pytest.mark.parametrize(
    "capacity_patch",
    [
        {"gpu_count": True},
        {"gpu_model": "   "},
        {"gpu_hours": "nonsense"},
        {"gpu_hours": 0},
        {"max_parallel_jobs": 0},
        {"max_parallel_jobs": "1"},
        {"max_parallel_jobs": True},
        {"api_budget": ""},
        {"api_budget": "unlimited"},
    ],
)
def test_start_rejects_malformed_or_unbounded_resource_contract(
    client: TestClient, capacity_patch: dict[str, object]
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    resource = client.app.state.db.fetch_one(
        "SELECT capacity_json FROM resources WHERE id=?", (campaign["resource_id"],)
    )
    capacity = json.loads(resource["capacity_json"])
    capacity.update(capacity_patch)
    client.app.state.db.execute(
        "UPDATE resources SET capacity_json=? WHERE id=?",
        (json.dumps(capacity), campaign["resource_id"]),
    )
    rejected = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "Resource contract is incomplete" in rejected.json()["detail"]


def test_start_rejects_past_wall_clock_deadline(client: TestClient) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    config = dict(campaign["config"])
    config["wall_clock_deadline"] = "2026-08-22T23:59:00+08:00"
    client.app.state.db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(config), campaign["id"]),
    )
    rejected = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "must be in the future" in rejected.json()["detail"]


def test_start_enforces_global_and_per_resource_concurrency_atomically(
    client: TestClient,
) -> None:
    blocker = _create_contract_ready_campaign(client, with_connection=True)
    target = _create_contract_ready_campaign(client, with_connection=True)
    db = client.app.state.db
    db.execute(
        "UPDATE campaigns SET execution_state='running' WHERE id=?", (blocker["id"],)
    )
    db.execute(
        "UPDATE app_settings SET value_json='1' WHERE key='max_concurrent_campaigns'"
    )
    global_rejected = client.post(
        f"/api/campaigns/{target['id']}/start", json=START_APPROVAL
    )
    assert global_rejected.status_code == 409
    assert "Global campaign concurrency limit" in global_rejected.json()["detail"]

    db.execute(
        "UPDATE app_settings SET value_json='2' WHERE key='max_concurrent_campaigns'"
    )
    db.execute(
        "UPDATE campaigns SET resource_id=? WHERE id=?",
        (target["resource_id"], blocker["id"]),
    )
    resource_rejected = client.post(
        f"/api/campaigns/{target['id']}/start", json=START_APPROVAL
    )
    assert resource_rejected.status_code == 409
    assert "Resource parallel-job limit" in resource_rejected.json()["detail"]


def test_release_reference_is_not_called_pinned_without_probed_full_sha(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    config = dict(campaign["config"])
    config["argus_release_sha"] = "main-latest"
    client.app.state.db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(config), campaign["id"]),
    )

    class FakeClient:
        def create_daemon(self, **kwargs):
            return _applied_start_receipt("reference-only")

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    assert client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    ).status_code == 200
    manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / campaign["id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["argus_release_reference"] == "main-latest"
    assert manifest["release_reference_source"] == "campaign_config_reference"
    assert manifest["release_pinned"] is False
    assert manifest["release_pin_source"] is None


def test_matching_full_revision_from_current_probe_is_release_pinned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    sha = "a" * 40
    db = client.app.state.db
    row = db.fetch_one(
        "SELECT metadata_json FROM connections WHERE id=?", (campaign["connection_id"],)
    )
    metadata = json.loads(row["metadata_json"])
    metadata.update({"launch_compatible": True, "argus_revision": sha})
    db.execute(
        "UPDATE connections SET status='online',last_checked_at=?,metadata_json=? WHERE id=?",
        (datetime.now(UTC).isoformat(), json.dumps(metadata), campaign["connection_id"]),
    )

    class FakeClient:
        def create_daemon(self, **kwargs):
            return _applied_start_receipt("pinned-release")

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    assert client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    ).status_code == 200
    manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / campaign["id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["argus_release_sha"] == sha
    assert manifest["release_pinned"] is True
    assert manifest["release_pin_source"] == "launch_compatible_target_probe"
    detail = client.get(f"/api/campaigns/{campaign['id']}").json()
    assert detail["release_pinned"] is True
    assert detail["release_reference"] == sha


@pytest.mark.parametrize(
    ("resource_update", "expected"),
    [
        ({"enabled": False}, "disabled"),
        ({"availability_state": "reserved"}, "not available"),
    ],
)
def test_retry_rechecks_live_resource_admission(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    resource_update: dict[str, object],
    expected: str,
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)
    calls = 0

    class LostResponse:
        def create_daemon(self, **kwargs):
            nonlocal calls
            calls += 1
            raise ArgusWebApiError("lost after dispatch")

    monkeypatch.setattr("foundry.api._client", lambda request, row: LostResponse())
    assert client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    ).status_code == 502
    assert client.patch(
        f"/api/resources/{campaign['resource_id']}", json=resource_update
    ).status_code == 200
    retry = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert retry.status_code == 409
    assert expected in retry.json()["detail"]
    assert calls == 1


def test_start_approval_is_frozen_in_manifest_event_and_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _create_contract_ready_campaign(client, with_connection=True)

    class LostResponse:
        def create_daemon(self, **kwargs):
            raise ArgusWebApiError("lost after dispatch")

    monkeypatch.setattr("foundry.api._client", lambda request, row: LostResponse())
    assert client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    ).status_code == 502
    manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / campaign["id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    approval = manifest["human_launch_approval"]
    for key, value in START_APPROVAL.items():
        assert approval[key] == value
    event = client.app.state.db.fetch_one(
        "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
        "AND event_type='campaign.start_authorized'",
        (campaign["id"],),
    )
    assert json.loads(event["payload_json"])["human_launch_approval"] == approval
    changed = {**START_APPROVAL, "actor": "different-operator"}
    retry = client.post(f"/api/campaigns/{campaign['id']}/start", json=changed)
    assert retry.status_code == 409
    assert "preserve the immutable human approval" in retry.json()["detail"]


def test_approval_decision_requires_nonblank_audit_reason(client: TestClient) -> None:
    idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
    campaign = client.post(
        "/api/campaigns", json={"venue_key": "PPOPP", "idea_id": idea["id"]}
    ).json()
    client.app.state.db.execute(
        "UPDATE campaigns SET schedule_state='awaiting_approval' WHERE id=?",
        (campaign["id"],),
    )
    for reason in (None, "", "   "):
        payload: dict[str, object] = {"decision": "approve"}
        if reason is not None:
            payload["reason"] = reason
        response = client.post(
            f"/api/approvals/approval-{campaign['id']}", json=payload
        )
        assert response.status_code == 422


def test_websocket_replays_events(client: TestClient) -> None:
    client.app.state.db.append_event("tests", "test.created", payload={"ok": True})
    with client.websocket_connect("/api/ws?after_id=0") as websocket:
        assert websocket.receive_json()["type"] == "ready"
        message = websocket.receive_json()
        assert message.get("event", {}).get("event_type") == "test.created"


def test_websocket_accepts_configured_browser_origin(client: TestClient) -> None:
    with client.websocket_connect(
        "/api/ws?after_id=0", headers={"origin": "http://LOCALHOST:5173/"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "ready"


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example",
        "null",
        "http://localhost:5173.attacker.example",
        "http://user:password@localhost:5173",
    ],
)
def test_websocket_rejects_untrusted_browser_origin(
    client: TestClient, origin: str
) -> None:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/ws?after_id=0", headers={"origin": origin}
        ):
            pass
    assert exc_info.value.code == 1008

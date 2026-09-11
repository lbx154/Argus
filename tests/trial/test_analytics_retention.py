"""Retention and consent integration using only synthetic, temporary data."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.analytics_routes import register_analytics
from argus_skill.trial.interaction_capture import Capture, get_interaction, list_interactions


@pytest.fixture
def environment(tmp_path):
    now = [2_000_000_000.0]
    analytics = Analytics(
        tmp_path / "index",
        {
            "trial-01": {"data_dir": str(tmp_path / "tenant"), "internal_test": False},
            "trial-02": {"data_dir": str(tmp_path / "internal"), "internal_test": True},
        },
        tmp_path / "missing-meter.sqlite3",
        tmp_path / "missing-compute.sqlite3",
        notice_version="notice-v1",
        retention_days=7,
        clock=lambda: now[0],
    )
    identity = [{"role": "admin", "readonly": False}]
    app = FastAPI()
    register_analytics(app, analytics, lambda request: identity[0])
    with TestClient(app) as client:
        yield analytics, now, identity, client


def capture(analytics, tenant="trial-01"):
    return Capture(
        analytics, tenant, None, "/api/projects",
        {"text": "Synthetic research request"},
    )


def counts(analytics):
    with analytics._db() as db:
        return {
            table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("events", "interactions", "consents")
        }


def prune(client):
    return client.post("/admin/api/prune", headers={"Origin": "http://testserver"})


def test_admin_retention_counts_boundaries_versions_and_source_preservation(environment):
    analytics, now, _, client = environment
    sources = {}
    for tenant in analytics.tenants:
        analytics.record_consent(tenant, analytics.notice_version)
        analytics.record_request(tenant, "POST", "/api/projects", 200, 1)
        old = capture(analytics, tenant)
        if tenant == "trial-01":
            old.feed(b'{"kind":"chat","reply":"Synthetic visible response"}')
            old.finish(200, True, "application/json")
        root = analytics.tenants[tenant]["data_dir"] / "home/.argus-skill/projects/s-fixture"
        root.mkdir(parents=True)
        for name in ("session.json", "events.jsonl", "transcript.jsonl"):
            path = root / name
            sources[path] = b'{"text":"Synthetic source retained"}\n'
            path.write_bytes(sources[path])
    with analytics._db() as db:
        receipts = [tuple(row) for row in db.execute("SELECT * FROM consents")]

    now[0] += 1
    analytics.record_request("trial-01", "POST", "/api/projects", 200, 1)
    boundary = capture(analytics)
    now[0] += analytics.retention_days * 86400
    fresh = capture(analytics)
    analytics.notice_version = "notice-v2"

    response = prune(client)
    assert response.status_code == 200
    assert response.json() == {"deleted_events": 2, "deleted_interactions": 2}
    with analytics._db() as db:
        assert {row["id"] for row in db.execute("SELECT id FROM interactions")} == {
            boundary.id, fresh.id,
        }
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert [tuple(row) for row in db.execute("SELECT * FROM consents")] == receipts
    assert all(path.read_bytes() == original for path, original in sources.items())
    assert prune(client).json() == {"deleted_events": 0, "deleted_interactions": 0}
    now[0] += 1
    assert prune(client).json() == {"deleted_events": 1, "deleted_interactions": 1}
    assert counts(analytics) == {"events": 0, "interactions": 1, "consents": 2}


def test_retention_before_any_capture_and_metadata_return_compatibility(environment):
    analytics, _, _, client = environment
    assert analytics.prune() == 0
    response = prune(client)
    assert response.status_code == 200
    assert response.json() == {"deleted_events": 0, "deleted_interactions": 0}


def test_consent_reads_remain_available_while_collector_holds_a_write_transaction(environment):
    analytics, _, _, _ = environment
    analytics.record_consent("trial-01", analytics.notice_version)
    with analytics._db() as writer:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("DELETE FROM events WHERE tenant_id=?", ("trial-01",))
        assert analytics.consented("trial-01", analytics.notice_version)
        assert not analytics.consented("trial-02", analytics.notice_version)


@pytest.mark.parametrize(
    ("identity", "origin", "status"),
    [
        (None, "http://testserver", 401),
        ({"role": "trial", "tenant": "trial-01", "readonly": False}, "http://testserver", 403),
        ({"role": "admin", "readonly": True}, "http://testserver", 403),
        ({"role": "admin", "readonly": False}, "https://foreign.invalid", 403),
        ({"role": "admin", "readonly": False}, None, 403),
    ],
)
def test_retention_requires_mutating_same_origin_admin(environment, identity, origin, status):
    analytics, now, identities, client = environment
    analytics.record_consent("trial-01", analytics.notice_version)
    analytics.record_request("trial-01", "POST", "/api/projects", 200, 1)
    capture(analytics)
    now[0] += (analytics.retention_days + 1) * 86400
    identities[0] = identity
    response = client.post("/admin/api/prune", headers={"Origin": origin} if origin else {})
    assert response.status_code == status
    assert counts(analytics) == {"events": 1, "interactions": 1, "consents": 1}


def test_capture_and_operator_reads_require_current_notice_consent(environment):
    analytics, _, _, client = environment
    for version in (None, "notice-old"):
        if version:
            analytics.record_consent("trial-01", version)
        assert analytics.record_request("trial-01", "POST", "/api/projects", 200, 1) is None
        with pytest.raises(AnalyticsError, match="consent_required"):
            capture(analytics)
        for path in ("interactions", "interactions/1", "projects"):
            response = client.get(f"/admin/api/tenants/trial-01/{path}")
            assert response.status_code == 403
            assert response.json() == {"detail": "consent_required"}

    analytics.record_consent("trial-01", analytics.notice_version)
    recorded = capture(analytics)
    recorded.feed(b'{"kind":"chat","reply":"Synthetic visible response"}')
    recorded.finish(200, True, "application/json")
    response = client.get(f"/admin/api/tenants/trial-01/interactions/{recorded.id}")
    assert response.status_code == 200
    assert response.json()["input"] == {"text": "Synthetic research request"}
    assert response.json()["result"]["reply"] == "Synthetic visible response"

    analytics.record_consent("trial-02", analytics.notice_version)
    assert client.get(
        f"/admin/api/tenants/trial-02/interactions/{recorded.id}",
    ).status_code == 404
    analytics.notice_version = "notice-v2"
    assert client.get("/admin/api/tenants/trial-01/interactions").status_code == 403
    analytics.record_consent("trial-01", analytics.notice_version)
    assert list_interactions(analytics, "trial-01") == {"interactions": []}
    with pytest.raises(AnalyticsError, match="interaction_not_found"):
        get_interaction(analytics, "trial-01", recorded.id)


def test_notice_change_blocks_finishing_existing_capture(environment):
    analytics, _, _, _ = environment
    analytics.record_consent("trial-01", analytics.notice_version)
    recorded = capture(analytics)
    recorded.feed(b'{"kind":"chat","reply":"Synthetic visible response"}')
    analytics.notice_version = "notice-v2"
    analytics.record_consent("trial-01", analytics.notice_version)
    with pytest.raises(AnalyticsError, match="consent_required"):
        recorded.finish(200, True, "application/json")
    with analytics._db() as db:
        row = db.execute("SELECT state, result FROM interactions WHERE id=?", (recorded.id,)).fetchone()
    assert tuple(row) == ("incomplete", None)


def test_policy_distinguishes_metadata_interaction_copies_and_source_files(environment):
    analytics, _, _, _ = environment
    policy = analytics.policy()
    assert policy["retention_days"] == 7
    assert "no bodies" in policy["request_metadata"]
    assert "bounded" in policy["interaction_copies"]
    assert "visible response" in policy["interaction_copies"]
    assert "not copied" in policy["source_project_traces"]
    assert "metadata events and captured interactions" in policy["retention_scope"]
    assert policy["source_trace_files_deleted_by_retention"] is False
    assert "separately" in policy["consent_receipts_retention"]
    assert "current notice consent required" in policy["consent_scope"]
    assert policy["redaction"] == "best-effort"
    assert policy["operator_review_required_before_training_or_publication"] is True
    assert policy["automatic_training_or_external_transmission"] is False

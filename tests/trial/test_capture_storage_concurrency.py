"""Synthetic SQLite/WAL regressions for live capture under concurrent HTTP use."""

import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import FastAPI

from argus_skill.trial.analytics_routes import register_analytics
from argus_skill.trial.training_bridge import TrainingBridge
from argus_skill.trial.training_data import COMBINED_NOTICE_VERSION
from tests.trial.test_analytics import consent
from tests.trial.test_analytics import setup as setup
from tests.trial.test_training_runtime import registration, verifier
from tests.trial.test_training_runtime import training as training


def test_portal_anchor_keeps_wal_attached_without_pinning_a_transaction(setup):
    analytics, _, _ = setup
    consent(analytics)
    analytics.open_storage()
    anchor = analytics._storage_anchor
    analytics.open_storage()
    assert analytics._storage_anchor is anchor
    try:
        with analytics._db() as db:
            assert db.execute("PRAGMA synchronous").fetchone()[0] == 1
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        wal = analytics.path.with_name(analytics.path.name + "-wal")
        analytics.record_request("trial-01", "GET", "/", 200, 1)
        inode = wal.stat().st_ino
        with ThreadPoolExecutor(max_workers=6) as workers:
            jobs = [workers.submit(analytics.record_request, "trial-01", "GET", "/", 200, 1)
                    for _ in range(48)]
            assert len({job.result(timeout=10) for job in jobs}) == 48
            # WAL readers must remain available during a separate write transaction.
            with analytics._db() as writer:
                writer.execute("BEGIN IMMEDIATE")
                assert workers.submit(analytics.consented, "trial-01", analytics.notice_version).result(timeout=2)
        assert wal.stat().st_ino == inode  # no last-connection checkpoint/unlink between requests
        assert anchor.in_transaction is False
    finally:
        analytics.close_storage()
    analytics.close_storage()
    assert analytics._storage_anchor is None
    with sqlite3.connect(analytics.path) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 49


def test_runtime_registration_indexes_only_its_project_and_keeps_initial_boundary(training, monkeypatch):
    analytics = training.analytics
    analytics.notice_version = COMBINED_NOTICE_VERSION
    analytics.record_consent("tenant-one", analytics.notice_version)
    project = analytics.tenants["tenant-one"]["global_root"] / "projects/s-new"
    project.mkdir()
    (project / "session.json").write_text(json.dumps({"id": "s-new"}))
    source = project / "events.jsonl"
    source.write_text(json.dumps({"type": "ui.argus", "text": "BEFORE_INDEX_BOUNDARY"}) + "\n")
    request = registration()
    request["value"]["sid"] = "s-new"
    bridge = TrainingBridge(training, "tenant-one", verifier)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("runtime registration must not scan/prune a whole tenant")

    with monkeypatch.context() as patch:
        patch.setattr(training.journal, "poll", forbidden)
        patch.setattr(training.journal, "prune", forbidden)
        patch.setattr(analytics, "projects", forbidden)
        result = bridge.dispatch(request, (10, 0, 0))
    assert result["enabled"] is True
    with source.open("a") as stream:
        stream.write(json.dumps({"type": "ui.argus", "text": "AFTER_INDEX_BOUNDARY"}) + "\n")
    training.journal.poll("tenant-one")
    with analytics._db() as db:
        records = " ".join(row[0] for row in db.execute(
            "SELECT record FROM journey_events WHERE tenant_id='tenant-one' AND sid='s-new'",
        ))
    assert "BEFORE_INDEX_BOUNDARY" not in records
    assert "AFTER_INDEX_BOUNDARY" in records


def test_slow_consent_read_does_not_block_the_http_event_loop(setup, monkeypatch):
    analytics, _, _ = setup
    consent(analytics)
    entered, release = threading.Event(), threading.Event()
    original = analytics.consented

    def slow_consent(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(analytics, "consented", slow_consent)
    app = FastAPI()
    register_analytics(app, analytics, lambda request: {
        "tenant": "trial-01", "role": "trial", "readonly": False,
    } if request.url.path == "/blocked" else None)

    @app.get("/blocked")
    async def blocked():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.get("/blocked"))
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
            response = await asyncio.wait_for(client.get("/health"), timeout=1)
            assert response.status_code == 200 and not release.is_set()
            release.set()
            assert (await pending).status_code == 200

    timer = threading.Timer(2, release.set)
    timer.start()
    try:
        asyncio.run(exercise())
    finally:
        release.set()
        timer.cancel()


def test_registration_database_outage_is_reported_as_storage_failure(training, monkeypatch):
    bridge = TrainingBridge(training, "tenant-one", verifier)

    def unavailable(*_args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(training.journal, "ensure_project", unavailable)
    from argus_skill.trial.analytics import AnalyticsError

    try:
        bridge.dispatch(registration(), (10, 0, 0))
    except AnalyticsError as error:
        assert error.status == 503 and error.code == "training_storage_unavailable"
    else:
        raise AssertionError("storage failure must remain visible")
    assert bridge.status()["counts"]["registration_failed"] == 1
    assert bridge.status()["last_error_code"] == "training_storage_unavailable"

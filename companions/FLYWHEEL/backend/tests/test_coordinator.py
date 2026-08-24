from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings
from foundry.integrations.argus_webapi import ConnectionTest


def test_coordinator_projects_real_argus_mission_view_and_artifacts(tmp_path: Path) -> None:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "foundry.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://127.0.0.1:5174",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as client:
        connection = client.post(
            "/api/connections",
            json={
                "name": "Mock Argus",
                "kind": "local",
                "base_url": "http://127.0.0.1:8799",
            },
        ).json()
        campaign = client.post(
            "/api/campaigns",
            json={"venue_key": "PPOPP", "title": "Projection test"},
        ).json()
        db = client.app.state.db
        db.execute(
            "UPDATE campaigns SET connection_id=?,argus_project_id='s-projection',"
            "execution_state='running' WHERE id=?",
            (connection["id"], campaign["id"]),
        )

        class FakeArgus:
            def snapshot(self, sid: str, *, events_limit: int):
                assert sid == "s-projection" and events_limit == 500
                return {
                    "daemon": {"alive": True, "state": "working"},
                    "mission_view": {
                        "mission": {
                            "summary": "Engineer is validating the decisive falsifier.",
                            "objective": "Fallback objective",
                        },
                        "stage": {"label": "Evidence"},
                        "roles": [{"role": "engineer", "status": "active"}],
                    },
                }

            def events(self, sid: str, *, limit: int):
                assert limit == 1_000
                return [{"id": "evt-1", "type": "engineer.progress"}]

            def artifacts(self, sid: str):
                return [{"path": "paper/main.pdf", "exists": True, "kind": "pdf"}]

        coordinator = client.app.state.coordinator
        coordinator._client = lambda row: FakeArgus()  # type: ignore[method-assign]
        asyncio.run(coordinator.poll_campaigns())

        projected = client.get(f"/api/campaigns/{campaign['id']}").json()
        assert projected["process_alive"] is True
        assert projected["making_progress"] is True
        assert projected["snapshot_stale"] is False
        assert projected["last_summary"] == "Engineer is validating the decisive falsifier."
        assert projected["last_snapshot"]["foundry_artifacts_status"] == "available"
        assert projected["last_snapshot"]["foundry_artifacts"][0]["path"] == "paper/main.pdf"


def test_coordinator_does_not_promote_incompatible_connection_to_online(tmp_path: Path) -> None:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "foundry.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://127.0.0.1:5174",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as client:
        connection = client.post(
            "/api/connections",
            json={
                "name": "Capability-incomplete Argus",
                "kind": "local",
                "base_url": "http://127.0.0.1:8799",
                "metadata": {"operator_note": "preserve me"},
            },
        ).json()

        class FakeArgus:
            def test_connection(self) -> ConnectionTest:
                return ConnectionTest(
                    ok=True,
                    authenticated=True,
                    authentication_required=True,
                    runtime={"revision": "revision-under-test"},
                    protocol={"name": "argus.webapi", "major": 1, "minor": 13},
                    capabilities=(
                        "daemon.admission.v1",
                        "daemon.command.v1",
                        "mission.view.v1",
                        "snapshot.schema.v1",
                    ),
                    snapshot_schema_version=7,
                    backend_ready=True,
                )

        coordinator = client.app.state.coordinator
        coordinator._client = lambda row: FakeArgus()  # type: ignore[method-assign]
        asyncio.run(coordinator.poll_connections())
        asyncio.run(coordinator.poll_connections())

        persisted = next(
            item
            for item in client.get("/api/connections").json()["items"]
            if item["id"] == connection["id"]
        )
        assert persisted["status"] == "incompatible"
        assert "argus.webapi major 1" in persisted["last_error"]
        assert persisted["metadata"]["operator_note"] == "preserve me"
        assert persisted["metadata"]["protocol_compatible"] is True
        assert persisted["metadata"]["launch_compatible"] is False
        assert persisted["metadata"]["missing_capabilities"] == ["research.events.v1"]
        assert persisted["metadata"]["argus_revision"] == "revision-under-test"

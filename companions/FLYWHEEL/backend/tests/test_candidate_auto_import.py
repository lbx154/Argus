from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings
from foundry.integrations.argus_webapi import ArtifactDownload
from foundry.services.candidate_import import canonical_bytes


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                database_path=tmp_path / "foundry.db",
                data_dir=tmp_path / "runtime",
                seed_data_dir=Path(__file__).resolve().parents[2] / "data" / "seeds",
                cors_origins=("http://localhost:5173",),
                poll_interval_seconds=0,
                auto_seed=True,
            )
        )
    )


def _candidate() -> dict:
    return {
        "candidate_key": "team-specific-runtime-causality",
        "title": "Team-specific runtime causality",
        "problem_gap": "A measured gap",
        "core_hypothesis": "A falsifiable hypothesis",
        "mechanism": "A specified mechanism",
        "closest_work": ["doi:10.example/primary"],
        "differentiation_claim": "A bounded distinction",
        "public_or_authorized_data": ["public benchmark"],
        "method": "Method",
        "strongest_baselines": ["baseline@sha"],
        "decisive_experiments": ["held-out falsifier"],
        "falsifier": "No effect on held-out test",
        "estimated_resources": {"gpu_hours": 8},
        "elapsed_time_plan": "14 days",
        "venue_fit": "PPOPP method and systems evidence",
        "risks": ["collision"],
        "ethics_and_license": "public, permissive",
        "expected_information_gain": "High even for null result",
        "terminal_recommendation": "revise",
        "team_specific_advantage": "The frozen team has the required measurement testbed.",
        "condition_fit_counterfactual": "Demote if the team loses testbed access.",
        "novelty_collision_test": {
            "search_cutoff": "2026-08-24",
            "closest_source_ids": ["doi:10.example/primary"],
            "falsifier": "Prior work already establishes the same mechanism and scope.",
        },
    }


def _setup_run(client: TestClient) -> tuple[dict, dict]:
    connection = client.post(
        "/api/connections",
        json={"name": "Bound Argus", "kind": "local", "base_url": "http://127.0.0.1:8799"},
    ).json()
    profile_response = client.post(
        "/api/team-profiles",
        json={
            "name": "Runtime causality group",
            "expertise": ["runtime systems"],
            "methods": ["causal measurement"],
            "data_access": ["public benchmark"],
            "constraints": {"gpu_hours": 16},
            "goals": {"contribution": "mechanistic result"},
            "policy": {"private_data": False},
        },
    )
    assert profile_response.status_code == 201, profile_response.text
    run_response = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile_response.json()["id"],
            "venue_key": "PPOPP",
            "connection_id": connection["id"],
            "candidate_count": 3,
            "finalist_count": 1,
            "completion_target": "One collision-tested direction or NO_WINNER.",
            "create_campaign": True,
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    client.app.state.db.execute(
        "UPDATE campaigns SET argus_project_id='s-auto-import',execution_state='running' WHERE id=?",
        (run["campaign_id"],),
    )
    return run, connection


class _ArtifactArgus:
    def __init__(self, candidates: list[dict], manifest: dict) -> None:
        self.payloads = {
            "CANDIDATES.json": canonical_bytes(candidates),
            "CANDIDATES_MANIFEST.json": canonical_bytes(manifest),
        }

    def snapshot(self, sid: str, *, events_limit: int) -> dict:
        assert sid == "s-auto-import" and events_limit == 500
        return {
            "daemon": {"alive": False, "state": "stopped"},
            "mission_view": {
                "mission": {
                    "status": "complete",
                    "completed_at": 1_787_500_000.0,
                    "summary": "Conditioned portfolio delivered.",
                }
            },
        }

    def events(self, sid: str, *, limit: int) -> list[dict]:
        assert sid == "s-auto-import" and limit == 1_000
        return [{"type": "project.completed", "ts": 1_787_500_000.0}]

    def artifacts(self, sid: str) -> list[dict]:
        return [
            {
                "path": path,
                "exists": True,
                "kind": "json",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in self.payloads.items()
        ]

    def download_artifact(
        self, sid: str, path: str, *, max_bytes: int | None = None
    ) -> ArtifactDownload:
        content = self.payloads[path]
        assert max_bytes is None or len(content) <= max_bytes
        return ArtifactDownload(
            path=path,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            content_type="application/json",
            content=content,
        )


def test_terminal_bound_candidate_artifacts_are_auto_imported(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        run, _ = _setup_run(client)
        candidates = [_candidate()]
        manifest = {
            "schema_version": "flywheel.ideation-candidates/1",
            "condition_sha256": run["condition_sha256"],
            "objective_sha256": run["objective_sha256"],
            "candidates_sha256": hashlib.sha256(canonical_bytes(candidates)).hexdigest(),
            "candidate_count": 1,
        }
        client.app.state.coordinator._client = lambda row: _ArtifactArgus(  # type: ignore[method-assign]
            candidates, manifest
        )
        asyncio.run(client.app.state.coordinator.poll_campaigns())

        detail = client.get(f"/api/ideation/runs/{run['id']}").json()
        campaign = client.get(f"/api/campaigns/{run['campaign_id']}").json()
        assert detail["state"] == "awaiting_labels"
        assert len(detail["candidates"]) == 1
        assert detail["candidate_manifest"] == manifest
        stored = client.app.state.db.fetch_one(
            "SELECT imported_from FROM generated_idea_candidates WHERE ideation_run_id=?",
            (run["id"],),
        )
        assert stored is not None and stored["imported_from"] == "argus_artifact"
        imported_event = client.app.state.db.fetch_one(
            "SELECT payload_json FROM events WHERE event_type='ideation.candidates_imported' "
            "AND entity_id=? ORDER BY id DESC LIMIT 1",
            (run["id"],),
        )
        assert imported_event is not None
        provenance = json.loads(imported_event["payload_json"])["provenance_receipt"]
        assert provenance["transport"] == "argus_allowlisted_artifact_download"
        assert provenance["argus_project_id"] == "s-auto-import"
        assert {item["path"] for item in provenance["artifacts"]} == {
            "CANDIDATES.json",
            "CANDIDATES_MANIFEST.json",
        }
        assert campaign["execution_state"] == "completed"
        assert campaign["completed_at"] is not None
        assert campaign["config"]["ideation_artifact_import"]["status"] == "imported"


def test_unbound_candidate_artifacts_are_quarantined_not_imported(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        run, _ = _setup_run(client)
        candidates = [_candidate()]
        manifest = {
            "schema_version": "flywheel.ideation-candidates/1",
            "condition_sha256": "0" * 64,
            "objective_sha256": run["objective_sha256"],
            "candidates_sha256": hashlib.sha256(canonical_bytes(candidates)).hexdigest(),
            "candidate_count": 1,
        }
        client.app.state.coordinator._client = lambda row: _ArtifactArgus(  # type: ignore[method-assign]
            candidates, manifest
        )
        asyncio.run(client.app.state.coordinator.poll_campaigns())

        detail = client.get(f"/api/ideation/runs/{run['id']}").json()
        campaign = client.get(f"/api/campaigns/{run['campaign_id']}").json()
        assert detail["state"] == "awaiting_import"
        assert detail["candidates"] == []
        assert campaign["execution_state"] == "needs_attention"
        quarantine = campaign["config"]["ideation_artifact_import"]
        assert quarantine["status"] == "quarantined"
        assert quarantine["code"] == "condition_binding_mismatch"
        assert quarantine["raw_artifact_persisted"] is False
        audit = client.get("/api/events?limit=200").json()["items"]
        assert any(
            event["event_type"] == "ideation.candidate_artifacts_quarantined"
            for event in audit
        )


def test_daemon_stop_without_terminal_evidence_never_marks_completion(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        connection = client.post(
            "/api/connections",
            json={"name": "Event Argus", "kind": "local", "base_url": "http://127.0.0.1:8799"},
        ).json()
        campaign = client.post(
            "/api/campaigns", json={"venue_key": "PPOPP", "title": "Truth projection"}
        ).json()
        client.app.state.db.execute(
            "UPDATE campaigns SET connection_id=?,argus_project_id='s-events',execution_state='running' "
            "WHERE id=?",
            (connection["id"], campaign["id"]),
        )

        class EventArgus:
            event_poll = 0
            snapshot_poll = 0

            def snapshot(self, sid: str, *, events_limit: int) -> dict:
                alive = self.snapshot_poll == 0
                self.snapshot_poll += 1
                return {
                    "daemon": {"alive": alive, "state": "working" if alive else "stopped"},
                    "mission_view": {"mission": {"status": "working", "summary": "No terminal receipt."}},
                    "continuous": {
                        "done_at": "2026-08-24T12:00:00Z",
                        "done_reason": "operator drain-stop",
                    },
                }

            def events(self, sid: str, *, limit: int) -> list[dict]:
                rows = (
                    [{"id": "first", "type": "engineer.progress"}]
                    if self.event_poll == 0
                    else [{"id": "new-tail", "type": "engineer.progress"}]
                )
                self.event_poll += 1
                return rows

            def artifacts(self, sid: str) -> list[dict]:
                return []

        fake = EventArgus()
        client.app.state.coordinator._client = lambda row: fake  # type: ignore[method-assign]
        asyncio.run(client.app.state.coordinator.poll_campaigns())
        asyncio.run(client.app.state.coordinator.poll_campaigns())
        asyncio.run(client.app.state.coordinator.poll_campaigns())

        projected = client.get(f"/api/campaigns/{campaign['id']}").json()
        assert projected["execution_state"] == "needs_attention"
        assert projected["completed_at"] is None
        assert projected["config"]["argus_event_gap_detected"] is True
        assert len(projected["config"]["argus_event_cursor"]["fingerprints"]) == 1
        gaps = [
            event
            for event in client.get("/api/events?limit=200").json()["items"]
            if event["event_type"] == "argus.event_gap_detected"
        ]
        assert len(gaps) == 1

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings


def test_seed_catalog_is_preview_only_and_cannot_start(tmp_path: Path) -> None:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "flywheel.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://127.0.0.1:5175",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as client:
        idea = client.get("/api/ideas?venue_key=PPOPP&limit=1").json()["items"][0]
        prompt = client.get(f"/api/ideas/{idea['id']}/prompt")
        assert prompt.status_code == 200
        assert prompt.json()["execution_ready"] is False
        assert "TeamProfile" in " ".join(prompt.json()["missing_before_launch"])

        campaign = client.post(
            "/api/campaigns",
            json={"venue_key": "PPOPP", "idea_id": idea["id"]},
        )
        assert campaign.status_code == 201
        start = client.post(
            f"/api/campaigns/{campaign.json()['id']}/start",
            json={
                "human_approved": True,
                "approval_reason": "This must still fail because a seed is unconditioned.",
                "actor": "pytest",
            },
        )
        assert start.status_code == 409
        assert "not executable" in start.json()["detail"]


def test_manual_campaign_without_source_binding_cannot_start(tmp_path: Path) -> None:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "flywheel.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://127.0.0.1:5175",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as client:
        campaign = client.post(
            "/api/campaigns",
            json={
                "venue_key": "PPOPP",
                "title": "Unbound manual objective",
                "objective": "A manually entered prompt with no immutable source receipt.",
            },
        )
        assert campaign.status_code == 201
        start = client.post(
            f"/api/campaigns/{campaign.json()['id']}/start",
            json={
                "human_approved": True,
                "approval_reason": "This must fail before any network or resource check.",
                "actor": "pytest",
            },
        )
        assert start.status_code == 409
        assert "no verified conditioned" in start.json()["detail"]


def test_forged_rebuttal_type_without_immutable_rows_cannot_start(tmp_path: Path) -> None:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "flywheel.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://127.0.0.1:5175",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as client:
        campaign = client.post(
            "/api/campaigns",
            json={
                "venue_key": "PPOPP",
                "title": "Forged rebuttal",
                "objective": "No submission or rebuttal receipt exists.",
                "config": {
                    "campaign_kind": "rebuttal_follow_up",
                    "source_campaign_id": "missing-source",
                    "submission_id": "missing-submission",
                    "rebuttal_objective_sha256": "0" * 64,
                },
            },
        )
        assert campaign.status_code == 201
        start = client.post(
            f"/api/campaigns/{campaign.json()['id']}/start",
            json={
                "human_approved": True,
                "approval_reason": "A type label alone must never authorize launch.",
                "actor": "pytest",
            },
        )
        assert start.status_code == 409
        assert "immutable source binding is unavailable" in start.json()["detail"]

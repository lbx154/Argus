from __future__ import annotations

import json
from pathlib import Path

import foundry.api as api_module
import pytest
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "foundry.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://localhost:5173",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as value:
        yield value


def _reviewable_campaign(client: TestClient) -> dict:
    connection = client.post(
        "/api/connections",
        json={
            "name": "Review target",
            "kind": "local",
            "base_url": "http://127.0.0.1:8799",
        },
    ).json()
    resource = client.post(
        "/api/resources",
        json={
            "name": "Review resource",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 10,
                "api_budget": "100k token hard cap",
                "max_parallel_jobs": 1,
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
        },
    ).json()
    snapshot = {
        "foundry_artifacts": [
            {"path": "paper/DRAFT.md", "exists": True, "kind": "markdown"}
        ]
    }
    client.app.state.db.execute(
        "UPDATE campaigns SET argus_project_id=?,last_snapshot_json=? WHERE id=?",
        ("argus-project-1", json.dumps(snapshot), campaign["id"]),
    )
    return campaign


class _FakeArgus:
    def artifact(self, sid: str, path: str) -> dict:
        assert sid == "argus-project-1"
        assert path == "paper/DRAFT.md"
        preview = "# Draft\n\nClaim supported by a versioned experiment ledger."
        return {
            "path": path,
            "exists": True,
            "kind": "markdown",
            "preview": preview,
            "size": len(preview.encode("utf-8")),
            "truncated": False,
        }


class _FakeProcess:
    next_pid = 9100

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        type(self).next_pid += 1
        self.pid = type(self).next_pid


def _approval() -> dict:
    return {
        "human_approved": True,
        "actor": "review-panel-operator",
        "approval_reason": "Review cost and frozen evidence scope were checked.",
    }


def test_review_requires_attributable_human_approval(client: TestClient) -> None:
    campaign = _reviewable_campaign(client)
    response = client.post(
        f"/api/campaigns/{campaign['id']}/review",
        json={"reviewer_kind": "venue_reviewer", "rubric": {}},
    )
    assert response.status_code == 422


def test_review_uses_frozen_argus_evidence_not_client_paths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _reviewable_campaign(client)
    monkeypatch.setattr(api_module, "_client", lambda request, connection: _FakeArgus())
    monkeypatch.setattr(api_module.subprocess, "Popen", _FakeProcess)
    response = client.post(
        f"/api/campaigns/{campaign['id']}/review",
        json={
            "reviewer_kind": "novelty_reviewer",
            "rubric": {
                "weights": {"novelty": 1},
                "evidence_refs": ["C:/operator-secret.txt"],
            },
            **_approval(),
        },
    )
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["evidence_snapshot_state"] == "evidence"
    assert receipt["evidence_artifact_count"] == 1
    queued = json.loads(
        (
            client.app.state.settings.data_dir
            / "viewer"
            / "inbox"
            / f"{receipt['review_id']}.json"
        ).read_text(encoding="utf-8")
    )
    assert queued["evidence_refs"] == ["argus://argus-project-1/paper/DRAFT.md"]
    assert "operator-secret" not in json.dumps(queued)
    assert queued["human_review_approval"]["actor"] == "review-panel-operator"


def test_five_reviewer_panel_preserves_independent_receipts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign = _reviewable_campaign(client)
    monkeypatch.setattr(api_module, "_client", lambda request, connection: _FakeArgus())
    monkeypatch.setattr(api_module.subprocess, "Popen", _FakeProcess)
    kinds = [
        "novelty_reviewer",
        "methods_reviewer",
        "resource_reviewer",
        "venue_reviewer",
        "integrity_reviewer",
    ]
    response = client.post(
        f"/api/campaigns/{campaign['id']}/review-panel",
        json={"reviewer_kinds": kinds, "rubrics": {}, **_approval()},
    )
    assert response.status_code == 202, response.text
    panel = response.json()
    assert panel["panel_size"] == 5
    assert panel["acceptance_probability"] is None
    assert {row["reviewer_kind"] for row in panel["reviewers"]} == set(kinds)
    assert len({row["review_id"] for row in panel["reviewers"]}) == 5
    assert len({row["evidence_snapshot_sha256"] for row in panel["reviewers"]}) == 1
    rows = client.app.state.db.fetch_all(
        "SELECT reviewer_kind,feedback_json FROM reviews WHERE campaign_id=?",
        (campaign["id"],),
    )
    assert {row["reviewer_kind"] for row in rows} == set(kinds)
    assert all(
        json.loads(row["feedback_json"])["human_review_approval"]["human_approved"]
        is True
        for row in rows
    )

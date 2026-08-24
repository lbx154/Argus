from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import foundry.db as db_module
import foundry.flywheel_api as flywheel_api_module
import pytest
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings
from foundry.db import MIGRATIONS, Database, utc_now
from foundry.services.flywheel_data import ContentObjectStore


@pytest.fixture()
def flywheel_client(tmp_path: Path) -> TestClient:
    seed_dir = Path(__file__).resolve().parents[2] / "data" / "seeds"
    settings = Settings(
        database_path=tmp_path / "flywheel.db",
        data_dir=tmp_path / "runtime",
        seed_data_dir=seed_dir,
        cors_origins=("http://localhost:5174",),
        poll_interval_seconds=0,
        auto_seed=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _confirmed_team(client: TestClient) -> str:
    draft = client.post(
        "/api/team-intakes/extract",
        json={
            "raw_text": "我们做机器学习和系统，有 2 张 H100，预算 2000万 tokens，时间 8 周。"
        },
    )
    assert draft.status_code == 201, draft.text
    extracted = draft.json()
    assert extracted["state"] == "draft"
    assert extracted["extracted"]["constraints"]["gpu_count"] == 2
    assert "H100" in extracted["extracted"]["constraints"]["gpu_models"]
    confirmed = client.post(
        f"/api/team-intakes/{extracted['id']}/confirm",
        json={
            "actor": "research-lead",
            "name": "Systems ML team",
            "profile": extracted["extracted"],
            "training_consent": True,
            "license_basis": "team-owned profile; internal research license",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["team_profile_id"]


def _unbound_episode(client: TestClient, *, consent: bool = True) -> dict:
    team_profile_id = _confirmed_team(client)
    response = client.post(
        "/api/episodes",
        json={
            "title": "Reliable agent evaluation",
            "objective": "Test a falsifiable reliability claim and preserve negative results.",
            "team_profile_id": team_profile_id,
            "training_consent": consent,
            "license_basis": "team-owned research artifacts; internal training permitted",
            "metadata": {"target": "oral-quality evidence, not a guaranteed decision"},
            "links": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _episode(client: TestClient, *, consent: bool = True) -> dict:
    team_profile_id = _confirmed_team(client)
    run_response = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": team_profile_id,
            "venue_key": "PPOPP",
            "candidate_count": 3,
            "finalist_count": 1,
            "completion_target": "One falsifiable candidate or NO_WINNER.",
            "create_campaign": True,
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    candidates = [
        {
            "candidate_key": "verified-data-loop",
            "title": "Verified data-loop candidate",
            "problem_gap": "A measured systems reliability gap",
            "core_hypothesis": "The mechanism changes a held-out reliability metric",
            "mechanism": "A bounded runtime feedback mechanism",
            "closest_work": ["doi:10.example/primary"],
            "differentiation_claim": "A testable distinction from the closest work",
            "public_or_authorized_data": ["public benchmark"],
            "method": "Controlled systems measurement",
            "strongest_baselines": ["baseline@sha"],
            "decisive_experiments": ["held-out falsifier"],
            "falsifier": "No held-out reliability change",
            "estimated_resources": {"gpu_hours": 8},
            "elapsed_time_plan": "14 days",
            "venue_fit": "PPOPP systems evidence",
            "risks": ["novelty collision"],
            "ethics_and_license": "public and permissive",
            "expected_information_gain": "High even for a null result",
            "terminal_recommendation": "revise",
            "team_specific_advantage": "The frozen team owns the measurement workflow.",
            "condition_fit_counterfactual": "Demote without the frozen team resources.",
            "novelty_collision_test": {
                "search_cutoff": "2026-08-24",
                "closest_source_ids": ["doi:10.example/primary"],
                "falsifier": "Prior work establishes the same mechanism and scope.",
            },
        }
    ]
    payload = (
        json.dumps(candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    artifact_sha256 = hashlib.sha256(payload).hexdigest()
    imported = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "human_entered",
            "artifact_sha256": artifact_sha256,
            "manifest": {
                "schema_version": "flywheel.ideation-candidates/1",
                "condition_sha256": run["condition_sha256"],
                "objective_sha256": run["objective_sha256"],
                "candidates_sha256": artifact_sha256,
                "candidate_count": 1,
            },
        },
    )
    assert imported.status_code == 201, imported.text
    candidate = imported.json()["candidates"][0]
    campaign_response = client.post(
        f"/api/ideation/candidates/{candidate['id']}/campaign",
        json={"completion_target": "Reach a falsifiable terminal state."},
    )
    assert campaign_response.status_code == 201, campaign_response.text
    campaign = campaign_response.json()
    response = client.post(
        "/api/episodes",
        json={
            "title": "Reliable agent evaluation",
            "objective": "Test a falsifiable reliability claim and preserve negative results.",
            "ideation_run_id": run["id"],
            "candidate_id": candidate["id"],
            "campaign_id": campaign["id"],
            "training_consent": consent,
            "license_basis": "team-owned research artifacts; internal training permitted",
            "metadata": {"target": "oral-quality evidence, not a guaranteed decision"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _confirmed_review(client: TestClient, episode_id: str) -> dict:
    staged = client.post(
        f"/api/episodes/{episode_id}/review-imports",
        json={
            "source_kind": "paste",
            "raw_text": "Reviewer A: score 6. Main concern: missing held-out robustness test.",
            "source_ref": "manual paste from author-owned review",
        },
    )
    assert staged.status_code == 201, staged.text
    assert staged.json()["fetch_performed"] is False
    confirmed = client.post(
        f"/api/review-imports/{staged.json()['id']}/confirm",
        json={
            "actor": "paper-owner",
            "parsed": {
                "reviews": [
                    {
                        "reviewer_alias": "reviewer-a",
                        "score": 6,
                        "concerns": ["missing held-out robustness test"],
                    }
                ]
            },
            "redaction_confirmed": True,
            "training_consent": True,
            "license_basis": "author-provided review; internal model improvement",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def test_unbound_episode_is_archivable_but_never_data_eligible(
    flywheel_client: TestClient,
) -> None:
    episode = _unbound_episode(flywheel_client)
    sealed = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "archive an unbound historical record"},
    )
    assert sealed.status_code == 201, sealed.text
    detail = flywheel_client.get(f"/api/episodes/{episode['id']}").json()
    assert detail["data_eligibility"]["eligible"] is False
    assert "source_campaign_missing" in detail["data_eligibility"][
        "ineligibility_reasons"
    ]
    preview = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    assert preview["counts"] == {"eligible": 0, "excluded": 1}
    assert "source_campaign_missing" in preview["excluded"][0]["reason"]

    spoofed = flywheel_client.post(
        "/api/episodes",
        json={
            "title": "Spoofed lineage",
            "team_profile_id": episode["team_profile_id"],
            "links": [
                {
                    "entity_type": "campaign",
                    "entity_id": "forged",
                    "relation": "execution",
                }
            ],
        },
    )
    assert spoofed.status_code == 422
    assert "system lineage relations" in spoofed.text


def test_episode_hash_chain_review_gate_and_integrity(flywheel_client: TestClient) -> None:
    episode = _episode(flywheel_client)
    staged = flywheel_client.post(
        f"/api/episodes/{episode['id']}/review-imports",
        json={"source_kind": "json", "payload": {"score": 5, "comment": "needs ablation"}},
    )
    assert staged.status_code == 201
    assert staged.json()["raw_object_sha256"] is None
    assert flywheel_client.app.state.db.fetch_all("SELECT * FROM content_objects") == []
    blocked = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "premature seal"},
    )
    assert blocked.status_code == 409
    confirmed = flywheel_client.post(
        f"/api/review-imports/{staged.json()['id']}/confirm",
        json={
            "actor": "lead",
            "redaction_confirmed": True,
            "training_consent": True,
            "license_basis": "author-supplied review; internal training",
        },
    )
    assert confirmed.status_code == 200

    first = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "submission evidence frozen", "terminal_state": "submitted"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["revision_number"] == 1
    assert len(first.json()["manifest_sha256"]) == 64
    second = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "decision state recorded", "terminal_state": "decided"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["revision_number"] == 2
    assert second.json()["parent_revision_id"] == first.json()["id"]
    assert second.json()["chain_sha256"] != first.json()["chain_sha256"]

    verification = flywheel_client.get(f"/api/episodes/{episode['id']}/verify")
    assert verification.status_code == 200
    assert verification.json()["valid"] is True
    assert verification.json()["head_revision"]["id"] == second.json()["id"]
    detail = flywheel_client.get(f"/api/episodes/{episode['id']}").json()
    assert detail["gates"]["integrity_verified"] is True
    assert detail["data_eligibility"]["eligible"] is True
    assert len(detail["revisions"]) == 2


def test_revision_and_snapshot_tables_are_database_immutable(
    flywheel_client: TestClient,
) -> None:
    episode = _episode(flywheel_client)
    sealed = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "freeze initial negative result"},
    ).json()
    db = flywheel_client.app.state.db
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE episode_revisions SET reason='rewritten' WHERE id=?", (sealed["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM episode_revisions WHERE id=?", (sealed["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="object set is sealed"):
        db.execute(
            "INSERT INTO episode_revision_objects(revision_id,object_sha256,role,created_at) "
            "VALUES(?,?,?,?)",
            (sealed["id"], "0" * 64, "late-injection", "2026-08-23T00:00:00+00:00"),
        )

    preview = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    snapshot_response = flywheel_client.post(
        "/api/dataset-snapshots",
        json={
            "name": "verified episode set v1",
            "actor": "dataset-curator",
            "license_basis": "internal research training snapshot",
            "episode_ids": [episode["id"]],
            "require_training_consent": True,
            "expected_selection_sha256": preview["selection_sha256"],
        },
    )
    assert snapshot_response.status_code == 201, snapshot_response.text
    snapshot = snapshot_response.json()
    assert snapshot["training_started"] is False
    assert flywheel_client.post(
        f"/api/dataset-snapshots/{snapshot['id']}/verify"
    ).json()["valid"] is True
    assert flywheel_client.get("/api/dataset-snapshots").json()["items"][0]["member_count"] == 1
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE dataset_snapshots SET name='rewritten' WHERE id=?", (snapshot["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM dataset_snapshot_members WHERE snapshot_id=?", (snapshot["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="member set is sealed"):
        db.execute(
            "INSERT INTO dataset_snapshot_members"
            "(snapshot_id,revision_id,episode_id,manifest_sha256,created_at) VALUES(?,?,?,?,?)",
            (
                snapshot["id"],
                "late-revision",
                episode["id"],
                "0" * 64,
                "2026-08-23T00:00:00+00:00",
            ),
        )


def test_dataset_preview_enforces_consent_and_preview_race_gate(
    flywheel_client: TestClient,
) -> None:
    no_consent = _episode(flywheel_client, consent=False)
    flywheel_client.post(
        f"/api/episodes/{no_consent['id']}/seal",
        json={"actor": "lead", "reason": "archive without training consent"},
    )
    preview = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [no_consent["id"]], "require_training_consent": True},
    )
    assert preview.status_code == 200
    assert preview.json()["counts"] == {"eligible": 0, "excluded": 1}
    assert "training_consent_missing" in preview.json()["excluded"][0]["reason"]
    create = flywheel_client.post(
        "/api/dataset-snapshots",
        json={
            "name": "must stay empty",
            "actor": "curator",
            "license_basis": "internal",
            "episode_ids": [no_consent["id"]],
            "require_training_consent": True,
            "expected_selection_sha256": "0" * 64,
        },
    )
    assert create.status_code == 409


def test_snapshot_creation_holds_write_reservation_through_selection_recheck(
    flywheel_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(flywheel_client)
    sealed = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "freeze selection member"},
    )
    assert sealed.status_code == 201, sealed.text
    preview = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    original_preview = flywheel_api_module.selection_preview

    def assert_writer_reserved(*args, **kwargs):  # type: ignore[no-untyped-def]
        competing = sqlite3.connect(
            flywheel_client.app.state.settings.database_path, timeout=0
        )
        try:
            competing.execute("PRAGMA busy_timeout=0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competing.execute("BEGIN IMMEDIATE")
        finally:
            competing.close()
        return original_preview(*args, **kwargs)

    monkeypatch.setattr(flywheel_api_module, "selection_preview", assert_writer_reserved)
    created = flywheel_client.post(
        "/api/dataset-snapshots",
        json={
            "name": "transactionally frozen selection",
            "actor": "curator",
            "license_basis": "team-owned research training",
            "episode_ids": [episode["id"]],
            "require_training_consent": True,
            "expected_selection_sha256": preview["selection_sha256"],
        },
    )
    assert created.status_code == 201, created.text


def test_frozen_snapshot_remains_valid_after_append_only_episode_activity(
    flywheel_client: TestClient,
) -> None:
    episode = _episode(flywheel_client)
    first_review = _confirmed_review(flywheel_client, episode["id"])
    first = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "freeze first review"},
    )
    assert first.status_code == 201, first.text
    preview = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    snapshot = flywheel_client.post(
        "/api/dataset-snapshots",
        json={
            "name": "frozen revision v1",
            "actor": "curator",
            "license_basis": "team-owned research training",
            "episode_ids": [episode["id"]],
            "require_training_consent": True,
            "expected_selection_sha256": preview["selection_sha256"],
        },
    ).json()
    assert flywheel_client.get(
        f"/api/dataset-snapshots/{snapshot['id']}/verify"
    ).json()["valid"] is True

    _confirmed_review(flywheel_client, episode["id"])
    assert flywheel_client.get(f"/api/episodes/{episode['id']}/verify").json()["valid"] is False
    frozen = flywheel_client.get(
        f"/api/dataset-snapshots/{snapshot['id']}/verify"
    ).json()
    assert frozen["valid"] is True
    assert all(check["valid"] for check in frozen["checks"])

    resealed = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "append second review"},
    )
    assert resealed.status_code == 201, resealed.text
    assert flywheel_client.get(
        f"/api/dataset-snapshots/{snapshot['id']}/verify"
    ).json()["valid"] is True

    row = flywheel_client.app.state.db.fetch_one(
        "SELECT storage_path FROM content_objects WHERE sha256=?",
        (first_review["raw_object_sha256"],),
    )
    assert row is not None
    object_path = (
        flywheel_client.app.state.settings.data_dir
        / "data-vault"
        / "objects"
        / row["storage_path"]
    )
    object_path.write_text("tampered frozen member", encoding="utf-8")
    assert flywheel_client.get(
        f"/api/dataset-snapshots/{snapshot['id']}/verify"
    ).json()["valid"] is False


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        (
            "/api/team-intakes/extract",
            {"raw_text": "GPU 2xH100; api_key=abcdefghijklmnopqrstuv"},
        ),
    ],
)
def test_probable_credentials_are_rejected_before_persistence(
    flywheel_client: TestClient, endpoint: str, payload: dict
) -> None:
    response = flywheel_client.post(endpoint, json=payload)
    assert response.status_code in {409, 422, 500}
    assert flywheel_client.app.state.db.fetch_all("SELECT * FROM team_intakes") == []


def test_secret_gate_rejects_provider_credentials_and_allows_token_budgets(
    flywheel_client: TestClient,
) -> None:
    draft = flywheel_client.post(
        "/api/team-intakes/extract",
        json={"raw_text": "Systems team, 2xH100, 1M tokens, four weeks."},
    ).json()
    github_token = "ghp_" + "A" * 36
    rejected = flywheel_client.post(
        f"/api/team-intakes/{draft['id']}/confirm",
        json={
            "actor": "lead",
            "profile": {
                "expertise": ["systems"],
                "methods": [],
                "data_access": [],
                "constraints": {"github_token": github_token},
                "goals": {},
                "policy": {},
            },
            "license_basis": "team owned",
        },
    )
    assert rejected.status_code == 422, rejected.text
    assert flywheel_client.app.state.db.fetch_all("SELECT * FROM team_profiles") == []

    accepted = flywheel_client.post(
        f"/api/team-intakes/{draft['id']}/confirm",
        json={
            "actor": "lead",
            "profile": {
                "expertise": ["systems"],
                "methods": [],
                "data_access": [],
                "constraints": {
                    "token_budget": "2M tokens hard cap",
                    "max_tokens": 2_000_000,
                    "api_budget": "$100 hard cap",
                },
                "goals": {},
                "policy": {},
            },
            "license_basis": "team owned",
        },
    )
    assert accepted.status_code == 200, accepted.text

    value_leak = flywheel_client.post(
        "/api/team-intakes/extract",
        json={"raw_text": f"Systems team credential {github_token}"},
    )
    assert value_leak.status_code == 422


def test_team_intake_confirmation_is_an_atomic_one_shot_cas(
    flywheel_client: TestClient,
) -> None:
    rounds = 20
    for index in range(rounds):
        draft = flywheel_client.post(
            "/api/team-intakes/extract",
            json={"raw_text": f"Systems team {index}, 2xH100, 1M tokens, four weeks."},
        ).json()
        barrier = Barrier(2)

        def confirm(actor: str):
            barrier.wait()
            return flywheel_client.post(
                f"/api/team-intakes/{draft['id']}/confirm",
                json={
                    "actor": actor,
                    "profile": draft["extracted"],
                    "license_basis": "team owned",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(confirm, ("lead-a", "lead-b")))
        assert sorted(response.status_code for response in responses) == [200, 409]

    db = flywheel_client.app.state.db
    profile_count = db.fetch_one("SELECT COUNT(*) AS count FROM team_profiles")
    confirmed_count = db.fetch_one(
        "SELECT COUNT(*) AS count FROM team_intakes WHERE state='confirmed'"
    )
    assert profile_count and profile_count["count"] == rounds
    assert confirmed_count and confirmed_count["count"] == rounds


def test_content_object_tampering_is_detected(flywheel_client: TestClient) -> None:
    episode = _episode(flywheel_client)
    review = _confirmed_review(flywheel_client, episode["id"])
    flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "review incorporated"},
    )
    row = flywheel_client.app.state.db.fetch_one(
        "SELECT storage_path FROM content_objects WHERE sha256=?",
        (review["raw_object_sha256"],),
    )
    assert row is not None
    object_path = flywheel_client.app.state.settings.data_dir / "data-vault" / "objects" / row["storage_path"]
    object_path.write_text("tampered", encoding="utf-8")
    verification = flywheel_client.get(f"/api/episodes/{episode['id']}/verify").json()
    assert verification["valid"] is False
    assert any(check["name"].startswith("object_") and not check["valid"] for check in verification["checks"])


def test_confirmed_review_after_head_requires_reseal_for_dataset(
    flywheel_client: TestClient,
) -> None:
    episode = _episode(flywheel_client)
    first = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "freeze pre-review head"},
    )
    assert first.status_code == 201, first.text
    initially = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    assert initially["counts"]["eligible"] == 1

    _confirmed_review(flywheel_client, episode["id"])
    detail = flywheel_client.get(f"/api/episodes/{episode['id']}").json()
    assert detail["gates"]["reviews_sealed_in_head"] is False
    assert detail["data_eligibility"]["eligible"] is False
    assert detail["data_eligibility"]["confirmed_reviews_sealed_in_head"] is False
    stale = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    assert stale["counts"]["eligible"] == 0
    assert "confirmed_reviews_not_sealed_in_head" in stale["excluded"][0]["reason"]

    resealed = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "seal newly confirmed review"},
    )
    assert resealed.status_code == 201, resealed.text
    current = flywheel_client.get(f"/api/episodes/{episode['id']}").json()
    assert current["gates"]["reviews_sealed_in_head"] is True
    assert current["data_eligibility"]["eligible"] is True


def test_review_draft_discard_is_audited_and_does_not_block_seal(
    flywheel_client: TestClient,
) -> None:
    episode = _episode(flywheel_client)
    staged = flywheel_client.post(
        f"/api/episodes/{episode['id']}/review-imports",
        json={"source_kind": "json", "payload": {"score": 1, "comment": "wrong upload"}},
    ).json()
    discarded = flywheel_client.post(
        f"/api/review-imports/{staged['id']}/discard",
        json={"actor": "paper-owner", "reason": "uploaded the wrong review packet"},
    )
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["state"] == "discarded"
    confirm = flywheel_client.post(
        f"/api/review-imports/{staged['id']}/confirm",
        json={
            "actor": "paper-owner",
            "redaction_confirmed": True,
            "training_consent": False,
            "license_basis": "archival only",
        },
    )
    assert confirm.status_code == 409
    sealed = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "discarded draft is not evidence"},
    )
    assert sealed.status_code == 201, sealed.text
    detail = flywheel_client.get(f"/api/episodes/{episode['id']}").json()
    audit = next(item for item in detail["review_imports"] if item["id"] == staged["id"])
    assert audit["state"] == "discarded"
    assert audit["discard_reason"] == "uploaded the wrong review packet"
    assert detail["data_eligibility"]["pending_review_count"] == 0


def test_episode_list_is_lightweight_and_does_not_verify_objects(
    flywheel_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    episode = _episode(flywheel_client)

    def must_not_verify(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("list endpoint must not perform full integrity verification")

    monkeypatch.setattr(
        "foundry.services.flywheel_data.EpisodeService.verify_episode", must_not_verify
    )
    response = flywheel_client.get("/api/episodes")
    assert response.status_code == 200, response.text
    summary = next(item for item in response.json()["items"] if item["id"] == episode["id"])
    assert summary["summary_only"] is True
    assert summary["revision_count"] == 0
    assert summary["head_integrity_valid"] is None
    assert summary["data_eligibility"]["verification_required"] is True
    assert "revisions" not in summary


def test_binary_review_object_is_not_reported_as_secret_scan_passed(
    flywheel_client: TestClient,
) -> None:
    episode = _episode(flywheel_client)
    pdf = b"%PDF-1.7\nreview bytes:\xff\xfe\n%%EOF\n"
    staged = flywheel_client.post(
        f"/api/episodes/{episode['id']}/review-imports",
        json={
            "source_kind": "pdf",
            "payload": {
                "filename": "binary-review.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(pdf).decode("ascii"),
            },
        },
    ).json()
    blocked = flywheel_client.post(
        f"/api/review-imports/{staged['id']}/confirm",
        json={
            "actor": "paper-owner",
            "redaction_confirmed": False,
            "training_consent": False,
            "license_basis": "archival only",
        },
    )
    assert blocked.status_code == 422
    assert flywheel_client.app.state.db.fetch_all("SELECT * FROM content_objects") == []
    confirmed = flywheel_client.post(
        f"/api/review-imports/{staged['id']}/confirm",
        json={
            "actor": "paper-owner",
            "redaction_confirmed": True,
            "training_consent": False,
            "license_basis": "archival only",
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    row = flywheel_client.app.state.db.fetch_one(
        "SELECT * FROM content_objects WHERE sha256=?",
        (confirmed.json()["raw_object_sha256"],),
    )
    assert row is not None
    assert row["secret_scan_state"] == "not_scannable_binary"
    assert row["redaction_scan_state"] == "not_scannable_binary"
    assert row["manual_redaction_required"] == 1


def test_legacy_binary_review_without_manual_confirmation_is_fail_closed(
    flywheel_client: TestClient,
) -> None:
    episode = _episode(flywheel_client)
    db = flywheel_client.app.state.db
    objects = ContentObjectStore(
        db, flywheel_client.app.state.settings.data_dir / "data-vault" / "objects"
    )
    obj = objects.put_bytes(
        b"%PDF-1.7\nlegacy unscannable review:\xff\xfe\n%%EOF\n",
        media_type="application/pdf",
        metadata={"source_kind": "pdf", "stage": "legacy_import"},
    )
    now = utc_now()
    db.execute(
        "INSERT INTO review_import_batches("
        "id,episode_id,source_kind,source_ref,state,raw_payload_json,raw_object_sha256,"
        "parsed_json,redaction_confirmed,training_consent,license_basis,confirmed_by,"
        "confirmed_at,created_at,updated_at) "
        "VALUES('legacy-unreviewed-pdf',?, 'pdf','legacy','confirmed','{}',?,'{}',0,1,"
        "'team owned','legacy-import',?,?,?)",
        (episode["id"], obj.sha256, now, now, now),
    )

    blocked_seal = flywheel_client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "lead", "reason": "must not seal unreviewed binary"},
    )
    assert blocked_seal.status_code == 409
    assert "manual binary review" in blocked_seal.json()["detail"]
    preview = flywheel_client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    ).json()
    assert preview["counts"]["eligible"] == 0
    assert "review_redaction_or_manual_binary_confirmation_missing" in preview["excluded"][0][
        "reason"
    ]


def test_v6_migration_upgrades_a_v5_database(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-v5.db"
    connection = sqlite3.connect(database_path)
    try:
        for version, sql in MIGRATIONS:
            if version > 5:
                break
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                (version, utc_now()),
            )
        now = utc_now()
        digest = "a" * 64
        connection.execute(
            "INSERT INTO content_objects"
            "(sha256,media_type,byte_length,storage_path,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (digest, "application/pdf", 12, "aa/aa/" + digest, "{}", now),
        )
        connection.execute(
            "INSERT INTO research_episodes"
            "(id,title,objective,state,training_consent,license_basis,metadata_json,created_at,updated_at) "
            "VALUES('legacy-episode','Legacy','Preserve data','active',1,'internal','{}',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO episode_revisions"
            "(id,episode_id,revision_number,manifest_json,manifest_sha256,chain_sha256,"
            "object_count,reason,sealed_by,sealed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-revision",
                "legacy-episode",
                1,
                "{}",
                "b" * 64,
                "c" * 64,
                1,
                "legacy",
                "owner",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO episode_revision_objects"
            "(revision_id,object_sha256,role,created_at) VALUES(?,?,?,?)",
            ("legacy-revision", digest, "review_import:pdf", now),
        )
        connection.execute(
            "UPDATE research_episodes SET head_revision_id='legacy-revision' "
            "WHERE id='legacy-episode'"
        )
        connection.execute(
            "INSERT INTO review_import_batches"
            "(id,episode_id,source_kind,state,raw_payload_json,raw_object_sha256,"
            "parsed_json,redaction_confirmed,created_at,updated_at) "
            "VALUES('legacy-review','legacy-episode','pdf','confirmed','{}',?,'{}',1,?,?)",
            (digest, now, now),
        )
        connection.commit()
    finally:
        connection.close()

    Database(database_path).migrate()
    with sqlite3.connect(database_path) as migrated:
        versions = {row[0] for row in migrated.execute("SELECT version FROM schema_migrations")}
        assert 6 in versions
        assert 8 in versions
        run_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(ideation_runs)")
        }
        candidate_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(generated_idea_candidates)")
        }
        review_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(review_import_batches)")
        }
        content_columns = {
            row[1] for row in migrated.execute("PRAGMA table_info(content_objects)")
        }
        foreign_key_violations = list(migrated.execute("PRAGMA foreign_key_check"))
        migrated_object = migrated.execute(
            "SELECT secret_scan_state,redaction_scan_state,manual_redaction_required "
            "FROM content_objects WHERE sha256=?",
            ("a" * 64,),
        ).fetchone()
        migrated_ref = migrated.execute(
            "SELECT object_sha256 FROM episode_revision_objects "
            "WHERE revision_id='legacy-revision'"
        ).fetchone()
    assert "candidate_artifact_sha256" in run_columns
    assert "condition_sha256" in run_columns
    assert "candidate_manifest_json" in run_columns
    assert "artifact_sha256" in candidate_columns
    assert {"discarded_by", "discarded_at", "discard_reason"} <= review_columns
    assert {"redaction_scan_state", "manual_redaction_required"} <= content_columns
    assert foreign_key_violations == []
    assert migrated_object == ("not_scannable_binary", "not_scannable_binary", 1)
    assert migrated_ref == ("a" * 64,)


def test_v8_backfills_legacy_condition_digest_before_freezing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "condition-backfill.db"
    all_migrations = MIGRATIONS
    monkeypatch.setattr(db_module, "MIGRATIONS", all_migrations[:7])
    database = Database(database_path)
    database.migrate()
    now = utc_now()
    snapshot = {"z": 1, "a": "团队条件"}
    database.execute(
        "INSERT INTO venues(id,venue_key,display_name,created_at,updated_at) "
        "VALUES(1,'LEGACY','Legacy venue',?,?)",
        (now, now),
    )
    database.execute(
        "INSERT INTO team_profiles(id,name,created_at,updated_at) "
        "VALUES('legacy-team','Legacy team',?,?)",
        (now, now),
    )
    database.execute(
        "INSERT INTO ideation_runs(id,team_profile_id,venue_id,state,condition_schema_version,"
        "condition_snapshot_json,objective_sha256,objective_path,created_at,updated_at) "
        "VALUES('legacy-run','legacy-team',1,'objective_ready',2,?,?,?, ?,?)",
        (json.dumps(snapshot, ensure_ascii=False), "a" * 64, "legacy-objective.md", now, now),
    )

    monkeypatch.setattr(db_module, "MIGRATIONS", all_migrations)
    database.migrate()
    expected = hashlib.sha256(
        (json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    persisted = database.fetch_one(
        "SELECT condition_sha256,candidate_manifest_json FROM ideation_runs WHERE id='legacy-run'"
    )
    assert persisted == {"condition_sha256": expected, "candidate_manifest_json": "{}"}
    with pytest.raises(sqlite3.IntegrityError, match="condition digest is immutable"):
        database.execute(
            "UPDATE ideation_runs SET condition_sha256=? WHERE id='legacy-run'", ("f" * 64,)
        )


def test_migration_version_is_atomic_retryable_and_restores_foreign_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "atomic-migration.db"
    database = Database(database_path)
    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        (
            (
                1,
                """
                CREATE TABLE partial_table(id INTEGER PRIMARY KEY);
                INSERT INTO table_that_does_not_exist(id) VALUES(1);
                """,
            ),
        ),
    )
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        database.migrate()

    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='partial_table'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 0

    monkeypatch.setattr(
        db_module,
        "MIGRATIONS",
        ((1, "CREATE TABLE complete_table(id INTEGER PRIMARY KEY);"),),
    )
    database.migrate()
    database.migrate()
    with database.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='complete_table'"
        ).fetchone() is not None
        assert [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        ] == [1]

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from foundry.app import create_app
from foundry.config import Settings

START_APPROVAL = {
    "human_approved": True,
    "approval_reason": "Operator verified the conditioned candidate and immutable launch packet.",
    "actor": "workflow-integrity-test",
}
BASE_PREFLIGHT_ATTESTATIONS = {
    "compute_inventory_and_capacity_verified": True,
    "data_access_and_license_reviewed": True,
    "non_compute_prerequisites_reviewed": True,
}
PPOPP_WALL_CLOCK = "2027-07-01T18:00:00+08:00"


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


def _profile(client: TestClient, name: str, expertise: list[str]) -> dict:
    response = client.post(
        "/api/team-profiles",
        json={
            "name": name,
            "expertise": expertise,
            "methods": ["causal inference", "systems measurement"],
            "data_access": ["public datasets only"],
            "constraints": {"team_size": 2, "person_months": 3, "private_data": False},
            "goals": {"contribution": "mechanistic method", "artifact": "reproducible code"},
            "policy": {"human_subjects": "not authorized"},
            "training_consent": True,
            "license_basis": "Team-owned labels released for research training",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _run(client: TestClient, profile_id: str) -> dict:
    response = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile_id,
            "venue_key": "PPOPP",
            "candidate_count": 3,
            "finalist_count": 2,
            "completion_target": "Two resource-feasible survivors or a documented NO_WINNER.",
            "create_campaign": True,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _candidate(key: str, title: str) -> dict:
    return {
        "candidate_key": key,
        "title": title,
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
        "team_specific_advantage": "The frozen team combines systems measurement and causal inference.",
        "condition_fit_counterfactual": "Demote if the team lacks its authorized measurement testbed.",
        "novelty_collision_test": {
            "search_cutoff": "2026-08-24",
            "closest_source_ids": ["doi:10.example/primary"],
            "falsifier": "A prior work establishes the same mechanism and claim scope.",
        },
    }


def _dimensions(offset: float = 0) -> dict[str, float]:
    return {
        "novelty_evidence": 7 + offset,
        "falsifiability": 8,
        "resource_fit": 9,
        "venue_fit": 7,
        "methodological_soundness": 8,
        "integrity_risk": 3,
        "expected_information_gain": 8,
    }


def _artifact_sha256(candidates: list[dict]) -> str:
    payload = (
        json.dumps(candidates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_manifest(run: dict, candidates: list[dict]) -> dict:
    return {
        "schema_version": "flywheel.ideation-candidates/1",
        "condition_sha256": run["condition_sha256"],
        "objective_sha256": run["objective_sha256"],
        "candidates_sha256": _artifact_sha256(candidates),
        "candidate_count": len(candidates),
    }


def _import_candidates(client: TestClient, run: dict, candidates: list[dict]) -> dict:
    digest = _artifact_sha256(candidates)
    response = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "human_entered",
            "artifact_sha256": digest,
            "manifest": _candidate_manifest(run, candidates),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _launch_ready_candidate(client: TestClient) -> tuple[dict, dict]:
    suffix = uuid.uuid4().hex[:8]
    connection = client.post(
        "/api/connections",
        json={
            "name": f"Conditioned Argus {suffix}",
            "kind": "local",
            "base_url": f"http://127.0.0.1:{18000 + int(suffix[:3], 16) % 1000}",
        },
    ).json()
    row = client.app.state.db.fetch_one(
        "SELECT metadata_json FROM connections WHERE id=?", (connection["id"],)
    )
    metadata = json.loads(row["metadata_json"])
    metadata["launch_compatible"] = True
    client.app.state.db.execute(
        "UPDATE connections SET status='online',metadata_json=? WHERE id=?",
        (json.dumps(metadata), connection["id"]),
    )
    resource = client.post(
        "/api/resources",
        json={
            "name": f"Conditioned GPU {suffix}",
            "resource_type": "gpu_pool",
            "capacity": {
                "configured": True,
                "gpu_count": 1,
                "gpu_model": "Mock GPU",
                "gpu_hours": 48,
                "api_budget": "1M tokens hard cap",
                "max_parallel_jobs": 1,
                "wall_clock_deadline": PPOPP_WALL_CLOCK,
            },
        },
    ).json()
    profile = _profile(client, f"Launch-ready team {suffix}", ["systems measurement"])
    run_response = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile["id"],
            "venue_key": "PPOPP",
            "resource_id": resource["id"],
            "connection_id": connection["id"],
            "candidate_count": 3,
            "finalist_count": 2,
            "completion_target": "Produce a falsifiable portfolio or NO_WINNER.",
            "preflight_attestations": BASE_PREFLIGHT_ATTESTATIONS,
            "create_campaign": True,
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    imported = _import_candidates(
        client,
        run,
        [_candidate("launch", "Launch-bound candidate")],
    )
    return run, imported["candidates"][0]


def _candidate_campaign(
    client: TestClient, candidate_id: str, variant: str
) -> dict:
    response = client.post(
        f"/api/ideation/candidates/{candidate_id}/campaign",
        json={
            "completion_target": (
                "Verify or falsify the selected mechanism under the frozen conditions: "
                + variant
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_human_conditioned_template_tracks_compiler_schema_v2() -> None:
    template = (
        Path(__file__).resolve().parents[2]
        / "prompts"
        / "CONDITIONED_IDEATION_TEMPLATE.md"
    ).read_text(encoding="utf-8")
    assert '"schema_version": 3' in template
    assert '"source_context"' in template
    assert '"preflight_attestations"' in template
    assert "source_context.content_sha256" in template
    assert "DEBATER_A_BUILDER" in template
    assert "DEBATER_B_BREAKER" in template
    assert "ARBITER" in template
    assert "CANDIDATES_MANIFEST.json" in template
    assert '"team_specific_advantage"' in template
    assert '"condition_fit_counterfactual"' in template
    assert '"search_cutoff"' in template
    assert '"closest_source_ids"' in template
    assert '"falsifier"' in template


def test_team_conditions_change_the_frozen_prompt_and_create_idle_campaign(
    client: TestClient,
) -> None:
    profile_a = _profile(client, "Tiny systems team", ["compiler runtime"])
    profile_b = _profile(client, "Theory group", ["distributed lower bounds"])
    run_a = _run(client, profile_a["id"])
    run_b = _run(client, profile_b["id"])

    assert run_a["objective_sha256"] != run_b["objective_sha256"]
    assert run_a["condition_snapshot"]["team"]["expertise"] == ["compiler runtime"]
    assert run_b["condition_snapshot"]["team"]["expertise"] == [
        "distributed lower bounds"
    ]
    assert "Two-sided debate protocol" in run_a["objective"]
    assert "NO_WINNER" in run_a["objective"]
    campaign = client.get(f"/api/campaigns/{run_a['campaign_id']}").json()
    assert campaign["execution_state"] == "idle"
    assert campaign["argus_project_id"] is None
    assert campaign["config"]["campaign_kind"] == "conditioned_ideation"
    assert Path(run_a["objective_path"]).read_bytes().decode("utf-8") == run_a["objective"]


def test_conditioned_ideation_start_reauthenticates_frozen_objective(
    client: TestClient,
) -> None:
    profile = _profile(client, "Start-bound ideation team", ["runtime verification"])
    run = _run(client, profile["id"])
    campaign_id = run["campaign_id"]

    # The intact campaign passes provenance admission and reaches the next,
    # independent connection gate.
    intact = client.post(
        f"/api/campaigns/{campaign_id}/start", json=START_APPROVAL
    )
    assert intact.status_code == 409
    assert "Select an Argus connection" in intact.json()["detail"]

    blocked_patch = client.patch(
        f"/api/campaigns/{campaign_id}", json={"objective": "patch tamper"}
    )
    assert blocked_patch.status_code == 409
    assert "provenance is immutable" in blocked_patch.json()["detail"]

    client.app.state.db.execute(
        "UPDATE campaigns SET objective='tampered prompt' WHERE id=?", (campaign_id,)
    )
    rejected = client.post(
        f"/api/campaigns/{campaign_id}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "Conditioned ideation integrity check failed" in rejected.json()["detail"]


def test_conditioned_ideation_start_reauthenticates_condition_hash_and_path(
    client: TestClient, tmp_path: Path
) -> None:
    profile = _profile(client, "Path-bound ideation team", ["artifact integrity"])
    run = _run(client, profile["id"])
    campaign_id = run["campaign_id"]
    db = client.app.state.db
    campaign = client.get(f"/api/campaigns/{campaign_id}").json()
    forged = {**campaign["config"], "condition_sha256": "0" * 64}
    db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(forged), campaign_id),
    )
    rejected_hash = client.post(
        f"/api/campaigns/{campaign_id}/start", json=START_APPROVAL
    )
    assert rejected_hash.status_code == 409
    assert "config_condition_sha256" in rejected_hash.json()["detail"]

    db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(campaign["config"]), campaign_id),
    )
    outside = tmp_path / "outside-objective.md"
    outside.write_text(run["objective"], encoding="utf-8")
    # Simulate a lower-level store compromise that bypassed the normal
    # immutable-row trigger; Start must still reject the escaped path.
    db.execute("DROP TRIGGER immutable_ideation_run_condition")
    db.execute(
        "UPDATE ideation_runs SET objective_path=? WHERE id=?", (str(outside), run["id"])
    )
    rejected_path = client.post(
        f"/api/campaigns/{campaign_id}/start", json=START_APPROVAL
    )
    assert rejected_path.status_code == 409
    assert "immutable objective files are unavailable" in rejected_path.json()["detail"]


def test_conditioned_ideation_launch_manifest_names_its_provenance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _candidate_record = _launch_ready_candidate(client)

    class FakeArgus:
        def create_daemon(self, **kwargs: object) -> dict[str, object]:
            return {
                "sid": "ideation-project",
                "rc": 0,
                "spawned": True,
                "command_status": "applied",
            }

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeArgus())
    started = client.post(
        f"/api/campaigns/{run['campaign_id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 200, started.text
    manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / run["campaign_id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["prompt_manifest"]["compiler"] == "conditioned-ideation"
    assert manifest["prompt_manifest"]["launch_provenance"][
        "campaign_kind"
    ] == "conditioned_ideation"


def test_confirmed_one_sentence_condition_is_preserved_and_immutable(
    client: TestClient,
) -> None:
    raw = "我们两个人，有 1 张 RTX 4090、两百万 token，只用公开数据，六周内完成系统测量研究。"
    extracted = client.post("/api/team-intakes/extract", json={"raw_text": raw})
    assert extracted.status_code == 201, extracted.text
    intake = extracted.json()
    profile = {
        **intake["extracted"],
        "expertise": ["systems measurement"],
        "methods": ["controlled experiments"],
        "data_access": ["public data only"],
        "goals": {"contribution": "mechanistic systems result"},
    }
    confirmed = client.post(
        f"/api/team-intakes/{intake['id']}/confirm",
        json={"actor": "operator", "name": "One sentence team", "profile": profile},
    )
    assert confirmed.status_code == 200, confirmed.text
    run = _run(client, confirmed.json()["team_profile_id"])
    origin = run["condition_snapshot"]["team"]["origin"]

    assert origin["operator_statement"] == raw
    assert origin["operator_statement_bound"] is True
    assert origin["operator_statement_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert origin["kind"] == "confirmed_operator_intake"
    assert run["condition_snapshot"]["team"]["data_access"] == ["public data only"]

    db = client.app.state.db
    with pytest.raises(sqlite3.IntegrityError, match="confirmed team intake is immutable"):
        db.execute("UPDATE team_intakes SET raw_text='rewritten' WHERE id=?", (intake["id"],))


def test_frozen_condition_digest_and_snapshot_are_database_immutable(
    client: TestClient,
) -> None:
    profile = _profile(client, "Frozen condition team", ["verification"])
    run = _run(client, profile["id"])
    db = client.app.state.db

    persisted = db.fetch_one(
        "SELECT condition_sha256 FROM ideation_runs WHERE id=?", (run["id"],)
    )
    assert persisted == {"condition_sha256": run["condition_sha256"]}
    with pytest.raises(sqlite3.IntegrityError, match="frozen condition is immutable"):
        db.execute(
            "UPDATE ideation_runs SET condition_snapshot_json='{}' WHERE id=?",
            (run["id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="condition digest is immutable"):
        db.execute(
            "UPDATE ideation_runs SET condition_sha256=? WHERE id=?",
            ("f" * 64, run["id"]),
        )


def test_training_consent_requires_license_basis(client: TestClient) -> None:
    response = client.post(
        "/api/team-profiles",
        json={"name": "Unlicensed labels", "training_consent": True},
    )
    assert response.status_code == 422
    assert "license" in response.text.lower()


def test_source_snapshot_is_pair_bound_hashed_and_changes_the_condition(
    client: TestClient,
) -> None:
    profile = _profile(client, "Freshness team", ["measurement"])
    ref = "artifact://reviewed-source-packet/private-locator"
    ref_only = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile["id"],
            "venue_key": "PPOPP",
            "source_snapshot_ref": ref,
            "create_campaign": False,
        },
    )
    assert ref_only.status_code == 422
    short_digest = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile["id"],
            "venue_key": "PPOPP",
            "source_snapshot_ref": ref,
            "source_snapshot_sha256": "a" * 40,
            "create_campaign": False,
        },
    )
    assert short_digest.status_code == 422

    first = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile["id"],
            "venue_key": "PPOPP",
            "source_snapshot_ref": ref,
            "source_snapshot_sha256": "a" * 64,
            "create_campaign": False,
        },
    )
    assert first.status_code == 201, first.text
    run_a = first.json()
    source_context = run_a["condition_snapshot"]["source_context"]
    assert source_context["content_sha256"] == "a" * 64
    assert source_context["operator_snapshot_bound"] is True
    assert source_context["fresh_discovery_required"] is False
    assert source_context["reference_sha256"]
    assert ref not in json.dumps(run_a["condition_snapshot"])
    assert ref in run_a["objective"]

    second = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile["id"],
            "venue_key": "PPOPP",
            "source_snapshot_ref": ref,
            "source_snapshot_sha256": "b" * 64,
            "create_campaign": False,
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["condition_sha256"] != run_a["condition_sha256"]

    duplicate = client.post(
        "/api/ideation/runs",
        json={
            "team_profile_id": profile["id"],
            "venue_key": "PPOPP",
            "source_snapshot_ref": ref,
            "source_snapshot_sha256": "a" * 64,
            "create_campaign": False,
        },
    )
    assert duplicate.status_code == 409


def test_run_detail_reads_and_verifies_frozen_objective(client: TestClient) -> None:
    profile = _profile(client, "Objective integrity team", ["measurement"])
    run = _run(client, profile["id"])

    detail = client.get(f"/api/ideation/runs/{run['id']}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["objective"] == run["objective"]
    condition_bytes = (
        json.dumps(
            payload["condition_snapshot"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    assert payload["condition_sha256"] == hashlib.sha256(condition_bytes).hexdigest()

    objective_path = Path(payload["objective_path"])
    objective_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    objective_path.write_text("tampered objective", encoding="utf-8")
    corrupted = client.get(f"/api/ideation/runs/{run['id']}")
    assert corrupted.status_code == 409
    assert "integrity check failed" in corrupted.text.lower()


def test_candidate_artifact_binding_episode_selection_and_db_immutability(
    client: TestClient,
) -> None:
    profile = _profile(client, "Candidate provenance team", ["systems"])
    run = _run(client, profile["id"])
    candidates = [_candidate("A", "Candidate A"), _candidate("B", "Candidate B")]
    digest = _artifact_sha256(candidates)

    mismatch = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "human_entered",
            "artifact_sha256": "0" * 64,
            "manifest": _candidate_manifest(run, candidates),
        },
    )
    assert mismatch.status_code == 409
    assert client.app.state.db.fetch_all(
        "SELECT * FROM generated_idea_candidates WHERE ideation_run_id=?", (run["id"],)
    ) == []

    accepted = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "human_entered",
            "artifact_sha256": digest,
            "manifest": _candidate_manifest(run, candidates),
        },
    )
    assert accepted.status_code == 201, accepted.text
    selected = accepted.json()["candidates"][0]
    assert accepted.json()["candidate_artifact_sha256"] == digest
    assert selected["artifact_sha256"] == digest

    db = client.app.state.db
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE generated_idea_candidates SET artifact_sha256=? WHERE id=?",
            ("f" * 64, selected["id"]),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE generated_idea_candidates SET title='rewritten' WHERE id=?",
            (selected["id"],),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM generated_idea_candidates WHERE id=?", (selected["id"],))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE ideation_runs SET candidate_artifact_sha256=? WHERE id=?",
            ("f" * 64, run["id"]),
        )

    missing_selection = client.post(
        "/api/episodes",
        json={"title": "Missing selection", "ideation_run_id": run["id"]},
    )
    assert missing_selection.status_code == 422
    missing_execution = client.post(
        "/api/episodes",
        json={
            "title": "Missing candidate execution",
            "ideation_run_id": run["id"],
            "candidate_id": selected["id"],
        },
    )
    assert missing_execution.status_code == 422
    assert "conditioned candidate execution campaign" in missing_execution.text
    execution_campaign = _candidate_campaign(
        client, selected["id"], "episode-provenance-binding"
    )
    episode_response = client.post(
        "/api/episodes",
        json={
            "title": "Selected candidate episode",
            "objective": "Falsify the selected candidate under the frozen conditions.",
            "ideation_run_id": run["id"],
            "candidate_id": selected["id"],
            "campaign_id": execution_campaign["id"],
            "training_consent": True,
            "license_basis": "Team-owned research trajectory released for training",
        },
    )
    assert episode_response.status_code == 201, episode_response.text
    episode = episode_response.json()
    assert episode["team_profile_id"] == run["team_profile_id"]
    assert episode["venue_id"] == run["venue_id"]
    assert episode["campaign_id"] == execution_campaign["id"]
    assert episode["campaign_id"] != run["campaign_id"]
    frozen = episode["metadata"]["selected_candidate"]
    assert frozen["candidate_id"] == selected["id"]
    assert frozen["artifact_sha256"] == digest
    assert frozen["candidate_snapshot"] == candidates[0]
    receipt = frozen["binding_receipt"]
    assert frozen["condition_sha256"] == run["condition_sha256"]
    assert frozen["candidate_artifact_sha256"] == digest
    assert frozen["candidate_record_sha256"] == receipt["candidate_record_sha256"]
    assert frozen["candidate_input_sha256"] == receipt["candidate_input_sha256"]
    assert frozen["candidate_prompt_sha256"] == receipt["candidate_prompt_sha256"]
    assert receipt["campaign_id"] == execution_campaign["id"]
    assert any(
        link["entity_id"] == selected["id"]
        and link["relation"] == "selected_candidate"
        for link in episode["links"]
    )
    execution_link = next(
        link
        for link in episode["links"]
        if link["entity_id"] == execution_campaign["id"]
        and link["relation"] == "execution"
    )
    assert execution_link["metadata"]["binding_receipt"] == receipt
    assert any(
        link["entity_id"] == run["campaign_id"]
        and link["relation"] == "ideation_source"
        for link in episode["links"]
    )

    sealed = client.post(
        f"/api/episodes/{episode['id']}/seal",
        json={"actor": "provenance-test", "reason": "freeze candidate lineage"},
    )
    assert sealed.status_code == 201, sealed.text
    sealed_manifest = sealed.json()["manifest"]
    assert sealed_manifest["episode"]["campaign_id"] == execution_campaign["id"]
    assert sealed_manifest["episode"]["metadata"]["selected_candidate"][
        "binding_receipt"
    ] == receipt
    assert any(
        link["entity_id"] == execution_campaign["id"]
        and link["relation"] == "execution"
        and link["metadata"]["binding_receipt"]["receipt_sha256"]
        == receipt["receipt_sha256"]
        for link in sealed_manifest["entity_links"]
    )
    preview = client.post(
        "/api/dataset-snapshots/preview",
        json={"episode_ids": [episode["id"]], "require_training_consent": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible"][0]["episode_id"] == episode["id"]
    snapshot = client.post(
        "/api/dataset-snapshots",
        json={
            "name": "candidate lineage export",
            "actor": "provenance-test",
            "license_basis": "Team-owned research trajectory released for training",
            "episode_ids": [episode["id"]],
            "require_training_consent": True,
            "expected_selection_sha256": preview.json()["selection_sha256"],
        },
    )
    assert snapshot.status_code == 201, snapshot.text
    assert snapshot.json()["manifest"]["members"][0]["episode_id"] == episode["id"]
    assert (
        client.get(f"/api/dataset-snapshots/{snapshot.json()['id']}/verify").json()[
            "valid"
        ]
        is True
    )

    conflicting = client.post(
        "/api/episodes",
        json={
            "title": "Conflicting binding",
            "ideation_run_id": run["id"],
            "candidate_id": selected["id"],
            "venue_id": 999999,
        },
    )
    assert conflicting.status_code == 409


def test_episode_candidate_execution_binding_rejects_crossed_lineage(
    client: TestClient,
) -> None:
    profile_a = _profile(client, "Episode binding team A", ["systems"])
    profile_b = _profile(client, "Episode binding team B", ["theory"])
    run_a = _run(client, profile_a["id"])
    run_b = _run(client, profile_b["id"])
    imported_a = _import_candidates(
        client,
        run_a,
        [_candidate("A1", "Run A selected"), _candidate("A2", "Run A alternate")],
    )
    imported_b = _import_candidates(
        client,
        run_b,
        [_candidate("B1", "Run B selected")],
    )
    selected_a = imported_a["candidates"][0]
    alternate_a = imported_a["candidates"][1]
    selected_b = imported_b["candidates"][0]
    execution_a = _candidate_campaign(client, selected_a["id"], "episode-cross-check")

    ideation_as_execution = client.post(
        "/api/episodes",
        json={
            "title": "Ideation is not execution",
            "ideation_run_id": run_a["id"],
            "candidate_id": selected_a["id"],
            "campaign_id": run_a["campaign_id"],
        },
    )
    assert ideation_as_execution.status_code == 409
    assert "ideation campaign cannot be recorded as execution" in ideation_as_execution.text

    wrong_candidate = client.post(
        "/api/episodes",
        json={
            "title": "Wrong candidate",
            "ideation_run_id": run_a["id"],
            "candidate_id": alternate_a["id"],
            "campaign_id": execution_a["id"],
        },
    )
    assert wrong_candidate.status_code == 409
    assert "selected candidate" in wrong_candidate.text

    wrong_run = client.post(
        "/api/episodes",
        json={
            "title": "Wrong run",
            "ideation_run_id": run_b["id"],
            "candidate_id": selected_b["id"],
            "campaign_id": execution_a["id"],
        },
    )
    assert wrong_run.status_code == 409
    assert "selected ideation run" in wrong_run.text

    persisted = client.app.state.db.fetch_all(
        "SELECT id FROM research_episodes WHERE title IN (?,?,?)",
        ("Ideation is not execution", "Wrong candidate", "Wrong run"),
    )
    assert persisted == []


def test_candidate_manifest_rejects_cross_run_binding(client: TestClient) -> None:
    profile_a = _profile(client, "Manifest team A", ["measurement"])
    profile_b = _profile(client, "Manifest team B", ["formal verification"])
    run_a = _run(client, profile_a["id"])
    run_b = _run(client, profile_b["id"])
    candidates = [_candidate("A", "Condition-bound candidate")]
    manifest = _candidate_manifest(run_a, candidates)
    digest = _artifact_sha256(candidates)

    crossed = client.post(
        f"/api/ideation/runs/{run_b['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "human_entered",
            "artifact_sha256": digest,
            "manifest": manifest,
        },
    )
    assert crossed.status_code == 409
    assert "condition_binding_mismatch" in crossed.text
    assert client.app.state.db.fetch_all(
        "SELECT * FROM generated_idea_candidates WHERE ideation_run_id=?", (run_b["id"],)
    ) == []


def test_each_conditioned_direction_compiles_a_distinct_idle_campaign(
    client: TestClient,
) -> None:
    profile_a = _profile(client, "Measurement lab", ["runtime measurement"])
    profile_b = _profile(client, "Theory lab", ["distributed lower bounds"])
    run_a = _run(client, profile_a["id"])
    run_b = _run(client, profile_b["id"])
    direction_a1 = _candidate("A1", "Mechanism route")
    direction_a2 = {
        **_candidate("A2", "Counterfactual route"),
        "core_hypothesis": "A different falsifiable mechanism",
        "method": "Counterfactual scheduler analysis",
    }
    direction_b = {
        **_candidate("B1", "Mechanism route"),
        "team_specific_advantage": "The frozen team can prove distributed lower bounds.",
        "condition_fit_counterfactual": "Demote for a team without proof expertise.",
    }
    imported_a = _import_candidates(client, run_a, [direction_a1, direction_a2])
    imported_b = _import_candidates(client, run_b, [direction_b])

    campaign_a1 = client.post(
        f"/api/ideation/candidates/{imported_a['candidates'][0]['id']}/campaign",
        json={"completion_target": "Establish or falsify the mechanism under the frozen budget."},
    )
    campaign_a2 = client.post(
        f"/api/ideation/candidates/{imported_a['candidates'][1]['id']}/campaign",
        json={"completion_target": "Establish or falsify the mechanism under the frozen budget."},
    )
    campaign_b = client.post(
        f"/api/ideation/candidates/{imported_b['candidates'][0]['id']}/campaign",
        json={"completion_target": "Establish or falsify the mechanism under the frozen budget."},
    )
    for response in (campaign_a1, campaign_a2, campaign_b):
        assert response.status_code == 201, response.text
        payload = response.json()
        assert payload["execution_state"] == "idle"
        assert payload["argus_project_id"] is None
        assert payload["launch_triggered"] is False
        assert payload["config"]["campaign_kind"] == "conditioned_candidate_research"
        assert payload["config"]["condition_snapshot_bound"] is True
        assert payload["config"]["seed_catalog_source"] is False
        assert payload["config"]["candidate_prompt_sha256"] == payload[
            "candidate_prompt_sha256"
        ]
    prompt_shas = {
        campaign_a1.json()["candidate_prompt_sha256"],
        campaign_a2.json()["candidate_prompt_sha256"],
        campaign_b.json()["candidate_prompt_sha256"],
    }
    assert len(prompt_shas) == 3
    assert run_a["condition_sha256"] != run_b["condition_sha256"]

    repeated = client.post(
        f"/api/ideation/candidates/{imported_a['candidates'][0]['id']}/campaign",
        json={"completion_target": "Establish or falsify the mechanism under the frozen budget."},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == campaign_a1.json()["id"]
    assert repeated.json()["idempotent"] is True

    seed_attempt = client.post("/api/ideation/candidates/1/campaign", json={})
    assert seed_attempt.status_code == 404


def test_normal_conditioned_candidate_passes_integrity_gate_and_starts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, candidate = _launch_ready_candidate(client)
    campaign = _candidate_campaign(client, candidate["id"], "normal-start")

    class FakeClient:
        def create_daemon(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["objective"] == campaign["objective"]
            return {
                "sid": "conditioned-project",
                "rc": 0,
                "spawned": True,
                "command_status": "applied",
            }

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeClient())
    started = client.post(
        f"/api/campaigns/{campaign['id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 200, started.text
    assert started.json()["execution_state"] == "running"
    receipt = client.app.state.db.fetch_one(
        "SELECT * FROM conditioned_campaign_bindings WHERE campaign_id=?",
        (campaign["id"],),
    )
    assert receipt is not None
    manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / campaign["id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["prompt_sha256"] == receipt["candidate_prompt_sha256"]
    assert (
        manifest["prompt_manifest"]["launch_provenance"]["receipt_sha256"]
        == receipt["receipt_sha256"]
    )
    assert manifest["prompt_manifest"]["compiler"] == "conditioned-candidate"


def test_conditioned_candidate_patch_and_start_fail_closed_on_tampering(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, candidate = _launch_ready_candidate(client)

    patch_target = _candidate_campaign(client, candidate["id"], "patch-protection")
    for payload in (
        {"objective": "tampered"},
        {"config": {}},
        {"connection_id": None},
        {"resource_id": None},
    ):
        rejected = client.patch(f"/api/campaigns/{patch_target['id']}", json=payload)
        assert rejected.status_code == 409, (payload, rejected.text)
        assert "provenance is immutable" in rejected.text
    allowed_title = client.patch(
        f"/api/campaigns/{patch_target['id']}",
        json={"title": "Display title may change"},
    )
    assert allowed_title.status_code == 200

    class ForbiddenClient:
        def create_daemon(self, **kwargs: object) -> dict[str, object]:
            raise AssertionError("integrity failures must happen before Argus dispatch")

    monkeypatch.setattr("foundry.api._client", lambda request, row: ForbiddenClient())
    db = client.app.state.db

    objective_tamper = _candidate_campaign(client, candidate["id"], "objective-tamper")
    db.execute(
        "UPDATE campaigns SET objective='tampered objective' WHERE id=?",
        (objective_tamper["id"],),
    )
    rejected = client.post(
        f"/api/campaigns/{objective_tamper['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "objective bytes or SHA changed" in rejected.text

    config_delete = _candidate_campaign(client, candidate["id"], "config-delete")
    db.execute("UPDATE campaigns SET config_json='{}' WHERE id=?", (config_delete["id"],))
    rejected = client.post(
        f"/api/campaigns/{config_delete['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "config was removed or retyped" in rejected.text

    config_tamper = _candidate_campaign(client, candidate["id"], "config-tamper")
    changed_config = dict(config_tamper["config"])
    changed_config["condition_sha256"] = "f" * 64
    db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(changed_config), config_tamper["id"]),
    )
    rejected = client.post(
        f"/api/campaigns/{config_tamper['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "config binding mismatch" in rejected.text

    preflight_tamper = _candidate_campaign(client, candidate["id"], "preflight-tamper")
    changed_config = dict(preflight_tamper["config"])
    changed_config["preflight_attestations"] = {
        "compute_inventory_and_capacity_verified": False
    }
    db.execute(
        "UPDATE campaigns SET config_json=? WHERE id=?",
        (json.dumps(changed_config), preflight_tamper["id"]),
    )
    rejected = client.post(
        f"/api/campaigns/{preflight_tamper['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "preflight_attestations" in rejected.text

    alternate_venue = db.fetch_one("SELECT id FROM venues WHERE id<>? LIMIT 1", (run["venue_id"],))
    assert alternate_venue is not None
    for column, value in (
        ("idea_id", 1),
        ("venue_id", alternate_venue["id"]),
        ("deadline_id", None),
        ("connection_id", None),
        ("resource_id", None),
    ):
        source_tamper = _candidate_campaign(
            client, candidate["id"], f"source-column-tamper-{column}"
        )
        db.execute(f"UPDATE campaigns SET {column}=? WHERE id=?", (value, source_tamper["id"]))
        rejected = client.post(
            f"/api/campaigns/{source_tamper['id']}/start", json=START_APPROVAL
        )
        assert rejected.status_code == 409
        assert "frozen source mismatch" in rejected.text

    file_tamper = _candidate_campaign(client, candidate["id"], "file-tamper")
    binding = db.fetch_one(
        "SELECT * FROM conditioned_campaign_bindings WHERE campaign_id=?",
        (file_tamper["id"],),
    )
    objective_path = Path(binding["objective_path"])
    objective_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    objective_path.write_text("tampered file", encoding="utf-8")
    rejected = client.post(
        f"/api/campaigns/{file_tamper['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "objective bytes or SHA changed" in rejected.text

    contract_tamper = _candidate_campaign(client, candidate["id"], "contract-file-tamper")
    contract_binding = db.fetch_one(
        "SELECT * FROM conditioned_campaign_bindings WHERE campaign_id=?",
        (contract_tamper["id"],),
    )
    contract_path = Path(contract_binding["objective_path"]).with_name(
        "CANDIDATE_CONTRACT.json"
    )
    contract_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    contract_path.write_text("{}", encoding="utf-8")
    rejected = client.post(
        f"/api/campaigns/{contract_tamper['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "candidate contract binding changed" in rejected.text

    with pytest.raises(sqlite3.IntegrityError, match="binding is immutable"):
        db.execute(
            "UPDATE conditioned_campaign_bindings SET condition_sha256=? WHERE campaign_id=?",
            ("e" * 64, patch_target["id"]),
        )

    forged = client.post(
        "/api/campaigns",
        json={
            "venue_key": "PPOPP",
            "deadline_id": run["deadline_id"],
            "connection_id": run["connection_id"],
            "resource_id": run["resource_id"],
            "title": "Forged conditioned campaign",
            "objective": "not bound",
            "config": {"campaign_kind": "conditioned_candidate_research"},
        },
    )
    assert forged.status_code == 201, forged.text
    rejected = client.post(
        f"/api/campaigns/{forged.json()['id']}/start", json=START_APPROVAL
    )
    assert rejected.status_code == 409
    assert "binding receipt is missing" in rejected.text


def test_campaign_reads_project_verified_launch_eligibility(
    client: TestClient,
) -> None:
    profile = _profile(client, "Launch projection team", ["systems measurement"])
    run = _run(client, profile["id"])
    ideation_campaign_id = run["campaign_id"]

    ideas = client.get("/api/ideas?venue_key=PPOPP").json()["items"]
    assert ideas
    seed = client.post(
        "/api/campaigns",
        json={"venue_key": "PPOPP", "idea_id": ideas[0]["id"]},
    )
    assert seed.status_code == 201, seed.text
    manual = client.post(
        "/api/campaigns",
        json={"venue_key": "PPOPP", "objective": "Unbound manual objective"},
    )
    assert manual.status_code == 201, manual.text

    imported = _import_candidates(
        client, run, [_candidate("projection", "Projection candidate")]
    )
    candidate_campaign = client.post(
        f"/api/ideation/candidates/{imported['candidates'][0]['id']}/campaign",
        json={},
    )
    assert candidate_campaign.status_code == 201, candidate_campaign.text
    candidate_campaign_id = candidate_campaign.json()["id"]

    listed = {
        item["id"]: item for item in client.get("/api/campaigns?limit=500").json()["items"]
    }
    assert listed[seed.json()["id"]]["launch_eligible"] is False
    assert listed[seed.json()["id"]]["launch_provenance_valid"] is False
    assert "Seed catalog" in listed[seed.json()["id"]]["launch_ineligibility_reason"]
    assert listed[manual.json()["id"]]["launch_eligible"] is False
    assert listed[manual.json()["id"]]["launch_provenance_valid"] is False
    assert listed[ideation_campaign_id]["launch_eligible"] is True
    assert listed[ideation_campaign_id]["launch_provenance_kind"] == "conditioned_ideation"
    assert listed[candidate_campaign_id]["launch_eligible"] is True
    assert listed[candidate_campaign_id]["launch_provenance_valid"] is True
    assert listed[candidate_campaign_id]["launch_state_eligible"] is True

    detail = client.get(f"/api/campaigns/{candidate_campaign_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["launch_eligible"] is True

    client.app.state.db.execute(
        "UPDATE campaigns SET objective=? WHERE id=?",
        ("tampered candidate objective", candidate_campaign_id),
    )
    tampered_detail = client.get(f"/api/campaigns/{candidate_campaign_id}")
    assert tampered_detail.status_code == 200, tampered_detail.text
    assert tampered_detail.json()["launch_eligible"] is False
    assert tampered_detail.json()["launch_provenance_valid"] is False
    assert "objective bytes or SHA changed" in tampered_detail.json()[
        "launch_ineligibility_reason"
    ]
    tampered_list = {
        item["id"]: item for item in client.get("/api/campaigns?limit=500").json()["items"]
    }
    assert tampered_list[candidate_campaign_id]["launch_eligible"] is False
    dashboard = {
        item["id"]: item for item in client.get("/api/dashboard").json()["campaigns"]
    }
    assert dashboard[candidate_campaign_id]["launch_eligible"] is False
    assert dashboard[candidate_campaign_id]["launch_provenance_valid"] is False


def test_completed_conditioned_campaign_is_not_launch_state_eligible(
    client: TestClient,
) -> None:
    profile = _profile(client, "Completed launch projection", ["formal methods"])
    run = _run(client, profile["id"])
    imported = _import_candidates(
        client, run, [_candidate("completed", "Completed candidate")]
    )
    created = client.post(
        f"/api/ideation/candidates/{imported['candidates'][0]['id']}/campaign",
        json={},
    ).json()
    client.app.state.db.execute(
        "UPDATE campaigns SET execution_state='completed' WHERE id=?", (created["id"],)
    )

    detail = client.get(f"/api/campaigns/{created['id']}").json()
    assert detail["launch_provenance_valid"] is True
    assert detail["launch_state_eligible"] is False
    assert detail["launch_eligible"] is False
    assert "execution_state=completed" in detail["launch_state_ineligibility_reason"]


def test_team_profile_create_and_patch_reject_secrets_before_persistence(
    client: TestClient,
) -> None:
    before = len(client.get("/api/team-profiles?include_disabled=true").json())
    rejected_create = client.post(
        "/api/team-profiles",
        json={
            "name": "Unsafe profile",
            "constraints": {"api_key": "abcdefghijklmnopqrstuv"},
        },
    )
    assert rejected_create.status_code == 422
    assert len(client.get("/api/team-profiles?include_disabled=true").json()) == before

    profile = _profile(client, "Safe profile", ["verification"])
    rejected_patch = client.patch(
        f"/api/team-profiles/{profile['id']}",
        json={"metadata": {"password": "abcdefghijklmnopqrstuv"}},
    )
    assert rejected_patch.status_code == 422
    unchanged = client.get("/api/team-profiles?include_disabled=true").json()
    current = next(item for item in unchanged if item["id"] == profile["id"])
    assert current["metadata"] == {}


def test_candidate_scalar_and_pairwise_labels_export_with_group_safe_split(
    client: TestClient,
) -> None:
    profile = _profile(client, "Labeling team", ["network systems"])
    run = _run(client, profile["id"])
    candidate_payload = [_candidate("A", "Candidate A"), _candidate("B", "Candidate B")]
    artifact_sha256 = _artifact_sha256(candidate_payload)
    imported = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidate_payload,
            "imported_from": "human_entered",
            "artifact_sha256": artifact_sha256,
            "manifest": _candidate_manifest(run, candidate_payload),
        },
    )
    assert imported.status_code == 201, imported.text
    candidates = imported.json()["candidates"]
    assert imported.json()["candidate_artifact_sha256"] == artifact_sha256
    assert all(candidate["artifact_sha256"] == artifact_sha256 for candidate in candidates)
    label = client.post(
        f"/api/ideation/candidates/{candidates[0]['id']}/labels",
        json={
            "labeler_alias": "Reviewer 1",
            "decision": "shortlist",
            "dimensions": _dimensions(),
            "rationale_redacted": "Evidence and resource fit are strong.",
            "redaction_confirmed": True,
            "training_consent": True,
            "license_basis": "Team-owned annotation",
        },
    )
    assert label.status_code == 201, label.text
    assert label.json()["training_export_eligible"] is True
    assert label.json()["training_lineage"]["campaign_kind"] == "conditioned_ideation"
    pair = client.post(
        f"/api/ideation/runs/{run['id']}/pairwise",
        json={
            "left_candidate_id": candidates[0]["id"],
            "right_candidate_id": candidates[1]["id"],
            "winner": "left",
            "labeler_alias": "Reviewer 1",
            "rationale_redacted": "A has a more decisive falsifier.",
            "redaction_confirmed": True,
            "training_consent": True,
            "license_basis": "Team-owned annotation",
        },
    )
    assert pair.status_code == 201, pair.text
    assert pair.json()["training_export_eligible"] is True
    reversed_duplicate = client.post(
        f"/api/ideation/runs/{run['id']}/pairwise",
        json={
            "left_candidate_id": candidates[1]["id"],
            "right_candidate_id": candidates[0]["id"],
            "winner": "right",
            "labeler_alias": "Reviewer 1",
            "rationale_redacted": "Same comparison in reverse order.",
            "redaction_confirmed": True,
            "training_consent": True,
            "license_basis": "Team-owned annotation",
        },
    )
    assert reversed_duplicate.status_code == 409

    exported = client.get("/api/datasets/training-export")
    records = [json.loads(line) for line in exported.text.splitlines()]
    assert {record["schema"] for record in records} == {
        "argus-flywheel/conditioned-idea-label/v2",
        "argus-flywheel/conditioned-idea-preference/v2",
    }
    assert all(record["artifact_sha256"] == artifact_sha256 for record in records)
    assert len({record["split"] for record in records}) == 1
    assert all(record["group_id"] == f"ideation:{run['id']}" for record in records)
    assert all(
        (
            record.get("training_lineage", {}).get("campaign_kind")
            == "conditioned_ideation"
        )
        or (
            record.get("left_training_lineage", {}).get("campaign_kind")
            == "conditioned_ideation"
            and record.get("right_training_lineage", {}).get("campaign_kind")
            == "conditioned_ideation"
        )
        for record in records
    )
    assert exported.headers["x-automatic-training"] == "false"


def test_outcome_rebuttal_follow_up_is_idle_and_export_is_consent_gated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_record, candidate = _launch_ready_candidate(client)
    research_campaign = _candidate_campaign(client, candidate["id"], "outcome-source")
    base = {
        "campaign_id": research_campaign["id"],
        "submission_version": "camera-ready-candidate-v3",
        "reviewer_feedback": [
            {
                "reviewer": "Reviewer 1",
                "score": 6,
                "score_label": "weak accept",
                "confidence": 4,
                "opinion_redacted": "Clarify the held-out workload and confidence interval.",
                "questions": ["Was the workload frozen before the final run?"],
            },
            {
                "reviewer": "Reviewer 2",
                "score": 4,
                "score_label": "weak reject",
                "opinion_redacted": "The nearest-work distinction needs a direct experiment.",
            },
        ],
        "decision": "pending",
        "consent_to_training_export": True,
        "review_license_confirmed": True,
    }
    rejected = client.post("/api/outcomes/submissions", json=base)
    assert rejected.status_code == 422
    created = client.post(
        "/api/outcomes/submissions",
        json={**base, "redaction_confirmed": True},
    )
    assert created.status_code == 201, created.text
    submission = created.json()
    assert submission["training_export_eligible"] is True
    assert len(submission["reviewer_feedback"]) == 2
    listed = client.get("/api/outcomes/submissions").json()
    assert listed["total"] == 1
    assert listed["items"][0]["submission_version"] == "camera-ready-candidate-v3"

    follow_up = client.post(
        f"/api/outcomes/submissions/{submission['id']}/follow-up",
        json={
            "actor": "corresponding-author-alias",
            "approval_reason": "Prepare a bounded draft for human review.",
        },
    )
    assert follow_up.status_code == 201, follow_up.text
    receipt = follow_up.json()
    assert receipt["launch_triggered"] is False
    assert receipt["submission_triggered"] is False
    campaign = client.get(f"/api/campaigns/{receipt['campaign_id']}").json()
    assert campaign["execution_state"] == "idle"
    assert campaign["argus_project_id"] is None
    rebuttal_episode = client.post(
        "/api/episodes",
        json={
            "title": "Verified rebuttal trajectory",
            "objective": "Preserve the bounded rebuttal evidence trajectory.",
            "campaign_id": receipt["campaign_id"],
            "training_consent": True,
            "license_basis": "Team-owned rebuttal trajectory released for training",
        },
    )
    assert rebuttal_episode.status_code == 201, rebuttal_episode.text
    rebuttal_lineage = rebuttal_episode.json()["metadata"]["training_provenance"]
    assert rebuttal_lineage["campaign_kind"] == "rebuttal_follow_up"
    assert rebuttal_lineage["source_candidate_campaign_id"] == research_campaign["id"]
    assert rebuttal_lineage["candidate_id"] == candidate["id"]
    assert len(rebuttal_lineage["candidate_input_sha256"]) == 64
    assert len(rebuttal_lineage["candidate_prompt_sha256"]) == 64
    sealed_rebuttal = client.post(
        f"/api/episodes/{rebuttal_episode.json()['id']}/seal",
        json={"actor": "paper-owner", "reason": "freeze verified rebuttal lineage"},
    )
    assert sealed_rebuttal.status_code == 201, sealed_rebuttal.text
    rebuttal_preview = client.post(
        "/api/dataset-snapshots/preview",
        json={
            "episode_ids": [rebuttal_episode.json()["id"]],
            "require_training_consent": True,
        },
    ).json()
    assert rebuttal_preview["counts"] == {"eligible": 1, "excluded": 0}
    forged_config = {
        **campaign["config"],
        "preflight_attestations": {"compute_inventory_and_capacity_verified": False},
    }
    blocked_patch = client.patch(
        f"/api/campaigns/{receipt['campaign_id']}", json={"config": forged_config}
    )
    assert blocked_patch.status_code == 409
    assert "provenance is immutable" in blocked_patch.json()["detail"]

    class FakeArgus:
        def create_daemon(self, **kwargs: object) -> dict[str, object]:
            return {
                "sid": "rebuttal-project",
                "rc": 0,
                "spawned": True,
                "command_status": "applied",
            }

    monkeypatch.setattr("foundry.api._client", lambda request, row: FakeArgus())
    started = client.post(
        f"/api/campaigns/{receipt['campaign_id']}/start", json=START_APPROVAL
    )
    assert started.status_code == 200, started.text
    assert started.json()["argus_project_id"] == "rebuttal-project"
    launch_manifest = json.loads(
        (
            client.app.state.settings.data_dir
            / "campaigns"
            / receipt["campaign_id"]
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert launch_manifest["prompt_manifest"]["compiler"] == "rebuttal-follow-up"
    assert launch_manifest["prompt_manifest"]["launch_provenance"][
        "campaign_kind"
    ] == "rebuttal_follow_up"
    retry = client.post(
        f"/api/outcomes/submissions/{submission['id']}/follow-up",
        json={
            "actor": "corresponding-author-alias",
            "approval_reason": "Prepare a bounded draft for human review.",
        },
    )
    assert retry.status_code == 200
    assert retry.json()["campaign_id"] == receipt["campaign_id"]
    assert retry.json()["idempotent"] is True

    exported = client.get("/api/outcomes/training-export")
    records = [json.loads(line) for line in exported.text.splitlines()]
    outcome_records = [
        record for record in records if record["schema"] == "argus-flywheel/outcome-review/v2"
    ]
    assert len(outcome_records) == 2
    assert len({record["split"] for record in outcome_records}) == 1
    assert all("submission_ref" not in record for record in outcome_records)
    one_submission = client.get(
        f"/api/outcomes/submissions/{submission['id']}/training-export"
    )
    assert one_submission.status_code == 200
    assert one_submission.headers["x-training-record-count"] == "2"
    client.app.state.db.execute(
        "UPDATE submission_records SET metadata_json='{}' WHERE id=?",
        (submission["id"],),
    )
    tampered = client.get(
        f"/api/outcomes/submissions/{submission['id']}/training-export"
    )
    assert tampered.status_code == 409
    assert "lineage" in tampered.text.lower()


def test_pre_execution_ideation_submission_is_recorded_but_never_exported(
    client: TestClient,
) -> None:
    profile = _profile(client, "Pre-execution outcome team", ["systems measurement"])
    run = _run(client, profile["id"])
    created = client.post(
        "/api/outcomes/submissions",
        json={
            "campaign_id": run["campaign_id"],
            "submission_version": "not-a-real-candidate-execution",
            "reviewer_feedback": [
                {
                    "reviewer": "Reviewer 1",
                    "opinion_redacted": "This is a historical record only.",
                }
            ],
            "decision": "reject",
            "consent_to_training_export": True,
            "review_license_confirmed": True,
            "redaction_confirmed": True,
        },
    )
    assert created.status_code == 201, created.text
    submission = created.json()
    assert submission["training_lineage_verified"] is False
    assert submission["training_export_eligible"] is False
    assert any(
        "pre-execution" in reason
        for reason in submission["training_lineage_ineligibility_reasons"]
    )
    per_submission = client.get(
        f"/api/outcomes/submissions/{submission['id']}/training-export"
    )
    assert per_submission.status_code == 409
    assert client.get("/api/outcomes/training-export").text == ""
    follow_up = client.post(
        f"/api/outcomes/submissions/{submission['id']}/follow-up",
        json={"actor": "owner", "approval_reason": "must remain blocked"},
    )
    assert follow_up.status_code == 409


def test_public_candidate_import_cannot_claim_argus_provenance(
    client: TestClient,
) -> None:
    profile = _profile(client, "Provenance boundary team", ["systems"])
    run = _run(client, profile["id"])
    candidates = [_candidate("spoof", "Spoofed provenance")]
    response = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "argus_artifact",
            "artifact_sha256": _artifact_sha256(candidates),
            "manifest": _candidate_manifest(run, candidates),
        },
    )
    assert response.status_code == 422
    assert client.app.state.db.fetch_all(
        "SELECT * FROM generated_idea_candidates WHERE ideation_run_id=?", (run["id"],)
    ) == []


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("title", 7),
        ("problem_gap", None),
        ("core_hypothesis", ""),
        ("mechanism", {}),
        ("closest_work", []),
        ("public_or_authorized_data", [""]),
        ("method", None),
        ("strongest_baselines", []),
        ("decisive_experiments", [" "]),
        ("estimated_resources", {}),
        ("elapsed_time_plan", []),
        ("venue_fit", ""),
        ("risks", []),
        ("ethics_and_license", None),
        ("expected_information_gain", ""),
        ("terminal_recommendation", "debate"),
        ("team_specific_advantage", 1),
        ("condition_fit_counterfactual", ""),
        (
            "novelty_collision_test",
            {"search_cutoff": "2026-08-24", "closest_source_ids": [], "falsifier": "x"},
        ),
    ],
)
def test_hash_correct_malformed_candidate_is_rejected_fail_closed(
    client: TestClient, field: str, bad_value: object
) -> None:
    suffix = uuid.uuid4().hex[:8]
    profile = _profile(client, f"Schema boundary {suffix}", ["systems"])
    run = _run(client, profile["id"])
    candidate = _candidate(f"bad-{suffix}", "Malformed candidate")
    candidate[field] = bad_value
    candidates = [candidate]
    response = client.post(
        f"/api/ideation/runs/{run['id']}/candidates",
        json={
            "candidates": candidates,
            "imported_from": "human_entered",
            "artifact_sha256": _artifact_sha256(candidates),
            "manifest": _candidate_manifest(run, candidates),
        },
    )
    assert response.status_code == 422, response.text
    assert client.app.state.db.fetch_all(
        "SELECT * FROM generated_idea_candidates WHERE ideation_run_id=?", (run["id"],)
    ) == []


def test_negative_and_no_winner_candidate_recommendations_remain_valid(
    client: TestClient,
) -> None:
    profile = _profile(client, "Negative result preservation", ["systems"])
    run = _run(client, profile["id"])
    no_winner = _candidate("no-winner", "No supported winner")
    no_winner["terminal_recommendation"] = "NO_WINNER"
    negative = _candidate("negative", "Useful negative result")
    negative["terminal_recommendation"] = "NEGATIVE_RESULT"
    imported = _import_candidates(client, run, [no_winner, negative])
    assert [row["candidate"]["terminal_recommendation"] for row in imported["candidates"]] == [
        "NO_WINNER",
        "NEGATIVE_RESULT",
    ]

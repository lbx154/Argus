from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from argus_skill.manager.control_state import CampaignControlStore


def _store(tmp_path: Path) -> CampaignControlStore:
    state_root = tmp_path / "state"
    project_root = tmp_path / "project"
    state_root.mkdir()
    project_root.mkdir()
    (state_root / "continuous.json").write_text(
        json.dumps({"objective": "prove the result", "generation": 3}),
        encoding="utf-8",
    )
    return CampaignControlStore(state_root, project_root=project_root)


def test_head_last_revisions_make_old_waits_stale(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()

    wait_head = store.activate_wait(
        identity=identity,
        wait_id="wait-1",
        blocker_fingerprint="source:missing",
        recheck_token="source-v1",
    )
    assert store.is_wait_current(
        campaign_epoch=identity.campaign_epoch,
        state_revision=wait_head.state_revision,
        wait_id="wait-1",
    )

    evidence_head = store.clear_wait_for_new_evidence(
        identity=identity,
        stage_projection={"current_stage": "evaluate", "status": "done"},
        terminal_evidence=[{"event_id": "terminal-1", "sha256": "abc"}],
        reason="Manager reconciled terminal evidence",
    )

    assert evidence_head.state_revision == wait_head.state_revision + 1
    assert not store.is_wait_current(
        campaign_epoch=identity.campaign_epoch,
        state_revision=wait_head.state_revision,
        wait_id="wait-1",
    )
    snapshot = store.read_snapshot()
    assert snapshot is not None
    assert snapshot["active_wait"] is None
    assert snapshot["terminal_evidence"][0]["event_id"] == "terminal-1"
    assert len(list(store.revisions_root.glob("*.json"))) == 2


def test_clear_wait_if_current_does_not_clear_newer_wait(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    old_head = store.activate_wait(
        identity=identity,
        wait_id="wait-old",
        blocker_fingerprint="source:old",
        recheck_token="source-v1",
    )
    store.activate_wait(
        identity=identity,
        wait_id="wait-new",
        blocker_fingerprint="source:new",
        recheck_token="source-v2",
    )

    cleared = store.clear_wait_if_current(
        identity=identity,
        expected_state_revision=old_head.state_revision,
        expected_wait_id="wait-old",
        reason="stale runtime attempted cleanup",
    )

    assert cleared is None
    snapshot = store.read_snapshot()
    assert snapshot is not None
    assert snapshot["active_wait"]["wait_id"] == "wait-new"


def test_authorization_is_manager_owned_campaign_bound_and_one_shot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    evidence = store.project_root / "research" / "RESULT.json"
    evidence.parent.mkdir()
    evidence.write_text('{"decision":"NO_GO"}', encoding="utf-8")
    validator = store.project_root / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir()
    validator.write_text("def test_contract(): pass\n", encoding="utf-8")

    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:terminal-contract",
        allowed_actions=["validator_repair", "acceptance_retry", "unknown"],
        scope="tests/terminal only",
        allowed_write_paths=["tests/test_terminal_contract.py"],
        evidence_paths=["research/RESULT.json"],
        forbidden_mutations=["research/RESULT.json"],
        source_channel="vscode",
        source_message_id="web-1",
        validator_id="terminal-contract",
        acceptance_retries=1,
    )

    assert authorization.allowed_actions == (
        "validator_repair",
        "acceptance_retry",
    )
    assert authorization.source_channel == "vscode"
    assert authorization.frozen_evidence[0]["sha256"] not in {"", "missing"}
    assert store.read_snapshot()["authorization_ids"] == [
        authorization.authorization_id
    ]

    consumed = store.consume_authorization(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
    )
    assert consumed["event"] == "consumed"
    assert store.read_head().state_revision == authorization.state_revision + 1
    assert store.read_snapshot()["authorization_ids"] == []

    with pytest.raises(ValueError, match="already consumed"):
        store.consume_authorization(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="resume_blocked_work",
            identity=identity,
        )


def test_authorization_is_rejected_after_newer_manager_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:v1",
        allowed_actions=["resume_blocked_work"],
        scope="validator only",
        evidence_paths=[],
        source_channel="web",
        source_message_id="message-1",
    )
    store.clear_wait_for_new_evidence(
        identity=identity,
        stage_projection={"status": "newer"},
        reason="Manager observed newer state",
    )

    with pytest.raises(ValueError, match="stale relative to Manager HEAD"):
        store.consume_authorization(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="validator_repair",
            identity=identity,
        )


def test_authorization_fails_closed_on_campaign_or_evidence_drift(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    evidence = store.project_root / "evidence.json"
    evidence.write_text("before", encoding="utf-8")
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:v1",
        allowed_actions=["resume_blocked_work"],
        scope="validator only",
        evidence_paths=["evidence.json"],
        source_channel="web",
        source_message_id="message-1",
    )

    evidence.write_text("after", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen evidence changed"):
        store.consume_authorization(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="resume_blocked_work",
            identity=identity,
        )

    other = store.campaign_identity(objective="different objective", campaign_epoch=4)
    with pytest.raises(ValueError, match="campaign mismatch"):
        store.consume_authorization(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="resume_blocked_work",
            identity=other,
        )


def test_authorization_rejects_project_root_and_symlink_write_scopes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    outside = tmp_path / "outside.py"
    outside.write_text("before\n", encoding="utf-8")
    link = store.project_root / "validator.py"
    link.symlink_to(outside)

    directory = store.project_root / "tests"
    directory.mkdir()

    for repair_path in (".", "validator.py", "tests"):
        with pytest.raises(ValueError, match="project child|symlink|files"):
            store.issue_authorization(
                identity=identity,
                blocker_fingerprint="validator:unsafe-path",
                allowed_actions=["validator_repair"],
                scope="validator only",
                allowed_write_paths=[repair_path],
                evidence_paths=[],
                source_channel="web",
                source_message_id="message-unsafe",
                validator_id="unsafe-path",
                acceptance_retries=1,
            )


def test_validator_repair_rejects_symlink_created_after_authorization(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    evidence = store.project_root / "evidence.json"
    evidence.write_text("frozen\n", encoding="utf-8")
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "validator_defect",
            "validator_id": "new-validator",
            "repair_paths": ["tests/new_validator.py"],
        }],
        reason="Reviewer diagnosed a missing validator",
    )
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:new-validator",
        allowed_actions=["validator_repair"],
        scope="validator only",
        allowed_write_paths=["tests/new_validator.py"],
        evidence_paths=["evidence.json"],
        forbidden_mutations=["evidence.json"],
        source_channel="web",
        source_message_id="message-symlink-race",
        validator_id="new-validator",
        acceptance_retries=1,
    )
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    validator = store.project_root / "tests" / "new_validator.py"
    validator.parent.mkdir()
    validator.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        store.claim_repair_capability(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="validator_repair",
            identity=identity,
            mission_id="repair-symlink-race",
        )


def test_validator_repair_rejects_directory_created_after_authorization(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "validator_defect",
            "validator_id": "new-validator",
            "repair_paths": ["tests/new_validator.py"],
        }],
        reason="Reviewer diagnosed a missing validator",
    )
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:new-validator",
        allowed_actions=["validator_repair"],
        scope="validator only",
        allowed_write_paths=["tests/new_validator.py"],
        evidence_paths=[],
        source_channel="web",
        source_message_id="message-directory-race",
        validator_id="new-validator",
        acceptance_retries=1,
    )
    (store.project_root / "tests" / "new_validator.py").mkdir(parents=True)

    with pytest.raises(ValueError, match="must identify files"):
        store.claim_repair_capability(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="validator_repair",
            identity=identity,
            mission_id="repair-directory-race",
        )


def test_authorization_issuance_compares_observed_wait_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    observed = store.activate_wait(
        identity=identity,
        wait_id="wait-observed",
        blocker_fingerprint="validator:observed",
        recheck_token="observed-v1",
    )
    store.activate_wait(
        identity=identity,
        wait_id="wait-newer",
        blocker_fingerprint="validator:newer",
        recheck_token="newer-v1",
    )

    with pytest.raises(ValueError, match="HEAD changed"):
        store.issue_authorization(
            identity=identity,
            blocker_fingerprint="validator:observed",
            allowed_actions=["resume_blocked_work"],
            scope="active blocker",
            evidence_paths=[],
            source_channel="web",
            source_message_id="message-raced",
            expected_state_revision=observed.state_revision,
            expected_wait_id="wait-observed",
        )


def _prepare_validator_repair(
    store: CampaignControlStore,
) -> tuple[object, object, Path, Path]:
    identity = store.campaign_identity()
    evidence = store.project_root / "research" / "RESULT.json"
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text('{"decision":"NO_GO"}', encoding="utf-8")
    validator = store.project_root / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir(exist_ok=True)
    validator.write_text("def test_contract(): assert False\n", encoding="utf-8")
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "validator_defect",
            "validator_id": "terminal-contract",
            "repair_paths": ["tests/test_terminal_contract.py"],
            "failure_source_evidence": [{
                "artifact": "tests/test_terminal_contract.py",
                "observation": "validator binds a stale hash",
            }],
        }],
        reason="Reviewer diagnosed validator defect",
    )
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:terminal-contract",
        allowed_actions=["validator_repair", "acceptance_retry"],
        scope="tests/terminal only",
        allowed_write_paths=["tests/test_terminal_contract.py"],
        evidence_paths=["research/RESULT.json"],
        forbidden_mutations=["research/RESULT.json"],
        source_channel="vscode",
        source_message_id="message-1",
        validator_id="terminal-contract",
        acceptance_retries=1,
    )
    return identity, authorization, evidence, validator


def test_validator_repair_capability_is_one_shot_and_freezes_science(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity, authorization, evidence, validator = _prepare_validator_repair(store)

    capability = store.claim_repair_capability(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
        mission_id="repair-1",
    )
    validator.write_text("def test_contract(): assert True\n", encoding="utf-8")
    started = store.begin_acceptance_retry(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
    )
    assert started.acceptance_retries_remaining == 0
    closed = store.close_repair_capability(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
        accepted=True,
        reason="frozen evidence passed the repaired validator",
    )

    assert closed["status"] == "accepted"
    assert closed["guard_errors"] == []
    assert evidence.read_text(encoding="utf-8") == '{"decision":"NO_GO"}'
    assert store.read_snapshot()["active_capability"] is None
    with pytest.raises(ValueError, match="not current"):
        store.begin_acceptance_retry(
            capability_id=capability.capability_id,
            nonce=capability.nonce,
            identity=identity,
        )


def test_closed_capability_recovers_after_head_commit_crash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity, authorization, _evidence, validator = _prepare_validator_repair(store)
    capability = store.claim_repair_capability(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
        mission_id="repair-crash",
    )
    validator.write_text("def test_contract(): assert True\n", encoding="utf-8")
    store.begin_acceptance_retry(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
    )
    acceptance_head = store.read_head()
    assert acceptance_head is not None
    store.close_repair_capability(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
        accepted=True,
        reason="acceptance passed before process crash",
    )
    closed_head = store.read_head()
    assert closed_head is not None

    # Simulate power loss after the durable closed event but before HEAD commit.
    (store.control_root / closed_head.snapshot).unlink()
    store.head_path.write_text(
        json.dumps({"version": 1, **asdict(acceptance_head)}),
        encoding="utf-8",
    )

    recovered = store.current_repair_capability(mission_id="repair-crash")

    assert recovered is not None
    assert recovered["event"] == "closed"
    assert recovered["accepted"] is True
    assert store.read_snapshot()["active_capability"] is None


def test_claimed_capability_recovers_after_head_commit_crash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity, authorization, _evidence, _validator = _prepare_validator_repair(store)
    issued_head = store.read_head()
    assert issued_head is not None
    capability = store.claim_repair_capability(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
        mission_id="repair-claim-crash",
    )
    claimed_head = store.read_head()
    assert claimed_head is not None
    (store.control_root / claimed_head.snapshot).unlink()
    store.head_path.write_text(
        json.dumps({"version": 1, **asdict(issued_head)}),
        encoding="utf-8",
    )

    recovered = store.current_repair_capability(
        mission_id="repair-claim-crash",
    )

    assert recovered is not None
    assert recovered["capability_id"] == capability.capability_id
    assert recovered["status"] == "claimed"
    snapshot = store.read_snapshot()
    assert snapshot["authorization_ids"] == []
    assert snapshot["active_capability"]["capability_id"] == capability.capability_id


def test_started_acceptance_is_rejected_after_restart_without_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity, authorization, _evidence, validator = _prepare_validator_repair(store)
    capability = store.claim_repair_capability(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
        mission_id="repair-acceptance-crash",
    )
    validator.write_text("def test_contract(): assert True\n", encoding="utf-8")
    claimed_head = store.read_head()
    assert claimed_head is not None
    store.begin_acceptance_retry(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
    )
    started_head = store.read_head()
    assert started_head is not None
    (store.control_root / started_head.snapshot).unlink()
    store.head_path.write_text(
        json.dumps({"version": 1, **asdict(claimed_head)}),
        encoding="utf-8",
    )

    recovered = store.current_repair_capability(
        mission_id="repair-acceptance-crash",
    )

    assert recovered is not None
    assert recovered["event"] == "closed"
    assert recovered["status"] == "rejected"
    assert recovered["accepted"] is False
    assert "not replayed" in recovered["reason"]
    assert store.read_snapshot()["active_capability"] is None


def test_validator_repair_rejects_write_outside_authorized_paths(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity, authorization, evidence, _validator = _prepare_validator_repair(store)
    capability = store.claim_repair_capability(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
        mission_id="repair-2",
    )
    evidence.write_text('{"decision":"GO"}', encoding="utf-8")

    with pytest.raises(ValueError, match="frozen evidence changed"):
        store.begin_acceptance_retry(
            capability_id=capability.capability_id,
            nonce=capability.nonce,
            identity=identity,
        )


def test_scientific_evidence_failure_cannot_claim_validator_repair(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity, authorization, _evidence, _validator = _prepare_validator_repair(store)
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "scientific_evidence_failure",
            "validator_id": "",
            "repair_paths": [],
        }],
        reason="Reviewer found scientific evidence failure",
    )

    with pytest.raises(ValueError, match="stale relative to Manager HEAD"):
        store.claim_repair_capability(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="validator_repair",
            identity=identity,
            mission_id="repair-3",
        )


def test_new_campaign_starts_revision_chain_without_rewriting_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_identity = store.campaign_identity()
    old_head = store.activate_wait(
        identity=old_identity,
        wait_id="old-wait",
        blocker_fingerprint="old:blocker",
        recheck_token="old-v1",
    )

    new_identity = store.campaign_identity(
        objective="new campaign objective",
        campaign_epoch=4,
    )
    new_head = store.clear_wait_for_new_evidence(
        identity=new_identity,
        reason="Manager started a new campaign",
    )

    assert old_head.state_revision == 1
    assert new_head.state_revision == 1
    assert new_head.campaign_id != old_head.campaign_id
    assert len(list(store.revisions_root.glob("*.json"))) == 2


def test_same_epoch_new_objective_keeps_immutable_revision_history(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    old_identity = store.campaign_identity()
    store.activate_wait(
        identity=old_identity,
        wait_id="old-wait",
        blocker_fingerprint="old:blocker",
        recheck_token="old-v1",
    )
    new_identity = store.campaign_identity(
        objective="new objective at the same epoch",
        campaign_epoch=old_identity.campaign_epoch,
    )
    store.activate_wait(
        identity=new_identity,
        wait_id="new-wait",
        blocker_fingerprint="new:blocker",
        recheck_token="new-v1",
    )

    revisions = list(store.revisions_root.glob("*.json"))
    assert len(revisions) == 2
    assert {json.loads(path.read_text())["campaign_id"] for path in revisions} == {
        old_identity.campaign_id,
        new_identity.campaign_id,
    }

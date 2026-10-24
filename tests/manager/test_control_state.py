from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from argus.manager.control_state import CampaignControlStore


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


def test_authorization_is_manager_owned_campaign_bound_and_one_shot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    validator = store.project_root / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir()
    validator.write_text("def test_contract(): pass\n", encoding="utf-8")

    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:terminal-contract",
        allowed_actions=["validator_repair", "acceptance_retry", "unknown"],
        scope="tests/terminal only",
        allowed_write_paths=["tests/test_terminal_contract.py"],
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
    public = store.public_authorization(store.get_authorization(authorization.authorization_id))
    assert public["allowed_write_paths"] == ["tests/test_terminal_contract.py"]
    assert store.read_snapshot()["authorization_ids"] == [authorization.authorization_id]

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
        source_channel="web",
        source_message_id="message-1",
    )
    store.commit_revision(
        identity=identity,
        updates={"stage_projection": {"status": "newer"}},
        reason="Manager observed newer state",
    )

    with pytest.raises(ValueError, match="stale relative to Manager HEAD"):
        store.consume_authorization(
            authorization_id=authorization.authorization_id,
            nonce=authorization.nonce,
            action="validator_repair",
            identity=identity,
        )


def test_authorization_fails_closed_on_campaign_drift(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:v1",
        allowed_actions=["resume_blocked_work"],
        scope="validator only",
        source_channel="web",
        source_message_id="message-1",
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
    require_symlink_support,
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
                        source_channel="web",
                source_message_id="message-unsafe",
                validator_id="unsafe-path",
                acceptance_retries=1,
            )


def test_authorization_issuance_compares_observed_head_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity = store.campaign_identity()
    observed, _ = store.commit_revision(
        identity=identity, updates={"stage_projection": {"status": "observed"}}, reason="observed",
    )
    store.commit_revision(
        identity=identity, updates={"stage_projection": {"status": "newer"}}, reason="newer",
    )

    with pytest.raises(ValueError, match="HEAD changed"):
        store.issue_authorization(
            identity=identity,
            blocker_fingerprint="validator:observed",
            allowed_actions=["resume_blocked_work"],
            scope="active blocker",
                source_channel="web",
            source_message_id="message-raced",
            expected_state_revision=observed.state_revision,
        )


def _prepare_validator_repair(
    store: CampaignControlStore,
) -> tuple[object, object, Path]:
    identity = store.campaign_identity()
    validator = store.project_root / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir(exist_ok=True)
    validator.write_text("def test_contract(): assert False\n", encoding="utf-8")
    store.commit_revision(
        identity=identity,
        updates={
            "terminal_evidence": [
                {
                    "failure_source": "validator_defect",
                    "validator_id": "terminal-contract",
                    "repair_paths": ["tests/test_terminal_contract.py"],
                }
            ],
        },
        reason="Reviewer diagnosed validator defect",
    )
    authorization = store.issue_authorization(
        identity=identity,
        blocker_fingerprint="validator:terminal-contract",
        allowed_actions=["validator_repair", "acceptance_retry"],
        scope="tests/terminal only",
        allowed_write_paths=["tests/test_terminal_contract.py"],
        source_channel="vscode",
        source_message_id="message-1",
        validator_id="terminal-contract",
        acceptance_retries=1,
    )
    return identity, authorization, validator


def test_validator_repair_capability_is_one_shot(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity, authorization, validator = _prepare_validator_repair(store)

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
        reason="the repaired validator passed",
    )

    assert closed["status"] == "accepted"
    assert store.read_snapshot()["active_capability"] is None
    with pytest.raises(ValueError, match="not current"):
        store.begin_acceptance_retry(
            capability_id=capability.capability_id,
            nonce=capability.nonce,
            identity=identity,
        )


def test_authorization_ignores_file_changes_and_legacy_freeze_fields(
    tmp_path: Path,
) -> None:
    # Whether an authorization holds is the operator's call: no file change
    # voids it, and the freeze fields older records carry are ignored.
    store = _store(tmp_path)
    identity, authorization, validator = _prepare_validator_repair(store)
    rows = [json.loads(line) for line in store.authorization_path.read_text().splitlines()]
    rows[-1].update(
        allowed_write_baseline=[{"path": "tests/test_terminal_contract.py", "facts": [0, 0, 0]}],
        frozen_evidence=[{"path": "research/RESULT.json", "facts": "missing"}],
        frozen_tree={"files": []},
        forbidden_mutations=["research/RESULT.json"],
    )
    store.authorization_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (store.project_root / "research").mkdir()
    (store.project_root / "research" / "RESULT.json").write_text("{}", encoding="utf-8")
    validator.write_text("def test_contract(): assert True\n", encoding="utf-8")

    capability = store.claim_repair_capability(
        authorization_id=authorization.authorization_id,
        nonce=authorization.nonce,
        action="validator_repair",
        identity=identity,
        mission_id="repair-legacy",
    )
    store.begin_acceptance_retry(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
    )
    closed = store.close_repair_capability(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
        accepted=True,
        reason="the repaired validator passed",
    )

    assert closed["status"] == "accepted"
    assert store.current_repair_capability(mission_id="repair-legacy")["status"] == "accepted"


def test_claimed_capability_recovers_after_head_commit_crash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    identity, authorization, _validator = _prepare_validator_repair(store)
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
    identity, authorization, validator = _prepare_validator_repair(store)
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


def test_scientific_evidence_failure_cannot_claim_validator_repair(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    identity, authorization, _validator = _prepare_validator_repair(store)
    store.commit_revision(
        identity=identity,
        updates={
            "terminal_evidence": [
                {
                    "failure_source": "scientific_evidence_failure",
                    "validator_id": "",
                    "repair_paths": [],
                }
            ],
        },
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

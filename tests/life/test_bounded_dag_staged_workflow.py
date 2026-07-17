from __future__ import annotations

import json
from types import SimpleNamespace

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.life.supervisor import LifeSupervisor, LifeSupervisorConfig


class _Sink:
    def handle_event(self, event):  # noqa: ANN001
        return None


class _CaptureRunner:
    def __init__(self) -> None:
        self.kwargs = None

    def execute(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs
        return SimpleNamespace(
            success=True,
            status="done",
            stop_reason="",
            rounds=1,
            stage_transition={"action": "hold"},
        )


class _RepairRunner(_CaptureRunner):
    def __init__(
        self,
        path,
        content: str,
        *,
        failure_source: str = "",
    ) -> None:  # noqa: ANN001
        super().__init__()
        self.path = path
        self.content = content
        self.failure_source = failure_source

    def execute(self, **kwargs):  # noqa: ANN003
        self.kwargs = kwargs
        self.path.write_text(self.content, encoding="utf-8")
        return SimpleNamespace(
            success=True,
            status="done",
            stop_reason="validator repaired and acceptance passed",
            rounds=1,
            stage_transition={"action": "hold"},
            final_review_status="done",
            failure_source=self.failure_source,
            validator_id="terminal-contract",
        )


class _RunnerMustNotRun(_CaptureRunner):
    def execute(self, **kwargs):  # noqa: ANN003
        raise AssertionError("a durably closed acceptance must not rerun")


def _authorized_repair(tmp_path):  # noqa: ANN001
    from argus_skill.manager.control_state import CampaignControlStore

    life = tmp_path / "life"
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    memory = LifeMemory.open(life)
    (life / "continuous.json").write_text(
        json.dumps({"objective": "repair terminal gate", "generation": 2}),
        encoding="utf-8",
    )
    evidence = workdir / "research" / "RESULT.json"
    evidence.parent.mkdir()
    evidence.write_text('{"decision":"NO_GO"}', encoding="utf-8")
    validator = workdir / "tests" / "test_terminal_contract.py"
    validator.parent.mkdir()
    validator.write_text("def test_contract(): assert False\n", encoding="utf-8")
    store = CampaignControlStore(life, project_root=workdir)
    identity = store.campaign_identity()
    store.clear_wait_for_new_evidence(
        identity=identity,
        terminal_evidence=[{
            "failure_source": "validator_defect",
            "validator_id": "terminal-contract",
            "repair_paths": ["tests/test_terminal_contract.py"],
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
    item = memory.backlog.add(BacklogItem.new(
        title="repair validator",
        objective="repair the diagnosed validator without changing science",
        tags=["planner", "scope:bounded"],
        authorization_id=authorization.authorization_id,
        authorization_action="validator_repair",
    ))
    return memory, workdir, store, evidence, validator, item


def test_bounded_dag_node_keeps_vertical_stage_workflow(tmp_path) -> None:
    memory = LifeMemory.open(tmp_path / "life")
    memory.backlog.add(
        BacklogItem.new(
            title="scope",
            objective="complete scope",
            tags=["planner", "bounded_dag_node", "scope:bounded"],
        )
    )
    runner = _CaptureRunner()
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=False,
            project_worktree=tmp_path,
            artifact_root=tmp_path,
        ),
    )

    supervisor.tick()

    assert runner.kwargs is not None
    assert "workflow_mode_override" not in runner.kwargs
    assert runner.kwargs["preplanned"] is True
    assert runner.kwargs["max_rounds_override"] >= 2


def test_validator_repair_claims_capability_and_forces_one_direct_round(
    tmp_path,
) -> None:
    memory, workdir, store, evidence, validator, item = _authorized_repair(tmp_path)
    runner = _RepairRunner(validator, "def test_contract(): assert True\n")
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="repair terminal gate",
            project_worktree=workdir,
            artifact_root=workdir,
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    assert runner.kwargs["workflow_mode_override"] == "direct"
    assert runner.kwargs["max_rounds_override"] == 1
    assert store.read_snapshot()["active_capability"] is None
    assert store.authorization_events()[-1]["status"] == "accepted"
    assert evidence.read_text(encoding="utf-8") == '{"decision":"NO_GO"}'
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "done"


def test_validator_repair_cannot_mutate_frozen_scientific_evidence(tmp_path) -> None:
    memory, workdir, store, evidence, _validator, item = _authorized_repair(tmp_path)
    runner = _RepairRunner(evidence, '{"decision":"GO"}')
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="repair terminal gate",
            project_worktree=workdir,
            artifact_root=workdir,
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is False
    assert result["status"] == "error"
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert store.authorization_events()[-1]["status"] == "rejected"
    assert "frozen evidence changed" in store.authorization_events()[-1]["guard_errors"]


def test_validator_repair_requires_clean_reviewer_failure_source(tmp_path) -> None:
    memory, workdir, store, _evidence, validator, item = _authorized_repair(tmp_path)
    runner = _RepairRunner(
        validator,
        "def test_contract(): assert True\n",
        failure_source="provenance_binding_defect",
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="repair terminal gate",
            project_worktree=workdir,
            artifact_root=workdir,
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is False
    assert result["status"] == "error"
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert store.authorization_events()[-1]["status"] == "rejected"


def test_supervisor_recovers_closed_repair_without_rerunning_acceptance(
    tmp_path,
) -> None:
    from dataclasses import asdict

    memory, workdir, store, _evidence, validator, item = _authorized_repair(tmp_path)
    identity = store.campaign_identity()
    authorization = store.get_authorization(item.authorization_id)
    assert authorization is not None
    capability = store.claim_repair_capability(
        authorization_id=item.authorization_id,
        nonce=str(authorization["nonce"]),
        action="validator_repair",
        identity=identity,
        mission_id=item.id,
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
    (store.control_root / closed_head.snapshot).unlink()
    store.head_path.write_text(
        json.dumps({"version": 1, **asdict(acceptance_head)}),
        encoding="utf-8",
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_RunnerMustNotRun(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="repair terminal gate",
            project_worktree=workdir,
            artifact_root=workdir,
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is True
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "done"
    assert store.read_snapshot()["active_capability"] is None


def test_supervisor_rejects_interrupted_acceptance_without_rerunning(
    tmp_path,
) -> None:
    memory, workdir, store, _evidence, validator, item = _authorized_repair(tmp_path)
    identity = store.campaign_identity()
    authorization = store.get_authorization(item.authorization_id)
    assert authorization is not None
    capability = store.claim_repair_capability(
        authorization_id=item.authorization_id,
        nonce=str(authorization["nonce"]),
        action="validator_repair",
        identity=identity,
        mission_id=item.id,
    )
    validator.write_text("def test_contract(): assert True\n", encoding="utf-8")
    store.begin_acceptance_retry(
        capability_id=capability.capability_id,
        nonce=capability.nonce,
        identity=identity,
    )
    supervisor = LifeSupervisor(
        memory=memory,
        runner=_RunnerMustNotRun(),
        sink=_Sink(),
        config=LifeSupervisorConfig(
            continuous=True,
            continuous_objective="repair terminal gate",
            project_worktree=workdir,
            artifact_root=workdir,
        ),
    )

    result = supervisor.tick()

    assert result is not None and result["success"] is False
    assert result["status"] == "error"
    stored = next(row for row in memory.backlog.all() if row.id == item.id)
    assert stored.status == "failed"
    assert "restricted validator repair rejected" in stored.last_error
    assert store.authorization_events()[-1]["event"] == "closed"
    assert store.authorization_events()[-1]["accepted"] is False

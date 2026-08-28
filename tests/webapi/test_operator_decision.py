from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from argus_skill.core.operator_decision import build_operator_decision
from argus_skill.daemon.state import read_continuous_state, write_continuous_config
from argus_skill.life.memory import BacklogItem, MemoryBundle
from argus_skill.manager import front_door
from argus_skill.webapi import manager_pending_question


def _blocked_project(tmp_path, sid: str = "s-decision"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mem = MemoryBundle.for_cwd(workspace, global_root=tmp_path, fingerprint=sid)
    mem.init()
    item = mem.backlog.add(BacklogItem.new(title="Blocked", objective="Do work", item_id="item"))
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="Provider access is missing.",
        question="Use the fallback?",
        options=[
            {
                "id": "local-fallback",
                "label": "Use the local fallback",
                "description": "Use the local fallback.",
                "requires_note": False,
            },
            {
                "id": "stop",
                "label": "Stop this campaign",
                "description": "Keep the current work and stop.",
                "requires_note": False,
            },
        ],
    )
    mem.backlog.update(
        item.id,
        status="paused_operator",
        pending_question="Use the fallback?",
        operator_decision=card,
    )
    return mem, card


def _bound_blocked_project(tmp_path, sid: str = "s-decision"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mem = MemoryBundle.for_cwd(workspace, global_root=tmp_path, fingerprint=sid)
    mem.init()
    write_continuous_config(mem.project_root, enabled=True, objective="standing work")
    item = mem.backlog.add(
        BacklogItem.new(title="Blocked", objective="Do work", item_id="item")
    )
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="Provider access is missing.",
        question="Use the fallback?",
        options=[
            {
                "id": "local-fallback",
                "label": "Use the local fallback",
                "description": "Use the local fallback.",
                "requires_note": False,
            },
            {
                "id": "stop",
                "label": "Stop this campaign",
                "description": "Keep the current work and stop.",
                "requires_note": False,
            },
        ],
        project_id=sid,
    )
    mem.backlog.update(
        item.id,
        status="paused_operator",
        pending_question="Use the fallback?",
        operator_decision=card,
    )
    return mem, card


def test_stop_option_resolves_item_and_disables_campaign(tmp_path) -> None:
    mem, card = _blocked_project(tmp_path)
    write_continuous_config(mem.project_root, enabled=True, objective="standing work")

    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "stop",
        global_root=tmp_path,
    )

    assert result is not None and result["stopped"] is True
    item = next(row for row in mem.backlog.all() if row.id == "item")
    assert item.status == "aborted"
    assert item.operator_decision["selected_option"] == "stop"
    assert item.operator_decision["resume_requested"] is False
    assert read_continuous_state(mem.project_root).enabled is False
    events = [
        json.loads(line)
        for line in (mem.project_root / "events.jsonl").read_text().splitlines()
    ]
    stopped = [
        event
        for event in events
        if event.get("type") == "life.operator_question.answered"
    ]
    assert stopped and stopped[-1]["stopped"] is True
    assert "event_validation" not in stopped[-1]


def test_agent_option_routes_text_through_answer_handler(tmp_path, monkeypatch) -> None:
    _mem, card = _blocked_project(tmp_path)
    seen: dict[str, object] = {}

    def answer(sid, item_id, text, **kwargs):
        seen.update(sid=sid, item_id=item_id, text=text, kwargs=kwargs)
        return {"resolved": True, "reply": "continued"}

    monkeypatch.setattr(manager_pending_question, "manager_answer_pending_question", answer)
    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )

    assert result == {
        "resolved": True,
        "reply": "continued",
        "decision_id": card["id"],
    }
    assert seen["text"] == "Use the local fallback."
    assert seen["kwargs"]["decision_option"] == "local-fallback"


def test_repeated_decision_is_idempotent_across_reopened_memory(
    tmp_path,
    monkeypatch,
) -> None:
    mem, card = _bound_blocked_project(tmp_path)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit decisions must not require a model call")
        ),
    )
    first = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )
    second = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )

    assert first is not None and first["application_status"] == "accepted"
    assert second is not None and second["application_status"] == "already_applied"
    assert first["item"]["id"] == second["item"]["id"]
    assert first["resolution_id"] == second["resolution_id"]
    assert first["resume_requested"] is True
    assert len(mem.backlog.all()) == 2

    stale = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "custom",
        "Try a different route.",
        global_root=tmp_path,
    )
    assert stale is not None and stale["application_status"] == "stale"

    maintenance = mem.backlog.add(
        BacklogItem.new(
            title="Reviewed maintenance",
            objective="Publish reviewed maintenance",
            item_id="maintenance",
        )
    )
    deployment_card = build_operator_decision(
        item_id=maintenance.id,
        title=maintenance.title,
        reason="Reviewer accepted the change.",
        question="Adopt the reviewed change?",
        options=[
            {"id": "adopt", "label": "Adopt", "description": "Deploy it."},
            {"id": "decline", "label": "Decline", "description": "Do not deploy."},
        ],
    )
    deployment_card["decision_kind"] = "framework_deployment"
    mem.backlog.update(
        maintenance.id,
        status="paused_operator",
        pending_question="Adopt the reviewed change?",
        operator_decision=deployment_card,
    )
    pending = mem.project_root / "maintenance" / "pending"
    pending.mkdir(parents=True)
    (pending / f"{maintenance.id}.json").write_text(
        json.dumps({
            "repository": str(tmp_path),
            "worktree": str(tmp_path / "worktree"),
            "public_base": "base",
            "reviewed_candidate": "candidate",
            "reviewer_verdict": "done",
            "acceptance_command": ["python", "-V"],
            "evidence_refs": ["events.jsonl:1"],
            "mission_id": maintenance.id,
            "receipt_dir": str(tmp_path / "receipts"),
            "origin_remote": "origin",
            "private_remote": "private",
            "input_digest": "changed-after-card-creation",
            "approval_binding": {
                "input_digest": "frozen-identity",
            },
        }),
        encoding="utf-8",
    )
    approvals: list[dict[str, object]] = []
    deployments: list[dict[str, object]] = []

    def approve(_change, approval):
        approvals.append(approval)
        return approval

    monkeypatch.setattr(
        "argus_skill.maintenance.deploy_boundary.approve_reviewed_change",
        approve,
    )

    def partial_deployment(_change, approval):
        deployments.append(approval)
        return {
            "verdict": "REJECT",
            "baseline_failures": [],
            "candidate_failures": [],
            "acceptance_passed": True,
            "release_matches_source": True,
            "both_publication_routes_complete": False,
            "partial_publication": True,
            "daemon_roll_permitted": False,
        }

    monkeypatch.setattr(
        "argus_skill.maintenance.deploy_boundary.deploy_reviewed_change",
        partial_deployment,
    )
    monkeypatch.setattr(
        "argus_skill.life.supervisor._mission_execution_runtime.dispose_maintenance_worktree",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify.notify_pending_question",
        lambda *_args, **_kwargs: None,
    )

    partial = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        deployment_card["id"],
        "adopt",
        global_root=tmp_path,
    )
    replayed_old = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        deployment_card["id"],
        "adopt",
        global_root=tmp_path,
    )

    assert partial is not None and partial["deployment"]["partial_publication"] is True
    assert replayed_old is not None and replayed_old["application_status"] == "stale"
    assert len(approvals) == 1
    assert len(deployments) == 1
    assert deployments[0]["input_digest"] == "frozen-identity"
    current = next(row for row in mem.backlog.history() if row.id == maintenance.id)
    assert current.operator_decision["id"] != deployment_card["id"]
    assert current.operator_decision["status"] == "pending"
    assert [
        option["id"] for option in current.operator_decision["options"]
    ] == ["adopt"]
    declined_recovery = manager_pending_question.manager_answer_pending_question(
        "s-decision",
        maintenance.id,
        "decline",
        global_root=tmp_path,
    )
    assert declined_recovery == {
        "error": "choose an available deployment option",
        "answered_item_id": maintenance.id,
    }
    sidecar = json.loads(
        (pending / f"{maintenance.id}.json").read_text(encoding="utf-8")
    )
    assert sidecar["approval_binding"] == {"input_digest": "frozen-identity"}


def test_campaign_generation_change_does_not_block_pending_decision(
    tmp_path,
    monkeypatch,
) -> None:
    mem, card = _bound_blocked_project(tmp_path)
    card["campaign_generation"] = read_continuous_state(mem.project_root).generation
    mem.backlog.update("item", operator_decision=card)
    write_continuous_config(
        mem.project_root,
        enabled=True,
        objective="a newer standing objective",
    )
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit decisions must not require a model call")
        ),
    )

    result = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "local-fallback",
        global_root=tmp_path,
    )

    assert result is not None and result["application_status"] == "accepted"
    assert result["resolved"] is True
    item = next(row for row in mem.backlog.all() if row.id == "item")
    assert item.pending_question == ""
    assert item.operator_decision["status"] == "resolved"


def test_concurrent_same_decision_returns_accepted_and_already_applied(
    tmp_path,
    monkeypatch,
) -> None:
    mem, card = _bound_blocked_project(tmp_path)
    monkeypatch.setattr(
        front_door,
        "manager_triage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit decisions must not require a model call")
        ),
    )

    def resolve():
        return manager_pending_question.manager_resolve_operator_decision(
            "s-decision",
            card["id"],
            "local-fallback",
            global_root=tmp_path,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: resolve(), range(2)))

    assert {result["application_status"] for result in results if result} == {
        "accepted",
        "already_applied",
    }
    assert len(mem.backlog.all()) == 2


def test_stop_decision_replay_does_not_advance_campaign_twice(tmp_path) -> None:
    mem, card = _bound_blocked_project(tmp_path)

    first = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "stop",
        global_root=tmp_path,
    )
    generation_after_first = read_continuous_state(mem.project_root).generation
    second = manager_pending_question.manager_resolve_operator_decision(
        "s-decision",
        card["id"],
        "stop",
        global_root=tmp_path,
    )

    assert first is not None and first["application_status"] == "accepted"
    assert second is not None and second["application_status"] == "already_applied"
    assert read_continuous_state(mem.project_root).generation == generation_after_first

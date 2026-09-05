"""Declining a deployment preserves operator stops and uncommitted evidence."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from argus_skill.core.operator_decision import build_operator_decision
from argus_skill.core.session import SessionMeta, write_session_meta
from argus_skill.daemon.state import GRACEFUL_STOP_REASON, write_continuous_config
from argus_skill.life.memory import BacklogItem, MemoryBundle
from argus_skill.webapi.manager_pending_question import manager_resolve_operator_decision


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    )


def _pending_maintenance(tmp_path: Path, *, layout: str = "global"):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "tracked.txt").write_text("reviewed content\n")
    (repository / ".gitignore").write_text("evidence.log\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "reviewed candidate")
    worktree = tmp_path / "maintenance-worktree"
    _git(repository, "worktree", "add", "--detach", str(worktree), "HEAD")

    root = tmp_path / "state"
    sid = "s-maintenance"
    mem = MemoryBundle.for_cwd(repository, global_root=root, fingerprint=sid)
    write_session_meta(
        root, SessionMeta(id=sid, created=1, last_active=1, cwd=str(repository)),
    )
    item = mem.backlog.add(BacklogItem.new(
        title="Reviewed framework change",
        objective="Repair the framework",
        item_id="maintenance",
        execution_workdir=str(worktree),
    ))
    card = build_operator_decision(
        item_id=item.id,
        title=item.title,
        reason="Independent review passed.",
        question="Deploy the reviewed candidate?",
        options=[
            {"id": "adopt", "label": "Adopt", "description": "Deploy it."},
            {"id": "decline", "label": "Decline", "description": "Do not deploy."},
        ],
    )
    card["decision_kind"] = "framework_deployment"
    mem.backlog.update(
        item.id, status="paused_operator", pending_question=card["question"],
        operator_decision=card,
    )
    sidecar_root = root if layout == "global" else mem.project_root
    sidecar = sidecar_root / "maintenance" / "pending" / f"{item.id}.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({
        "repository": str(repository), "worktree": str(worktree),
        "mission_id": item.id, "reviewed_candidate": "reviewed-candidate",
        "reviewer_verdict": "done",
    }))
    write_continuous_config(
        mem.project_root, enabled=False, objective="standing work",
        done_reason=GRACEFUL_STOP_REASON,
    )
    return mem, card, sidecar, worktree


def _decline(mem, card):
    return manager_resolve_operator_decision(
        mem.project.fingerprint, card["id"], "decline",
        note="Superseded by the reviewed repair on main.",
        global_root=mem.global_root,
    )


def test_decline_and_replay_preserve_the_exact_clock_out(tmp_path: Path) -> None:
    mem, card, _sidecar, _worktree = _pending_maintenance(tmp_path)
    continuous = mem.project_root / "continuous.json"
    before = continuous.read_bytes()

    first = _decline(mem, card)
    assert first["application_status"] == "accepted"
    assert first["resume_requested"] is False
    assert continuous.read_bytes() == before

    replay = _decline(mem, card)
    assert replay["application_status"] == "already_applied"
    assert replay["resume_requested"] is False
    assert continuous.read_bytes() == before


@pytest.mark.parametrize("layout", ["global", "project"])
def test_decline_cleans_the_actual_sidecar_layout(tmp_path: Path, layout: str) -> None:
    mem, card, sidecar, worktree = _pending_maintenance(tmp_path, layout=layout)

    result = _decline(mem, card)

    assert result["deployment"] == {"verdict": "DECLINED"}
    assert not sidecar.exists()
    assert not worktree.exists()
    [item] = mem.backlog.history()
    assert item.operator_decision["selected_option"] == "decline"
    assert item.operator_decision["note"] == "Superseded by the reviewed repair on main."


@pytest.mark.parametrize("evidence_path", ["tracked.txt", "untracked.txt", "evidence.log"])
@pytest.mark.parametrize("layout", ["global", "project"])
def test_decline_retains_dirty_worktree_and_audits_cleanup(
    tmp_path: Path, evidence_path: str, layout: str,
) -> None:
    mem, card, sidecar, worktree = _pending_maintenance(tmp_path, layout=layout)
    evidence = worktree / evidence_path
    evidence.write_text("uncommitted evidence must survive\n")
    sidecar_before = sidecar.read_bytes()

    result = _decline(mem, card)

    assert result["application_status"] == "accepted"
    assert evidence.read_text() == "uncommitted evidence must survive\n"
    assert sidecar.read_bytes() == sidecar_before
    assert result["maintenance_cleanup"]["status"] == "retained"
    assert result["maintenance_cleanup"]["reason"]
    [item] = mem.backlog.history()
    assert item.status == "aborted"
    assert item.execution_workdir == str(worktree)
    assert item.operator_decision["maintenance_cleanup"] == result["maintenance_cleanup"]
    assert _decline(mem, card)["maintenance_cleanup"] == result["maintenance_cleanup"]
    events = [json.loads(line) for line in (mem.project_root / "events.jsonl").read_text().splitlines()]
    answered = [event for event in events if event["type"] == "life.operator_question.answered"]
    assert answered[-1]["maintenance_cleanup"] == result["maintenance_cleanup"]


@pytest.mark.parametrize("endpoint", ["answer", "resolve"])
def test_http_decline_does_not_start_a_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str,
) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from argus_skill.webapi import server

    mem, card, _sidecar, _worktree = _pending_maintenance(tmp_path)
    before = (mem.project_root / "continuous.json").read_bytes()
    starts = []
    monkeypatch.setattr(
        server, "start_project_daemon",
        lambda *args, **kwargs: starts.append((args, kwargs)) or {"rc": 0},
    )
    prefix = f"/api/projects/{mem.project.fingerprint}"
    if endpoint == "answer":
        path, body = f"{prefix}/backlog/maintenance/answer", {"text": "decline"}
    else:
        path = f"{prefix}/decisions/{card['id']}/resolve"
        body = {"option_id": "decline", "note": "Superseded by main."}

    response = TestClient(server.create_app(global_root=mem.global_root)).post(path, json=body)

    assert response.status_code == 200
    assert response.json()["resume_requested"] is False
    assert starts == []
    assert (mem.project_root / "continuous.json").read_bytes() == before


def test_partial_publication_reports_that_the_sidecar_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.maintenance import deploy_boundary

    mem, card, sidecar, worktree = _pending_maintenance(tmp_path)
    metadata = json.loads(sidecar.read_text())
    metadata.update(
        public_base="base", acceptance_command=["python", "-V"],
        evidence_refs=[], receipt_dir=str(tmp_path / "receipts"),
        origin_remote="origin", private_remote="private",
        approval_binding={"input_digest": "reviewed-identity"},
    )
    sidecar.write_text(json.dumps(metadata))
    before = sidecar.read_bytes()
    monkeypatch.setattr(deploy_boundary, "approve_reviewed_change", lambda *args: object())
    monkeypatch.setattr(deploy_boundary, "deploy_reviewed_change", lambda *args: {
        "verdict": "REJECT", "baseline_failures": [], "candidate_failures": [],
        "acceptance_passed": True, "release_matches_source": True,
        "both_publication_routes_complete": False, "partial_publication": True,
        "daemon_roll_permitted": False,
    })
    monkeypatch.setattr(
        "argus_skill.life.supervisor.pending_notify.notify_pending_question",
        lambda *args: None,
    )

    result = manager_resolve_operator_decision(
        mem.project.fingerprint, card["id"], "adopt", global_root=mem.global_root,
    )

    assert result["deployment"]["partial_publication"] is True
    assert not worktree.exists()
    assert sidecar.read_bytes() == before
    assert result["maintenance_cleanup"]["status"] == "removed"
    assert result["maintenance_cleanup"]["sidecar_retained"] is True
    [item] = mem.backlog.history()
    assert item.operator_decision["status"] == "pending"
    assert item.operator_decision["maintenance_cleanup"] == result["maintenance_cleanup"]

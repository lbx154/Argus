"""Maintenance decisions publish once and preserve stops and authoring evidence."""
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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


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
    _git(repository, 'branch', '-M', 'main')
    origin = tmp_path / 'origin.git'
    _git(repository, 'init', '--bare', '-q', str(origin))
    _git(repository, 'remote', 'add', 'origin', str(origin))
    _git(repository, 'push', '-q', 'origin', 'main')
    candidate = _git(repository, 'rev-parse', 'HEAD')
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
        "mission_id": item.id, "reviewed_candidate": candidate,
        "reviewer_verdict": "done",
    }))
    write_continuous_config(
        mem.project_root, enabled=False, objective="standing work",
        done_reason=GRACEFUL_STOP_REASON,
    )
    return mem, card, sidecar, worktree


@pytest.mark.parametrize('layout', ['global', 'project'])
def test_adopt_publishes_once_and_handoff_is_project_scoped(tmp_path, monkeypatch, layout):
    from argus_skill.daemon.handoff import _consume_deployment_handoff
    from argus_skill.maintenance import publication

    mem, card, sidecar, worktree = _pending_maintenance(tmp_path, layout=layout)
    publish = publication.publish_reviewed_change
    calls = []

    def record(*args):
        calls.append(args)
        return publish(*args)

    monkeypatch.setattr(publication, 'publish_reviewed_change', record)
    first = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert first['application_status'] == 'accepted'
    assert first['deployment']['verdict'] == 'ADOPT'
    runtime = Path(first['deployment']['runtime_source_root'])
    assert runtime.is_dir() and runtime != worktree
    assert _consume_deployment_handoff(mem.project_root) == runtime
    assert not sidecar.exists() and not worktree.exists()
    assert first['resume_requested'] is False
    assert 'baseline_failure_count' not in first['deployment']
    assert 'both_publication_routes_complete' not in first['deployment']
    again = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert again['application_status'] == 'already_applied' and len(calls) == 1


def test_publication_failure_retains_same_decision_and_authoring_evidence(tmp_path, monkeypatch):
    from argus_skill.maintenance import publication

    mem, card, sidecar, worktree = _pending_maintenance(tmp_path)
    before = sidecar.read_bytes()
    publish = publication.publish_reviewed_change

    def fail(*_args):
        raise RuntimeError('remote temporarily unavailable')

    monkeypatch.setattr(publication, 'publish_reviewed_change', fail)
    first = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert first['application_status'] == 'retryable' and not first['resolved']
    assert sidecar.read_bytes() == before and worktree.is_dir()
    [pending] = mem.backlog.history()
    assert pending.operator_decision == card
    assert pending.status == 'paused_operator'
    assert not (mem.project_root / 'maintenance/receipts/daemon-roll.json').exists()
    monkeypatch.setattr(publication, 'publish_reviewed_change', publish)
    second = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert second['application_status'] == 'accepted'


def test_handoff_failure_retries_same_decision_without_republishing(tmp_path, monkeypatch):
    from argus_skill.daemon import handoff
    from argus_skill.maintenance import publication

    mem, card, sidecar, worktree = _pending_maintenance(tmp_path)
    (worktree / 'tracked.txt').write_text('new reviewed content\n')
    _git(worktree, 'commit', '-qam', 'reviewed repair')
    candidate = _git(worktree, 'rev-parse', 'HEAD')
    metadata = json.loads(sidecar.read_text())
    metadata['reviewed_candidate'] = candidate
    sidecar.write_text(json.dumps(metadata))
    card['reviewed_candidate'] = candidate
    mem.backlog.update('maintenance', operator_decision=card)
    before = sidecar.read_bytes()
    continuous = (mem.project_root / 'continuous.json').read_bytes()
    original_git = publication._git
    request_handoff = handoff.request_deployment_handoff
    pushes = []

    def record_git(root, *args, **kwargs):
        if args[0] == 'push':
            pushes.append(args)
        return original_git(root, *args, **kwargs)

    def fail_handoff(*_args):
        raise OSError('handoff write failed')

    monkeypatch.setattr(publication, '_git', record_git)
    monkeypatch.setattr(handoff, 'request_deployment_handoff', fail_handoff)
    first = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert first['application_status'] == 'retryable' and not first['resolved']
    assert _git(tmp_path / 'origin.git', 'rev-parse', 'main') == candidate
    assert len(pushes) == 1
    assert sidecar.read_bytes() == before and worktree.is_dir()
    [pending] = mem.backlog.history()
    assert pending.operator_decision == card and pending.status == 'paused_operator'
    assert handoff._consume_deployment_handoff(mem.project_root) is None

    monkeypatch.setattr(handoff, 'request_deployment_handoff', request_handoff)
    second = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert second['application_status'] == 'accepted'
    assert len(pushes) == 1
    assert handoff._consume_deployment_handoff(mem.project_root) == Path(second['deployment']['runtime_source_root'])
    assert not sidecar.exists() and not worktree.exists()
    assert (mem.project_root / 'continuous.json').read_bytes() == continuous


def test_adopt_cannot_switch_to_a_commit_changed_after_review(tmp_path, monkeypatch):
    from argus_skill.maintenance import publication

    mem, card, sidecar, _worktree = _pending_maintenance(tmp_path)
    metadata = json.loads(sidecar.read_text())
    card['reviewed_candidate'] = metadata['reviewed_candidate']
    mem.backlog.update('maintenance', operator_decision=card)
    metadata['reviewed_candidate'] = '0' * 40
    sidecar.write_text(json.dumps(metadata))
    calls = []
    monkeypatch.setattr(publication, 'publish_reviewed_change', lambda *args: calls.append(args))
    result = manager_resolve_operator_decision(mem.project.fingerprint, card['id'], 'adopt', global_root=mem.global_root)
    assert not result['resolved'] and calls == []
    assert 'candidate changed' in result['error']


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

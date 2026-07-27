"""Publishing a reviewed self-maintenance fix waits for the operator.

Policy decision (2026-07-26): the daemon used to push a branch and open a PR by
itself once a fix was independently reviewed, canaried and adopted locally —
`auto_merge=False`, but the branch and PR appeared without anyone asking. The
operator chose the stricter option: nothing leaves the machine until a human
approves it.

Everything before publication is deliberately unchanged. The fix is still
authored, reviewed, canaried and adopted, and `local_active` stays a complete
terminal state — the daemon keeps repairing itself, it just stops publishing on
its own.

The approval is bound to one reviewed commit and is single-use. A blanket
"publishing is allowed" flag would authorise whatever the NEXT self-maintenance
cycle produced, which is precisely the thing the operator asked to stop.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from argus_skill.daemon.self_maintenance import (
    _PUBLICATION_APPROVAL_TTL_SECONDS,
    _PUBLICATION_AWAITING,
    DaemonSelfMaintenance,
    SelfMaintenanceState,
)

_COMMIT = "a" * 40
_OTHER = "b" * 40


class _Probe(SelfMaintenanceState):
    """The real state object, pointed at a temporary life directory.

    Nothing here overrides production code: the state file, its lock and the
    read/write path are the ones the daemon uses, and events are captured
    through the `on_event` hook the class already exposes. A double that
    reimplemented `_state`/`_write_state` would keep passing even if the real
    persistence broke.
    """

    def __init__(self, tmp_path: Path) -> None:  # noqa: D107 - test double
        self.events: list[dict] = []
        super().__init__(life_dir=tmp_path, on_event=self.events.append)


def _awaiting(tmp_path: Path, commit: str = _COMMIT) -> _Probe:
    probe = _Probe(tmp_path)
    probe._write_state(
        phase="local_active",
        publication_status=_PUBLICATION_AWAITING,
        awaiting_commit=commit,
        incident_id="inc-1",
    )
    return probe


def test_nothing_is_published_without_an_approval(tmp_path: Path) -> None:
    probe = _awaiting(tmp_path)

    reason = probe._publication_approval_error(_COMMIT)

    assert reason
    assert "approval required" in reason


def test_an_approved_commit_publishes_once(tmp_path: Path) -> None:
    probe = _awaiting(tmp_path)
    assert probe.approve_publication(_COMMIT) == ""

    assert probe._publication_approval_error(_COMMIT) == ""
    # Single-use: the approval is consumed before the push, so a failed publish
    # cannot silently retry forever on one grant.
    assert probe._publication_approval_error(_COMMIT) != ""


def test_an_approval_does_not_carry_over_to_the_next_fix(tmp_path: Path) -> None:
    # The whole point of binding to a commit: approving what you reviewed must
    # not authorise whatever the daemon writes next.
    probe = _awaiting(tmp_path)
    probe.approve_publication(_COMMIT)

    reason = probe._publication_approval_error(_OTHER)

    assert "approved a different commit" in reason


def test_an_expired_approval_is_refused(tmp_path: Path) -> None:
    probe = _awaiting(tmp_path)
    probe.approve_publication(_COMMIT)
    probe._write_state(
        publication_approved_at=time.time() - _PUBLICATION_APPROVAL_TTL_SECONDS - 1
    )

    assert "expired" in probe._publication_approval_error(_COMMIT)


def test_approving_a_commit_that_is_not_waiting_is_refused(tmp_path: Path) -> None:
    probe = _awaiting(tmp_path)

    assert "no reviewed fix waiting" in probe.approve_publication(_OTHER)


def test_approving_with_nothing_waiting_is_refused(tmp_path: Path) -> None:
    probe = _Probe(tmp_path)

    assert "no reviewed fix is waiting" in probe.approve_publication(_COMMIT)


def test_a_short_commit_prefix_is_accepted(tmp_path: Path) -> None:
    # The operator reads short hashes off `--status`; requiring 40 characters
    # would make the gate annoying enough to route around.
    probe = _awaiting(tmp_path)

    assert probe.approve_publication(_COMMIT[:12]) == ""
    assert probe._publication_approval_error(_COMMIT) == ""


def test_the_pending_fix_is_visible(tmp_path: Path) -> None:
    # A gate with no way to see through it just accumulates work silently.
    probe = _awaiting(tmp_path)
    probe._write_state(worktree="/tmp/wt", local_accepted_at=123.0)

    pending = probe.pending_publication()

    assert pending is not None
    assert pending["commit"] == _COMMIT
    assert pending["incident_id"] == "inc-1"
    assert pending["worktree"] == "/tmp/wt"


@pytest.mark.parametrize("status", ["opened", "unavailable", "failed", ""])
def test_nothing_is_reported_pending_in_other_states(
    tmp_path: Path,
    status: str,
) -> None:
    probe = _Probe(tmp_path)
    probe._write_state(publication_status=status, awaiting_commit=_COMMIT)

    assert probe.pending_publication() is None


def test_approval_is_recorded_as_an_auditable_event(tmp_path: Path) -> None:
    probe = _awaiting(tmp_path)

    probe.approve_publication(_COMMIT, approved_by="lbx154")

    approved = [
        e for e in probe.events
        if e.get("type") == "manager.self_maintenance.publication_approved"
    ]
    assert approved and approved[0]["commit"] == _COMMIT
    assert approved[0]["approved_by"] == "lbx154"


def test_the_daemon_still_adopts_fixes_locally() -> None:
    """The gate must hold publication only — not self-repair.

    Reverse assertion: if `local_active` ever became conditional on approval,
    the daemon would stop fixing itself while waiting for a human, which is the
    opposite of what was asked for.
    """
    import inspect

    from argus_skill.daemon import self_maintenance as mod

    source = inspect.getsource(mod.DaemonSelfMaintenance)
    gate = source.index("_publication_approval_error(reviewed_commit)")
    local_active = source.index('phase="local_active"')
    assert local_active < gate, (
        "the local adoption must be written before the approval gate is consulted"
    )


class _CanaryProbe(DaemonSelfMaintenance):
    """A full controller whose only fakes are the two network-facing seams.

    `publish_after_canary` — the method that actually contains the gate — runs
    for real here, including the git checks. Only choosing a push target and
    performing the push are stubbed, because those leave the machine.
    """

    def __init__(self, tmp_path: Path) -> None:  # noqa: D107 - test double
        self.events: list[dict] = []
        self.published: list[str] = []
        super().__init__(
            life_dir=tmp_path / "life",
            framework_root=tmp_path / "src",
            project_workdir=tmp_path / "wd",
            manager=None,
            memory=None,
            on_event=self.events.append,
        )

    def _publication_target(self, worktree):
        return ("origin", "")

    def _publish_reviewed_change(self, *, state, worktree, branch, reviewed_commit, target):
        self.published.append(reviewed_commit)
        return "https://example.invalid/pr/1"


def _canary_ready(tmp_path: Path) -> tuple[_CanaryProbe, str]:
    """A probe parked exactly where the daemon sits after a reviewed canary."""
    import subprocess

    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin",
    }
    (worktree / "f.txt").write_text("x", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q", "-b", "fix"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "reviewed fix"],
    ):
        subprocess.run(cmd, cwd=worktree, env=env, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, env=env,
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    probe = _CanaryProbe(tmp_path)
    probe._write_state(
        phase="canary_running",
        canary_source_root=str(probe.framework_root.resolve()),
        canary_kind="repair",
        commit=head,
        worktree=str(worktree),
        branch="fix",
        incident_id="inc-9",
    )
    return probe, head


_PROGRESS = {"results": [{"success": True, "status": "done"}], "planning_cycles": 1}


def test_the_real_publish_path_pushes_nothing_without_approval(tmp_path: Path) -> None:
    """The behavioural reverse assertion: delete the gate and this goes red."""
    probe, head = _canary_ready(tmp_path)

    probe.publish_after_canary(summary=_PROGRESS)

    assert probe.published == [], "a branch was published without operator approval"
    state = probe._state()
    assert state["phase"] == "local_active", "the fix must still be adopted locally"
    assert state["publication_status"] == _PUBLICATION_AWAITING
    assert state["awaiting_commit"] == head
    assert any(
        event["type"] == "manager.self_maintenance.publication_awaiting_approval"
        for event in probe.events
    )


def test_the_real_publish_path_pushes_once_approved(tmp_path: Path) -> None:
    """And the gate must be openable, or it is just a broken feature."""
    probe, head = _canary_ready(tmp_path)
    assert probe.approve_publication(head) == ""

    probe.publish_after_canary(summary=_PROGRESS)

    assert probe.published == [head]
    assert probe._state()["pr_url"] == "https://example.invalid/pr/1"


def test_publication_resumes_after_operator_approves_waiting_fix(
    tmp_path: Path,
) -> None:
    """The normal order is canary, visible prompt, then human approval."""
    probe, head = _canary_ready(tmp_path)

    probe.publish_after_canary(summary=_PROGRESS)
    assert probe._state()["publication_status"] == _PUBLICATION_AWAITING
    assert probe.approve_publication(head) == ""

    probe.publish_after_canary(summary={})

    assert probe.published == [head]
    assert probe._state()["pr_url"] == "https://example.invalid/pr/1"


def test_waiting_for_approval_does_not_poll_publication_target(
    tmp_path: Path,
) -> None:
    probe, head = _canary_ready(tmp_path)
    probe.publish_after_canary(summary=_PROGRESS)

    probe._publication_target = lambda _worktree: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("publication target polled before approval")
    )

    assert probe.publish_after_canary(summary={}) == head


def test_approved_publication_retries_after_transient_target_failure(
    tmp_path: Path,
) -> None:
    probe, head = _canary_ready(tmp_path)
    probe.publish_after_canary(summary=_PROGRESS)
    assert probe.approve_publication(head) == ""

    probe._publication_target = lambda _worktree: (  # type: ignore[method-assign]
        None,
        "temporary GitHub authentication failure",
    )
    probe.publish_after_canary(summary={})
    assert probe._state()["publication_status"] == "unavailable"

    probe._publication_target = lambda _worktree: ("origin", "")  # type: ignore[method-assign]
    probe._write_state(publication_last_attempt_at=0.0)
    probe.publish_after_canary(summary={})

    assert probe.published == [head]


def test_interrupted_pending_publication_resumes_safely(tmp_path: Path) -> None:
    probe, head = _canary_ready(tmp_path)
    probe._write_state(
        phase="local_active",
        publication_status="pending",
        local_accepted_at=time.time(),
    )

    probe.publish_after_canary(summary={})

    assert probe.published == []
    state = probe._state()
    assert state["publication_status"] == _PUBLICATION_AWAITING
    assert state["awaiting_commit"] == head

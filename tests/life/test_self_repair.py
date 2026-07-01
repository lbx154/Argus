"""Self-repair capture: an argus mission that edits its own source is snapshotted
to a review branch, surgically (only newly-touched package files) and safely
(no working-tree / main mutation, no operator-WIP sweep)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus_skill.life.self_repair import (
    CaptureResult,
    capture_if_self_modified,
    capture_self_repair,
    dirty_self_source,
    self_source_repo_root,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "argus-repo"
    (root / "argus_skill" / "tools").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "benchmarks").mkdir()  # operator WIP area, must never be captured
    _git(root, "init")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "argus_skill" / "__init__.py").write_text("x = 1\n")
    (root / "argus_skill" / "tools" / "checker.py").write_text("def check():\n    return 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def test_dirty_self_source_scopes_to_package_and_tests(repo: Path) -> None:
    # Engineer edits a package file + adds a test; operator has unrelated WIP.
    (repo / "argus_skill" / "tools" / "checker.py").write_text("def check():\n    return 2\n")
    (repo / "tests" / "test_new.py").write_text("def test_x():\n    assert True\n")
    (repo / "benchmarks" / "wip.json").write_text("{}\n")       # WIP — excluded
    (repo / "README.md").write_text("top-level file\n")          # outside — excluded
    dirty = dirty_self_source(repo)
    assert dirty == {"argus_skill/tools/checker.py", "tests/test_new.py"}


def test_capture_commits_only_named_files_to_review_branch(repo: Path) -> None:
    (repo / "argus_skill" / "tools" / "checker.py").write_text("def check():\n    return 2\n")
    res = capture_self_repair(
        repo, files={"argus_skill/tools/checker.py"},
        session_label="sess1", message="self-repair: fix checker",
    )
    assert isinstance(res, CaptureResult)
    assert res.branch == "argus-self-repair/sess1"
    # The review branch has the change; main/HEAD and the working tree do NOT move.
    assert _git(repo, "rev-parse", "HEAD") == res.parent
    branch_blob = _git(repo, "show", f"{res.branch}:argus_skill/tools/checker.py")
    assert "return 2" in branch_blob
    head_blob = _git(repo, "show", "HEAD:argus_skill/tools/checker.py")
    assert "return 1" in head_blob                       # HEAD untouched
    # Working tree still carries the engineer's live change (not reverted).
    assert "return 2" in (repo / "argus_skill" / "tools" / "checker.py").read_text()
    # The scratch index did NOT leak into the real index (nothing staged).
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_capture_refuses_paths_outside_package(repo: Path) -> None:
    (repo / "benchmarks" / "wip.json").write_text("{}\n")
    res = capture_self_repair(
        repo, files={"benchmarks/wip.json", "README.md"},
        session_label="s", message="should be empty",
    )
    assert res is None                                   # nothing eligible → no-op


def test_capture_accumulates_on_one_branch_across_missions(repo: Path) -> None:
    (repo / "argus_skill" / "tools" / "checker.py").write_text("def check():\n    return 2\n")
    r1 = capture_self_repair(
        repo, files={"argus_skill/tools/checker.py"},
        session_label="run", message="mission 1",
    )
    assert r1 is not None
    # Mission 2 adds a new file; capture parents on the branch tip → linear history.
    (repo / "argus_skill" / "helper.py").write_text("def h():\n    return 9\n")
    r2 = capture_self_repair(
        repo, files={"argus_skill/tools/checker.py", "argus_skill/helper.py"},
        session_label="run", message="mission 2",
    )
    assert r2 is not None and r2.parent == r1.commit     # accumulates on the branch
    log = _git(repo, "log", "--oneline", r2.branch)
    assert "mission 1" in log and "mission 2" in log
    assert "return 9" in _git(repo, "show", f"{r2.branch}:argus_skill/helper.py")


def test_capture_noop_when_tree_identical(repo: Path) -> None:
    # No self-source change → nothing to commit.
    res = capture_self_repair(
        repo, files={"argus_skill/__init__.py"},
        session_label="s", message="noop",
    )
    assert res is None


def test_capture_if_self_modified_excludes_boot_baseline(repo: Path) -> None:
    # A file already dirty at boot (operator's in-progress edit) is the baseline.
    (repo / "argus_skill" / "__init__.py").write_text("x = 1\n# operator WIP\n")
    baseline = dirty_self_source(repo)
    assert "argus_skill/__init__.py" in baseline
    # The mission then edits a DIFFERENT package file.
    (repo / "argus_skill" / "tools" / "checker.py").write_text("def check():\n    return 42\n")
    res = capture_if_self_modified(
        session_label="sess", baseline=baseline,
        message="self-repair", repo_root=repo,
    )
    assert res is not None
    assert res.files == ("argus_skill/tools/checker.py",)  # boot WIP excluded
    # The operator's WIP file is NOT on the review branch.
    assert _git(repo, "show", f"{res.branch}:argus_skill/__init__.py") == "x = 1"


def test_capture_if_self_modified_noop_when_nothing_new(repo: Path) -> None:
    baseline = dirty_self_source(repo)
    assert capture_if_self_modified(
        session_label="s", baseline=baseline, message="m", repo_root=repo
    ) is None


def test_self_source_repo_root_none_outside_git(tmp_path: Path) -> None:
    # A non-repo package dir → None (feature auto-disables on wheel installs).
    fake = tmp_path / "site-packages" / "argus_skill" / "__init__.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("x = 1\n")
    assert self_source_repo_root(pkg_file=str(fake)) is None


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.closed = False

    def handle_event(self, e: dict) -> None:
        self.events.append(e)

    def handle_stream_line(self, s: str, line: str) -> None: ...

    def close(self) -> None:
        self.closed = True


def test_sink_captures_on_mission_completed_and_forwards(repo: Path) -> None:
    from argus_skill.life.self_repair import SelfRepairSink

    down = _RecordingSink()
    sink = SelfRepairSink(
        down, repo_root=repo, baseline=dirty_self_source(repo), session_label="obs",
    )
    # A mission edits argus source, then completes.
    (repo / "argus_skill" / "tools" / "checker.py").write_text("def check():\n    return 7\n")
    sink.handle_event({"type": "round.main.completed"})       # forwarded, no capture
    sink.handle_event({"type": "life.mission.completed", "status": "done"})

    types = [e.get("type") for e in down.events]
    # Original events forwarded in order, plus a capture event appended.
    assert types[:2] == ["round.main.completed", "life.mission.completed"]
    assert "self_repair.captured" in types
    cap = next(e for e in down.events if e["type"] == "self_repair.captured")
    assert cap["files"] == ["argus_skill/tools/checker.py"]
    assert cap["branch"] == "argus-self-repair/obs"
    assert "return 7" in _git(repo, "show", f"{cap['branch']}:argus_skill/tools/checker.py")


def test_sink_no_capture_when_source_untouched(repo: Path) -> None:
    from argus_skill.life.self_repair import SelfRepairSink

    down = _RecordingSink()
    sink = SelfRepairSink(
        down, repo_root=repo, baseline=dirty_self_source(repo), session_label="obs",
    )
    sink.handle_event({"type": "life.mission.completed", "status": "done"})
    assert [e.get("type") for e in down.events] == ["life.mission.completed"]  # no capture
    sink.close()
    assert down.closed


def test_sink_build_inert_outside_git(monkeypatch, tmp_path: Path) -> None:
    from argus_skill.life import self_repair as sr

    down = _RecordingSink()
    monkeypatch.setattr(sr, "self_source_repo_root", lambda *a, **k: None)
    # Outside a git checkout, build() returns the downstream unwrapped (inert).
    assert sr.SelfRepairSink.build(down, session_label="x") is down


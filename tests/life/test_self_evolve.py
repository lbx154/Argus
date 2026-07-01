"""Controlled self-evolution: Manager review + hard test gate + land-as-argus."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus_skill.life.self_evolve import (
    ARGUS_AUTHOR_NAME,
    evolve_capture,
    land_self_repair,
    review_self_repair,
    run_test_gate,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "argus_skill").mkdir(parents=True)
    (root / "tests").mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "argus_skill" / "__init__.py").write_text("")
    (root / "argus_skill" / "m.py").write_text("def f():\n    return 1\n")
    (root / "tests" / "test_m.py").write_text(
        "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 1\n"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _capture_commit(repo: Path, *, edits: dict[str, str]) -> tuple[str, list[str]]:
    """Make a self-repair-style commit (off HEAD) with the given file contents,
    without moving HEAD's working tree — mirrors how self_repair captures."""
    for path, content in edits.items():
        (repo / path).write_text(content)
    _git(repo, "add", "--", *edits)
    _git(repo, "commit", "-m", "captured self-repair")
    commit = _git(repo, "rev-parse", "HEAD")
    # roll HEAD back so the capture is an off-HEAD commit (like the review branch)
    _git(repo, "branch", "cap", commit)
    _git(repo, "reset", "--hard", "HEAD~1")
    return commit, list(edits)


# --- test gate -------------------------------------------------------------

def test_gate_passes_for_green_tests(repo: Path) -> None:
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 2\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 2\n",
    })
    gate = run_test_gate(repo, commit=commit, test_files=files)
    assert gate.passed, gate.tail


def test_gate_fails_for_red_tests(repo: Path) -> None:
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 2\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 999\n",
    })
    gate = run_test_gate(repo, commit=commit, test_files=files)
    assert not gate.passed


def test_gate_fails_with_no_touched_tests(repo: Path) -> None:
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 3\n"})
    gate = run_test_gate(repo, commit=commit, test_files=files)
    assert not gate.passed and "no touched test" in gate.tail


# --- review (Manager judge) -----------------------------------------------

class _FakeRunner:
    def __init__(self, message: str, *, raise_it: bool = False) -> None:
        self._message = message
        self._raise = raise_it

    def run_exec(self, *, prompt, options, run_label, resume_thread_id):  # noqa: ANN001
        if self._raise:
            raise RuntimeError("backend down")

        class _R:
            last_agent_message = self._message
        return _R()


def test_review_parses_approve() -> None:
    r = _FakeRunner('sure: {"approve": true, "risk": "low", "reason": "clean type hint"}')
    v = review_self_repair(r, diff="d", files=["argus_skill/m.py"], test_tail="1 passed")
    assert v.approve and v.risk == "low"


def test_review_fails_closed_on_backend_error() -> None:
    r = _FakeRunner("", raise_it=True)
    v = review_self_repair(r, diff="d", files=["x"], test_tail="")
    assert v.approve is False and v.risk == "high"


def test_review_fails_closed_on_unparseable() -> None:
    r = _FakeRunner("I think it's fine but no JSON here")
    v = review_self_repair(r, diff="d", files=["x"], test_tail="")
    assert v.approve is False


def test_review_stringified_false_is_not_approval() -> None:
    # A reject as a stringified bool must NOT be read as approval (bool("false") is True).
    r = _FakeRunner('{"approve": "false", "risk": "high", "reason": "do not merge"}')
    v = review_self_repair(r, diff="d", files=["x"], test_tail="")
    assert v.approve is False


def test_review_takes_last_verdict_not_illustrative_example() -> None:
    # Narration includes an example approval, then the REAL reject verdict.
    r = _FakeRunner(
        'An approval looks like {"approve": true, "risk": "low", "reason": "ex"}. '
        'But here: {"approve": false, "risk": "high", "reason": "regression risk"}'
    )
    v = review_self_repair(r, diff="d", files=["x"], test_tail="")
    assert v.approve is False and "regression" in v.reason


# --- land (as argus) -------------------------------------------------------

def test_land_advances_evolve_branch_as_argus(repo: Path) -> None:
    _git(repo, "branch", "argus-evolve", "main")            # not checked out
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 7\n"})
    before = _git(repo, "rev-parse", "argus-evolve")
    res = land_self_repair(repo, source_commit=commit, files=files,
                           target_branch="argus-evolve", message="argus: land it")
    assert res.landed
    assert _git(repo, "rev-parse", "argus-evolve") != before
    assert _git(repo, "show", "argus-evolve:argus_skill/m.py").strip() == "def f():\n    return 7".strip()
    assert _git(repo, "log", "-1", "--format=%an", "argus-evolve") == ARGUS_AUTHOR_NAME
    assert res.pushed_to is None
    # the shared, checked-out main was NOT touched
    assert _git(repo, "show", "main:argus_skill/m.py").strip() == "def f():\n    return 1".strip()


def test_land_refuses_protected_branch(repo: Path) -> None:
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 7\n"})
    for prot in ("main", "master", "MAIN"):
        res = land_self_repair(repo, source_commit=commit, files=files,
                               target_branch=prot, message="nope")
        assert not res.landed and "protected" in res.reason


def test_land_refuses_checked_out_branch(repo: Path) -> None:
    # A non-protected branch that IS checked out (the repo's default) must be
    # refused too — plumbing onto a live worktree would desync it.
    checked = _git(repo, "symbolic-ref", "--short", "HEAD")   # 'main'
    _git(repo, "branch", "sidebr", "main")
    _git(repo, "checkout", "-q", "sidebr")                     # now sidebr is checked out
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 7\n"})
    res = land_self_repair(repo, source_commit=commit, files=files,
                           target_branch="sidebr", message="nope")
    assert not res.landed and "checked out" in res.reason
    _git(repo, "checkout", "-q", checked)


def test_land_does_not_touch_working_tree(repo: Path) -> None:
    _git(repo, "branch", "argus-evolve", "main")
    # Dirty the live working tree (as the daemon would); land must not disturb it.
    (repo / "argus_skill" / "live.py").write_text("# uncommitted daemon edit\n")
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 8\n"})
    land_self_repair(repo, source_commit=commit, files=files,
                     target_branch="argus-evolve", message="argus: land")
    assert (repo / "argus_skill" / "live.py").read_text() == "# uncommitted daemon edit\n"


def test_land_pushes_only_when_remote_set(repo: Path, tmp_path: Path) -> None:
    _git(repo, "branch", "argus-evolve", "main")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], capture_output=True)
    _git(repo, "remote", "add", "sandbox", str(bare))
    _git(repo, "push", "sandbox", "argus-evolve")
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 9\n"})
    res = land_self_repair(repo, source_commit=commit, files=files, target_branch="argus-evolve",
                           message="argus: land+push", remote="sandbox")
    assert res.landed and res.pushed_to == "sandbox/argus-evolve"
    assert _git(bare, "log", "-1", "--format=%an", "argus-evolve") == ARGUS_AUTHOR_NAME


# --- orchestrator ----------------------------------------------------------

def test_evolve_lands_when_gate_and_review_pass(repo: Path) -> None:
    _git(repo, "branch", "argus-evolve", "main")
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 5\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 5\n",
    })
    r = _FakeRunner('{"approve": true, "risk": "low", "reason": "correct + tested"}')
    out = evolve_capture(runner=r, repo_root=repo, commit=commit, files=files)  # default → argus-evolve
    assert out.stage == "landed" and out.land and out.land.landed
    assert _git(repo, "log", "-1", "--format=%an", "argus-evolve") == ARGUS_AUTHOR_NAME
    assert out.land.target_branch == "argus-evolve"


def test_evolve_rejects_on_red_gate_without_review(repo: Path) -> None:
    _git(repo, "branch", "argus-evolve", "main")
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 5\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 111\n",
    })
    # Even an approving Manager cannot save a red test bar — gate runs first.
    r = _FakeRunner('{"approve": true, "risk": "low", "reason": "looks fine"}')
    before = _git(repo, "rev-parse", "argus-evolve")
    out = evolve_capture(runner=r, repo_root=repo, commit=commit, files=files)
    assert out.stage == "gated" and out.verdict is None   # review never ran
    assert _git(repo, "rev-parse", "argus-evolve") == before


def test_evolve_rejects_when_manager_rejects(repo: Path) -> None:
    _git(repo, "branch", "argus-evolve", "main")
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 5\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 5\n",
    })
    r = _FakeRunner('{"approve": false, "risk": "high", "reason": "behavioral, unclear"}')
    before = _git(repo, "rev-parse", "argus-evolve")
    out = evolve_capture(runner=r, repo_root=repo, commit=commit, files=files)
    assert out.stage == "rejected"
    assert _git(repo, "rev-parse", "argus-evolve") == before


# --- SelfEvolveSink --------------------------------------------------------

class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def handle_event(self, e: dict) -> None:
        self.events.append(e)

    def handle_stream_line(self, s: str, line: str) -> None: ...

    def close(self) -> None: ...


def test_evolve_sink_lands_approved_to_argus_evolve_branch(repo: Path) -> None:
    from argus_skill.life.self_evolve import SelfEvolveSink

    _git(repo, "branch", "argus-evolve", "main")            # target branch exists
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 5\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 5\n",
    })
    down = _RecordingSink()
    sink = SelfEvolveSink(
        down, runner=_FakeRunner('{"approve": true, "risk": "low", "reason": "ok"}'),
        repo_root=repo, target_branch="argus-evolve", remote=None, pr_base=None,
    )
    before_main = _git(repo, "rev-parse", "main")
    sink.handle_event({"type": "self_repair.captured", "commit": commit, "files": files})

    types = [e.get("type") for e in down.events]
    assert "self_repair.captured" in types                  # forwarded unchanged
    assert "self_evolve.landed" in types                    # outcome emitted
    # argus-evolve advanced as argus; main (shared) NOT touched.
    assert _git(repo, "log", "-1", "--format=%an", "argus-evolve") == ARGUS_AUTHOR_NAME
    assert _git(repo, "rev-parse", "main") == before_main


def test_evolve_sink_rejects_leave_branch_untouched(repo: Path) -> None:
    from argus_skill.life.self_evolve import SelfEvolveSink

    _git(repo, "branch", "argus-evolve", "main")
    commit, files = _capture_commit(repo, edits={
        "argus_skill/m.py": "def f():\n    return 5\n",
        "tests/test_m.py": "from argus_skill.m import f\n\ndef test_f():\n    assert f() == 5\n",
    })
    before = _git(repo, "rev-parse", "argus-evolve")
    down = _RecordingSink()
    sink = SelfEvolveSink(
        down, runner=_FakeRunner('{"approve": false, "risk": "high", "reason": "no"}'),
        repo_root=repo, target_branch="argus-evolve", remote=None, pr_base=None,
    )
    sink.handle_event({"type": "self_repair.captured", "commit": commit, "files": files})
    assert "self_evolve.rejected" in [e.get("type") for e in down.events]
    assert _git(repo, "rev-parse", "argus-evolve") == before   # not advanced


# --- gated direct-to-main push ---------------------------------------------

def test_push_to_main_fast_forwards_remote(repo: Path, tmp_path: Path) -> None:
    from argus_skill.life.self_evolve import push_commit_to_remote_main

    _git(repo, "branch", "argus-evolve", "main")
    bare = tmp_path / "prod.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], capture_output=True)
    _git(repo, "remote", "add", "prod", str(bare))
    _git(repo, "push", "prod", "main")
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 42\n"})
    land = land_self_repair(repo, source_commit=commit, files=files,
                            target_branch="argus-evolve", message="argus: land")
    ok, where = push_commit_to_remote_main(repo, commit=land.commit, remote="prod", main_ref="main")
    assert ok and where == "prod/main"
    # remote main now carries argus's approved commit, authored by argus
    assert _git(bare, "log", "-1", "--format=%an", "main") == ARGUS_AUTHOR_NAME
    assert "return 42" in _git(bare, "show", "main:argus_skill/m.py")


def test_push_to_main_refuses_diverged_remote(repo: Path, tmp_path: Path) -> None:
    from argus_skill.life.self_evolve import push_commit_to_remote_main

    _git(repo, "branch", "argus-evolve", "main")
    bare = tmp_path / "prod.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], capture_output=True)
    _git(repo, "remote", "add", "prod", str(bare))
    _git(repo, "push", "prod", "main")
    # Another operator advances remote main so it diverges from our base.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], capture_output=True)
    _git(other, "config", "user.email", "lsk@t")
    _git(other, "config", "user.name", "lsk")
    (other / "unrelated.txt").write_text("lsk work\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-m", "lsk change")
    _git(other, "push", "origin", "main")
    # argus's push must be REFUSED (non-ff), never clobbering lsk's commit.
    commit, files = _capture_commit(repo, edits={"argus_skill/m.py": "def f():\n    return 7\n"})
    land = land_self_repair(repo, source_commit=commit, files=files,
                            target_branch="argus-evolve", message="argus: land")
    ok, msg = push_commit_to_remote_main(repo, commit=land.commit, remote="prod", main_ref="main")
    assert not ok                                            # refused, not forced
    assert _git(bare, "log", "-1", "--format=%an", "main") == "lsk"   # lsk's commit intact

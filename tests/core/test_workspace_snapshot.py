"""HO-7a: workspace snapshots live in Argus's own repository, never the user's."""
from __future__ import annotations

import subprocess
from pathlib import Path

from argus.core.file_facts import file_facts
from argus.core.workspace_snapshot import LARGE_FILE_BYTES, Change, WorkspaceSnapshots

_GIT = ["git", "-c", "user.name=u", "-c", "user.email=u@example.invalid"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        [*_GIT, *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout


def _write(root: Path, relative: str, text: str = "x\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _snapshots(tmp_path: Path, workdir: Path) -> WorkspaceSnapshots:
    return WorkspaceSnapshots(tmp_path / "state" / "snapshots.git", workdir, "node:train/eval")


def _stored(snapshots: WorkspaceSnapshots, label: str) -> list[str]:
    ref = f"refs/argus/node_train_eval/{label}"
    return _git(snapshots.store, "--git-dir", str(snapshots.store), "ls-tree", "-r", "--name-only", ref).split()


def test_plain_directory_diffs_text_and_lists_binary_and_large_files(tmp_path):
    work = tmp_path / "work"
    _write(work, "train.py", "lr = 0.1\nepochs = 3\n")
    _write(work, "notes.md", "draft\n")
    _write(work, "logs/run.txt", "step 1\n")
    big = work / "data" / "big.bin"
    big.parent.mkdir()
    big.write_bytes(b"0" * (LARGE_FILE_BYTES + 1))
    snapshots = _snapshots(tmp_path, work)
    assert snapshots.take("baseline")

    _write(work, "train.py", "lr = 0.01\nepochs = 3\n")
    (work / "notes.md").unlink()
    (work / "logs" / "run.txt").unlink()
    (work / "logs").rmdir()
    _write(work, "logs", "a file now\n")
    _write(work, "results/eval.json", '{"acc": 0.93}\n')
    (work / "plot.png").write_bytes(b"\x89PNG\0\0")
    big.write_bytes(b"1" * (LARGE_FILE_BYTES + 5))
    assert snapshots.take("candidate")

    assert snapshots.changes("baseline", "candidate") == [
        Change("data/big.bin", sizes=(LARGE_FILE_BYTES + 1, LARGE_FILE_BYTES + 5)),
        Change("logs", 1, 0),
        Change("logs/run.txt", 0, 1),
        Change("notes.md", 0, 1),
        Change("plot.png"),
        Change("results/eval.json", 1, 0),
        Change("train.py", 1, 1),
    ]
    assert snapshots.hunks("baseline", "candidate", ["train.py"]) == [
        ["@@ -1,2 +1,2 @@", "-lr = 0.1", "+lr = 0.01", " epochs = 3"],
    ]
    # A large file is recorded by its facts alone; its content is not stored.
    assert "data/big.bin" not in _stored(snapshots, "candidate")


def test_environments_caches_argus_state_and_ignored_files_are_left_out(tmp_path):
    work = tmp_path / "work"
    _write(work, "main.py")
    _write(work, ".gitignore", "outputs/\n")
    snapshots = _snapshots(tmp_path, work)
    assert snapshots.take("baseline")
    for relative in (
        ".argus/runs/r1/log.txt", ".venv/lib/site.py", "venv/bin/python",
        "node_modules/pkg/index.js", "src/__pycache__/m.pyc", ".pytest_cache/v",
        "pkg.egg-info/PKG-INFO", "outputs/model.ckpt",
    ):
        _write(work, relative)
    _write(work, "src/model.py", "class Model: ...\n")
    assert snapshots.take("candidate")

    assert [change.path for change in snapshots.changes("baseline", "candidate")] == ["src/model.py"]


def test_a_work_tree_holding_the_repository_never_records_it(tmp_path):
    _write(tmp_path, "main.py")
    snapshots = _snapshots(tmp_path, tmp_path)
    assert snapshots.take("baseline")
    _write(tmp_path, "src/model.py")
    assert snapshots.take("candidate")

    assert _stored(snapshots, "candidate") == ["main.py", "src/model.py"]
    assert [change.path for change in snapshots.changes("baseline", "candidate")] == ["src/model.py"]


def test_nested_repositories_are_their_own(tmp_path):
    work = tmp_path / "work"
    _write(work, "main.py")
    vendored = work / "third_party" / "lib"
    _write(vendored, "lib.py")
    _git(vendored, "init", "-q")
    _git(vendored, "add", "lib.py")
    _git(vendored, "commit", "-qm", "lib")
    empty = work / "scratch"
    empty.mkdir()
    _git(empty, "init", "-q")
    _write(empty, "wip.py")
    snapshots = _snapshots(tmp_path, work)
    assert snapshots.take("baseline")
    assert _stored(snapshots, "baseline") == ["main.py"]

    _write(vendored, "lib.py", "changed\n")
    _write(empty, "wip.py", "changed\n")
    assert snapshots.take("candidate")
    assert snapshots.changes("baseline", "candidate") == []


def test_the_users_repository_head_index_refs_and_tree_are_untouched(tmp_path):
    work = tmp_path / "repo"
    _write(work, "a.py", "a = 1\n")
    _write(work, ".gitignore", "build/\n")
    _git(work, "init", "-q")
    _git(work, "add", "-A")
    _git(work, "commit", "-qm", "init")
    _write(work, "a.py", "a = 2\n")
    _git(work, "add", "a.py")
    _write(work, "b.py", "b = 1\n")
    _write(work, "build/out.o")

    def observed():
        return (
            _git(work, "rev-parse", "HEAD"),
            file_facts(work / ".git" / "index"),
            _git(work, "for-each-ref"),
            # Observing must not write the index itself, as a plain status may.
            _git(work, "--no-optional-locks", "status", "--porcelain=v1", "--untracked-files=all"),
            sorted(str(path.relative_to(work)) for path in work.rglob("*") if ".git" not in path.parts),
        )

    before = observed()
    snapshots = _snapshots(tmp_path, work)
    assert snapshots.take("baseline")
    _write(work, "b.py", "b = 2\n")
    assert snapshots.take("candidate")

    after = observed()
    assert after == before
    assert "refs/argus" not in after[2]
    assert snapshots.changes("baseline", "candidate") == [Change("b.py", 1, 1)]
    assert _stored(snapshots, "candidate") == [".gitignore", "a.py", "b.py"]


def test_a_kept_baseline_stays_and_a_broken_store_reports_failure(tmp_path):
    work = tmp_path / "work"
    _write(work, "a.py", "1\n")
    snapshots = _snapshots(tmp_path, work)
    assert snapshots.take("baseline", keep=True)
    _write(work, "a.py", "2\n")
    assert snapshots.take("baseline", keep=True)
    assert snapshots.take("candidate")
    assert snapshots.changes("baseline", "candidate") == [Change("a.py", 1, 1)]
    assert snapshots.changes("baseline", "missing") is None

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")
    broken = WorkspaceSnapshots(blocked, work, "line")
    assert broken.take("baseline") is False
    assert broken.changes("baseline", "candidate") is None


def test_a_git_stopped_mid_write_does_not_block_the_next_snapshot(tmp_path):
    work = tmp_path / "work"
    _write(work, "train.py")
    snapshots = _snapshots(tmp_path, work)
    assert snapshots.take("baseline")
    lock = snapshots.store / "indexes" / "node_train_eval.lock"
    lock.write_text("")  # what a git killed by the timeout leaves behind

    assert not snapshots.take("candidate")
    assert not lock.exists()
    assert snapshots.take("candidate")

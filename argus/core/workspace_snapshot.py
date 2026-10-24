"""The workspace as commits in Argus's own git repository, so two states diff.

The repository lives in the project's state directory and only borrows the
workspace as its work tree: the user's repository, index and refs are never
read or written. What :mod:`.workspace_facts` leaves out is left out here too,
as are nested repositories and whatever the workspace's ignore files exclude.
A file over ``LARGE_FILE_BYTES`` is not stored; its ``file_facts`` ride in the
commit message, so a change to it still shows, by size.

Each holder of the line keeps the snapshot it last held the work as, with the
outside inputs (the operator's and the Manager's) it held it under and the
verdict it left, so a return of unchanged work is a fact read from the store,
not a judgment.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .atomic_replace import replace
from .file_facts import file_facts
from .workspace_facts import IGNORED_NAMES, fact_text

LARGE_FILE_BYTES = 1 << 20
# A git that has not finished by then (a stale lock, a hung mount, a first
# snapshot of a huge tree) leaves the review without a diff, never waiting.
GIT_TIMEOUT_SECONDS = 60.0
_EXCLUDES = "".join(f"{name}\n" for name in sorted({*IGNORED_NAMES, "*.egg-info"}))
_IDENTITY = {
    "GIT_AUTHOR_NAME": "Argus",
    "GIT_AUTHOR_EMAIL": "argus@localhost",
    "GIT_COMMITTER_NAME": "Argus",
    "GIT_COMMITTER_EMAIL": "argus@localhost",
}


@dataclass(frozen=True)
class Change:
    """One path that differs between two snapshots."""

    path: str
    #: Changed lines; ``None`` when git cannot count them (a binary file).
    added: int | None = None
    deleted: int | None = None
    #: Bytes before and after (``None``: absent) of a file too large to store.
    sizes: tuple[int | None, int | None] | None = None


@dataclass(frozen=True)
class WorkspaceSnapshots:
    """Snapshots of ``workdir`` for one line of work, kept in the repository ``store``."""

    store: Path
    workdir: Path
    line: str

    @property
    def _name(self) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", self.line)

    def _ref(self, label: str) -> str:
        return f"refs/argus/{self._name}/{label}"

    def _git(self, *args: str, stdin: bytes = b"", check: bool = True) -> bytes:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(
            _IDENTITY,
            GIT_DIR=str(self.store),
            # One index per line of work: its stat cache is for one work tree.
            GIT_INDEX_FILE=str(self.store / "indexes" / self._name),
            GIT_LITERAL_PATHSPECS="1",
        )
        if args[0] != "init":  # a repository is created without a work tree
            env["GIT_WORK_TREE"] = str(self.workdir)
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "commit.gpgSign=false", *args],
            input=stdin, env=env, capture_output=True, check=check, timeout=GIT_TIMEOUT_SECONDS,
        ).stdout

    def _paths(self, *args: str) -> list[str]:
        out = self._git("ls-files", "-z", "--exclude-standard", *args)
        return [os.fsdecode(path) for path in out.split(b"\0") if path]

    def _update(self, *args: str, paths: list[str]) -> None:
        self._git("update-index", "-z", *args, "--stdin",
                  stdin=b"\0".join(os.fsencode(path) for path in paths))

    def take(self, label: str, *, keep: bool = False) -> bool:
        """Record the workspace as ``label``; with ``keep``, a recorded one stays."""
        ref = self._ref(label)
        try:
            if not (self.store / "HEAD").exists():
                self.store.mkdir(parents=True, exist_ok=True)
                self._git("init", "-q", "--bare", "--template=")
            elif keep and self._git("rev-parse", "-q", "--verify", ref, check=False):
                return True
            exclude = self.store / "info" / "exclude"
            exclude.parent.mkdir(exist_ok=True)
            if not exclude.is_file() or exclude.read_text(encoding="utf-8") != _EXCLUDES:
                exclude.write_text(_EXCLUDES, encoding="utf-8")
            (self.store / "indexes").mkdir(exist_ok=True)
            # Stored before, excluded now (a new ignore rule): no longer the work.
            dropped = self._paths("--cached", "--ignored")
            stored, large, skip = [], {}, set(dropped)
            # A workspace that holds the state holds this repository: never record it.
            store, work = self.store.resolve(), self.workdir.resolve()
            own = store.relative_to(work).as_posix() + "/" if store.is_relative_to(work) else None
            for path in self._paths("--cached", "--others"):
                # A trailing slash marks a nested repository: its files are its own.
                if path in skip or path.endswith("/"):
                    continue
                if own and path.startswith(own):
                    dropped.append(path)
                    continue
                full = self.workdir / path
                try:
                    mode = os.lstat(full)
                except (FileNotFoundError, NotADirectoryError):
                    stored.append(path)  # gone: the update removes it
                    continue
                regular = stat.S_ISREG(mode.st_mode)
                if regular and mode.st_size > LARGE_FILE_BYTES:
                    large[path] = file_facts(full)
                elif stat.S_ISLNK(mode.st_mode) or (regular and os.access(full, os.R_OK)):
                    stored.append(path)
                    continue
                dropped.append(path)
            self._update("--force-remove", paths=dropped)
            self._update("--add", "--remove", "--replace", paths=stored)
            tree = self._git("write-tree").decode().strip()
            commit = self._git("commit-tree", tree, stdin=fact_text(large).encode())
            self._git("update-ref", ref, commit.decode().strip())
            self._git("gc", "--auto", "--quiet", check=False)
            return True
        except (OSError, subprocess.SubprocessError):
            # A git stopped mid-write leaves its index lock; the next try needs it gone.
            with contextlib.suppress(OSError):
                (self.store / "indexes" / f"{self._name}.lock").unlink(missing_ok=True)
            return False

    def _large(self, label: str) -> dict[str, list[int]]:
        return self._facts(self._ref(label))

    def _facts(self, ref: str) -> dict[str, list[int]]:
        commit = self._git("cat-file", "commit", ref).decode("utf-8", "replace")
        return json.loads(commit.split("\n\n", 1)[1])

    @property
    def _holders_path(self) -> Path:
        return self.store / "holders" / f"{self._name}.json"

    def _holders(self) -> dict[str, dict]:
        try:
            holders = json.loads(self._holders_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(holders, dict):
            return {}
        return {holder: held for holder, held in holders.items() if isinstance(held, dict)}

    def hold(self, holder: str, label: str, inputs: object, verdict: dict | None = None) -> None:
        """Record that ``holder`` held this line of work as snapshot ``label``, given ``inputs``, leaving ``verdict``."""
        try:
            commit = self._git("rev-parse", "-q", "--verify", self._ref(label)).decode().strip()
            self._git("update-ref", self._ref(f"held-{holder}"), commit)
            self._holders_path.parent.mkdir(exist_ok=True)
            pending = self._holders_path.with_suffix(".tmp")
            held = {holder: {"inputs": inputs, "verdict": verdict}}
            pending.write_text(json.dumps({**self._holders(), **held}), encoding="utf-8")
            replace(pending, self._holders_path)
        except (OSError, subprocess.SubprocessError):
            return

    def held_unchanged(self, holder: str, label: str, inputs: object) -> bool:
        """Whether ``holder`` already held this line as snapshot ``label`` is, given the same ``inputs``."""
        return inputs is not None and self._holders().get(holder, {}).get("inputs") == inputs and (
            self.changes(f"held-{holder}", label) == []
        )

    def held_verdict(self, holder: str) -> dict | None:
        """The verdict ``holder`` left when it last held this line, if any."""
        verdict = self._holders().get(holder, {}).get("verdict")
        return verdict if isinstance(verdict, dict) else None

    def held_elsewhere(self, holder: str, label: str, inputs: object) -> list[dict]:
        """What ``holder`` left on each other line that held the workspace as snapshot ``label`` is, given ``inputs``.

        The lines share this store, so the same files are the same tree and
        the same large-file facts whichever line recorded them. A line held
        under other ``inputs`` (something new arrived since) does not count.
        """
        if inputs is None:
            return []
        try:
            out = self._git("for-each-ref", "--format=%(refname) %(tree)", "refs/argus/").decode()
            trees = dict(row.split(" ", 1) for row in out.splitlines() if " " in row)
            mine = trees.get(self._ref(label))
            if mine is None:
                return []
            facts, found = self._facts(self._ref(label)), []
            for path in (self.store / "holders").glob("*.json"):
                held = json.loads(path.read_text(encoding="utf-8")).get(holder)
                ref = f"refs/argus/{path.stem}/held-{holder}"
                if (
                    path.stem != self._name and isinstance(held, dict) and held.get("inputs") == inputs
                    and trees.get(ref) == mine and self._facts(ref) == facts
                ):
                    found.append(held)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError, AttributeError):
            return []
        return found

    def changes(self, base: str, head: str) -> list[Change] | None:
        """Every path that differs from ``base`` to ``head``, by path; ``None`` if unknown."""
        try:
            out = self._git("diff", "--no-renames", "--numstat", "-z", self._ref(base), self._ref(head))
            before, after = self._large(base), self._large(head)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None
        found = []
        for row in out.decode("utf-8", "replace").split("\0"):
            if row:
                added, deleted, path = row.split("\t", 2)
                binary = added == "-"
                found.append(Change(path, None if binary else int(added), None if binary else int(deleted)))
        for path in sorted({*before, *after}):
            if before.get(path) != after.get(path):
                found.append(Change(path, sizes=(
                    before[path][0] if path in before else None,
                    after[path][0] if path in after else None,
                )))
        return sorted(found, key=lambda change: change.path)

    def hunks(self, base: str, head: str, paths: list[str]) -> list[list[str]]:
        """The diff hunks of each of ``paths``, in order; ``[]`` if unavailable."""
        if not paths:
            return []
        try:
            out = self._git(
                "diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color",
                self._ref(base), self._ref(head), "--", *paths,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        excerpts = []
        for text in out.decode("utf-8", "replace").split("\ndiff --git "):
            lines = text.splitlines()
            start = next((i for i, line in enumerate(lines) if line.startswith("@@")), len(lines))
            excerpts.append(lines[start:])
        return excerpts if len(excerpts) == len(paths) else []


__all__ = ["Change", "LARGE_FILE_BYTES", "WorkspaceSnapshots"]

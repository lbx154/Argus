"""Content binding for staged source; never changes the caller's index or refs."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

import portalocker

REPORT_NAME = "pr-regression-report.json"


class GateError(RuntimeError):
    pass


class CleanupError(GateError):
    """Owned subprocesses did not settle; keep their workspace for diagnosis."""


def git(
    root: Path, *args: str, env: dict[str, str] | None = None, input: bytes | None = None,
) -> bytes:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "file",
    })
    environment.update(env or {})
    prefix = [
        "git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
        "-C", str(root),
    ]
    if args[0] == "diff":
        config = subprocess.run(
            [*prefix, "config", "--includes", "--null", "--name-only", "--list"],
            capture_output=True, env=environment, timeout=30,
        )
        if config.returncode:
            raise GateError("Cannot safely inspect Git filter configuration.")
        keys = config.stdout.decode("utf-8", "replace").lower().split("\0")
        if any(re.fullmatch(r"filter\..+\.(clean|process)", key) for key in keys):
            raise GateError("Configured Git clean/process filters are unsupported; verify must not execute them.")
        if any(key == "extensions.partialclone" or re.fullmatch(r"remote\..+\.promisor", key) for key in keys):
            raise GateError("Partial/promisor clones are unsupported; verification cannot fetch missing objects.")
    result = subprocess.run(
        [*prefix, *args],
        input=input, capture_output=True, env=environment, timeout=180,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GateError(f"git {args[0]} failed: {detail[:1000]}")
    return result.stdout


def resolve_commit(root: Path, ref: str) -> str:
    return git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


def repository(path: Path) -> Path:
    return Path(git(path.resolve(), "rev-parse", "--show-toplevel").decode().strip())


def default_base(root: Path) -> str:
    candidates = ("refs/remotes/origin/main", "refs/heads/main")
    existing = set(git(root, "for-each-ref", "--format=%(refname)", *candidates).decode().splitlines())
    for ref in candidates:
        if ref in existing:
            resolve_commit(root, ref)
            return ref
    raise GateError("No local origin/main or main reference; supply --base <commit-or-ref>.")


def require_staged_source(root: Path) -> None:
    dirty = git(root, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z",
                "--", ".", f":(exclude,literal){REPORT_NAME}")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    pending = [
        path.decode("utf-8", "replace")
        for path in (*dirty.split(b"\0"), *untracked.split(b"\0"))
        if path and path != REPORT_NAME.encode()
    ]
    if pending:
        raise GateError(
            "Stage the intended source first (git add); unstaged/untracked source: "
            + ", ".join(sorted(set(pending))[:8])
        )
    report = root / REPORT_NAME
    if report.is_symlink() or (report.exists() and not report.is_file()):
        raise GateError(f"{REPORT_NAME} must be a regular generated file, not a symlink/directory.")


@contextlib.contextmanager
def private_index(root: Path) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="pr-gate-index-") as temporary:
        target = Path(temporary) / "index"
        raw_path = Path(git(root, "rev-parse", "--git-path", "index").decode().strip())
        source = raw_path if raw_path.is_absolute() else root / raw_path
        if source.exists():
            shutil.copyfile(source, target)
        environment = {"GIT_INDEX_FILE": str(target)}
        if not target.exists():
            git(root, "read-tree", "HEAD", env=environment)
        yield environment


def filtered_tree(root: Path, env: dict[str, str]) -> str:
    git(root, "update-index", "--force-remove", "--", REPORT_NAME, env=env)
    tree = git(root, "write-tree", env=env).decode().strip()
    entries = git(root, "ls-tree", "-r", "-z", tree)
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        if metadata.startswith(b"160000 "):
            raise GateError("Submodules are not supported by this prototype: " + path.decode("utf-8", "replace"))
        if path.startswith(REPORT_NAME.encode() + b"/"):
            raise GateError(f"{REPORT_NAME} is reserved for one report file.")
    return tree


@dataclass(frozen=True)
class Snapshot:
    repository: Path
    base_ref: str
    target_base_sha: str
    merge_base_sha: str
    head_sha: str
    base_tree: str
    candidate_tree: str

    def binding(self) -> dict[str, object]:
        values = asdict(self)
        values.pop("repository")
        values.pop("head_sha")  # Committing an unchanged staged tree must not invalidate evidence.
        values.pop("base_ref")  # Ref spelling is not code identity.
        return {
            **values,
            "comparison": "merge_base_to_staged_source",
            "excluded_paths": [REPORT_NAME],
        }


def capture(path: Path, base_ref: str | None = None) -> Snapshot:
    root = repository(path)
    if git(root, "rev-parse", "--show-object-format").strip() != b"sha1":
        raise GateError("This prototype currently requires a SHA-1 Git repository.")
    require_staged_source(root)
    ref = base_ref or default_base(root)
    target = resolve_commit(root, ref)
    head = resolve_commit(root, "HEAD")
    merge_base = git(root, "merge-base", target, head).decode().strip()
    with private_index(root) as environment:
        candidate_tree = filtered_tree(root, environment)
        git(root, "read-tree", merge_base, env=environment)
        base_tree = filtered_tree(root, environment)
    require_staged_source(root)
    if resolve_commit(root, "HEAD") != head or resolve_commit(root, ref) != target:
        raise GateError("HEAD or the base reference changed while capturing source.")
    with private_index(root) as environment:
        if filtered_tree(root, environment) != candidate_tree:
            raise GateError("The staged source changed while capturing its snapshot.")
    return Snapshot(root, ref, target, merge_base, head, base_tree, candidate_tree)


def materialize(snapshot: Snapshot, run_dir: Path) -> dict[str, str]:
    mirror = run_dir / "repository.git"
    # Local --no-hardlinks copies even the newly written, unreferenced tree objects.
    git(run_dir, "clone", "--quiet", "--bare", "--no-hardlinks",
        str(snapshot.repository), str(mirror))
    identity = {
        "GIT_AUTHOR_NAME": "Local PR gate", "GIT_AUTHOR_EMAIL": "pr-gate@localhost",
        "GIT_COMMITTER_NAME": "Local PR gate", "GIT_COMMITTER_EMAIL": "pr-gate@localhost",
    }
    base = git(mirror, "commit-tree", snapshot.base_tree, input=b"Filtered base snapshot\n",
               env=identity).decode().strip()
    candidate = git(
        mirror, "commit-tree", snapshot.candidate_tree, "-p", base,
        input=b"Staged source snapshot\n", env=identity,
    ).decode().strip()
    return {"base_sha": base, "candidate_sha": candidate}


def source_inventory(snapshot: Snapshot) -> dict[str, dict[str, dict[str, str]]]:
    result = {}
    for side, tree in (("base", snapshot.base_tree), ("candidate", snapshot.candidate_tree)):
        files = {}
        for row in git(snapshot.repository, "ls-tree", "-r", "-z", tree).split(b"\0"):
            if row:
                header, path = row.split(b"\t", 1)
                mode, _kind, oid = header.decode().split()
                files[path.decode()] = {"mode": mode, "oid": oid}
        result[side] = files
    return result


@contextlib.contextmanager
def publication_lock(root: Path) -> Iterator[None]:
    raw = Path(git(root, "rev-parse", "--git-path", "pr-gate.publish.lock").decode().strip())
    path = raw if raw.is_absolute() else root / raw
    if path.is_symlink():
        raise GateError("Publication lock must not be a symlink.")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(str(path), mode="a", timeout=10):
            yield
    except portalocker.exceptions.LockException as exc:
        raise GateError("Another gate is publishing a report; retry after it finishes.") from exc

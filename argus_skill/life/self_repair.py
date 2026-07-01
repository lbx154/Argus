"""Self-repair capture (自修复捕获): when argus edits its OWN source during a
mission, snapshot that change as a reviewable commit on a dedicated review
branch — never a silently-floating mutation, never auto-merged to main.

EN: argus is editable-installed, so a self-hosted mission's engineer can edit the
running ``argus_skill`` package — a genuine CODE-LEVEL self-improvement (e.g.
fixing a harness bug it hit and diagnosed). Left as an uncommitted working-tree
change that is easy to lose, hard to audit, and mixed with unrelated work. This
module captures ONLY the ``argus_skill/`` + ``tests/`` files a mission NEWLY
touched (a boot baseline is excluded, so pre-existing operator WIP is never swept
up) into ONE commit per capture on ``argus-self-repair/<session>``, based on HEAD.
It does NOT touch the working tree, ``main``, untracked files outside the package,
or ``.gitignore``-d artifacts. Fail-soft everywhere: a capture bug must NEVER
break a mission.

中文：argus 是 editable 安装,自托管 mission 的 engineer 可能改到正在运行的
``argus_skill`` 源码——这是真正的代码级自我改进(如修它撞到并诊断出的 harness bug)。
若留作未提交的工作树改动,易丢、难审、且和无关改动混在一起。本模块只把某次 mission
新改动的 ``argus_skill/`` + ``tests/`` 文件(排除启动基线,故绝不卷入既有 operator
WIP)捕获成 ``argus-self-repair/<session>`` 分支上的一个待审 commit(基于 HEAD),不动
工作树、不动 ``main``、不碰包外 untracked、不碰 ``.gitignore`` 文件。全程失败即静默,
绝不弄坏 mission。
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Only files under these top-level paths are ever eligible for capture. This is
# the hard containment boundary: the daemon's operator WIP (benchmarks/, data/,
# docs/ …) and any path outside the package/tests are NEVER touched.
# 仅这些顶层路径下的文件可被捕获——这是硬边界:包/测试之外的一切绝不触碰。
_SELF_SUBPATHS: tuple[str, ...] = ("argus_skill", "tests")

_BRANCH_PREFIX = "argus-self-repair"


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of one self-repair capture."""

    branch: str
    commit: str          # sha of the new review commit
    parent: str          # parent sha (branch tip or HEAD)
    files: tuple[str, ...]


def _git(repo_root: Path, *args: str, env_index: str | None = None) -> tuple[int, str, str]:
    """Run a git command in ``repo_root``. Returns (rc, stdout, stderr), fail-soft."""
    import os

    env = None
    if env_index is not None:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = env_index
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, env=env, timeout=60,
        )
        # rstrip newline ONLY — never .strip(): porcelain status lines carry a
        # LEADING status column (e.g. " M path") that a full strip would corrupt.
        return proc.returncode, proc.stdout.rstrip("\n"), proc.stderr.strip()
    except Exception as exc:  # noqa: BLE001 — git must never crash the caller
        log.debug("self_repair git %s failed: %s", args, exc)
        return 1, "", str(exc)


def self_source_repo_root(pkg_file: str | None = None) -> Path | None:
    """The git repo backing the RUNNING ``argus_skill`` package, or None.

    EN: resolves ``git rev-parse --show-toplevel`` from the installed package dir,
    so self-modifications to the code that is actually executing are what we watch
    (editable install → the repo; a wheel install → not a repo → None → disabled).
    中文：从已安装的包目录解析仓库根,监视的正是"真正在跑的代码"的自我改动;非 git
    安装(wheel)返回 None → 功能自动关闭。
    """
    try:
        if pkg_file is None:
            import argus_skill

            pkg_file = argus_skill.__file__
        start = Path(pkg_file).resolve().parent
    except Exception:  # noqa: BLE001
        return None
    rc, out, _ = _git(start, "rev-parse", "--show-toplevel")
    if rc != 0 or not out:
        return None
    root = Path(out)
    return root if root.exists() else None


def dirty_self_source(repo_root: Path, *, subpaths: tuple[str, ...] = _SELF_SUBPATHS) -> set[str]:
    """Repo-relative paths under ``subpaths`` that are modified or newly added.

    Uses ``git status --porcelain`` so it honours ``.gitignore`` (no __pycache__,
    no build artifacts) and covers both tracked edits and new source files. Deleted
    paths are ignored (a self-repair adds/edits code; a mission deleting package
    files is out of scope and left for a human).
    """
    rc, out, _ = _git(
        repo_root, "status", "--porcelain", "--untracked-files=all", "--", *subpaths
    )
    if rc != 0 or not out:
        return set()
    files: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if code.strip() == "D" or code == " D":
            continue  # deletions are out of scope for v1
        # Rename form "old -> new": keep the new path.
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        # Strip optional quoting git applies to unusual paths.
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        if any(path == sp or path.startswith(sp + "/") for sp in subpaths):
            files.add(path)
    return files


def capture_self_repair(
    repo_root: Path,
    *,
    files: set[str] | list[str],
    session_label: str,
    message: str,
    subpaths: tuple[str, ...] = _SELF_SUBPATHS,
) -> CaptureResult | None:
    """Commit the working-tree version of ``files`` onto the session review branch.

    Builds the commit with a scratch index (never disturbs the real index or the
    working tree), parented on the branch tip if it exists else HEAD, so repeated
    captures over a run accumulate as an auditable history on one branch. Returns
    None (no-op) when there is nothing eligible or on any error. Never raises.
    用临时 index 构建 commit(不动真 index/工作树),父提交为分支 tip 或 HEAD,故一次
    运行内多次捕获在同一分支上累积成可审计历史。无可捕获或出错时返回 None,绝不抛异常。
    """
    import os
    import tempfile

    # Containment: only ever commit paths under the allowed subpaths.
    safe = sorted(
        f for f in files
        if any(f == sp or f.startswith(sp + "/") for sp in subpaths)
    )
    if not safe:
        return None
    branch = f"{_BRANCH_PREFIX}/{session_label}"
    try:
        # Parent = existing review-branch tip, else HEAD.
        rc, tip, _ = _git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if rc == 0 and tip:
            parent = tip
        else:
            rc, head, _ = _git(repo_root, "rev-parse", "HEAD")
            if rc != 0 or not head:
                return None
            parent = head

        idx_fd, idx_path = tempfile.mkstemp(prefix="argus-self-repair-idx-")
        os.close(idx_fd)
        try:
            rc, _, err = _git(repo_root, "read-tree", parent, env_index=idx_path)
            if rc != 0:
                log.debug("self_repair read-tree failed: %s", err)
                return None
            rc, _, err = _git(repo_root, "add", "--", *safe, env_index=idx_path)
            if rc != 0:
                log.debug("self_repair add failed: %s", err)
                return None
            rc, tree, err = _git(repo_root, "write-tree", env_index=idx_path)
            if rc != 0 or not tree:
                log.debug("self_repair write-tree failed: %s", err)
                return None
        finally:
            try:
                os.unlink(idx_path)
            except OSError:
                pass

        # A no-op capture (tree identical to parent) is not worth a commit.
        rc, parent_tree, _ = _git(repo_root, "rev-parse", f"{parent}^{{tree}}")
        if rc == 0 and parent_tree == tree:
            return None

        rc, commit, err = _git(
            repo_root, "commit-tree", tree, "-p", parent, "-m", message
        )
        if rc != 0 or not commit:
            log.debug("self_repair commit-tree failed: %s", err)
            return None
        rc, _, err = _git(repo_root, "update-ref", f"refs/heads/{branch}", commit)
        if rc != 0:
            log.debug("self_repair update-ref failed: %s", err)
            return None
        return CaptureResult(
            branch=branch, commit=commit, parent=parent, files=tuple(safe)
        )
    except Exception as exc:  # noqa: BLE001 — capture must never break a mission
        log.debug("self_repair capture raised: %s", exc)
        return None


def capture_if_self_modified(
    *,
    session_label: str,
    baseline: set[str],
    message: str,
    repo_root: Path | None = None,
) -> CaptureResult | None:
    """Convenience: capture the self-source files newly dirtied since ``baseline``.

    ``baseline`` is the set from :func:`dirty_self_source` taken at daemon boot, so
    pre-existing operator WIP under the package is excluded. No-op / None when the
    package is not a git checkout, nothing new was touched, or on any error.
    """
    root = repo_root or self_source_repo_root()
    if root is None:
        return None
    now_dirty = dirty_self_source(root)
    newly = now_dirty - set(baseline or set())
    if not newly:
        return None
    return capture_self_repair(
        root, files=newly, session_label=session_label, message=message
    )


class SelfRepairSink:
    """Event-sink observer that snapshots self-source edits at mission boundaries.

    Wraps a downstream ``EventSink``. On every ``life.mission.completed`` it snapshots
    any argus_skill/tests files the run has NEWLY touched (vs the boot baseline) onto
    the review branch, and emits a ``self_repair.captured`` event downstream so the
    capture shows up in events.jsonl / the cockpit. Every event is forwarded
    downstream unchanged and in order; the capture is best-effort and never raises.
    在每个 mission 完成边界把 run 新改动的 自身源码 快照到 review 分支,并向下游发
    ``self_repair.captured`` 事件。所有事件原样、按序转发;捕获尽力而为,绝不抛异常。
    """

    def __init__(self, downstream, *, repo_root: Path, baseline: set[str], session_label: str) -> None:
        self.downstream = downstream
        self.repo_root = repo_root
        self.baseline = set(baseline)
        self.session_label = session_label

    @classmethod
    def build(cls, downstream, *, session_label: str):
        """Return a wrapped sink, or the downstream UNCHANGED when the running
        package is not a git checkout (wheel install) so the feature is inert."""
        root = self_source_repo_root()
        if root is None:
            return downstream
        return cls(
            downstream,
            repo_root=root,
            baseline=dirty_self_source(root),
            session_label=session_label,
        )

    def handle_event(self, event) -> None:
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("self_repair: downstream handle_event raised; continuing")
        try:
            if isinstance(event, dict) and event.get("type") == "life.mission.completed":
                self._maybe_capture()
        except Exception:  # noqa: BLE001 — capture must never break the daemon
            log.debug("self_repair: capture at mission boundary failed", exc_info=True)

    def _maybe_capture(self) -> None:
        message = (
            "self-repair: argus edited its own source during a mission "
            f"(session {self.session_label}). Auto-captured for human review; "
            "not merged. 自修复:argus 在 mission 中改了自身源码,已捕获待审、未合并。"
        )
        res = capture_if_self_modified(
            session_label=self.session_label,
            baseline=self.baseline,
            message=message,
            repo_root=self.repo_root,
        )
        if res is None:
            return
        self.downstream.handle_event({
            "type": "self_repair.captured",
            "branch": res.branch,
            "commit": res.commit,
            "files": list(res.files),
            "count": len(res.files),
        })

    def handle_stream_line(self, stream: str, line: str) -> None:
        handler = getattr(self.downstream, "handle_stream_line", None)
        if handler is None:
            return
        try:
            handler(stream, line)
        except Exception:  # noqa: BLE001
            log.debug("self_repair: downstream stream handler raised", exc_info=True)

    def close(self) -> None:
        closer = getattr(self.downstream, "close", None)
        if closer is None:
            return
        try:
            closer()
        except Exception:  # noqa: BLE001
            log.debug("self_repair: downstream close raised", exc_info=True)

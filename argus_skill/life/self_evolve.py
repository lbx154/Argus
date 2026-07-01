"""Controlled self-evolution (受控自演化): review a captured self-repair, gate it
on a REAL passing test run, and only then land it to a target branch AS ``argus``.

EN: :mod:`argus_skill.life.self_repair` snapshots a mission's edits to its OWN
source onto a review branch (a pending, unreviewed commit). This module closes the
loop under strict guardrails — none of which is the agent's word alone:

  1. REVIEW  — the Manager judges the diff (genuine + correct + in-scope + safe,
     tests present). Conservative: default reject; behavioral changes need a
     strong justification. Reused via the Manager's own backend.
  2. TEST GATE — the touched tests are RUN, in an isolated temp worktree checked
     out at the capture commit (never the daemon's live tree). A green agent
     verdict with a red test bar is REJECTED. This is a hard gate, not advice.
  3. LAND    — only an approved + test-passing capture is merged onto the target
     branch, authored as ``argus``, via pure git plumbing (no checkout — safe
     against the daemon's dirty in-use working tree). Pushed ONLY to an
     explicitly-configured remote; by DEFAULT nothing leaves the local repo, and
     the real shared ``origin`` is never touched unless the caller opts in.

中文：self_repair 把 mission 对自身源码的改动快照成 review 分支上的待审 commit。本模块
在严格护栏下闭环(没有一条是"agent 说了算"):Manager 审 → 在检出到该 commit 的隔离临时
worktree 里真跑测试(硬门,绿 verdict + 红测试=拒) → 只有"批准且测试过"的才以 ``argus``
身份用纯 git plumbing 合入目标分支(不 checkout,故对 daemon 正在用的脏工作树安全);
仅当显式配置了 remote 才 push,默认不出本地、绝不碰真正的共享 ``origin``。
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# The git identity under which argus lands its own reviewed improvements.
ARGUS_AUTHOR_NAME = "argus"
ARGUS_AUTHOR_EMAIL = "argus@argus-skill"

# Structured verdict the Manager must emit when reviewing a self-repair.
SELF_REPAIR_REVIEW_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["approve", "risk", "reason"],
    "properties": {
        "approve": {
            "type": "boolean",
            "description": "true ONLY if this is a genuine, correct, in-scope, "
            "safe improvement with adequate test coverage. Default false on any "
            "doubt.",
        },
        "risk": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "behavioral/blast-radius risk of landing this to main",
        },
        "reason": {
            "type": "string",
            "description": "one or two sentences: why approve/reject",
        },
    },
}


@dataclass(frozen=True)
class SelfRepairVerdict:
    approve: bool
    risk: str
    reason: str


@dataclass(frozen=True)
class TestGateResult:
    passed: bool
    command: str
    tail: str            # last lines of the pytest output


@dataclass(frozen=True)
class LandResult:
    landed: bool
    target_branch: str
    commit: str          # sha landed on the target branch ("" if not landed)
    pushed_to: str | None
    reason: str


def _git(repo_root: Path, *args: str, env_index: str | None = None,
         author_argus: bool = False, timeout: int = 120) -> tuple[int, str, str]:
    env = dict(os.environ)
    if env_index is not None:
        env["GIT_INDEX_FILE"] = env_index
    if author_argus:
        env.update({
            "GIT_AUTHOR_NAME": ARGUS_AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": ARGUS_AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": ARGUS_AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": ARGUS_AUTHOR_EMAIL,
        })
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, env=env, timeout=timeout,
        )
        return p.returncode, p.stdout.rstrip("\n"), p.stderr.strip()
    except Exception as exc:  # noqa: BLE001 — git must never crash the caller
        return 1, "", str(exc)


# Shared production branches an autonomous agent must NEVER write/push.
_PROTECTED_BRANCHES = frozenset({"main", "master"})


def _is_protected(branch: str) -> bool:
    return str(branch or "").strip().lower() in _PROTECTED_BRANCHES


def _branch_checked_out(repo_root: Path, branch: str) -> bool:
    """True if ``branch`` is checked out in ANY worktree of this repo. Landing via
    plumbing onto a checked-out ref would desync that worktree — refuse it.
    Fail-CLOSED: on any error, assume it IS checked out (safer to refuse)."""
    rc, out, _ = _git(repo_root, "worktree", "list", "--porcelain")
    if rc != 0:
        return True
    want = f"branch refs/heads/{branch}"
    return any(line.strip() == want for line in out.splitlines())


# --- 1. REVIEW -------------------------------------------------------------

def review_self_repair(
    runner,
    *,
    diff: str,
    files: list[str],
    test_tail: str,
    reasoning_effort: str = "high",
) -> SelfRepairVerdict:
    """Ask the Manager (via its ``runner``) to judge a captured self-repair.

    Fail-CLOSED: any runner/parse error → reject. The prompt is deliberately
    conservative; a merge to shared code needs a clear, correct, well-tested,
    in-scope change, not merely a plausible one.
    失败即拒(fail-closed);提示刻意保守——合入共享代码需要清晰、正确、有测试、在范围内。
    """
    prompt = (
        "You are the Manager, gatekeeping whether argus may merge an improvement "
        "it made to its OWN source code into the shared branch. Approve ONLY a "
        "genuine, correct, minimal, in-scope improvement whose tests actually "
        "prove it. REJECT if: the change is speculative or cosmetic-only churn, "
        "it changes behavior without a clear justification and coverage, it "
        "touches unrelated code, the tests are weak/missing, or you are unsure. "
        "Default to REJECT on any doubt — a wrong merge to shared code is far "
        "costlier than a missed one.\n\n"
        f"Files changed: {', '.join(files)}\n\n"
        f"Test run (tail):\n{test_tail[-1500:]}\n\n"
        f"Diff:\n{diff[:12000]}\n\n"
        "Return the review verdict as JSON matching the schema: "
        '{"approve": bool, "risk": "low|medium|high", "reason": str}.'
    )
    try:
        from ..core.models import RunnerOptions

        result = runner.run_exec(
            prompt=prompt,
            options=RunnerOptions(reasoning_effort=reasoning_effort, skip_git_repo_check=True),
            run_label="manager-self-repair-review",
            resume_thread_id=None,
        )
        text = getattr(result, "last_agent_message", "") or getattr(result, "message", "")
        obj = _find_json_object(text)
        if not isinstance(obj, dict) or "approve" not in obj:
            return SelfRepairVerdict(False, "high", "unparseable review verdict")
        # Fail-CLOSED parse: only a real JSON boolean True approves. bool("false")
        # is truthy, so a stringified reject must NOT be read as approval.
        return SelfRepairVerdict(
            approve=(obj.get("approve") is True),
            risk=str(obj.get("risk") or "high"),
            reason=str(obj.get("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.debug("review_self_repair failed: %s", exc)
        return SelfRepairVerdict(False, "high", f"review error: {type(exc).__name__}")


def _find_json_object(text: str):
    """Return the LAST balanced JSON object that carries an ``approve`` key.

    Scans all balanced ``{...}`` and keeps the final one with ``approve`` — so an
    illustrative example object the model narrates BEFORE its real verdict is not
    mistaken for the verdict.
    取最后一个含 ``approve`` 键的平衡 JSON 对象,避免把叙述中的示例对象当成裁决。
    """
    import json

    if not text:
        return None
    chosen = None
    fallback = None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
                    if isinstance(obj, dict):
                        if fallback is None:
                            fallback = obj
                        if "approve" in obj:
                            chosen = obj
                    break
        start = text.find("{", start + 1)
    return chosen if chosen is not None else fallback


# --- 2. TEST GATE ----------------------------------------------------------

def run_test_gate(
    repo_root: Path,
    *,
    commit: str,
    test_files: list[str],
    source_files: list[str] | None = None,
    python: str | None = None,
    timeout: int = 900,
) -> TestGateResult:
    """Run the capture's tests at ``commit`` in an ISOLATED temp worktree.

    SOUND, not clever: if the capture edits ANY ``argus_skill/`` source file, the
    gate runs the WHOLE ``tests/`` tree at the capture commit — a behavioral edit
    to a widely-imported module is blast-radius-blind, so a sibling-dir heuristic
    is NOT a safe net. If only test files were touched, just those run. No touched
    test file at all → FAIL (nothing proves the change). Runs ``nice``-d so it
    yields to the production daemon on a shared box. Any non-zero pytest exit →
    fail. Never uses the daemon's live (dirty) working tree; prunes its worktree.
    要么稳,要么别做门:改了任何 ``argus_skill/`` 源码,就在该 commit 上跑整个
    ``tests/``(行为改动波及面不可知,兄弟目录启发式不安全);只改测试则只跑改动的。
    无测试即失败;``nice`` 降优先级让位生产 daemon;非零即失败;隔离 worktree 并清理。
    """
    tests = [f for f in test_files if f.startswith("tests/") and f.endswith(".py")]
    if not tests:
        return TestGateResult(False, "", "no touched test files → nothing proves the change")
    edits_source = any(f.startswith("argus_skill/") for f in (source_files or []))
    py = python or _default_python()
    wt = tempfile.mkdtemp(prefix="argus-evolve-gate-")
    try:
        rc, _, err = _git(repo_root, "worktree", "add", "--detach", wt, commit, timeout=120)
        if rc != 0:
            return TestGateResult(False, "git worktree add", f"worktree add failed: {err}")
        # Any source edit → the whole suite (blast-radius-safe). Test-only → just
        # the touched tests. Keep only targets present in the checked-out tree.
        wanted = (["tests"] + tests) if edits_source else tests
        present = [t for t in dict.fromkeys(wanted) if (Path(wt) / t).exists()]
        if not present:
            return TestGateResult(False, "", "no runnable test targets at commit")
        cmd = ["nice", "-n", "19", py, "-m", "pytest", *present, "-q", "-p", "no:cacheprovider"]
        try:
            env = dict(os.environ)
            env["PYTHONPATH"] = wt
            env["ARGUS_SKILL_SKIP_VAULT_PREFLIGHT"] = "1"
            proc = subprocess.run(
                cmd, cwd=wt, capture_output=True, text=True, env=env, timeout=timeout
            )
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
            return TestGateResult(proc.returncode == 0, " ".join(cmd), tail)
        except Exception as exc:  # noqa: BLE001
            return TestGateResult(False, " ".join(cmd[:6]), f"pytest run error: {exc}")
    finally:
        _git(repo_root, "worktree", "remove", "--force", wt, timeout=60)
        _git(repo_root, "worktree", "prune", timeout=30)  # never leak .git/worktrees/<id>
        try:
            import shutil

            shutil.rmtree(wt, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def _default_python() -> str:
    import sys

    return sys.executable or "python3"


# --- 3. LAND (as argus) ----------------------------------------------------

def land_self_repair(
    repo_root: Path,
    *,
    source_commit: str,
    files: list[str],
    target_branch: str = "argus-evolve",
    message: str,
    remote: str | None = None,
) -> LandResult:
    """Merge ``source_commit``'s version of ``files`` onto ``target_branch`` as
    ``argus``, via pure plumbing (no checkout). Push ONLY if ``remote`` is set.

    HARD-REFUSES a protected branch (main/master) or one currently checked out in
    any worktree — the plumbing is only safe on a ref no working tree holds, and
    the shared production branch must never be autonomously written. Push is a
    normal (non-force) push so a diverged remote branch is refused, never
    clobbered. Default target is the dedicated evolve branch so an omitted arg
    fails SAFE, not onto main.
    硬拒保护分支(main/master)或任何 worktree 正在检出的分支;push 用普通(非 force)推,
    远端分叉即拒不覆盖;默认目标是专属 evolve 分支,漏传参数也不会打到 main。
    """
    if _is_protected(target_branch):
        return LandResult(False, target_branch, "", None,
                          f"refusing to land on protected branch {target_branch!r}")
    if _branch_checked_out(repo_root, target_branch):
        return LandResult(False, target_branch, "", None,
                          f"target branch {target_branch!r} is checked out — refusing plumbing land")
    rc, tip, err = _git(repo_root, "rev-parse", "--verify", "--quiet", f"refs/heads/{target_branch}")
    if rc != 0 or not tip:
        return LandResult(False, target_branch, "", None, f"no target branch {target_branch!r}")

    idx_fd, idx = tempfile.mkstemp(prefix="argus-evolve-idx-")
    os.close(idx_fd)
    try:
        rc, _, err = _git(repo_root, "read-tree", tip, env_index=idx)
        if rc != 0:
            return LandResult(False, target_branch, "", None, f"read-tree failed: {err}")
        # Stage source_commit's version of each changed file into the scratch index.
        staged = 0
        for f in files:
            rc, meta, _ = _git(repo_root, "ls-tree", source_commit, "--", f)
            if rc != 0 or not meta:
                continue  # file not present at source (e.g. a deletion) → skip
            parts = meta.split()
            if len(parts) < 3:
                continue
            mode, blob = parts[0], parts[2]
            rc, _, err = _git(
                repo_root, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{f}",
                env_index=idx,
            )
            if rc == 0:
                staged += 1
        if staged == 0:
            return LandResult(False, target_branch, "", None, "no landable files")
        rc, tree, err = _git(repo_root, "write-tree", env_index=idx)
        if rc != 0 or not tree:
            return LandResult(False, target_branch, "", None, f"write-tree failed: {err}")
        rc, tgt_tree, _ = _git(repo_root, "rev-parse", f"{tip}^{{tree}}")
        if rc == 0 and tgt_tree == tree:
            return LandResult(False, target_branch, "", None, "no change vs target (already landed)")
        rc, commit, err = _git(
            repo_root, "commit-tree", tree, "-p", tip, "-m", message, author_argus=True,
        )
        if rc != 0 or not commit:
            return LandResult(False, target_branch, "", None, f"commit-tree failed: {err}")
        rc, _, err = _git(repo_root, "update-ref", f"refs/heads/{target_branch}", commit)
        if rc != 0:
            return LandResult(False, target_branch, "", None, f"update-ref failed: {err}")
    finally:
        try:
            os.unlink(idx)
        except OSError:
            pass

    pushed_to = None
    if remote:
        rc, _, err = _git(repo_root, "push", remote, f"{target_branch}:{target_branch}", timeout=120)
        if rc == 0:
            pushed_to = f"{remote}/{target_branch}"
        else:
            log.warning("self-evolve push to %s failed (landed locally): %s", remote, err)
    return LandResult(True, target_branch, commit, pushed_to, "landed as argus")


# --- orchestrator ----------------------------------------------------------

@dataclass(frozen=True)
class EvolveOutcome:
    stage: str           # "reviewed" | "gated" | "landed" | "rejected" | "error"
    verdict: SelfRepairVerdict | None
    gate: TestGateResult | None
    land: LandResult | None


def evolve_capture(
    *,
    runner,
    repo_root: Path,
    commit: str,
    files: list[str],
    target_branch: str = "argus-evolve",
    remote: str | None = None,
    reasoning_effort: str = "high",
) -> EvolveOutcome:
    """Full controlled loop for ONE captured self-repair commit: TEST GATE →
    Manager REVIEW → LAND (only if both pass). The test gate runs FIRST so a
    broken change never even reaches the (costlier) Manager review. Fail-closed:
    a change whose diff cannot even be shown to the reviewer is never landed."""
    diff_rc, diff, _ = _git(repo_root, "show", commit)
    test_files = [f for f in files if f.startswith("tests/")]
    source_files = [f for f in files if f.startswith("argus_skill/")]
    gate = run_test_gate(
        repo_root, commit=commit, test_files=test_files, source_files=source_files,
    )
    if not gate.passed:
        return EvolveOutcome("gated", None, gate, None)
    if diff_rc != 0 or not diff.strip():
        # No reviewable evidence → fail closed (never land a change we can't show).
        return EvolveOutcome("error", None, gate, None)
    verdict = review_self_repair(
        runner, diff=diff, files=files,
        test_tail=gate.tail, reasoning_effort=reasoning_effort,
    )
    if not verdict.approve:
        return EvolveOutcome("rejected", verdict, gate, None)
    msg = (
        f"argus: self-evolve — {verdict.reason}\n\n"
        f"Manager-approved (risk={verdict.risk}) + tests passed. "
        f"Files: {', '.join(files)}.\n"
        "Landed autonomously by argus under human-configured guardrails."
    )
    land = land_self_repair(
        repo_root, source_commit=commit, files=files,
        target_branch=target_branch, message=msg, remote=remote,
    )
    return EvolveOutcome("landed" if land.landed else "error", verdict, gate, land)


# --- PR (best-effort) ------------------------------------------------------

def open_or_update_pr(
    repo_root: Path,
    *,
    head: str,
    base: str = "main",
    title: str,
    body: str,
) -> str | None:
    """Ensure a PR ``head`` → ``base`` exists (best-effort, via ``gh``).

    Returns the PR URL, or None if gh is unavailable / already open / it fails.
    Never raises — a missing PR must not undo an already-pushed branch.
    确保存在 head→base 的 PR(尽力而为,用 gh);失败即 None,绝不抛异常。
    """
    import shutil

    if shutil.which("gh") is None:
        return None
    try:
        # Already open? (gh pr list is quiet on none.)
        p = subprocess.run(
            ["gh", "pr", "list", "--head", head, "--base", base, "--state", "open",
             "--json", "url", "-q", ".[0].url"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=60,
        )
        existing = (p.stdout or "").strip()
        if existing:
            return existing
        c = subprocess.run(
            ["gh", "pr", "create", "--head", head, "--base", base,
             "--title", title, "--body", body],
            cwd=str(repo_root), capture_output=True, text=True, timeout=90,
        )
        out = (c.stdout or "").strip()
        return out.splitlines()[-1] if out else None
    except Exception as exc:  # noqa: BLE001
        log.debug("open_or_update_pr failed: %s", exc)
        return None


# --- daemon observer: capture → evolve → push argus-evolve → PR ------------

class SelfEvolveSink:
    """Event-sink observer that runs the CONTROLLED evolve loop on each captured
    self-repair and pushes the approved ones to a dedicated ``argus-evolve``
    branch on a real remote (never main), opening a PR for human merge.

    Sits DOWNSTREAM of :class:`~argus_skill.life.self_repair.SelfRepairSink`, so it
    receives the ``self_repair.captured`` events that sink emits. For each: test
    gate → Manager review → land onto ``target_branch`` as ``argus`` → push to
    ``remote`` → best-effort PR. Emits ``self_evolve.landed`` / ``.rejected`` /
    ``.gated`` for the audit trail. Everything is fail-soft and forwards all
    events unchanged; the shared production ``main`` is never touched.
    在 SelfRepairSink 下游,收到每个 ``self_repair.captured`` 后跑受控闭环,把批准的以
    ``argus`` 身份落到 ``argus-evolve`` 分支并 push 到真 remote(绝不碰 main)+ 开 PR。
    """

    def __init__(
        self,
        downstream,
        *,
        runner,
        repo_root: Path,
        target_branch: str,
        remote: str | None,
        pr_base: str | None,
    ) -> None:
        self.downstream = downstream
        self.runner = runner
        self.repo_root = repo_root
        self.target_branch = target_branch
        self.remote = remote
        self.pr_base = pr_base

    @classmethod
    def build(cls, downstream, *, runner):
        """Return a wrapped sink, or the downstream UNCHANGED when the running
        package is not a git checkout or no runner is available (feature inert)."""
        from .self_repair import self_source_repo_root

        root = self_source_repo_root()
        if root is None or runner is None:
            return downstream
        target = os.environ.get("ARGUS_SKILL_SELF_EVOLVE_BRANCH", "argus-evolve").strip() or "argus-evolve"
        if _is_protected(target):
            # Anti-footgun: never let a misconfigured env aim the autonomous loop
            # at the shared production branch. Force the dedicated evolve branch.
            log.warning(
                "self-evolve: ARGUS_SKILL_SELF_EVOLVE_BRANCH=%r is protected; "
                "forcing 'argus-evolve'", target,
            )
            target = "argus-evolve"
        remote = os.environ.get("ARGUS_SKILL_SELF_EVOLVE_REMOTE", "").strip() or None
        pr_base = os.environ.get("ARGUS_SKILL_SELF_EVOLVE_PR_BASE", "").strip() or None
        # Ensure the target branch exists off the current main (once, best-effort).
        cls._ensure_branch(root, target)
        return cls(
            downstream, runner=runner, repo_root=root,
            target_branch=target, remote=remote, pr_base=pr_base,
        )

    @staticmethod
    def _ensure_branch(root: Path, branch: str) -> None:
        rc, _, _ = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if rc == 0:
            return
        for base in ("main", "master", "HEAD"):
            rc, sha, _ = _git(root, "rev-parse", "--verify", "--quiet", base)
            if rc == 0 and sha:
                _git(root, "branch", branch, sha)
                return

    def handle_event(self, event) -> None:
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("self_evolve: downstream handle_event raised; continuing")
        try:
            if isinstance(event, dict) and event.get("type") == "self_repair.captured":
                self._evolve(event)
        except Exception:  # noqa: BLE001 — evolve must never break the daemon
            log.debug("self_evolve: evolve on capture failed", exc_info=True)

    def _evolve(self, capture: dict) -> None:
        commit = str(capture.get("commit") or "")
        files = [str(f) for f in (capture.get("files") or [])]
        if not commit or not files:
            return
        outcome = evolve_capture(
            runner=self.runner, repo_root=self.repo_root, commit=commit, files=files,
            target_branch=self.target_branch, remote=self.remote,
        )
        ev: dict = {
            "type": f"self_evolve.{outcome.stage}",
            "commit": commit,
            "files": files,
            "target_branch": self.target_branch,
        }
        if outcome.verdict is not None:
            ev["approve"] = outcome.verdict.approve
            ev["risk"] = outcome.verdict.risk
            ev["reason"] = outcome.verdict.reason
        if outcome.gate is not None:
            ev["gate_passed"] = outcome.gate.passed
        if outcome.land is not None:
            ev["landed_commit"] = outcome.land.commit
            ev["pushed_to"] = outcome.land.pushed_to
        # Best-effort PR once something has actually been pushed to the remote.
        if (
            outcome.stage == "landed"
            and outcome.land is not None
            and outcome.land.pushed_to
            and self.pr_base
        ):
            url = open_or_update_pr(
                self.repo_root, head=self.target_branch, base=self.pr_base,
                title="argus: autonomous self-evolution improvements",
                body="Auto-opened by argus. Each commit is a self-repair that "
                "passed an independent test gate and Manager review. Human-merge "
                "when satisfied.",
            )
            if url:
                ev["pr_url"] = url
        try:
            self.downstream.handle_event(ev)
        except Exception:  # noqa: BLE001
            log.debug("self_evolve: emit outcome failed", exc_info=True)

    def handle_stream_line(self, stream: str, line: str) -> None:
        handler = getattr(self.downstream, "handle_stream_line", None)
        if handler is None:
            return
        try:
            handler(stream, line)
        except Exception:  # noqa: BLE001
            log.debug("self_evolve: downstream stream handler raised", exc_info=True)

    def close(self) -> None:
        closer = getattr(self.downstream, "close", None)
        if closer is None:
            return
        try:
            closer()
        except Exception:  # noqa: BLE001
            log.debug("self_evolve: downstream close raised", exc_info=True)

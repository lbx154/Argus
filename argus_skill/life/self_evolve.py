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
        return SelfRepairVerdict(
            approve=bool(obj.get("approve")),
            risk=str(obj.get("risk") or "high"),
            reason=str(obj.get("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001 — fail closed
        log.debug("review_self_repair failed: %s", exc)
        return SelfRepairVerdict(False, "high", f"review error: {type(exc).__name__}")


def _find_json_object(text: str):
    import json

    if not text:
        return None
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
                        return json.loads(text[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
        start = text.find("{", start + 1)
    return None


# --- 2. TEST GATE ----------------------------------------------------------

def run_test_gate(
    repo_root: Path,
    *,
    commit: str,
    test_files: list[str],
    python: str | None = None,
    timeout: int = 600,
) -> TestGateResult:
    """Run the capture's touched tests at ``commit`` in an ISOLATED temp worktree.

    Never uses the daemon's live (dirty) working tree. If no test files were
    touched, the gate FAILS (a self-repair with no test is not landable). Any
    non-zero pytest exit → fail.
    在检出到该 commit 的隔离临时 worktree 里跑测试;无测试文件即判失败;pytest 非零即失败。
    """
    tests = [f for f in test_files if f.startswith("tests/") and f.endswith(".py")]
    if not tests:
        return TestGateResult(False, "", "no touched test files → nothing proves the change")
    py = python or _default_python()
    wt = tempfile.mkdtemp(prefix="argus-evolve-gate-")
    try:
        rc, _, err = _git(repo_root, "worktree", "add", "--detach", wt, commit, timeout=120)
        if rc != 0:
            return TestGateResult(False, "git worktree add", f"worktree add failed: {err}")
        cmd = [py, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"]
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
            return TestGateResult(False, " ".join(cmd), f"pytest run error: {exc}")
    finally:
        _git(repo_root, "worktree", "remove", "--force", wt, timeout=60)
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
    target_branch: str = "main",
    message: str,
    remote: str | None = None,
) -> LandResult:
    """Merge ``source_commit``'s version of ``files`` onto ``target_branch`` as
    ``argus``, via pure plumbing (no checkout). Push ONLY if ``remote`` is set.

    Safe against the daemon's dirty in-use working tree: it advances the branch
    ref directly, touching neither the real index nor any checkout. ``remote`` is
    opt-in — by default the landed commit stays local and the shared origin is
    never contacted.
    用纯 plumbing 把该 commit 里这些文件的版本以 ``argus`` 身份合入目标分支(不 checkout);
    仅当给了 ``remote`` 才 push,默认不出本地、绝不碰共享 origin。
    """
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
    target_branch: str = "main",
    remote: str | None = None,
    reasoning_effort: str = "high",
) -> EvolveOutcome:
    """Full controlled loop for ONE captured self-repair commit: TEST GATE →
    Manager REVIEW → LAND (only if both pass). The test gate runs FIRST so a
    broken change never even reaches the (costlier) Manager review."""
    diff_rc, diff, _ = _git(repo_root, "show", commit)
    test_files = [f for f in files if f.startswith("tests/")]
    gate = run_test_gate(repo_root, commit=commit, test_files=test_files)
    if not gate.passed:
        return EvolveOutcome("gated", None, gate, None)
    verdict = review_self_repair(
        runner, diff=diff if diff_rc == 0 else "", files=files,
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

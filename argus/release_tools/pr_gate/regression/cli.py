"""Source-checkout prototype: one local check and one submitted JSON report."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

import jsonschema

if __package__:
    from . import evidence as local_evidence
    from . import runner as local_runner
    from . import runtime as run
    from .scope import analyze_scope, changed_paths
    from .snapshot import (
        REPORT_NAME,
        CleanupError,
        GateError,
        Snapshot,
        capture,
        git,
        materialize,
        publication_lock,
        repository,
        source_inventory,
    )
else:
    import evidence as local_evidence
    import runner as local_runner
    import runtime as run
    from scope import analyze_scope, changed_paths
    from snapshot import (
        REPORT_NAME,
        CleanupError,
        GateError,
        Snapshot,
        capture,
        git,
        materialize,
        publication_lock,
        repository,
        source_inventory,
    )

EXIT_CODES = {"PASSED": 0, "BLOCKED": 1, "INCOMPLETE": 2}


def unchanged_analysis(snapshot: Snapshot) -> dict:
    return {
        "schema_version": "pr-regression-study/v1",
        "pr_number": 0,
        "base_sha": snapshot.merge_base_sha, "candidate_sha": snapshot.merge_base_sha,
        "verdict": "NO_REGRESSION_FOUND", "analysis_mode": "short_circuit",
        "summary": "The staged source tree equals the merge-base source tree.",
        "inspected_paths": [f"git-tree:{snapshot.candidate_tree}"],
        "short_circuit_reason": "No code/content/mode change after excluding the reserved report.",
        "findings": [], "tests": [], "escalation_required": False,
        "limitations": ["Tree equality only; no agent behavior was evaluated."],
    }


def run_directory(repo: Path) -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    cache = (cache / "pr-regression-gate").expanduser().resolve()
    if cache.is_relative_to(repo):
        raise GateError("The run cache must be outside the repository being checked.")
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    return Path(tempfile.mkdtemp(prefix="prgate-", dir=cache))


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, jsonschema.ValidationError):
        return "Analysis/evidence does not satisfy the report schema."
    if isinstance(exc, json.JSONDecodeError):
        return "Analysis/evidence contains invalid JSON."
    if isinstance(exc, KeyError):
        return "Required authentication or analysis evidence field is unavailable."
    return f"{type(exc).__name__}: {exc}"[:1500]


def verify(repo: Path, base: str | None = None) -> dict[str, str]:
    current = capture(repo, base)
    report = local_evidence.read_report(current.repository / REPORT_NAME)
    if report["binding"] != current.binding():
        raise GateError("Report is stale: staged source, target base, or comparison base changed.")
    return local_evidence.verify_artifacts(report, source_inventory(current))


def publish(snapshot: Snapshot, report: dict, base: str | None) -> None:
    """Reject stale writers before replacing another check's valid report."""
    with publication_lock(snapshot.repository):
        current = capture(snapshot.repository, base)
        if current.binding() != snapshot.binding():
            raise GateError("Analysis became stale; the existing report was not overwritten.")
        if local_evidence.policy_identity() != report["policy"]:
            raise GateError("Gate policy changed; the existing report was not overwritten.")
        local_evidence.verify_artifacts(report, source_inventory(current))
        local_evidence.write_report(snapshot.repository, report)
        # Still detect a concurrent git add; another publisher cannot interleave here.
        verify(snapshot.repository, base)


def check(
    repo: Path, *, base: str | None = None, isolation: str = "native",
    model: str = "gpt-6-astra", timeout: int = 1200,
    analyzer: Callable | None = None,
) -> tuple[dict, Path | None]:
    if isolation not in {"native", "docker"} or not 1 <= timeout <= 1200:
        raise GateError("Use native/docker isolation and a timeout between 1 and 1200 seconds.")
    if not model.strip() or model.strip() == "auto":
        raise GateError("Use a named Copilot model, not automatic model routing.")
    snapshot = capture(repo, base)
    inventory = source_inventory(snapshot)
    policy = local_evidence.policy_identity()
    root = None
    report = {
        "schema_version": "local-pr-regression/v2",
        "created_at": run.now(), "run_id": str(uuid.uuid4()),
        "binding": snapshot.binding(), "policy": policy,
        "snapshots": {"pr_number": 0, "base_sha": snapshot.merge_base_sha,
                      "candidate_sha": snapshot.merge_base_sha},
        "execution": {"status": "unchanged", "isolation": "none"},
        "analysis": None, "explanation": "", "evidence": [],
        "scope": analyze_scope(None, inventory),
        "gate": {"status": "INCOMPLETE", "reason": "Analysis has not completed."},
    }
    started = time.monotonic()
    if snapshot.base_tree == snapshot.candidate_tree:
        report["analysis"] = unchanged_analysis(snapshot)
        report["explanation"] = "No source difference; no Copilot or test execution was necessary."
    else:
        root = run_directory(snapshot.repository)
        work = root / "work"
        work.mkdir()
        report["run_id"] = root.name
        report["execution"] = {"status": "error", "isolation": isolation}
        cleanup_safe = True
        try:
            local_runner.stage_assets(root, local_evidence.SKILL_PATH)
            identities = materialize(snapshot, root)
            report["snapshots"] = {"pr_number": 0, **identities}
            metadata = {
                **report["snapshots"],
                "title": "Local staged source changes",
                "body": "",
                "local_gate": True,
                "analysis_budget_seconds": timeout,
                "comparison": "merge_base_to_staged_source",
                "original_base_commit": snapshot.merge_base_sha,
                "target_base_commit": snapshot.target_base_sha,
                "base_tree": snapshot.base_tree,
                "candidate_tree": snapshot.candidate_tree,
                "changed_paths": changed_paths(inventory),
                "note": "Synthetic code-only snapshots; not a replay of integration with the current target branch.",
            }
            mirror = root / "repository.git"
            patch = git(
                mirror, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--binary",
                identities["base_sha"], identities["candidate_sha"],
            )
            (work / "change.patch").write_bytes(patch)
            run.write_json(work / "changed-paths.json", changed_paths(inventory))
            run.write_json(work / "input.json", metadata)
            print(f"Analyzing staged changes in {root} ({isolation}; {model}).", flush=True)
            if isolation == "native":
                print("Native mode runs trusted local tests with your user permissions; it is not an OS sandbox.", flush=True)
            execute = analyzer or local_runner.analyze
            report["execution"] = execute(root, metadata, isolation=isolation, model=model, timeout=timeout)
            analysis, explanation, evidence = local_evidence.collect(work, metadata, inventory)
            report.update(analysis=analysis, explanation=explanation, evidence=evidence)
        except (
            GateError, OSError, ValueError, KeyError, RuntimeError,
            subprocess.SubprocessError, jsonschema.ValidationError,
        ) as exc:
            cleanup_safe = not isinstance(exc, CleanupError)
            report.update(analysis=None, explanation="", evidence=[])
            report["execution"] = {
                "status": "error", "isolation": isolation, "error": _failure_message(exc),
            }
        finally:
            # Retain logs/receipts; do not accumulate source clones, package caches, or login state.
            disposable = (
                root / "repository.git", root / "bin", root / "home",
                work / "base", work / "candidate", work / "home",
            ) if cleanup_safe else ()
            for path in disposable:
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
    report["execution"]["duration_seconds"] = round(time.monotonic() - started, 3)
    report["execution"]["timeout_seconds"] = timeout
    report["scope"] = analyze_scope(
        report["analysis"], inventory, unchanged=report["execution"]["status"] == "unchanged",
    )
    report["gate"] = local_evidence.gate_policy(report, inventory)
    publish(snapshot, report, base)
    return report, root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pr-gate", description=(
        "Analyze staged source with Copilot + Skill; submit one JSON report. "
        "Native mode uses temporary state, not an OS sandbox."
    ))
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("check", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--repo", type=Path, default=Path.cwd())
        command.add_argument("--base", help="Local base ref/commit; defaults to origin/main, then main. No fetch.")
        if name == "check":
            command.add_argument("--isolation", choices=("native", "docker"), default="native")
            command.add_argument("--model", default="gpt-6-astra")
            command.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    old_handlers = {}
    if args.action == "check":
        run.STOP.clear()
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, lambda *_: run.STOP.set())
    try:
        if args.action == "check":
            report, directory = check(
                args.repo, base=args.base, isolation=args.isolation,
                model=args.model, timeout=args.timeout,
            )
            verdict = report["gate"]
            print(f"Report: {repository(args.repo) / REPORT_NAME}")
            if directory is not None:
                print(f"Private logs: {directory}")
        else:
            verdict = verify(args.repo, args.base)
        print(f"{verdict['status']}: {verdict['reason']}")
        return EXIT_CODES[verdict["status"]]
    except (
        GateError, OSError, ValueError, KeyError, subprocess.SubprocessError,
        jsonschema.ValidationError,
    ) as exc:
        print(f"INCOMPLETE: {_failure_message(exc)}")
        return 2
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())

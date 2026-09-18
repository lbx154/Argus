"""Run a paired, bounded probe without inherited user state or model credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import signal
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

if __package__:
    from argus.release_tools.pr_gate.owned_process import run_owned

    from .oracle import prepare_comparison, project_prefixes, verify_oracle_inputs
    from .probe_contract import signal_exit
else:
    from oracle import prepare_comparison, project_prefixes, verify_oracle_inputs
    from owned_process import run_owned
    from probe_contract import signal_exit

WORK = Path(os.environ.get("PR_GATE_WORK_ROOT", "/work")).resolve()
OUTPUT_LIMIT_BYTES = 1024 * 1024
STOP = threading.Event()


def _environment(root: Path, state: Path) -> dict:
    (state / "home").mkdir()
    return {
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(state / "home"),
        "TMPDIR": str(state),
        "LANG": "C.UTF-8",
        "ARGUS_SKILL_HOME": str(state / "argus"),
        "ARGUS_SKILL_SOURCE_ROOT": str(root),
        "ARGUS_SKILL_SAFE_MODE": "1",
        "COPILOT_HOME": str(state / "copilot"),
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "CUDA_VISIBLE_DEVICES": "",
        "HIP_VISIBLE_DEVICES": "",
    }


def _observation(root: Path, command: str, log: Path, result, started: float) -> dict:
    with log.open("rb") as stream:
        log_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "command": command,
        "cwd": str(root),
        "process_pid": result.pid,
        "exit_code": result.returncode,
        "timed_out": result.reason == "timeout",
        "interrupted": result.reason == "cancelled" or signal_exit(result.returncode),
        "output_limit_exceeded": result.reason == "output_limit",
        "cleanup_complete": result.cleanup_complete,
        "execution_error": result.reason or (
            "signal_terminated" if result.returncode < 0
            else "signal_exit_status" if signal_exit(result.returncode) else None
        ),
        "output_limit_bytes": OUTPUT_LIMIT_BYTES,
        "seconds": round(time.monotonic() - started, 3),
        "log": str(log.relative_to(WORK)),
        "log_sha256": log_digest,
    }


def run_one(*, root: Path, command: str, log: Path) -> dict:
    """Legacy bounded shell execution, not local-v2 comparative evidence."""
    with tempfile.TemporaryDirectory(prefix=".pr-probe-", dir=log.parent) as temporary:
        state = Path(temporary)
        environment = _environment(root, state)
        started = time.monotonic()
        result = run_owned(
            ["bash", "--noprofile", "--norc", "-c", command],
            cwd=root, environment=environment, stdout_path=log, timeout=120,
            output_limit=OUTPUT_LIMIT_BYTES, cancelled=STOP.is_set,
        )
        return _observation(root, command, log, result, started)


def _source_origin(
    *, root: Path, context: str, main_pid: int, traces: Path | None, log: Path,
    initial_issues: tuple[str, ...] = (),
) -> dict:
    issues = list(initial_issues)
    processes = {}
    if traces is not None:
        for path in sorted(traces.glob("*.jsonl")):
            try:
                if path.stat().st_size > 4 * OUTPUT_LIMIT_BYTES:
                    raise ValueError("origin trace exceeds its evidence budget")
                records = [json.loads(line) for line in path.read_text().splitlines()]
                if not records or any(
                    not isinstance(record, dict) or record.get("context_id") != context
                    or record.get("pid") != int(path.stem) for record in records
                ):
                    raise ValueError("origin trace identity mismatch")
                processes[int(path.stem)] = records
            except (OSError, ValueError) as exc:
                issues.append(f"unreadable_origin_trace: {path.name}: {exc}")
    selected = {main_pid} if main_pid in processes else set()
    while True:
        children = {
            pid for pid, records in processes.items()
            if any(record.get("event") == "startup" and record.get("ppid") in selected
                   for record in records)
        }
        if children <= selected:
            break
        selected.update(children)
    files = {}
    loaded = completed = False
    for pid in sorted(selected):
        records = processes[pid]
        starts = [record for record in records if record.get("event") == "startup"]
        ends = [record for record in records if record.get("event") == "completion"]
        if pid == main_pid:
            loaded, completed = len(starts) == 1, len(ends) == 1
        if len(starts) != 1 or len(ends) != 1:
            issues.append(f"guard_lifecycle_incomplete: pid={pid}")
        if any(record.get("source_root") != str(root) for record in starts + ends):
            issues.append(f"guard_source_root_mismatch: pid={pid}")
        launches = sum(record.get("event") == "python_child" for record in records)
        children = sum(
            any(record.get("event") == "startup" and record.get("ppid") == pid
                for record in processes[child]) for child in selected if child != pid
        )
        if launches != children:
            issues.append(f"python_child_guard_count_mismatch: pid={pid}")
        for record in records:
            if record.get("event") == "issue":
                issues.append(str(record.get("message", "unspecified_guard_issue")))
            elif record.get("event") == "file":
                relative = record.get("path")
                if (
                    not isinstance(relative, str) or Path(relative).is_absolute()
                    or ".." in Path(relative).parts
                    or not re.fullmatch(r"[0-9a-f]{40}", str(record.get("git_blob")))
                    or not re.fullmatch(r"[0-9a-f]{40}", str(record.get("lf_git_blob")))
                ):
                    issues.append(f"invalid_source_file_trace: pid={pid}")
                    continue
                entry = {key: record[key] for key in ("path", "git_blob", "lf_git_blob")}
                if relative in files and files[relative] != entry:
                    issues.append(f"source_changed_between_processes: {relative}")
                files[relative] = entry
    if not loaded:
        issues.append("main_guard_startup_missing")
    if not completed:
        issues.append("main_guard_completion_missing")
    if not files:
        issues.append("no_observable_product_source")
    status = "incomplete" if issues else "verified"
    document = {
        "schema_version": "probe-source-origin/v1",
        "source_root": str(root), "context_id": context, "main_pid": main_pid,
        "guard_loaded": loaded, "completed": completed, "status": status,
        "issues": sorted(set(issues)), "files": [files[key] for key in sorted(files)],
        "process_count": len(selected),
    }
    path = log.with_suffix(".source-origin.json")
    data = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(data)
    return {
        "path": path.relative_to(WORK).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(), "status": status,
    }


def _not_run(*, root: Path, command: str, log: Path, reason: str) -> dict:
    from types import SimpleNamespace

    started = time.monotonic()
    log.write_text(f"Probe not run: {reason}\n")
    observation = _observation(root, command, log, SimpleNamespace(
        pid=0, returncode=125, reason=f"not_run: {reason}", cleanup_complete=True,
    ), started)
    observation["source_origin"] = _source_origin(
        root=root, context=uuid.uuid4().hex, main_pid=0, traces=None, log=log,
        initial_issues=(f"not_run: {reason}",),
    )
    return observation


def run_local(
    *, root: Path, command: str, argv: list[str], log: Path,
    prefixes: list[str], oracle_files: dict,
) -> dict:
    """Run a preflight-approved Python command with private per-process traces."""
    root = root.resolve()
    context = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=".pr-probe-", dir=log.parent) as temporary:
        state = Path(temporary)
        traces = state / "origins"
        traces.mkdir(mode=0o700)
        environment = _environment(root, state)
        guard = Path(__file__).resolve().parent / "origin_guard"
        environment.update({
            "PYTHONPATH": os.pathsep.join((str(guard), str(root / "src"), str(root))),
            "PYTHONNOUSERSITE": "1",
            "PYTEST_ADDOPTS": "-o " + shlex.quote(f"cache_dir={state / 'pytest-cache'}"),
            "PR_GATE_SOURCE_ROOT": str(root), "PR_GATE_CONTEXT_ID": context,
            "PR_GATE_ORIGIN_TRACE_DIR": str(traces),
            "PR_GATE_PROJECT_PREFIXES": json.dumps(prefixes),
            "PR_GATE_ORACLE_PATHS": json.dumps(sorted(oracle_files)),
        })
        started = time.monotonic()
        result = run_owned(
            argv, cwd=root, environment=environment, stdout_path=log, timeout=120,
            output_limit=OUTPUT_LIMIT_BYTES, cancelled=STOP.is_set,
        )
        observation = _observation(root, command, log, result, started)
        observation["source_origin"] = _source_origin(
            root=root, context=context, main_pid=result.pid, traces=traces, log=log,
        )
        if observation["source_origin"]["status"] != "verified":
            error = "source_origin_incomplete"
            observation["execution_error"] = (
                f"{observation['execution_error']}; {error}"
                if observation["execution_error"] else error
            )
        return observation


def run_pair(
    *, base_root: Path, candidate_root: Path, base_command: str, candidate_command: str,
    directory: Path, oracle_paths: list[str] | tuple[str, ...] = (),
) -> dict:
    """Return local-v2 comparison and both observations, including skipped pairs."""
    comparison = prepare_comparison(
        base_root=base_root, candidate_root=candidate_root,
        base_command=base_command, candidate_command=candidate_command,
        evidence=directory, work=WORK, oracle_paths=oracle_paths,
    )
    result = {"comparison": comparison}
    prefixes = []
    if comparison["compatible"]:
        try:
            prefixes = project_prefixes(base_root, candidate_root)
        except (OSError, ValueError) as exc:
            comparison["compatible"] = False
            comparison["issues"].append(f"project_prefix_discovery_failed: {exc}")

    def check_oracles():
        for side, root in (("base", base_root), ("candidate", candidate_root)):
            failures = verify_oracle_inputs(
                root=root, argv=comparison["command_argv"], oracle_paths=oracle_paths,
                frozen=comparison["oracle_files"][side],
            )
            if failures:
                comparison["compatible"] = False
                comparison["issues"].extend(f"{side}: {failure}" for failure in failures)

    for side, root, command in (
        ("base", base_root, base_command), ("candidate", candidate_root, candidate_command),
    ):
        reason = (
            "comparison_incompatible: " + "; ".join(comparison["issues"])
            if not comparison["compatible"] else "cancelled" if STOP.is_set() else None
        )
        if reason:
            result[side] = _not_run(
                root=root.resolve(), command=command, log=directory / f"{side}.log", reason=reason,
            )
        else:
            result[side] = run_local(
                root=root, command=command, argv=comparison["command_argv"],
                log=directory / f"{side}.log", prefixes=prefixes,
                oracle_files=comparison["oracle_files"][side],
            )
            check_oracles()
    if not comparison["compatible"]:
        comparison["issues"] = sorted(set(comparison["issues"]))
        for side in ("base", "candidate"):
            observation = result[side]
            if observation["source_origin"]["status"] == "verified":
                descriptor = observation["source_origin"]
                path = WORK / descriptor["path"]
                document = json.loads(path.read_text())
                document["status"] = "incomplete"
                document["issues"].append("oracle_comparison_incomplete")
                data = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
                path.write_bytes(data)
                descriptor.update(status="incomplete", sha256=hashlib.sha256(data).hexdigest())
            if not observation["execution_error"]:
                observation["execution_error"] = "oracle_comparison_incomplete"
    return result


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--command")
    parser.add_argument("--base-command")
    parser.add_argument("--candidate-command")
    parser.add_argument("--oracle", action="append", default=[])
    args = parser.parse_args()
    if args.command is not None:
        if args.base_command is not None or args.candidate_command is not None:
            parser.error("--command cannot be combined with side-specific commands")
        args.base_command = args.candidate_command = args.command
    if args.base_command is None or args.candidate_command is None:
        parser.error("use --command, or both legacy side-specific commands")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", args.id):
        parser.error("probe id must be a simple name")
    evidence = WORK / "evidence"
    if len(list(evidence.glob("*/receipt.json"))) >= 6:
        parser.error("six-probe budget exhausted")
    directory = evidence / args.id
    directory.mkdir(parents=True, exist_ok=False)
    metadata = json.loads((WORK / "input.json").read_text())
    receipt = {
        "id": args.id,
        "base_sha": metadata["base_sha"],
        "candidate_sha": metadata["candidate_sha"],
    }
    if metadata.get("local_gate") is True:
        receipt.update(run_pair(
            base_root=WORK / "base", candidate_root=WORK / "candidate",
            base_command=args.base_command, candidate_command=args.candidate_command,
            directory=directory, oracle_paths=args.oracle,
        ))
    else:
        receipt["base"] = run_one(
            root=WORK / "base", command=args.base_command, log=directory / "base.log",
        )
        if STOP.is_set():
            raise SystemExit(130)
        receipt["candidate"] = run_one(
            root=WORK / "candidate", command=args.candidate_command,
            log=directory / "candidate.log",
        )
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

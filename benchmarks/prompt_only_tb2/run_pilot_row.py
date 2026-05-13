from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parents[1]
GENERATED_DIR = BASE_DIR / "generated"
RUNS_DIR = BASE_DIR / "runs"
ASSIGNMENT_PATH = GENERATED_DIR / "self_pilot_assignment.csv"
RUNS_CSV_PATH = GENERATED_DIR / "pilot_runs.csv"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_filename(value: str) -> str:
    safe = _SAFE_FILENAME_RE.sub("-", value).strip(".-")
    return safe or "task"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_assignment(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(
            f"assignment file not found: {path}\n"
            "Run extract_tb2_prompts.py first."
        )
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _select_row(
    rows: list[dict[str, str]],
    *,
    order: int | None,
    condition: str | None,
    task_id: str | None,
) -> dict[str, str]:
    matches = rows
    if order is not None:
        matches = [row for row in matches if int(row["order"]) == order]
    if condition is not None:
        matches = [row for row in matches if row["condition"] == condition]
    if task_id is not None:
        matches = [row for row in matches if row["task_id"] == task_id]
    if not matches:
        raise SystemExit("no assignment row matched the requested selector")
    if len(matches) > 1:
        choices = ", ".join(f"order={row['order']}" for row in matches[:10])
        raise SystemExit(f"selector matched multiple rows; pass --order ({choices})")
    return matches[0]


def _read_prompt(generated_dir: Path, row: dict[str, str]) -> tuple[Path, str]:
    rel = row.get("prompt_file", "")
    if not rel:
        raise SystemExit("assignment row has no prompt_file; regenerate prompts")
    path = generated_dir / rel
    if not path.exists():
        raise SystemExit(f"prompt file not found: {path}")
    return path, path.read_text(encoding="utf-8")


def _run_id(row: dict[str, str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        f"{stamp}-o{int(row['order']):03d}-"
        f"{row['condition']}-{_safe_filename(row['task_id'])}"
    )


def _default_timeout_seconds(row: dict[str, str], override_minutes: float | None) -> float:
    if override_minutes is not None:
        return override_minutes * 60.0
    # Hard TB tasks often have long official limits. Use task metadata when
    # present, but keep a practical self-pilot cap.
    raw = row.get("agent_timeout_sec", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 2 * 60 * 60
    return min(max(value, 30 * 60), 3 * 60 * 60)


def _version(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return " ".join(proc.stdout.strip().split())[:300]


def _prompt_with_runner_context(prompt: str, *, run_id: str, run_root: Path) -> str:
    return (
        "## Automated pilot runner context\n\n"
        f"- Pilot run id: `{run_id}`\n"
        f"- Current working directory / pilot root: `{run_root}`\n"
        "- The runner has sent this initial assignment automatically. If you can "
        "finish without more input, do so. If a human decision is required, stop "
        "and make the request explicit in your final response.\n\n"
        f"{prompt}"
    )


def _codex_command(args: argparse.Namespace, run_root: Path) -> list[str]:
    cmd = [
        args.codex_bin,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        args.codex_sandbox,
        "--cd",
        str(run_root),
    ]
    if args.codex_model:
        cmd.extend(["--model", args.codex_model])
    for extra in args.codex_extra_arg or []:
        cmd.append(extra)
    cmd.append("-")
    return cmd


def _argus_command(args: argparse.Namespace, run_root: Path) -> list[str]:
    if args.argus_cmd:
        cmd = args.argus_cmd[:]
    else:
        cmd = [sys.executable, "-m", "argus_skill"]
    cmd.extend([
        "--no-daemon",
        "--life-dir",
        str(run_root / ".argus-skill"),
    ])
    return cmd


def _command_for_row(args: argparse.Namespace, row: dict[str, str], run_root: Path) -> list[str]:
    if row["condition"] == "codex":
        return _codex_command(args, run_root)
    if row["condition"] == "argus":
        return _argus_command(args, run_root)
    raise SystemExit(f"unknown condition: {row['condition']}")


def _env_for_row(row: dict[str, str], run_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    if row["condition"] == "argus":
        env["ARGUS_SKILL_LIFE_BACKEND"] = "codex"
        env["ARGUS_SKILL_WORKDIR"] = str(run_root)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(REPO_ROOT) if not existing else f"{REPO_ROOT}{os.pathsep}{existing}"
        )
    return env


def _human_request_hint(text: str) -> bool:
    lower = text.lower()
    needles = [
        "need clarification",
        "need more information",
        "cannot proceed",
        "can't proceed",
        "please provide",
        "requires human",
        "human action",
        "docker is unavailable",
        "permission denied",
    ]
    return any(needle in lower for needle in needles)


def _append_csv(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(row)
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_process(
    *,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    stdin_text: str,
    timeout_seconds: float,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[int, bool]:
    timed_out = False
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:
            proc.communicate(stdin_text, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=20)
    return int(proc.returncode or 0), timed_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one TB v2 prompt-only pilot row.")
    parser.add_argument("--order", type=int, help="Assignment order to run.")
    parser.add_argument("--condition", choices=["codex", "argus"], help="Condition selector.")
    parser.add_argument("--task-id", help="Task selector.")
    parser.add_argument("--assignment", type=Path, default=ASSIGNMENT_PATH)
    parser.add_argument("--generated-dir", type=Path, default=GENERATED_DIR)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--results-csv", type=Path, default=RUNS_CSV_PATH)
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--timeout-minutes", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    parser.add_argument("--codex-sandbox", default="danger-full-access")
    parser.add_argument("--codex-model", default=os.environ.get("CODEX_MODEL", ""))
    parser.add_argument(
        "--codex-extra-arg",
        action="append",
        default=[],
        help="Extra argument appended to `codex exec` (repeatable).",
    )
    parser.add_argument(
        "--argus-cmd",
        nargs="+",
        help="Command used to launch argus-skill before --no-daemon/--life-dir.",
    )
    args = parser.parse_args(argv)

    if args.order is None and args.condition is None and args.task_id is None:
        parser.error("pass --order, or a --condition/--task-id selector")

    rows = _load_assignment(args.assignment)
    row = _select_row(
        rows,
        order=args.order,
        condition=args.condition,
        task_id=args.task_id,
    )
    prompt_source, base_prompt = _read_prompt(args.generated_dir, row)
    run_id = _run_id(row)
    run_root = args.run_root or args.runs_dir / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    prompt = _prompt_with_runner_context(base_prompt, run_id=run_id, run_root=run_root)
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    prompt_path = run_root / "prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    cmd = _command_for_row(args, row, run_root)
    env = _env_for_row(row, run_root)
    stdin_text = prompt
    if row["condition"] == "argus":
        stdin_text = f"{prompt}\n\n/exit\n"
    timeout_seconds = _default_timeout_seconds(row, args.timeout_minutes)

    stdout_path = run_root / "stdout.log"
    stderr_path = run_root / "stderr.log"
    metadata = {
        "run_id": run_id,
        "row": row,
        "run_root": str(run_root),
        "prompt_source": str(prompt_source),
        "prompt_path": str(prompt_path),
        "prompt_sha256": prompt_sha,
        "command": cmd,
        "timeout_seconds": timeout_seconds,
        "codex_version": _version([args.codex_bin, "--version"]),
        "argus_version": _version([sys.executable, "-m", "argus_skill", "--version"]),
        "docker_version": _version(["docker", "--version"]),
    }
    _write_json(run_root / "metadata.json", metadata)

    if args.dry_run:
        print(f"run_root: {run_root}")
        print("command:")
        print("  " + " ".join(cmd))
        print(f"prompt: {prompt_path}")
        return 0

    started = _utc_now()
    t0 = time.monotonic()
    exit_code, timed_out = _run_process(
        cmd=cmd,
        cwd=run_root,
        env=env,
        stdin_text=stdin_text,
        timeout_seconds=timeout_seconds,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    wall_seconds = time.monotonic() - t0
    ended = _utc_now()

    combined_tail = ""
    for path in (stdout_path, stderr_path):
        try:
            combined_tail += path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
    workspace = row.get("workspace", "").removeprefix("./")
    workspace_exists = bool(workspace) and (run_root / workspace).exists()
    human_hint = _human_request_hint(combined_tail)
    needs_human = timed_out or exit_code != 0 or human_hint or not workspace_exists
    notes = []
    if timed_out:
        notes.append("timed out")
    if exit_code != 0:
        notes.append(f"exit_code={exit_code}")
    if human_hint:
        notes.append("output appears to request human action")
    if not workspace_exists:
        notes.append(f"workspace not exported: {row.get('workspace', '')}")

    result = {
        "run_id": run_id,
        "order": row["order"],
        "pair_id": row["pair_id"],
        "condition": row["condition"],
        "task_id": row["task_id"],
        "workspace": row["workspace"],
        "docker_image": row["docker_image"],
        "run_root": str(run_root),
        "prompt_file": str(prompt_source),
        "prompt_sha256": prompt_sha,
        "started_at": started,
        "ended_at": ended,
        "wall_minutes": f"{wall_seconds / 60.0:.2f}",
        "exit_code": exit_code,
        "timed_out": timed_out,
        "needs_human": needs_human,
        "human_messages": 1,
        "nudges": 0,
        "status_checks": 0,
        "manual_commands": 0,
        "manual_rescue": "",
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "notes": "; ".join(notes),
    }
    _write_json(run_root / "result.json", result)
    _append_csv(args.results_csv, result)

    print(f"run_root: {run_root}")
    print(f"result  : {run_root / 'result.json'}")
    print(f"logs    : {stdout_path} ; {stderr_path}")
    print(f"csv     : {args.results_csv}")
    print(f"needs_human={str(needs_human).lower()} exit_code={exit_code} timed_out={timed_out}")
    return 1 if needs_human else 0


if __name__ == "__main__":
    raise SystemExit(main())

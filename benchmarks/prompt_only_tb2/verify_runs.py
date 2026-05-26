from __future__ import annotations

import argparse
import csv
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from benchmarks.prompt_only_tb2.summarize_runs import (
    _normalized_zero_touch_success,
    latest_per_assignment,
    load_result_rows,
)

_CONTAINER_RE = re.compile(r"\btb2-[A-Za-z0-9_.-]+\b")


def _safe_task(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _container_names_from_logs(run_root: Path) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for path in (run_root / "stdout.log", run_root / "stderr.log"):
        for match in _CONTAINER_RE.findall(_read_text(path)):
            if match not in seen:
                seen.add(match)
                names.append(match)
    return names


def _parse_time(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _running_containers() -> dict[str, float | None]:
    proc = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.CreatedAt}}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    containers: dict[str, float | None] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        name, _, created = line.partition("\t")
        containers[name.strip()] = _parse_time(created)
    return containers


def _container_created_at(name: str) -> float | None:
    proc = subprocess.run(
        ["docker", "inspect", "--format", "{{.Created}}", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return _parse_time(proc.stdout.strip())


def _row_window(row: dict[str, Any]) -> tuple[float | None, float | None]:
    start = _parse_time(row.get("started_at"))
    end = _parse_time(row.get("ended_at"))
    if start is not None:
        start -= 120
    if end is not None:
        end += 120
    return start, end


def _in_row_window(created: float | None, row: dict[str, Any]) -> bool:
    start, end = _row_window(row)
    if created is None:
        return start is None and end is None
    if start is not None and created < start:
        return False
    if end is not None and created > end:
        return False
    return True


def _candidate_container(
    row: dict[str, Any],
    running: Mapping[str, float | None] | set[str],
) -> str:
    run_root = Path(str(row.get("run_root") or ""))
    prefix = f"tb2-{row.get('condition')}-{_safe_task(str(row.get('task_id') or ''))}"
    running_map: dict[str, float | None]
    if isinstance(running, set):
        running_map = {name: None for name in running}
    else:
        running_map = {
            str(name): created for name, created in running.items()
        }

    names = _container_names_from_logs(run_root)
    names.extend(name for name in running_map if name.startswith(prefix))
    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()
    for name in names:
        if name in seen or name not in running_map:
            continue
        seen.add(name)
        created = running_map[name]
        if created is None:
            created = _container_created_at(name)
        if _in_row_window(created, row):
            candidates.append((float(created or 0), name))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return ""


def _workspace_app(row: dict[str, Any]) -> Path:
    run_root = Path(str(row.get("run_root") or ""))
    workspace = str(row.get("workspace") or "").removeprefix("./")
    app = run_root / workspace / "app"
    if app.exists():
        return app
    return run_root / workspace


def _reward_passed(reward: str) -> bool:
    text = reward.strip()
    if not text:
        return False
    try:
        return float(text) > 0
    except ValueError:
        return text.lower() in {"pass", "passed", "true", "yes"}


def _manual_attention_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "zero_touch_success": _normalized_zero_touch_success(row),
        "human_interactions_after_assignment": row.get(
            "human_interactions_after_assignment", ""
        ),
        "active_touch_minutes_after_assignment": row.get(
            "active_touch_minutes_after_assignment", ""
        ),
        "manual_commands": row.get("manual_commands", ""),
        "manual_rescue": row.get("manual_rescue", ""),
        "intervention_severity": row.get("intervention_severity", ""),
        "needs_human": row.get("needs_human", ""),
    }


def _run(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _verify_live_container(
    *,
    row: dict[str, Any],
    container: str,
    tests_dir: Path,
    log_dir: Path,
    timeout: int,
) -> dict[str, str]:
    setup = _run(
        [
            "docker",
            "exec",
            container,
            "bash",
            "-lc",
            "rm -rf /tests /logs/verifier && mkdir -p /tests /logs/verifier",
        ],
        timeout=60,
    )
    copy = _run(
        ["docker", "cp", f"{tests_dir.resolve()}/.", f"{container}:/tests"],
        timeout=120,
    )
    result = _run(
        ["docker", "exec", "-w", "/app", container, "bash", "/tests/test.sh"],
        timeout=timeout,
    )
    reward_proc = _run(
        [
            "docker",
            "exec",
            container,
            "bash",
            "-lc",
            "cat /logs/verifier/reward.txt 2>/dev/null || true",
        ],
        timeout=30,
    )
    reward = reward_proc.stdout.strip()
    output = (
        f"$ docker exec {container} <setup tests>\n{setup.stdout}\n"
        f"$ docker cp {tests_dir} {container}:/tests\n{copy.stdout}\n"
        f"$ docker exec -w /app {container} bash /tests/test.sh\n{result.stdout}\n"
        f"$ cat /logs/verifier/reward.txt\n{reward}\n"
    )
    (log_dir / "official-verifier.log").write_text(
        output, encoding="utf-8", errors="replace"
    )
    return {
        "accepted": str(_reward_passed(reward)),
        "reward": reward or "0",
        "verifier_exit": str(result.returncode),
        "verification_mode": "live_container",
        "container": container,
    }


def _verify_exported_app(
    *,
    row: dict[str, Any],
    app_dir: Path,
    tests_dir: Path,
    log_dir: Path,
    timeout: int,
) -> dict[str, str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{app_dir.resolve()}:/candidate:ro",
        "-v",
        f"{tests_dir.resolve()}:/tests:ro",
        "-v",
        f"{log_dir.resolve()}:/logs",
        "-w",
        "/app",
        str(row["docker_image"]),
        "bash",
        "-lc",
        "cp -a /candidate/. /app/ && bash /tests/test.sh",
    ]
    result = _run(cmd, timeout=timeout)
    (log_dir / "official-verifier.log").write_text(
        result.stdout, encoding="utf-8", errors="replace"
    )
    reward_path = log_dir / "verifier" / "reward.txt"
    reward = _read_text(reward_path).strip()
    return {
        "accepted": str(_reward_passed(reward)),
        "reward": reward or "0",
        "verifier_exit": str(result.returncode),
        "verification_mode": "exported_app",
        "container": "",
    }


def verify_row(
    row: dict[str, Any],
    *,
    tb_tasks_dir: Path,
    running: Mapping[str, float | None],
    prefer_live_containers: bool,
    timeout: int,
) -> dict[str, str]:
    run_root = Path(str(row["run_root"]))
    base_log_dir = run_root / (
        "verification-reward-live"
        if prefer_live_containers
        else "verification-reward-exported"
    )
    log_dir = base_log_dir
    suffix = 2
    while log_dir.exists():
        log_dir = run_root / f"{base_log_dir.name}-{suffix}"
        suffix += 1
    log_dir.mkdir(parents=True)

    tests_dir = tb_tasks_dir / str(row["task_id"]) / "tests"
    if not tests_dir.exists():
        raise FileNotFoundError(f"tests directory not found: {tests_dir}")

    container = _candidate_container(row, running) if prefer_live_containers else ""
    if container:
        result = _verify_live_container(
            row=row,
            container=container,
            tests_dir=tests_dir,
            log_dir=log_dir,
            timeout=timeout,
        )
    else:
        result = _verify_exported_app(
            row=row,
            app_dir=_workspace_app(row),
            tests_dir=tests_dir,
            log_dir=log_dir,
            timeout=timeout,
        )

    return {
        "order": str(row["order"]),
        "condition": str(row["condition"]),
        "task_id": str(row["task_id"]),
        **_manual_attention_fields(row),
        **result,
        "log": str((log_dir / "official-verifier.log").resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify prompt-only TB2 runs.")
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--tb-tasks-dir",
        type=Path,
        default=Path("/tmp/terminal-bench-2-1/tasks"),
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--no-live-containers",
        action="store_true",
        help="Verify only exported app snapshots, not still-running task containers.",
    )
    args = parser.parse_args(argv)

    rows = latest_per_assignment(load_result_rows(args.runs_dir))
    running = _running_containers()
    output_rows = []
    for row in sorted(rows, key=lambda r: int(r["order"])):
        print(f"=== verify order {row['order']} {row['condition']} {row['task_id']} ===")
        result = verify_row(
            row,
            tb_tasks_dir=args.tb_tasks_dir,
            running=running,
            prefer_live_containers=not args.no_live_containers,
            timeout=args.timeout_seconds,
        )
        print(
            "accepted={accepted} reward={reward} mode={verification_mode} "
            "container={container}".format(**result)
        )
        output_rows.append(result)

    out = args.out or args.runs_dir / "verification_summary.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "order",
                "condition",
                "task_id",
                "zero_touch_success",
                "human_interactions_after_assignment",
                "active_touch_minutes_after_assignment",
                "manual_commands",
                "manual_rescue",
                "intervention_severity",
                "needs_human",
                "accepted",
                "reward",
                "verifier_exit",
                "verification_mode",
                "container",
                "log",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

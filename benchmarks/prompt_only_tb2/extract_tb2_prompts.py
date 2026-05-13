from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

DATASET_NAME = "terminal-bench@2.0"
DEFAULT_DOWNLOAD_DIR = Path("/tmp/argus-skill-tb2-prompt-only")
DEFAULT_TASKS_ROOT = DEFAULT_DOWNLOAD_DIR / "terminal-bench"
OUT_DIR = Path(__file__).resolve().parent / "generated"
SELECTION_PATH = Path(__file__).resolve().parent / "self_pilot_selection.json"


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _download_dataset(download_dir: Path, *, overwrite: bool) -> Path:
    harbor = shutil.which("harbor")
    if harbor is None:
        raise SystemExit(
            "harbor CLI not found. Install Harbor or pass --tasks-root pointing "
            "at an existing terminal-bench@2.0 export."
        )
    cmd = [
        harbor,
        "dataset",
        "download",
        DATASET_NAME,
        "-o",
        str(download_dir),
        "--export",
    ]
    if overwrite:
        cmd.append("--overwrite")
    _run(cmd)
    return download_dir / "terminal-bench"


def _load_task(task_dir: Path) -> dict[str, Any]:
    task_id = task_dir.name
    instruction_path = task_dir / "instruction.md"
    config_path = task_dir / "task.toml"
    if not instruction_path.exists() or not config_path.exists():
        raise FileNotFoundError(f"missing instruction.md or task.toml for {task_id}")

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    metadata = config.get("metadata", {})
    agent = config.get("agent", {})
    verifier = config.get("verifier", {})
    environment = config.get("environment", {})
    instruction = instruction_path.read_text(encoding="utf-8").strip()
    return {
        "task_id": task_id,
        "dataset": DATASET_NAME,
        "difficulty": metadata.get("difficulty", ""),
        "category": metadata.get("category", ""),
        "tags": metadata.get("tags", []),
        "expert_time_estimate_min": metadata.get("expert_time_estimate_min", ""),
        "junior_time_estimate_min": metadata.get("junior_time_estimate_min", ""),
        "agent_timeout_sec": agent.get("timeout_sec", ""),
        "verifier_timeout_sec": verifier.get("timeout_sec", ""),
        "docker_image": environment.get("docker_image", ""),
        "prompt": instruction,
    }


def _load_tasks(tasks_root: Path) -> dict[str, dict[str, Any]]:
    if not tasks_root.exists():
        raise SystemExit(f"tasks root not found: {tasks_root}")
    tasks: dict[str, dict[str, Any]] = {}
    for task_dir in sorted(p for p in tasks_root.iterdir() if p.is_dir()):
        tasks[task_dir.name] = _load_task(task_dir)
    if not tasks:
        raise SystemExit(f"no tasks found under {tasks_root}")
    return tasks


def _load_selection() -> dict[str, Any]:
    return json.loads(SELECTION_PATH.read_text(encoding="utf-8"))


def _selected_rows(selection: dict[str, Any], tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    order = 1
    for pair in selection["pairs"]:
        for condition, key in (("codex", "codex_task_id"), ("argus", "argus_task_id")):
            task_id = pair[key]
            if task_id not in tasks:
                raise SystemExit(f"selection references missing task: {task_id}")
            task = tasks[task_id]
            rows.append({
                "order": order,
                "pair_id": pair["pair_id"],
                "condition": condition,
                "task_id": task_id,
                "difficulty": task["difficulty"],
                "category": task["category"],
                "expert_time_estimate_min": task["expert_time_estimate_min"],
                "docker_image": task["docker_image"],
                "rationale": pair["rationale"],
            })
            order += 1
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _write_prompt_pack(path: Path, selected: list[dict[str, Any]], tasks: dict[str, dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("TB v2 prompt-only self pilot\n")
        fh.write("============================\n\n")
        for row in selected:
            task = tasks[row["task_id"]]
            fh.write(f"Order: {row['order']}\n")
            fh.write(f"Condition: {row['condition']}\n")
            fh.write(f"Pair: {row['pair_id']}\n")
            fh.write(f"Task: {row['task_id']}\n")
            fh.write(f"Difficulty: {task['difficulty']}\n")
            fh.write(f"Category: {task['category']}\n")
            fh.write(f"Expert estimate min: {task['expert_time_estimate_min']}\n")
            fh.write(f"Docker image: {task['docker_image']}\n")
            fh.write("\nPrompt:\n")
            fh.write(task["prompt"])
            fh.write("\n\n" + "-" * 80 + "\n\n")


def _write_results_template(path: Path, selected: list[dict[str, Any]]) -> None:
    fields = [
        "order",
        "pair_id",
        "condition",
        "task_id",
        "started_at",
        "ended_at",
        "solved",
        "accepted",
        "wall_minutes",
        "active_touch_minutes",
        "human_messages",
        "nudges",
        "status_checks",
        "manual_commands",
        "manual_rescue",
        "test_runs",
        "cost_usd",
        "notes",
    ]
    rows = [{**row, **{field: "" for field in fields if field not in row}} for row in selected]
    _write_csv(path, rows, fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download terminal-bench@2.0 via Harbor before extracting.",
    )
    parser.add_argument(
        "--overwrite-download",
        action="store_true",
        help="Overwrite an existing Harbor export when used with --download.",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=DEFAULT_DOWNLOAD_DIR,
        help=f"Harbor export directory (default: {DEFAULT_DOWNLOAD_DIR}).",
    )
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=None,
        help="Existing terminal-bench task root containing task directories.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR}).",
    )
    args = parser.parse_args(argv)

    tasks_root = args.tasks_root
    if args.download:
        tasks_root = _download_dataset(args.download_dir, overwrite=args.overwrite_download)
    if tasks_root is None:
        tasks_root = DEFAULT_TASKS_ROOT

    tasks = _load_tasks(tasks_root)
    selection = _load_selection()
    selected = _selected_rows(selection, tasks)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    all_rows = [tasks[task_id] for task_id in sorted(tasks)]
    _write_jsonl(out_dir / "tb2_prompt_only_all.jsonl", all_rows)
    _write_csv(
        out_dir / "self_pilot_assignment.csv",
        selected,
        [
            "order",
            "pair_id",
            "condition",
            "task_id",
            "difficulty",
            "category",
            "expert_time_estimate_min",
            "docker_image",
            "rationale",
        ],
    )
    _write_prompt_pack(out_dir / "self_pilot_prompts.txt", selected, tasks)
    _write_results_template(out_dir / "results_template.csv", selected)

    print(f"loaded {len(tasks)} tasks from {tasks_root}")
    print(f"wrote prompt-only files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

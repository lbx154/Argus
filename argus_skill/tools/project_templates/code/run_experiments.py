"""Project-local experiment fan-out coordinator — standalone helper.

Launches a method x benchmark matrix as NON-BLOCKING sub-agent jobs so the
engineer submits everything and keeps working (drafting, analysis, more code)
instead of blocking on GPU runs. It is a *coordinator*, not a scheduler: it
wraps the framework's existing ``argus_skill.tools.subagent`` tool and assigns
GPUs by an explicit policy.

Define the matrix in ``experiments/MATRIX.json``::

    {
      "gpu_policy": "explicit",
      "conditions": [
        {"id": "baseline_no_skill", "command": ".venv/bin/python code/run_eval.py --method no_skill", "gpus": "0"},
        {"id": "proposed",          "command": ".venv/bin/python code/run_eval.py --method proposed", "gpus": "1,2,3", "run_dir": "experiments/runs/proposed"}
      ]
    }

Usage from the project root::

    .venv/bin/python code/run_experiments.py submit            # preview + launch
    .venv/bin/python code/run_experiments.py submit --dry-run  # show argv only
    .venv/bin/python code/run_experiments.py status            # aggregate status

GPU policies:
  * ``explicit``        - every condition must declare ``gpus`` (safest; required
                          for any multi-GPU/distributed command). Recommended.
  * ``fanout_one_gpu``  - round-robin one visible GPU per condition. Use ONLY when
                          each condition is single-GPU safe; maximizes throughput
                          by running conditions in parallel on different GPUs.
  * For one big job that needs every GPU, use ``explicit`` with a single
    condition whose ``gpus`` lists all devices, and launch your distributed
    runner inside its ``command``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import gpu_env
except ImportError as exc:  # pragma: no cover - only when run from wrong cwd
    raise SystemExit(
        "run_experiments.py must run from the project root so it can import its "
        "sibling code/gpu_env.py (e.g. `python code/run_experiments.py ...`)."
    ) from exc

try:
    import experiment_io
except ImportError:  # pragma: no cover - optional, only used for status audit
    experiment_io = None  # type: ignore[assignment]

DEFAULT_MATRIX_PATH = "experiments/MATRIX.json"
DEFAULT_TIMEOUT_SECONDS = 86_400
SUBAGENT_MODULE = "argus_skill.tools.subagent"


def framework_python() -> str:
    """Interpreter that can import the Argus harness (for the subagent tool)."""
    return os.environ.get("ARGUS_SKILL_PYTHON") or "python"


def _subagent_importable(python: str) -> bool:
    try:
        result = subprocess.run(
            [python, "-c", "import argus_skill.tools.subagent"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def load_matrix(path: str | os.PathLike[str]) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: matrix must be a JSON object")
    conditions = data.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError(f"{path}: 'conditions' must be a non-empty list")
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            raise ValueError(f"{path}: condition #{index} is not an object")
        if not condition.get("id"):
            raise ValueError(f"{path}: condition #{index} is missing 'id'")
        if not condition.get("command"):
            raise ValueError(f"{path}: condition {condition.get('id')!r} is missing 'command'")
    return data


def assign_gpus(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve each condition's GPU set per the matrix policy.

    Returns a list of plans: {id, command, gpus, timeout, run_dir}.
    Raises ValueError on policy violations (e.g. explicit policy with no gpus).
    """
    policy = matrix.get("gpu_policy", "explicit")
    visible = gpu_env.visible_devices()
    plans: list[dict[str, Any]] = []
    for index, condition in enumerate(matrix["conditions"]):
        gpus = condition.get("gpus")
        if gpus is None:
            if policy == "fanout_one_gpu":
                gpus = visible[index % len(visible)] if visible else ""
            elif policy == "explicit":
                raise ValueError(
                    f"condition {condition['id']!r}: gpu_policy 'explicit' requires "
                    "a 'gpus' field (e.g. \"0\" or \"0,1,2,3\"). Set it, or switch "
                    "gpu_policy to 'fanout_one_gpu' for single-GPU conditions."
                )
            else:
                raise ValueError(f"unknown gpu_policy {policy!r}")
        gpus = str(gpus)
        _warn_unallocated(condition["id"], gpus, visible)
        plans.append(
            {
                "id": condition["id"],
                "command": condition["command"],
                "gpus": gpus,
                "timeout": int(condition.get("timeout", DEFAULT_TIMEOUT_SECONDS)),
                "run_dir": condition.get("run_dir"),
            }
        )
    _warn_oversubscription(plans)
    return plans


def _warn_unallocated(condition_id: str, gpus: str, visible: list[str]) -> None:
    if not visible:
        return
    requested = [token.strip() for token in gpus.split(",") if token.strip()]
    outside = [device for device in requested if device not in visible]
    if outside:
        print(
            f"WARNING: condition {condition_id!r} requests GPU(s) {outside} outside "
            f"the allocation {visible}. Use only allocated devices.",
            file=sys.stderr,
        )


def _warn_oversubscription(plans: list[dict[str, Any]]) -> None:
    used: dict[str, list[str]] = {}
    for plan in plans:
        for device in (token.strip() for token in plan["gpus"].split(",") if token.strip()):
            used.setdefault(device, []).append(plan["id"])
    contended = {device: ids for device, ids in used.items() if len(ids) > 1}
    if contended:
        print(
            "WARNING: these conditions will run in parallel on the SAME GPU(s) and "
            f"may oversubscribe memory: {contended}. Give them disjoint GPUs or "
            "submit them sequentially.",
            file=sys.stderr,
        )


def build_submit_argv(python: str, plan: dict[str, Any], description: str) -> list[str]:
    inner = plan["command"]
    if plan["gpus"]:
        inner = f"env CUDA_VISIBLE_DEVICES={plan['gpus']} {inner}"
    return [
        python,
        "-m",
        SUBAGENT_MODULE,
        "submit",
        "--task-id",
        str(plan["id"]),
        "--description",
        description,
        "--command",
        inner,
        "--timeout",
        str(plan["timeout"]),
    ]


def cmd_submit(args: argparse.Namespace) -> int:
    matrix = load_matrix(args.matrix)
    plans = assign_gpus(matrix)
    python = framework_python()

    print(gpu_env.readiness_report())
    print("\n# Experiment matrix")
    for plan in plans:
        print(f"  - {plan['id']}: GPUs={plan['gpus'] or 'CPU'}  cmd={plan['command']}")
    print()

    if not args.dry_run and not _subagent_importable(python):
        print(
            f"ERROR: interpreter {python!r} cannot import {SUBAGENT_MODULE}. Set "
            "ARGUS_SKILL_PYTHON to the framework interpreter, or run under the daemon "
            "which injects it.",
            file=sys.stderr,
        )
        return 1

    for plan in plans:
        description = f"experiment condition {plan['id']}"
        argv = build_submit_argv(python, plan, description)
        if args.dry_run:
            print("DRY-RUN " + json.dumps(argv))
            continue
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
        sys.stdout.write(result.stdout)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            print(f"  submit FAILED for {plan['id']!r}", file=sys.stderr)
    if not args.dry_run:
        print(
            "\nSubmitted. Do NOT block: continue other work and poll with "
            "`python code/run_experiments.py status`. When a run completes, collect "
            "it (read status.json, count rows, write RUN_REPORT.md, update "
            "research/PIPELINE_STATE.json)."
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    matrix = load_matrix(args.matrix)
    python = framework_python()
    print(f"{'condition':<28} {'subagent':<12} {'rows':<8} run-dir contract")
    print("-" * 78)
    overall_ok = True
    for condition in matrix["conditions"]:
        task_id = str(condition["id"])
        sub_state = _subagent_state(python, task_id)
        rows = "-"
        contract = ""
        run_dir = condition.get("run_dir")
        if run_dir and experiment_io is not None:
            audit = experiment_io.validate_run(run_dir)
            rows = str(sum(audit["rows_by_method"].values()))
            if audit["complete_contract"]:
                contract = "ok"
            else:
                contract = "MISSING: " + ",".join(audit["missing_artifacts"])
                overall_ok = False
        elif run_dir:
            contract = "(install experiment_io to audit)"
        print(f"{task_id:<28} {sub_state:<12} {rows:<8} {contract}")
        if sub_state not in {"done", "completed"}:
            overall_ok = False
    print()
    print("All conditions complete and contract-valid." if overall_ok
          else "Some conditions are still running or missing run artifacts.")
    return 0 if overall_ok else 1


def _subagent_state(python: str, task_id: str) -> str:
    try:
        result = subprocess.run(
            [python, "-m", SUBAGENT_MODULE, "status", "--task-id", task_id],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "unknown"
    if "error" in data:
        return "not-found"
    return str(data.get("state", "unknown"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--matrix", default=DEFAULT_MATRIX_PATH, help="path to MATRIX.json")
    sub = parser.add_subparsers(dest="command", required=True)
    p_submit = sub.add_parser("submit", help="Launch all conditions as non-blocking sub-agent jobs")
    p_submit.add_argument("--dry-run", action="store_true", help="print submit argv without launching")
    p_submit.set_defaults(func=cmd_submit)
    p_status = sub.add_parser("status", help="Show aggregate status of all conditions")
    p_status.set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

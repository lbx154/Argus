"""Launch TB v2 fullbench sweeps with deterministic replicate metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from benchmarks.experiment_launcher import LaunchSpec, launch_detached
from benchmarks.tb2_fullbench_launcher import SUPPORTED_CONDITIONS, _build_spec, _slug


def _default_sweep_id(conditions: Iterable[str], replicates: int) -> str:
    condition_slug = _slug("-".join(conditions))
    return f"tb2-sweep-{condition_slug}-rep{replicates:02d}"


def _with_replicate_metadata(
    spec: LaunchSpec,
    *,
    sweep_id: str,
    conditions: list[str],
    replicate_index: int,
    replicate_total: int,
) -> LaunchSpec:
    metadata = dict(spec.metadata)
    metadata.update(
        {
            "sweep_id": sweep_id,
            "sweep_conditions": list(conditions),
            "replicate_index": replicate_index,
            "replicate_total": replicate_total,
        }
    )
    return LaunchSpec(
        run_root=spec.run_root,
        run_id=spec.run_id,
        command=spec.command,
        cwd=spec.cwd,
        env=spec.env,
        metadata=metadata,
        preflight=spec.preflight,
        stdout_log=spec.stdout_log,
        stderr_log=spec.stderr_log,
    )


def launch_sweep(
    *,
    conditions: list[str],
    run_root: Path,
    replicates: int,
    sweep_id: str | None = None,
) -> dict[str, Any]:
    if replicates < 1:
        raise SystemExit("--replicates must be >= 1")
    if not conditions:
        raise SystemExit("at least one --condition is required")

    normalized_conditions = list(conditions)
    invalid = [condition for condition in normalized_conditions if condition not in SUPPORTED_CONDITIONS]
    if invalid:
        raise SystemExit("unknown condition(s): " + ", ".join(sorted(set(invalid))))

    run_root = run_root.resolve()
    sweep_id = sweep_id or _default_sweep_id(normalized_conditions, replicates)
    sweep_root = run_root / sweep_id
    launched: list[dict[str, Any]] = []
    for condition in normalized_conditions:
        for replicate_index in range(1, replicates + 1):
            run_id = f"{condition}-r{replicate_index:02d}-of{replicates:02d}"
            spec = _with_replicate_metadata(
                _build_spec(condition, sweep_root, run_id),
                sweep_id=sweep_id,
                conditions=normalized_conditions,
                replicate_index=replicate_index,
                replicate_total=replicates,
            )
            run_dir = launch_detached(spec)
            launched.append(
                {
                    "condition": condition,
                    "replicate_index": replicate_index,
                    "replicate_total": replicates,
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "manifest_json": str(run_dir / "manifest.json"),
                    "status_json": str(run_dir / "status.json"),
                    "pid": str(run_dir / "pid"),
                    "stdout_log": str(run_dir / "stdout.log"),
                    "stderr_log": str(run_dir / "stderr.log"),
                }
            )

    summary = {
        "launcher": "tb2_fullbench_matrix_launcher",
        "sweep_id": sweep_id,
        "run_root": str(run_root),
        "sweep_root": str(sweep_root),
        "conditions": normalized_conditions,
        "replicates": replicates,
        "run_count": len(launched),
        "runs": launched,
    }
    sweep_root.mkdir(parents=True, exist_ok=True)
    (sweep_root / "launch-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--condition",
        action="append",
        default=[],
        choices=SUPPORTED_CONDITIONS,
        help="TB2 conditions to include in the sweep. Repeatable.",
    )
    parser.add_argument(
        "--replicates",
        type=int,
        default=1,
        help="Number of replicates to launch per condition.",
    )
    parser.add_argument(
        "--sweep-id",
        help="Optional explicit sweep id. Defaults to a deterministic slug.",
    )
    parser.add_argument(
        "--run-root",
        default="experiments",
        help="Root directory for sweep bundles.",
    )
    args = parser.parse_args(argv)

    summary = launch_sweep(
        conditions=args.condition,
        run_root=Path(args.run_root),
        replicates=args.replicates,
        sweep_id=args.sweep_id,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

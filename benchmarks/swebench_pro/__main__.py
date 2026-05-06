"""SWE-Bench-Pro CLI for argus-skill.

Examples:
    # smoke (1 task per repo, NodeBB only):
    python -m benchmarks.swebench_pro --repos NodeBB/NodeBB --max-tasks-per-repo 1

    # 10 tasks across all repos, results to a custom dir:
    python -m benchmarks.swebench_pro --max-tasks-per-repo 1 \
        --output-dir benchmarks/results/swebench_pro_argus_v1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .runner import (
    _DEFAULT_ENGINEER_EFFORT,
    _DEFAULT_ENGINEER_MODEL,
    _DEFAULT_MAX_ROUNDS,
    _DEFAULT_REVIEWER_EFFORT,
    _DEFAULT_REVIEWER_MODEL,
    _DEFAULT_ROUND_TIMEOUT,
    TaskResult,
    run_one_task,
)
from .task_loader import Task, load_tasks


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SWE-Bench-Pro for argus-skill")
    p.add_argument("--dataset", default="ScaleAI/SWE-bench_Pro")
    p.add_argument("--split", default="test")
    p.add_argument(
        "--local-jsonl",
        default=os.environ.get("ARGUS_SKILL_SWEBPRO_LOCAL_JSONL", ""),
        help="Optional local JSONL of tasks (overrides HF dataset).",
    )
    p.add_argument(
        "--namespace", default="jefzda",
        help="Dockerhub namespace hosting sweap-images (default: jefzda).",
    )
    p.add_argument("--repos", nargs="+", default=None)
    p.add_argument("--instance-ids", nargs="+", default=None)
    p.add_argument("--max-tasks-per-repo", type=int, default=None)
    p.add_argument("--engineer-model", default=_DEFAULT_ENGINEER_MODEL)
    p.add_argument("--engineer-effort", default=_DEFAULT_ENGINEER_EFFORT)
    p.add_argument("--reviewer-model", default=_DEFAULT_REVIEWER_MODEL)
    p.add_argument("--reviewer-effort", default=_DEFAULT_REVIEWER_EFFORT)
    p.add_argument("--max-rounds", type=int, default=_DEFAULT_MAX_ROUNDS)
    p.add_argument("--round-timeout", type=int, default=_DEFAULT_ROUND_TIMEOUT)
    p.add_argument("--no-reviewer", action="store_true",
                   help="Ablation: skip reviewer (1 engineer round only when set).")
    p.add_argument("--workers", type=int, default=int(
        os.environ.get("ARGUS_SKILL_SWEBPRO_WORKERS", "4")))
    p.add_argument(
        "--output-dir",
        default="benchmarks/results/swebench_pro_argus",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


async def _run_task_safe(task: Task, args, logger: logging.Logger) -> TaskResult:
    try:
        return await run_one_task(
            task,
            namespace=args.namespace,
            engineer_model=args.engineer_model,
            engineer_effort=args.engineer_effort,
            reviewer_model=args.reviewer_model,
            reviewer_effort=args.reviewer_effort,
            max_rounds=args.max_rounds,
            round_timeout=args.round_timeout,
            no_reviewer=args.no_reviewer,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] unhandled exception", task.instance_id)
        return TaskResult(
            instance_id=task.instance_id,
            repo=task.repo,
            error=f"{type(exc).__name__}:{exc}"[:500],
        )


async def _main_async(args: argparse.Namespace) -> int:
    logger = logging.getLogger("argus_skill.swebench_pro.cli")
    tasks = load_tasks(
        dataset=args.dataset,
        split=args.split,
        local_jsonl=args.local_jsonl or None,
        repos=args.repos,
        max_tasks_per_repo=args.max_tasks_per_repo,
        instance_ids=args.instance_ids,
    )
    logger.info("loaded %d tasks", len(tasks))
    if args.dry_run:
        for t in tasks:
            print(f"  {t.instance_id}\t{t.repo}\t{t.docker_image(args.namespace)}")
        return 0
    if not tasks:
        logger.warning("no tasks selected")
        return 0

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    # Truncate / start fresh; consumers (eval_swebench_pro_partial.sh) tolerate dups.
    results_path.write_text("")

    sema = asyncio.Semaphore(max(1, args.workers))

    async def worker(t: Task) -> TaskResult:
        async with sema:
            return await _run_task_safe(t, args, logger)

    coros = [worker(t) for t in tasks]
    n_ok = n_err = n_empty = 0
    for fut in asyncio.as_completed(coros):
        r = await fut
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")
        if r.error:
            n_err += 1
        elif not r.patch:
            n_empty += 1
        else:
            n_ok += 1
        logger.info(
            "progress: ok=%d empty=%d err=%d / total=%d",
            n_ok, n_empty, n_err, len(tasks),
        )

    summary = {
        "total": len(tasks),
        "patched": n_ok,
        "empty": n_empty,
        "errored": n_err,
        "engineer_model": args.engineer_model,
        "reviewer_model": args.reviewer_model,
        "max_rounds": args.max_rounds,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    logger.info("wrote %s", results_path)
    logger.info("summary: %s", summary)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

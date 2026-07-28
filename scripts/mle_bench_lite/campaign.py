#!/usr/bin/env python3
"""Continuously keep two prepared Lite competitions in flight."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

HERE = Path(__file__).resolve().parent


def env_cfg() -> dict[str, str]:
    out = {}
    for line in (HERE / "config.env").read_text().splitlines():
        if line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


CFG = env_cfg()
ROOT = Path(CFG["CAMPAIGN_ROOT"])
DATA = Path(CFG["DATA_ROOT"])
STATE = ROOT / "campaign-state.json"
LEDGER = ROOT / "waves.jsonl"
COMPETITION_LIST = Path(CFG.get("COMPETITION_LIST", str(HERE / "lite.txt")))


def ensure_grade_watcher() -> None:
    """Keep one detached Reviewer-gated grading watcher alive."""
    grade_root = ROOT / "grades"
    state_path = grade_root / "reviewer-approved-state.json"
    if state_path.exists():
        try:
            pid = int(json.loads(state_path.read_text()).get("pid", 0))
            if pid > 0:
                os.kill(pid, 0)
                return
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            pass
    grade_root.mkdir(parents=True, exist_ok=True)
    log = (grade_root / "grade-watcher.out").open("a")
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "grade_watcher.py")],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    log.close()
    (grade_root / "grade-watcher.pid").write_text(f"{proc.pid}\n")


def atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def prepared(comp: str) -> bool:
    base = DATA / comp / "prepared"
    public = base / "public"
    private = base / "private"
    return (
        public.is_dir()
        and next(public.iterdir(), None) is not None
        and private.is_dir()
        and next(private.iterdir(), None) is not None
    )


def result_path(comp: str) -> Path:
    return ROOT / "projects" / comp / "run-result.json"


def completed_result(comp: str) -> bool:
    path = result_path(comp)
    if not path.exists():
        return False
    try:
        return bool(json.loads(path.read_text()).get("benchmark_complete"))
    except (OSError, ValueError):
        return False


def run(slot: int, comp: str) -> dict[str, object]:
    before = time.time()
    proc = subprocess.run([str(HERE / "run_competition.sh"), str(slot), comp], check=False)
    payload = {"competition": comp, "slot": slot, "controller_exit_code": proc.returncode}
    if result_path(comp).exists():
        payload.update(json.loads(result_path(comp).read_text()))
    payload["controller_seconds"] = time.time() - before
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    ensure_grade_watcher()
    workers = max(1, min(2, args.workers))
    competitions = [x for x in COMPETITION_LIST.read_text().splitlines() if x]
    completed = {c for c in competitions if completed_result(c)}
    state: dict[str, object] = {
        "started_at": time.time(), "workers": workers,
        "completed": sorted(completed), "running": {}, "pending": [c for c in competitions if c not in completed],
        "retry_after": {},
    }
    atomic(STATE, state)
    wave = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        running = {}
        free_slots = list(range(workers))
        retry_after: dict[str, float] = {}
        while len(completed) < len(competitions):
            running_comps = {comp for _slot, comp in running.values()}
            now = time.time()
            candidates = [
                c for c in competitions
                if c not in completed
                and c not in running_comps
                and retry_after.get(c, 0) <= now
                and prepared(c)
            ]
            while free_slots and candidates:
                slot = free_slots.pop(0)
                comp = candidates.pop(0)
                running[pool.submit(run, slot, comp)] = (slot, comp)
            state["running"] = {str(slot): comp for slot, comp in running.values()}
            state["completed"] = sorted(completed)
            running_comps = {comp for _slot, comp in running.values()}
            state["pending"] = [c for c in competitions if c not in completed and c not in running_comps]
            state["updated_at"] = time.time()
            atomic(STATE, state)
            if not running:
                time.sleep(max(5, args.poll_seconds))
                continue
            done, _ = wait(running, timeout=max(5, args.poll_seconds), return_when=FIRST_COMPLETED)
            for future in done:
                slot, comp = running.pop(future)
                result = future.result()
                if result.get("benchmark_complete"):
                    completed.add(comp)
                else:
                    retry_after[comp] = time.time() + 300
                free_slots.append(slot)
                wave += 1
                result["wave_completion_index"] = wave
                with LEDGER.open("a") as handle:
                    handle.write(json.dumps(result, sort_keys=True) + "\n")
            state["retry_after"] = retry_after
    state["running"] = {}
    state["completed"] = sorted(completed)
    state["pending"] = []
    state["finished_at"] = time.time()
    atomic(STATE, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

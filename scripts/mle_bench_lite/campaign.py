#!/usr/bin/env python3
"""Supervise two MLE slots until every selected competition earns a medal.

The supervisor owns process lifecycle only. Argus owns each project, Reviewer
owns submission approval, and ``grade_watcher.py`` owns private grading. This
module joins those contracts without inspecting project internals.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def env_cfg() -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in (HERE / "config.env").read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value
    return out


CFG = env_cfg()
ROOT = Path(CFG["CAMPAIGN_ROOT"])
DATA = Path(CFG["DATA_ROOT"])
STATE = ROOT / "campaign-state.json"
LEDGER = ROOT / "waves.jsonl"
GRADE_HISTORY = ROOT / "grades" / "reviewer-approved-history.jsonl"
COMPETITION_LIST = Path(CFG.get("COMPETITION_LIST", str(HERE / "lite.txt")))
RUN_SCRIPT = (HERE / "run_competition.sh").resolve()


def atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def competition_ids() -> list[str]:
    return [
        line
        for raw in COMPETITION_LIST.read_text().splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


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


def run_result(comp: str) -> dict[str, Any]:
    path = result_path(comp)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def grade_history() -> list[dict[str, Any]]:
    if not GRADE_HISTORY.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in GRADE_HISTORY.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def medal_report(comp: str) -> dict[str, Any] | None:
    reports = [
        row
        for row in grade_history()
        if row.get("competition") == comp
        and isinstance(row.get("report"), dict)
        and bool(row["report"].get("any_medal"))
    ]
    return reports[-1] if reports else None


def completed_result(comp: str) -> bool:
    return bool(run_result(comp).get("benchmark_complete")) and medal_report(comp) is not None


@dataclass
class ActiveRun:
    slot: int
    competition: str
    pid: int
    started_at: float
    owned: subprocess.Popen[bytes] | None = None

    def alive(self) -> bool:
        if self.owned is not None:
            return self.owned.poll() is None
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        return True


def _process_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def discover_running() -> dict[int, ActiveRun]:
    """Adopt surviving slot workers after a controller restart."""
    found: dict[int, ActiveRun] = {}
    script = str(RUN_SCRIPT)
    for proc_dir in Path("/proc").glob("[0-9]*"):
        pid = int(proc_dir.name)
        parts = _process_cmdline(pid)
        try:
            index = parts.index(script)
            slot = int(parts[index + 1])
            competition = parts[index + 2]
        except (ValueError, IndexError):
            continue
        if slot in (0, 1) and slot not in found:
            found[slot] = ActiveRun(slot, competition, pid, time.time())
    return found


def start_run(slot: int, competition: str) -> ActiveRun:
    proc = subprocess.Popen(
        [str(RUN_SCRIPT), str(slot), competition],
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return ActiveRun(slot, competition, proc.pid, time.time(), proc)


def ensure_grade_watcher() -> None:
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


def append_ledger(result: dict[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as handle:
        handle.write(json.dumps(result, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--retry-seconds", type=float, default=30)
    args = parser.parse_args()
    workers = max(1, min(2, args.workers))
    competitions = competition_ids()
    ensure_grade_watcher()

    previous: dict[str, Any] = {}
    if STATE.exists():
        try:
            previous = json.loads(STATE.read_text())
        except (OSError, ValueError):
            previous = {}
    completed = {comp for comp in competitions if completed_result(comp)}
    running = {
        slot: run
        for slot, run in discover_running().items()
        if slot < workers and run.competition in competitions
    }
    retry_after: dict[str, float] = {}
    wave = sum(1 for _ in LEDGER.open()) if LEDGER.exists() else 0
    state: dict[str, Any] = {
        "started_at": previous.get("started_at", time.time()),
        "workers": workers,
        "completion_gate": "reviewer-approved bronze medal or better",
    }

    while len(completed) < len(competitions):
        now = time.time()
        for slot, active in list(running.items()):
            if active.alive():
                continue
            running.pop(slot)
            result = run_result(active.competition)
            grade = medal_report(active.competition)
            wave += 1
            ledger_entry = {
                **result,
                "competition": active.competition,
                "slot": slot,
                "controller_pid": active.pid,
                "controller_seconds": now - active.started_at,
                "wave_completion_index": wave,
                "medal_gate_satisfied": grade is not None,
                "medal_submission": grade,
            }
            append_ledger(ledger_entry)
            if result.get("benchmark_complete") and grade is not None:
                completed.add(active.competition)
            else:
                retry_after[active.competition] = now + max(5.0, args.retry_seconds)

        running_competitions = {active.competition for active in running.values()}
        candidates = [
            comp
            for comp in competitions
            if comp not in completed
            and comp not in running_competitions
            and retry_after.get(comp, 0) <= now
            and prepared(comp)
        ]
        for slot in range(workers):
            if slot in running or not candidates:
                continue
            competition = candidates.pop(0)
            running[slot] = start_run(slot, competition)

        running_competitions = {active.competition for active in running.values()}
        state.update(
            {
                "completed": sorted(completed),
                "running": {
                    str(slot): {
                        "competition": active.competition,
                        "pid": active.pid,
                        "adopted": active.owned is None,
                    }
                    for slot, active in running.items()
                },
                "pending": [
                    comp
                    for comp in competitions
                    if comp not in completed and comp not in running_competitions
                ],
                "retry_after": retry_after,
                "updated_at": time.time(),
            }
        )
        atomic(STATE, state)
        time.sleep(max(1.0, args.poll_seconds))

    state.update({"running": {}, "pending": [], "finished_at": time.time()})
    atomic(STATE, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prepare the official Lite split with bounded Kaggle-CLI concurrency."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (HERE / "config.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            env[key] = value
    return env


CFG = load_env()
DATA_ROOT = Path(CFG["DATA_ROOT"])
STATE = Path(CFG["CAMPAIGN_ROOT"]) / "prepare-state.json"
LOG_ROOT = Path(CFG["CAMPAIGN_ROOT"]) / "prepare-logs"
KAGGLE = CFG["KAGGLE_BIN"]
MLEBENCH = CFG["MLEBENCH_BIN"]
COMPETITION_LIST = Path(CFG.get("COMPETITION_LIST", str(HERE / "lite.txt")))


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def prepared(comp: str) -> bool:
    base = DATA_ROOT / comp / "prepared"
    return (
        (base / "public").is_dir()
        and next((base / "public").iterdir(), None) is not None
        and (base / "private").is_dir()
        and next((base / "private").iterdir(), None) is not None
    )


def run_one(comp: str) -> dict[str, object]:
    started = time.time()
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LOG_ROOT / f"{comp}.log"
    if prepared(comp):
        return {"competition": comp, "status": "already_prepared", "seconds": 0}
    env = os.environ.copy()
    env["KAGGLE_CONFIG_DIR"] = "/root/.kaggle"
    env["MLEBENCH_KAGGLE_CLI"] = KAGGLE
    # Preparation is infrastructure work, not a benchmark run. Some
    # competition-specific dependencies eagerly reserve every visible GPU on
    # import, which can starve the two isolated Argus workers.
    env["CUDA_VISIBLE_DEVICES"] = ""
    env["NVIDIA_VISIBLE_DEVICES"] = "void"
    env["ROCR_VISIBLE_DEVICES"] = ""
    with log_path.open("ab") as log:
        # Literal Kaggle CLI rule/access check before the official preparer.
        check = subprocess.run(
            [KAGGLE, "competitions", "files", "-c", comp, "--csv"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
        if check.returncode != 0:
            return {
                "competition": comp,
                "status": "kaggle_access_failed",
                "exit_code": check.returncode,
                "seconds": time.time() - started,
                "log": str(log_path),
            }
        proc = subprocess.run(
            [
                MLEBENCH,
                "prepare",
                "-c",
                comp,
                "--data-dir",
                str(DATA_ROOT),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    return {
        "competition": comp,
        "status": "prepared" if proc.returncode == 0 and prepared(comp) else "prepare_failed",
        "exit_code": proc.returncode,
        "seconds": time.time() - started,
        "log": str(log_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    competitions = [x for x in COMPETITION_LIST.read_text().splitlines() if x]
    state: dict[str, object] = {"started_at": time.time(), "results": {}}
    atomic_json(STATE, state)
    with ThreadPoolExecutor(max_workers=max(1, min(2, args.workers))) as pool:
        future_map = {pool.submit(run_one, comp): comp for comp in competitions}
        for future in as_completed(future_map):
            result = future.result()
            state["results"][result["competition"]] = result  # type: ignore[index]
            state["updated_at"] = time.time()
            atomic_json(STATE, state)
            print(json.dumps(result, sort_keys=True), flush=True)
    statuses = [x["status"] for x in state["results"].values()]  # type: ignore[union-attr]
    return 0 if all(x in {"prepared", "already_prepared"} for x in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())

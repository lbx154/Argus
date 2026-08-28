from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from argus_skill.verticals.nanogpt_speedrun.capstone import validate_capstone


def _write_capstone(root: Path) -> None:
    frozen = {
        "harness": "run_eval.py",
        "metric": "metrics.py",
        "data": "data/manifest.json",
        "budget": "config/budget.json",
    }
    entries = []
    for role, relpath in frozen.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{role} frozen", encoding="utf-8")
        entries.append({
            "role": role,
            "path": relpath,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    manifest = root / "research" / "NANOGPT_FREEZE.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "version": 1,
        "target_val_loss": 3.28,
        "hardware": {"gpu_count": 8, "gpu_model": "H100"},
        "frozen_files": entries,
    }), encoding="utf-8")


def _write_result(root: Path) -> None:
    result = root / "attempts" / "a1" / "results.csv"
    result.parent.mkdir(parents=True, exist_ok=True)
    with result.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("seconds_to_target", "val_loss", "gpu_count", "gpu_model"),
        )
        writer.writeheader()
        writer.writerow({
            "seconds_to_target": 77.3,
            "val_loss": 3.28,
            "gpu_count": 8,
            "gpu_model": "NVIDIA H100",
        })


def test_nanogpt_capstone_checks_freeze_metric_and_report(tmp_path: Path) -> None:
    _write_capstone(tmp_path)
    assert validate_capstone(tmp_path, "setup") == []
    assert validate_capstone(tmp_path, "measure")

    _write_result(tmp_path)
    assert validate_capstone(tmp_path, "measure") == []
    assert validate_capstone(tmp_path, "report") == ["report requires non-empty RESULTS.md"]

    (tmp_path / "RESULTS.md").write_text("# Results\n\n77.3 seconds", encoding="utf-8")
    assert validate_capstone(tmp_path, "report") == []

    (tmp_path / "metrics.py").write_text("metric changed", encoding="utf-8")
    assert "frozen file changed" in " ".join(validate_capstone(tmp_path, "report"))

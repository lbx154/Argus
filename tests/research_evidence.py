"""Shared fixtures for tests that need a research project whose Experiment stage is complete.

The Experiment stage closes only when ``experiments/claims.json`` validates and
``RESEARCH_NOTES.md`` carries the Experiment-stage heading; tests that drive a
project from Experiment into Paper write both with these helpers.
"""
from __future__ import annotations

import json
from pathlib import Path

from argus.verticals.research import experiment_claims as claims


def arm(name: str, mean: float, std: float = 0.01, n: int = 5) -> dict:
    return {"name": name, "mean": mean, "std": std, "n": n}


def valid_ledger(root: Path) -> dict:
    """Write raw evidence under ``root`` and return a ledger that validates there."""
    evidence = root / "experiments" / "attempt-1" / "results.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("{}", encoding="utf-8")
    source = root / "src" / "method.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("class OurMethod:\n    def fit(self):\n        return 0\n", encoding="utf-8")
    return {
        "schema_version": claims.SCHEMA_VERSION,
        "mechanism": [
            {
                "component": "regularized pivot selection",
                "idea_says": "pivots are drawn from the ridge-regularized diagonal",
                "implemented_in": "src/method.py:OurMethod.fit",
                "status": "faithful",
            }
        ],
        "reference_implementations": [
            {
                "name": "official-nystrom",
                "url": "https://example.org/official-nystrom",
                "revision": "v1.2.0",
                "used_for": "baseline implementation and evaluation protocol",
            }
        ],
        "claims": [
            {
                "claim_id": "headline-rmse",
                "statement": "ours lowers test RMSE against the strongest baseline",
                "role": "headline",
                "metric": "test_rmse",
                "direction": "lower",
                "dataset": "california_housing/test",
                "synthetic": False,
                "variation": "seeds",
                "seeds": [0, 1, 2, 3, 4],
                "ours": arm("ours", 0.50),
                "strongest_baseline": arm("nystrom", 0.60),
                "other_baselines": [arm("rff", 0.70), arm("greedy", 0.65)],
                "evidence": ["experiments/attempt-1/results.json"],
                "command": "python scripts/run.py --seeds 0 1 2 3 4",
                "status": "supported",
            }
        ],
    }


def write_ledger(root: Path, record: dict) -> Path:
    path = claims.claims_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def write_experiment_evidence(root: Path) -> None:
    """Make ``root`` a project whose Experiment stage passes its deterministic gate."""
    write_ledger(root, valid_ledger(root))
    (root / "RESEARCH_NOTES.md").write_text(
        "# Research notes — Experiment stage\n\nThesis: headline-rmse; strongest baseline nystrom.\n",
        encoding="utf-8",
    )

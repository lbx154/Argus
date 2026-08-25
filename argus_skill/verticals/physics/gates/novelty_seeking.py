"""Compatibility entry point for the retired novelty-table gate.

Innovation is judged from the work itself by Planner and Reviewer. A fixed
number of ideas, exact CSV columns, and numeric scores made agents optimize a
table rather than a discovery. Old sessions may still invoke this module, so it
remains a successful no-op instead of turning prompt evolution into a runtime
failure.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ....skills.research_gates import clear_gate_state

GATE_ID = "novelty_seeking"
STAGE = "review"
ARTIFACT = ""
MIN_DIRECTIONS = 0
REASONING_COLUMNS: tuple[str, ...] = ()
SCORE_COLUMNS: tuple[str, ...] = ()


def verify_novelty_seeking(project_root: object) -> list[dict]:
    _ = project_root
    return []


def run_gate(project_root: object) -> tuple[bool, list[dict]]:
    clear_gate_state(Path(str(project_root or ".")), GATE_ID)
    return True, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="physics-novelty-seeking-gate")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--advisory", action="store_true")
    parser.parse_args(argv)
    print("novelty-seeking gate retired: Planner and Reviewer judge the research")
    return 0


__all__ = [
    "ARTIFACT",
    "GATE_ID",
    "MIN_DIRECTIONS",
    "REASONING_COLUMNS",
    "SCORE_COLUMNS",
    "STAGE",
    "main",
    "run_gate",
    "verify_novelty_seeking",
]


if __name__ == "__main__":
    raise SystemExit(main())

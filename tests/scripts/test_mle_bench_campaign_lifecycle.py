from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2]
    / "scripts"
    / "mle_bench_lite"
    / "campaign_lifecycle.py"
)
SPEC = importlib.util.spec_from_file_location("mle_campaign_lifecycle", MODULE_PATH)
assert SPEC and SPEC.loader
campaign_lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign_lifecycle)


def test_descendant_pids_returns_transitive_process_tree() -> None:
    assert campaign_lifecycle.descendant_pids(
        10,
        {11: 10, 12: 11, 13: 10, 20: 99},
    ) == {11, 12, 13}

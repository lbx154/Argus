"""The Python ledger's normalized contributions agree with Node's native fold."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from argus.core.usage import UsageRecord, summarize_usage
from argus.webapi.cost_inputs import usage_summary_input

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "packages/runtime/fixtures"
CASES = json.loads((FIXTURES / "usage-summary.json").read_text(encoding="utf-8"))


def records(rows: list[dict]) -> list[UsageRecord]:
    return [UsageRecord.from_jsonable({**row, "call_id": f"call-{i}"}) for i, row in enumerate(rows)]


def assert_summary(actual: dict, expected: dict) -> None:
    assert actual.keys() == expected.keys()
    for key, value in expected.items():
        if isinstance(value, float):
            assert actual[key] == pytest.approx(value, rel=1e-12, abs=1e-14), key
        else:
            assert actual[key] == value, key


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_shared_summary_fixture(case: dict) -> None:
    assert_summary(summarize_usage(records(case["records"])).to_jsonable(), case["expected"])


@pytest.mark.integration
@pytest.mark.skipif(
    not shutil.which("node") or not (ROOT / "packages/runtime/dist/usageSummary.js").is_file(),
    reason="run npm ci && npm run build for Node/Python usage-summary comparison",
)
def test_generated_normalized_ledgers_match_node() -> None:
    rng = random.Random(20260923)
    streams = [case["records"] for case in CASES]
    for _ in range(180):
        rows = []
        for _ in range(rng.randrange(1, 15)):
            row = {key: rng.choice([None, 0, 5, 1000]) for key in [
                "input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens",
                "reasoning_output_tokens", "total_nano_aiu",
            ]}
            row.update(
                cost_usd=rng.choice([None, 0, 0.01, 1]),
                pricing_status=rng.choice(["priced", "partial", "unpriced", "not_billed"]),
                premium_requests=rng.choice([None, 0, 0.5, 1]),
                premium_request_cost_usd=rng.choice([None, 0, 0.02, 0.04]),
                model_usage=[{
                    "session_id": rng.choice([None, "thread", "other", "a1", "a"]),
                    "usage_event_id": rng.choice([None, 0, 1, 2, 12]),
                    "cost_usd": rng.choice([None, 0, 0.1]),
                    "total_nano_aiu": rng.choice([None, 100_000_000]),
                    "input_tokens": rng.randrange(200), "output_tokens": rng.randrange(20),
                } for _ in range(rng.randrange(4))],
            )
            rows.append(row)
            streams.append([usage_summary_input(r) for r in records(rows)])
    response = subprocess.run(
        [shutil.which("node"), str(FIXTURES / "usage-summary-evaluate.mjs")],
        input=json.dumps(streams), text=True, encoding="utf-8", capture_output=True, check=True, timeout=30,
    )
    results = json.loads(response.stdout)
    assert len(results) == len(streams)
    for rows, result in zip(streams, results, strict=True):
        assert_summary(result, summarize_usage(records(rows)).to_jsonable())

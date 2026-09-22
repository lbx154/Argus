"""Shared golden cases and differential checks against the real Node implementation."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from argus.core.pricing import quote_token_usage
from argus.core.token_usage import extract_token_usage

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "packages/runtime/fixtures"
USAGES = json.loads((FIXTURES / "token-usage.json").read_text(encoding="utf-8"))
QUOTES = json.loads((FIXTURES / "pricing.json").read_text(encoding="utf-8"))


def usage_json(events: list | None) -> dict:
    usage = extract_token_usage(events)
    return {**asdict(usage), "observed": usage.observed, "complete": usage.complete}


def assert_accounting_equal(actual: dict, expected: dict, cost_field: str) -> None:
    assert {k: v for k, v in actual.items() if k != cost_field} == {
        k: v for k, v in expected.items() if k != cost_field
    }
    if expected[cost_field] is None:
        assert actual[cost_field] is None
    else:
        assert actual[cost_field] == pytest.approx(expected[cost_field], rel=1e-12, abs=1e-14)


@pytest.mark.parametrize("case", USAGES, ids=lambda case: case["id"])
def test_shared_token_usage(case: dict) -> None:
    assert_accounting_equal(usage_json(case["events"]), case["expected"], "provider_cost_usd")


@pytest.mark.parametrize("case", QUOTES, ids=lambda case: case["id"])
def test_shared_pricing(case: dict) -> None:
    assert_accounting_equal(
        asdict(quote_token_usage(case["model"], **case["counts"])), case["expected"], "cost_usd",
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not shutil.which("node") or not (ROOT / "packages/runtime/dist/index.js").is_file(),
    reason="run npm ci && npm run build for Node/Python differential accounting checks",
)
def test_generated_streams_and_price_boundaries_match_python() -> None:
    rng = random.Random(20260922)
    values = [None, True, False, -5, 0, 1, 3.7, 31, 500, " 1_000 ", "１２", "2.5", "invalid", {}]
    costs = [None, True, -1, 0, 0.12, "1_0.2", "nan", "Infinity", "0x10"]
    aliases = [
        ("input_tokens", "prompt_tokens", "inputTokens"),
        ("cached_input_tokens", "cache_read_input_tokens", "cachedInputTokens"),
        ("cache_write_tokens", "cache_creation_input_tokens", "cacheWriteTokens"),
        ("output_tokens", "completion_tokens", "outputTokens"),
        ("reasoning_output_tokens", "reasoning_tokens", "reasoningOutputTokens"),
    ]

    def row() -> dict:
        return {rng.choice(names): rng.choice(values) for names in aliases if rng.random() < 0.6}

    streams = [case["events"] for case in USAGES]
    for _ in range(200):
        events = []
        for _ in range(rng.randint(1, 12)):
            kind = rng.randrange(6)
            if kind == 0:
                event = {"usage": row(), "content": row(), "data": {"usage": row()}, **row()}
                event[rng.choice(["cost_usd", "total_cost_usd", "costUSD"])] = rng.choice(costs)
            elif kind == 1:
                event = {"type": "assistant", "message": {"usage": row()}}
            elif kind == 2:
                event = {"type": "result", "num_turns": rng.randrange(4), "usage": row()}
            elif kind == 3:
                event = {"data": {names[-1]: rng.choice(values) for names in aliases if rng.random() < 0.6}}
            elif kind == 4:
                usage = {name: rng.choice(values) for name in ["input", "output", "cacheRead", "cacheWrite", "reasoning"] if rng.random() < 0.7}
                usage["cost"] = {"total": rng.choice(costs)}
                event = {"type": "message_end", "message": {
                    "role": "assistant", "usage": usage, "stopReason": rng.choice(["stop", "error", "aborted"]),
                }}
            else:
                event = {"part": {"cost": rng.choice(costs), "tokens": {
                    "input": rng.choice(values), "output": rng.choice(values),
                    "cache": {"read": rng.choice(values), "write": rng.choice(values)},
                }}}
            events.append(event)
            # Prefixes catch changes in precedence as final/cost-only frames arrive.
            streams.append(list(events))

    quotes = list(QUOTES)
    for _ in range(300):
        counts = {name: rng.choice([None, -1, 0, 1, 999, 272000, 272001, 300000]) for name in [
            "input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_output_tokens",
        ]}
        quotes.append({"model": rng.choice(["gpt-5.6-sol", "gpt-5.5", "gpt-5.6-luna", "unknown", "openai/gpt-5.4"]), "counts": counts})
    result = subprocess.run(
        [shutil.which("node"), str(FIXTURES / "accounting-evaluate.mjs")],
        input=json.dumps({"streams": streams, "quotes": quotes}),
        text=True, capture_output=True, encoding="utf-8", timeout=30, check=True,
    )
    output = json.loads(result.stdout)
    assert len(output["usages"]) == len(streams)
    assert len(output["quotes"]) == len(quotes)
    for events, actual in zip(streams, output["usages"], strict=True):
        assert_accounting_equal(actual, usage_json(events), "provider_cost_usd")
    for case, actual in zip(quotes, output["quotes"], strict=True):
        expected = asdict(quote_token_usage(case["model"], **case["counts"]))
        assert_accounting_equal(actual, expected, "cost_usd")

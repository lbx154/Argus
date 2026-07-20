from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """Token counts plus field-presence metadata.

    Zero is a valid count.  Presence flags keep a missing usage payload distinct
    from an explicitly reported zero so callers can surface ``partial`` rather
    than silently pricing an unknown call at ``$0.00``.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    input_tokens_present: bool = False
    cached_input_tokens_present: bool = False
    cache_write_tokens_present: bool = False
    output_tokens_present: bool = False
    reasoning_output_tokens_present: bool = False
    provider_cost_usd: float | None = None
    source: str = "missing"

    @property
    def observed(self) -> bool:
        return any(
            (
                self.input_tokens_present,
                self.cached_input_tokens_present,
                self.cache_write_tokens_present,
                self.output_tokens_present,
                self.reasoning_output_tokens_present,
            )
        )

    @property
    def complete(self) -> bool:
        # Cached/reasoning details are optional zero-valued sub-counts.  Input
        # and output are the two fields needed to select and price a token tier.
        return self.input_tokens_present and self.output_tokens_present

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (
            self.input_tokens,
            self.cached_input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
        )


def sum_token_counts(
    events: list[dict[str, Any]] | None,
) -> tuple[int, int, int, int]:
    """Best-effort token accounting from a Codex JSON event stream.

    Codex emits lifecycle-cumulative usage tuples, so we keep the LAST complete
    token-bearing tuple rather than summing per event.  Copilot emits per-message
    camelCase counts under ``data``; those are deltas and must be summed.
    """
    return extract_token_usage(events).as_tuple()


def extract_token_usage(
    events: list[dict[str, Any]] | None,
) -> TokenUsage:
    """Extract token usage without losing the distinction between zero/missing."""
    if not events:
        return TokenUsage()

    cumulative: TokenUsage | None = None
    delta_values = [0, 0, 0, 0, 0]
    delta_present = [False, False, False, False, False]
    opencode_values = [0, 0, 0, 0, 0]
    opencode_present = [False, False, False, False, False]
    opencode_cost_usd = 0.0
    opencode_cost_present = False

    for event in events:
        if not isinstance(event, dict):
            continue
        raw_usage = event.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        raw_content = event.get("content")
        content: dict[str, Any] = (
            raw_content if isinstance(raw_content, dict) else {}
        )
        standard_sources = (usage, event, content)
        standard_names = (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
        values = [0, 0, 0, 0, 0]
        present = [False, False, False, False, False]
        for index, name in enumerate(standard_names):
            for source in standard_sources:
                if name not in source:
                    continue
                present[index] = True
                values[index] = _coerce_int(source.get(name))
                break
        if any(present):
            cumulative = TokenUsage(
                input_tokens=values[0],
                cached_input_tokens=values[1],
                cache_write_tokens=values[2],
                output_tokens=values[3],
                reasoning_output_tokens=values[4],
                input_tokens_present=present[0],
                cached_input_tokens_present=present[1],
                cache_write_tokens_present=present[2],
                output_tokens_present=present[3],
                reasoning_output_tokens_present=present[4],
                source="cumulative",
            )

        raw_data = event.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        camel_names = (
            "inputTokens",
            "cachedInputTokens",
            "cacheWriteTokens",
            "outputTokens",
            "reasoningOutputTokens",
        )
        for index, name in enumerate(camel_names):
            if name not in data:
                continue
            delta_present[index] = True
            delta_values[index] += _coerce_int(data.get(name))

        raw_part = event.get("part")
        part: dict[str, Any] = raw_part if isinstance(raw_part, dict) else {}
        raw_tokens = part.get("tokens")
        tokens: dict[str, Any] = raw_tokens if isinstance(raw_tokens, dict) else {}
        if tokens:
            cost = _coerce_nonnegative_float(part.get("cost"))
            if cost is not None:
                opencode_cost_present = True
                opencode_cost_usd += cost
            raw_cache = tokens.get("cache")
            cache: dict[str, Any] = (
                raw_cache if isinstance(raw_cache, dict) else {}
            )
            fresh_present = "input" in tokens
            cache_read_present = "read" in cache
            cache_write_present = "write" in cache
            if fresh_present or cache_read_present or cache_write_present:
                opencode_present[0] = True
                opencode_values[0] += (
                    _coerce_int(tokens.get("input"))
                    + _coerce_int(cache.get("read"))
                    + _coerce_int(cache.get("write"))
                )
            if cache_read_present:
                opencode_present[1] = True
                opencode_values[1] += _coerce_int(cache.get("read"))
            if cache_write_present:
                opencode_present[2] = True
                opencode_values[2] += _coerce_int(cache.get("write"))
            if "output" in tokens:
                opencode_present[3] = True
                opencode_values[3] += _coerce_int(tokens.get("output"))
            if "reasoning" in tokens:
                opencode_present[4] = True
                opencode_values[4] += _coerce_int(tokens.get("reasoning"))

    # A standard cumulative tuple is authoritative when present.  The Copilot
    # message stream used by the real fixture has no such tuple and falls through
    # to the summed camelCase deltas.
    if cumulative is not None:
        return cumulative
    if any(delta_present):
        return TokenUsage(
            input_tokens=delta_values[0],
            cached_input_tokens=delta_values[1],
            cache_write_tokens=delta_values[2],
            output_tokens=delta_values[3],
            reasoning_output_tokens=delta_values[4],
            input_tokens_present=delta_present[0],
            cached_input_tokens_present=delta_present[1],
            cache_write_tokens_present=delta_present[2],
            output_tokens_present=delta_present[3],
            reasoning_output_tokens_present=delta_present[4],
            source="per_event",
        )
    if any(opencode_present):
        return TokenUsage(
            input_tokens=opencode_values[0],
            cached_input_tokens=opencode_values[1],
            cache_write_tokens=opencode_values[2],
            output_tokens=opencode_values[3],
            reasoning_output_tokens=opencode_values[4],
            input_tokens_present=opencode_present[0],
            cached_input_tokens_present=opencode_present[1],
            cache_write_tokens_present=opencode_present[2],
            output_tokens_present=opencode_present[3],
            reasoning_output_tokens_present=opencode_present[4],
            provider_cost_usd=(
                opencode_cost_usd if opencode_cost_present else None
            ),
            source="per_step",
        )
    return TokenUsage()


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _coerce_nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


__all__ = ["TokenUsage", "extract_token_usage", "sum_token_counts"]

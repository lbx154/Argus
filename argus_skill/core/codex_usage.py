from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

_TOKEN_ALIASES: tuple[tuple[str, ...], ...] = (
    ("input_tokens", "prompt_tokens", "inputTokens", "promptTokens"),
    (
        "cached_input_tokens",
        "cache_read_input_tokens",
        "cachedInputTokens",
        "cacheReadInputTokens",
    ),
    (
        "cache_write_tokens",
        "cache_creation_input_tokens",
        "cacheWriteTokens",
        "cacheCreationInputTokens",
    ),
    ("output_tokens", "completion_tokens", "outputTokens", "completionTokens"),
    (
        "reasoning_output_tokens",
        "reasoning_tokens",
        "reasoningOutputTokens",
        "reasoningTokens",
    ),
)


def _token_values_from_sources(
    sources: tuple[dict[str, Any], ...],
) -> tuple[list[int], list[bool]]:
    values = [0, 0, 0, 0, 0]
    present = [False, False, False, False, False]
    for index, aliases in enumerate(_TOKEN_ALIASES):
        for source in sources:
            for name in aliases:
                if name not in source:
                    continue
                present[index] = True
                values[index] = _coerce_int(source.get(name))
                break
            if present[index]:
                break
    return values, present


def _provider_cost_from_sources(
    sources: tuple[dict[str, Any], ...],
) -> float | None:
    for source in sources:
        for name in (
            "total_cost_usd",
            "cost_usd",
            "totalCostUsd",
            "costUSD",
        ):
            if name not in source:
                continue
            parsed = _coerce_nonnegative_float(source.get(name))
            if parsed is not None:
                return parsed
    return None


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
    provider_cost_usd: float | None = None
    anthropic_values = [0, 0, 0, 0, 0]
    anthropic_present = [False, False, False, False, False]
    anthropic_usage_rows = 0
    anthropic_unit_rows = 0
    result_num_turns = 0
    delta_values = [0, 0, 0, 0, 0]
    delta_present = [False, False, False, False, False]
    opencode_values = [0, 0, 0, 0, 0]
    opencode_present = [False, False, False, False, False]
    opencode_cost_usd = 0.0
    opencode_cost_present = False
    pi_values = [0, 0, 0, 0, 0]
    pi_present = [False, False, False, False, False]
    pi_cost_usd = 0.0
    pi_cost_present = False

    for event in events:
        if not isinstance(event, dict):
            continue
        raw_usage = event.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        raw_content = event.get("content")
        content: dict[str, Any] = raw_content if isinstance(raw_content, dict) else {}
        raw_data = event.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        raw_data_usage = data.get("usage")
        data_usage: dict[str, Any] = raw_data_usage if isinstance(raw_data_usage, dict) else {}
        standard_sources = (usage, data_usage, event, content)
        values, present = _token_values_from_sources(standard_sources)
        event_cost = _provider_cost_from_sources(standard_sources)
        if event_cost is not None:
            provider_cost_usd = event_cost
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
                provider_cost_usd=provider_cost_usd,
                source="cumulative",
            )

        raw_message = event.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
        raw_message_usage = message.get("usage")
        message_usage: dict[str, Any] = (
            raw_message_usage if isinstance(raw_message_usage, dict) else {}
        )
        # Claude/Anthropic ``assistant`` frames carry one API turn's usage in
        # ``message.usage``.  These are deltas and must be summed; treating the
        # last frame as cumulative under-counts every tool-using conversation.
        if str(event.get("type") or "").strip().casefold() == "assistant" and message_usage:
            row_values, row_present = _token_values_from_sources((message_usage,))
            if any(row_present):
                anthropic_usage_rows += 1
                if (
                    row_present[0]
                    and row_present[3]
                    and row_values[0] <= 1
                    and row_values[3] <= 1
                    and not any(row_values[index] for index in (1, 2, 4))
                ):
                    anthropic_unit_rows += 1
                for index in range(5):
                    if not row_present[index]:
                        continue
                    anthropic_present[index] = True
                    anthropic_values[index] += row_values[index]

        # Pi ``--mode json`` emits exactly one assistant ``message_end`` per
        # provider turn. Its usage names are input/output/cacheRead/cacheWrite/
        # reasoning and are deltas, so sum them across tool-using turns.
        if (
            str(event.get("type") or "").strip().casefold() == "message_end"
            and str(message.get("role") or "").strip().casefold() == "assistant"
            and message_usage
        ):
            fresh_present = "input" in message_usage
            cache_read_present = "cacheRead" in message_usage
            cache_write_present = "cacheWrite" in message_usage
            if fresh_present or cache_read_present or cache_write_present:
                pi_present[0] = True
                pi_values[0] += (
                    _coerce_int(message_usage.get("input"))
                    + _coerce_int(message_usage.get("cacheRead"))
                    + _coerce_int(message_usage.get("cacheWrite"))
                )
            if cache_read_present:
                pi_present[1] = True
                pi_values[1] += _coerce_int(message_usage.get("cacheRead"))
            if cache_write_present:
                pi_present[2] = True
                pi_values[2] += _coerce_int(message_usage.get("cacheWrite"))
            if "output" in message_usage:
                pi_present[3] = True
                pi_values[3] += _coerce_int(message_usage.get("output"))
            if "reasoning" in message_usage:
                pi_present[4] = True
                pi_values[4] += _coerce_int(message_usage.get("reasoning"))
            raw_pi_cost = message_usage.get("cost")
            pi_cost = (
                _coerce_nonnegative_float(raw_pi_cost.get("total"))
                if isinstance(raw_pi_cost, dict)
                else None
            )
            if pi_cost is not None:
                pi_cost_present = True
                pi_cost_usd += pi_cost

        if str(event.get("type") or "").strip().casefold() == "result":
            result_num_turns = max(
                result_num_turns,
                _coerce_int(event.get("num_turns") or event.get("numTurns")),
            )

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
            cache: dict[str, Any] = raw_cache if isinstance(raw_cache, dict) else {}
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

    anthropic_usage = TokenUsage(
        input_tokens=anthropic_values[0],
        cached_input_tokens=anthropic_values[1],
        cache_write_tokens=anthropic_values[2],
        output_tokens=anthropic_values[3],
        reasoning_output_tokens=anthropic_values[4],
        input_tokens_present=anthropic_present[0],
        cached_input_tokens_present=anthropic_present[1],
        cache_write_tokens_present=anthropic_present[2],
        output_tokens_present=anthropic_present[3],
        reasoning_output_tokens_present=anthropic_present[4],
        provider_cost_usd=provider_cost_usd,
        source="per_message",
    )
    request_unit_placeholder = bool(
        anthropic_usage_rows
        and anthropic_unit_rows == anthropic_usage_rows
        and (result_num_turns == 0 or result_num_turns == anthropic_usage_rows)
        and (
            cumulative is None
            or (
                cumulative.input_tokens == anthropic_usage_rows
                and cumulative.output_tokens == anthropic_usage_rows
                and cumulative.cached_input_tokens == 0
                and cumulative.cache_write_tokens == 0
                and cumulative.reasoning_output_tokens == 0
            )
        )
    )
    cumulative_turn_placeholder = bool(
        cumulative is not None
        and result_num_turns > 0
        and cumulative.input_tokens == result_num_turns
        and cumulative.output_tokens == result_num_turns
        and cumulative.cached_input_tokens == 0
        and cumulative.cache_write_tokens == 0
        and cumulative.reasoning_output_tokens == 0
        and anthropic_usage.observed
        and anthropic_usage.as_tuple() != cumulative.as_tuple()
    )

    # Some Anthropic-compatible gateways report one request unit as one input
    # and one output "token" for every agent turn.  Those values produced the
    # observed 1/1 and N/N telemetry and are not token counts.  Keep any
    # provider-reported cost but mark token usage absent instead of persisting a
    # confidently wrong number.
    if request_unit_placeholder:
        return TokenUsage(
            provider_cost_usd=provider_cost_usd,
            source="provider_request_units",
        )

    if cumulative_turn_placeholder:
        return anthropic_usage

    # A standard cumulative tuple is authoritative when present.  If the final
    # result omits usage, fall back to summed Claude ``message.usage`` rows.
    if cumulative is not None:
        if cumulative.provider_cost_usd is None and provider_cost_usd is not None:
            cumulative = replace(
                cumulative,
                provider_cost_usd=provider_cost_usd,
            )
        return cumulative
    if anthropic_usage.observed:
        return anthropic_usage
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
    if any(pi_present):
        return TokenUsage(
            input_tokens=pi_values[0],
            cached_input_tokens=pi_values[1],
            cache_write_tokens=pi_values[2],
            output_tokens=pi_values[3],
            reasoning_output_tokens=pi_values[4],
            input_tokens_present=pi_present[0],
            cached_input_tokens_present=pi_present[1],
            cache_write_tokens_present=pi_present[2],
            output_tokens_present=pi_present[3],
            reasoning_output_tokens_present=pi_present[4],
            provider_cost_usd=(pi_cost_usd if pi_cost_present else None),
            source="pi_message",
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
            provider_cost_usd=(opencode_cost_usd if opencode_cost_present else None),
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

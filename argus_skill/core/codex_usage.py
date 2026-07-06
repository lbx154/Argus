from __future__ import annotations

from typing import Any


def sum_token_counts(
    events: list[dict[str, Any]] | None,
) -> tuple[int, int, int, int]:
    """Best-effort token accounting from a Codex JSON event stream.

    Codex emits lifecycle-cumulative usage tuples, so we keep the LAST complete
    token-bearing tuple rather than summing per event.
    """
    if not events:
        return 0, 0, 0, 0
    last: tuple[int, int, int, int] = (0, 0, 0, 0)
    for event in events:
        if not isinstance(event, dict):
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        in_tok = 0
        cached_tok = 0
        out_tok = 0
        reasoning_out_tok = 0
        if usage is not None:
            in_tok = _coerce_int(usage.get("input_tokens"))
            cached_tok = _coerce_int(usage.get("cached_input_tokens"))
            out_tok = _coerce_int(usage.get("output_tokens"))
            reasoning_out_tok = _coerce_int(usage.get("reasoning_output_tokens"))
        if in_tok == 0:
            in_tok = _coerce_int(event.get("input_tokens"))
        if cached_tok == 0:
            cached_tok = _coerce_int(event.get("cached_input_tokens"))
        if out_tok == 0:
            out_tok = _coerce_int(event.get("output_tokens"))
        if reasoning_out_tok == 0:
            reasoning_out_tok = _coerce_int(event.get("reasoning_output_tokens"))
        if in_tok == 0 or cached_tok == 0 or out_tok == 0 or reasoning_out_tok == 0:
            content = event.get("content") if isinstance(event.get("content"), dict) else None
            if content is not None:
                if in_tok == 0:
                    in_tok = _coerce_int(content.get("input_tokens"))
                if cached_tok == 0:
                    cached_tok = _coerce_int(content.get("cached_input_tokens"))
                if out_tok == 0:
                    out_tok = _coerce_int(content.get("output_tokens"))
                if reasoning_out_tok == 0:
                    reasoning_out_tok = _coerce_int(content.get("reasoning_output_tokens"))
        if in_tok > 0 or cached_tok > 0 or out_tok > 0 or reasoning_out_tok > 0:
            last = (in_tok, cached_tok, out_tok, reasoning_out_tok)
    return last


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

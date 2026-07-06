"""Metering helpers for otherwise-unaccounted codex calls (F3 PART B).

Several codex calls — the Manager's stage/route/converse/domain-author turns and
``vertical_select.classify_vertical`` — emit none of the three events the cost
sink folds (``round.main.completed`` / ``round.review.completed`` /
``skill.cost.completed``), so their tokens are invisible to BOTH the per-mission
number (``cost_sink.total_usd``) and the daily cap. This module lets each such
call emit a ``codex.util.completed`` event the sink folds, closing those holes.

All emits are ``usage_scope="delta"`` — each turn reports its own per-turn input/
cached/output (even on the Manager's persistent resumed session, each turn truly
bills its full input, with the prefix-cache discount carried in
``cached_input_tokens``), so the sink sums per call = the correct billed cost.
Everything here is fail-soft: a metering bug must NEVER break a mission or a
decision.
"""
from __future__ import annotations

from typing import Any, Callable


def emit_codex_util_cost(
    on_event: Callable[[dict], None] | None,
    *,
    layer: str,
    model: str,
    result: Any,
    run_label: str = "",
) -> None:
    """Emit one ``codex.util.completed`` cost event for ``result`` (a RunnerResult-
    shaped object). Fail-soft: no-op when ``on_event`` is None or anything raises."""
    if on_event is None:
        return
    try:
        on_event({
            "type": "codex.util.completed",
            "agent_layer": layer,
            "model": model,
            "run_label": run_label,
            "input_tokens": int(getattr(result, "input_tokens", 0) or 0),
            "cached_input_tokens": int(getattr(result, "cached_input_tokens", 0) or 0),
            "output_tokens": int(getattr(result, "output_tokens", 0) or 0),
            "reasoning_output_tokens": int(
                getattr(result, "reasoning_output_tokens", 0) or 0
            ),
            # Copilot premium-request delta (0.0 off copilot). Without this a
            # copilot-backed Manager util turn bills premium the sink never sees.
            # copilot 高级请求增量(非 copilot 为 0.0)——否则 Manager 的 copilot 工具轮
            # 花费不进入成本表。
            "premium_requests": float(getattr(result, "premium_requests", 0.0) or 0.0),
            "usage_scope": "delta",
        })
    except Exception:  # noqa: BLE001 — metering must never break the caller
        pass


def metered_run_exec(
    run_exec: Callable[[str], Any],
    on_event: Callable[[dict], None] | None,
    *,
    layer: str,
    model: str,
    run_label: str,
) -> Callable[[str], Any]:
    """Wrap a ``run_exec(prompt) -> result`` callable so each call emits a
    ``codex.util.completed`` event afterwards. The wrapped result is returned
    unchanged; the metering is fail-soft."""
    def wrapped(prompt: str) -> Any:
        result = run_exec(prompt)
        emit_codex_util_cost(on_event, layer=layer, model=model, result=result,
                             run_label=run_label)
        return result
    return wrapped

"""Application-level maintenance of a long-lived Manager's provider context.

These watermarks bound retained conversation growth, not user quota or provider
admission. Reported usage stays untouched; absent usage uses a labelled estimate
of visible text. Rotation is decided before the next call and never retries it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONTEXT_TOKEN_WATERMARK = 16 * 1024
MIN_PROVIDER_TURNS = 4
CONTEXT_TOKEN_WATERMARK_ENV = "ARGUS_SKILL_MANAGER_SESSION_MAX_INPUT_TOKENS"
CONTEXT_WINDOW_ENV = "ARGUS_SKILL_MANAGER_SESSION_CONTEXT_WINDOW_TOKENS"


def configured_context_watermark() -> int:
    """A tunable maintenance target, distinct from quota or model context size.

    The default retains several complete 16 KiB observation/decision turns,
    while retiring growing request bodies well before native 128k compaction.
    Four turns protect a useful dialogue window and amortize a fresh prefix.
    """
    from ..core.knobs import env_int

    return env_int(CONTEXT_TOKEN_WATERMARK_ENV, CONTEXT_TOKEN_WATERMARK, minimum=1024)


def _nonnegative_int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def provider_context_limits(runner: Any, options: Any) -> tuple[int, int]:
    """Use explicit limits or bounded native Pi metadata; never guess a model id.

    A backend that does not expose its window retains its own hard admission /
    compaction rules. The optional explicit window is an operator assertion,
    not a change to the model or account limit.
    """
    from ..core.knobs import env_int
    from ..core.scoped_file import open_regular_file

    window = env_int(CONTEXT_WINDOW_ENV, 0)
    output = 0
    directory = os.environ.get("PI_CODING_AGENT_DIR", "")
    model = str(getattr(options, "model", "") or "")
    if getattr(runner, "backend", "") == "pi" and directory and "/" in model:
        provider, model_id = model.split("/", 1)
        try:
            with open_regular_file(Path(directory).expanduser() / "models.json") as handle:
                raw = handle.read(128 * 1024 + 1)
            if len(raw) <= 128 * 1024:
                config = json.loads(raw)
                models = config.get("providers", {}).get(provider, {}).get("models", [])
                metadata = next(row for row in models if isinstance(row, dict) and row.get("id") == model_id)
                window = window or _nonnegative_int(metadata.get("contextWindow"))
                output = _nonnegative_int(metadata.get("maxTokens"))
        except (OSError, ValueError, TypeError, AttributeError, StopIteration):
            pass
    return window, output or (max(1, window // 4) if window else 0)


def capacity_rotation_reason(
    previous: dict[str, Any], *, prompt: str = "", runner: Any = None, options: Any = None,
) -> str:
    """Retire a large provider context while preserving the project Manager."""
    capacity = previous.get("capacity")
    if not isinstance(capacity, dict) or capacity.get("version") != 1:
        if previous.get("version") == 2 and previous.get("recent_turns"):
            # Four saved excerpts cannot establish the size of an older native
            # transcript. One explicit handoff establishes accounted capacity.
            return "the saved provider context predates bounded capacity accounting"
        return ""
    window, output_reserve = provider_context_limits(runner, options)
    resident = (_nonnegative_int(capacity.get("context_tokens"))
                if capacity.get("basis") == "reported_tokens"
                else _nonnegative_int(capacity.get("visible_history_bytes")))
    # One UTF-8 byte per token is a conservative estimate for the next prompt,
    # not recorded usage. A known smaller model window wins over the four-turn
    # maintenance floor; no prompt or control field is cut to make it fit.
    if window and resident + len(prompt.encode("utf-8")) + output_reserve >= window:
        return "the available provider context needs room for the next Manager turn"
    # A large tool turn may itself cross the maintenance watermark. Keep a
    # useful dialogue window unless the known model capacity takes precedence.
    if _nonnegative_int(capacity.get("turns")) < MIN_PROVIDER_TURNS:
        return ""
    watermark = configured_context_watermark()
    if capacity.get("basis") == "reported_tokens":
        if _nonnegative_int(capacity.get("context_tokens")) >= watermark:
            return "the provider conversation reached the Manager context watermark"
    elif _nonnegative_int(capacity.get("visible_history_bytes")) >= 4 * watermark:
        return "the visible conversation reached the Manager history watermark"
    return ""


def remember_capacity(
    previous: dict[str, Any], prompt: str, result: Any, *, continued: bool,
    runner: Any = None, options: Any = None,
) -> dict[str, Any]:
    """Record context evidence separately from immutable call usage results."""
    capacity = previous.get("capacity") if continued else {}
    if not isinstance(capacity, dict):
        capacity = {}
    answer = str(getattr(result, "last_agent_message", "") or "")
    visible_bytes = (
        _nonnegative_int(capacity.get("visible_history_bytes"))
        + len(prompt.encode("utf-8")) + len(answer.encode("utf-8"))
    )
    input_tokens = _nonnegative_int(getattr(result, "input_tokens", 0))
    reported = getattr(result, "input_tokens_present", False) is True and input_tokens > 0
    output_tokens = _nonnegative_int(getattr(result, "output_tokens", 0))
    window, output_reserve = provider_context_limits(runner, options)
    return {
        "version": 1,
        "turns": _nonnegative_int(capacity.get("turns")) + 1,
        "basis": "reported_tokens" if reported else "visible_text_bytes",
        "watermark_tokens": configured_context_watermark(),
        "provider_context_window_tokens": window or None,
        "provider_output_reserve_tokens": output_reserve or None,
        # Input already includes cached tokens; adding cached_input_tokens again
        # would manufacture context size. Tool-heavy call totals are conservative.
        "context_tokens": input_tokens + output_tokens if reported else None,
        "visible_history_bytes": visible_bytes,
    }

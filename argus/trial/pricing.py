"""USD metering for activation codes, in the provider's nano-AIU unit.

Source of truth: every completed Copilot ``/responses`` result carries
``copilot_usage.total_nano_aiu`` -- the provider's own charge for that request,
computed from its per-model ``token_details`` (count x cost_per_batch /
batch_size). 1e9 nano-AIU = 1 AI credit = $0.01. The gateway settles with
that figure whenever it is present.

The operator's price table (USD per million tokens, per model) is used for two
things only: the per-request reservation, which must be an upper bound, and a
fallback charge when a completed response reports token usage but no cost. A
model missing from the table cannot be reserved, so requests for it are refused
for USD-limited codes instead of being run unmetered.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..provider_integrations.copilot_usage import NANO_AIU_PER_USD

# USD per million tokens -> nano-AIU per token.
_PER_TOKEN = NANO_AIU_PER_USD // 1_000_000


@dataclass(frozen=True)
class Price:
    """USD per million tokens; cache prices default to the input price."""
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None

    @classmethod
    def load(cls, data: dict) -> Price:
        if not isinstance(data, dict) or not {"input", "output"} <= data.keys() or data.keys() - {
            "input", "output", "cache_read", "cache_write",
        }:
            raise ValueError("Each price needs input/output and optional cache_read/cache_write")
        for value in data.values():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Prices must be finite non-negative USD per million tokens")
        return cls(**{key: float(value) for key, value in data.items()})

    def _rate(self, usd_per_million: float | None) -> int:
        return math.ceil((self.input if usd_per_million is None else usd_per_million) * _PER_TOKEN)

    def reserve(self, input_tokens: int, output_tokens: int) -> int:
        """Upper bound: every input token at the dearest input rate."""
        input_rate = max(self._rate(None), self._rate(self.cache_read), self._rate(self.cache_write))
        return max(1, input_tokens * input_rate + output_tokens * self._rate(self.output))

    def charge(self, usage: dict) -> int | None:
        """Fallback from Chat Completions usage when the provider reports no cost."""
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if type(prompt) is not int or type(completion) is not int or min(prompt, completion) < 0:
            return None
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        cached = cached if type(cached) is int and 0 <= cached <= prompt else 0
        # Uncached input may include cache writes; charge it at the dearer rate.
        fresh_rate = max(self._rate(None), self._rate(self.cache_write))
        return ((prompt - cached) * fresh_rate + cached * self._rate(self.cache_read)
                + completion * self._rate(self.output))


def load_prices(source: dict | str | Path | None) -> dict[str, Price]:
    """``{"model-id": {"input": 5, "output": 30, "cache_read": 0.5}}`` in USD per 1M tokens."""
    if source is None:
        return {}
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text())
    if not isinstance(data, dict):
        raise ValueError("The price table must map model IDs to prices")
    return {str(model): Price.load(value) for model, value in data.items()}


def provider_nano_aiu(*objects: object) -> int | None:
    """The provider's own charge from the first object carrying ``copilot_usage``."""
    for value in objects:
        usage = value.get("copilot_usage") if isinstance(value, dict) else None
        if isinstance(usage, dict):
            total = usage.get("total_nano_aiu")
            if type(total) is int and total >= 0:
                return total
    return None

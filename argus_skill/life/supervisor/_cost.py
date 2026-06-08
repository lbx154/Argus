from __future__ import annotations

import logging
from typing import Any

from ...core.ports import EventSink
from ...core.pricing import price_for, usd_for_tokens

log = logging.getLogger(__name__)

class _CostTrackingSink:
    """Wraps an ``EventSink`` to accumulate token counts.

    The mission engine emits ``round.main.completed`` and
    ``round.review.completed`` events that already carry per-call
    ``input_tokens`` / ``output_tokens`` (Phase-2 instrumentation). We
    fold them into running totals and forward every event downstream
    unchanged.
    """

    def __init__(
        self,
        downstream: EventSink,
        *,
        engineer_model: str,
        reviewer_model: str,
        on_phase_change: Any = None,  # Callable[[str, dict], None] | None
    ) -> None:
        self.downstream = downstream
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self.engineer_input_tokens = 0
        self.engineer_output_tokens = 0
        self.reviewer_input_tokens = 0
        self.reviewer_output_tokens = 0
        self._on_phase_change = on_phase_change
        self._reviewer_notified = False
        self._engineer_round_count = 0
        self.engineer_cached_input_tokens = 0
        self.reviewer_cached_input_tokens = 0
        self._cumulative_usage_baselines: dict[
            tuple[str, str], tuple[int, int, int]
        ] = {}

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "round.main.completed":
                in_tok, cached_tok, out_tok = self._usage_delta(
                    event,
                    layer="engineer",
                )
                self.engineer_input_tokens += in_tok
                self.engineer_cached_input_tokens += cached_tok
                self.engineer_output_tokens += out_tok
                self._engineer_round_count += 1
            elif kind == "round.review.started":
                if not self._reviewer_notified and self._on_phase_change:
                    self._reviewer_notified = True
                    try:
                        self._on_phase_change("reviewer", {
                            "round_index": event.get("round_index", 0),
                            "status": "started",
                            "engineer_rounds": self._engineer_round_count,
                        })
                    except Exception:  # noqa: BLE001
                        log.debug("phase change callback failed", exc_info=True)
            elif kind == "round.review.completed":
                in_tok, cached_tok, out_tok = self._usage_delta(
                    event,
                    layer="reviewer",
                )
                self.reviewer_input_tokens += in_tok
                self.reviewer_cached_input_tokens += cached_tok
                self.reviewer_output_tokens += out_tok
        except Exception:  # noqa: BLE001
            log.debug("cost-tracking sink ignored malformed event", exc_info=True)
        # Always forward.
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream event sink raised; continuing")

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Forward stream lines when the downstream sink supports them."""
        try:
            handler = getattr(self.downstream, "handle_stream_line", None)
            if handler is not None:
                handler(stream, line)
        except Exception:  # noqa: BLE001
            log.exception("downstream stream handler raised; continuing")

    def close(self) -> None:
        try:
            closer = getattr(self.downstream, "close", None)
            if closer is not None:
                closer()
        except Exception:  # noqa: BLE001
            log.exception("downstream close raised; continuing")

    def total_usd(self) -> float:
        return self.engineer_usd() + self.reviewer_usd()

    def engineer_usd(self) -> float:
        return usd_for_tokens(
            self.engineer_model,
            self.engineer_input_tokens,
            self.engineer_cached_input_tokens,
            self.engineer_output_tokens,
            price_lookup=price_for,
        )

    def reviewer_usd(self) -> float:
        return usd_for_tokens(
            self.reviewer_model,
            self.reviewer_input_tokens,
            self.reviewer_cached_input_tokens,
            self.reviewer_output_tokens,
            price_lookup=price_for,
        )

    def total_input_tokens(self) -> int:
        return self.engineer_input_tokens + self.reviewer_input_tokens

    def total_output_tokens(self) -> int:
        return self.engineer_output_tokens + self.reviewer_output_tokens

    def _usage_delta(
        self,
        event: dict[str, Any],
        *,
        layer: str,
    ) -> tuple[int, int, int]:
        raw = (
            int(event.get("input_tokens", 0) or 0),
            int(event.get("cached_input_tokens", 0) or 0),
            int(event.get("output_tokens", 0) or 0),
        )
        if str(event.get("usage_scope") or "delta").lower() != "cumulative":
            return raw

        session_id = str(
            event.get("session_id")
            or event.get("thread_id")
            or event.get("actor")
            or "__global__"
        )
        key = (layer, session_id)
        previous = self._cumulative_usage_baselines.get(key)
        self._cumulative_usage_baselines[key] = raw
        if previous is None:
            return raw
        delta = (
            raw[0] - previous[0],
            raw[1] - previous[1],
            raw[2] - previous[2],
        )
        if any(value < 0 for value in delta):
            log.debug(
                "cumulative usage decreased; treating current event as fresh delta "
                "(layer=%s, session_id=%s, previous=%s, current=%s)",
                layer,
                session_id,
                previous,
                raw,
            )
            return raw
        return delta


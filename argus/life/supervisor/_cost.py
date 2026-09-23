from __future__ import annotations

import logging
from typing import Any

from ...core.event_catalog import EventType
from ...core.ports import EventSink
from ...core.pricing import (
    copilot_usd_per_premium_request,
    price_for,
    usd_for_tokens,
)
from ...core.usage import UsageLedger, UsageRecord, UsageSummary, summarize_usage

log = logging.getLogger(__name__)
_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def copilot_usd_for_premium_requests(value: float) -> float:
    try:
        count = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        count = 0.0
    return count * copilot_usd_per_premium_request()


class _CostTrackingSink:
    """Forward events while exposing mission usage from the call ledger.

    Deterministic test runners without a real call ledger retain the historical
    event-folding fallback.
    """

    def __init__(
        self,
        downstream: EventSink,
        *,
        engineer_model: str,
        reviewer_model: str,
        on_phase_change: Any = None,  # Callable[[str, dict], None] | None
        usage_ledger: UsageLedger | None = None,
        mission_id: str | None = None,
        item_id: str | None = None,
    ) -> None:
        self.downstream = downstream
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self.engineer_input_tokens = 0
        self.engineer_output_tokens = 0
        self.engineer_reasoning_output_tokens = 0
        self.reviewer_input_tokens = 0
        self.reviewer_output_tokens = 0
        self.reviewer_reasoning_output_tokens = 0
        self.scientist_input_tokens = 0
        self.scientist_cached_input_tokens = 0
        self.scientist_output_tokens = 0
        self.scientist_reasoning_output_tokens = 0
        self.scientist_usage_by_model: dict[str, list[int]] = {}
        # F3: otherwise-unaccounted codex calls (manager stage/route/converse/
        # domain-author, vertical-classify) report via codex.util.completed.
        self.util_input_tokens = 0
        self.util_cached_input_tokens = 0
        self.util_output_tokens = 0
        self.util_reasoning_output_tokens = 0
        self.util_usage_by_model: dict[str, list[int]] = {}
        # Copilot premium-request spend (engineer + reviewer), summed from the
        # already-de-cumulated per-round deltas. Priced into total_usd().
        # copilot 高级请求花费(工程师+审查者)，由已去累计的单轮增量累加，计入 total_usd()。
        self.copilot_premium_requests = 0.0
        self._on_phase_change = on_phase_change
        self._reviewer_notified = False
        self._engineer_round_count = 0
        self.engineer_cached_input_tokens = 0
        self.reviewer_cached_input_tokens = 0
        self._cumulative_usage_baselines: dict[
            tuple[str, str], tuple[int, int, int, int]
        ] = {}
        self._usage_ledger = usage_ledger
        self._mission_id = str(mission_id or "") or None
        self._item_id = str(item_id or "") or None

    def handle_event(self, event: dict[str, Any]) -> None:
        # Keep the durable task identity separate from the per-attempt usage key.
        # A copy avoids mutating payloads also observed by other sinks.
        if self._item_id and isinstance(event, dict):
            event = {**event, "item_id": event.get("item_id") or self._item_id}
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind in {EventType.ROUND_MAIN_COMPLETED, EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED}:
                main_round = kind == EventType.ROUND_MAIN_COMPLETED
                in_tok, cached_tok, out_tok, reasoning_out_tok = self._usage_delta(
                    event, layer="engineer" if main_round else "engineer_skill_maintenance",
                )
                self.engineer_input_tokens += in_tok
                self.engineer_cached_input_tokens += cached_tok
                self.engineer_output_tokens += out_tok
                self.engineer_reasoning_output_tokens += reasoning_out_tok
                self.copilot_premium_requests += self._premium_delta(event)
                if main_round:
                    self._engineer_round_count += 1
            elif kind == EventType.ROUND_REVIEW_STARTED:
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
            elif kind == EventType.ROUND_REVIEW_COMPLETED:
                in_tok, cached_tok, out_tok, reasoning_out_tok = self._usage_delta(
                    event,
                    layer="reviewer",
                )
                self.reviewer_input_tokens += in_tok
                self.reviewer_cached_input_tokens += cached_tok
                self.reviewer_output_tokens += out_tok
                self.reviewer_reasoning_output_tokens += reasoning_out_tok
                self.copilot_premium_requests += self._premium_delta(event)
            elif kind == EventType.SKILL_COST_COMPLETED:
                self._record_scientist_usage(event)
                self.copilot_premium_requests += self._premium_delta(event)
            elif kind == EventType.CODEX_UTIL_COMPLETED:
                in_tok, cached_tok, out_tok, reasoning_out_tok = self._usage_delta(
                    event,
                    layer="util",
                )
                self.util_input_tokens += in_tok
                self.util_cached_input_tokens += cached_tok
                self.util_output_tokens += out_tok
                self.util_reasoning_output_tokens += reasoning_out_tok
                self.copilot_premium_requests += self._premium_delta(event)
                if any((in_tok, cached_tok, out_tok, reasoning_out_tok)):
                    key = str(event.get("model") or self.engineer_model)
                    bucket = self.util_usage_by_model.setdefault(key, [0, 0, 0, 0])
                    bucket[0] += in_tok
                    bucket[1] += cached_tok
                    bucket[2] += out_tok
                    bucket[3] += reasoning_out_tok
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
        return self.usage_summary().known_cost_usd

    def usage_summary(self) -> UsageSummary:
        if self._usage_ledger is not None:
            return self._usage_ledger.summary(mission_id=self._mission_id)
        cost = sum(self._event_costs().values())
        return UsageSummary(
            call_count=0, known_cost_usd=cost, cost_usd=cost, pricing_status="priced",
            priced_calls=0, partial_calls=0, unpriced_calls=0, not_billed_calls=0,
            input_tokens=(
                self.scientist_input_tokens + self.engineer_input_tokens
                + self.reviewer_input_tokens + self.util_input_tokens
            ),
            cached_input_tokens=(
                self.scientist_cached_input_tokens + self.engineer_cached_input_tokens
                + self.reviewer_cached_input_tokens + self.util_cached_input_tokens
            ),
            output_tokens=(
                self.scientist_output_tokens + self.engineer_output_tokens
                + self.reviewer_output_tokens + self.util_output_tokens
            ),
            reasoning_output_tokens=(
                self.scientist_reasoning_output_tokens + self.engineer_reasoning_output_tokens
                + self.reviewer_reasoning_output_tokens + self.util_reasoning_output_tokens
            ),
            premium_requests=self.copilot_premium_requests,
        )

    def _event_costs(self) -> dict[str, float]:
        """Fallback pricing for deterministic runners without a call ledger."""
        def price_by_model(buckets: dict[str, list[int]]) -> float:
            return sum(
                usd_for_tokens(
                    model, values[0], values[1], values[2],
                    reasoning_output_tokens=values[3], price_lookup=price_for,
                )
                for model, values in buckets.items()
            )

        return {
            "scientist_cost_usd": price_by_model(self.scientist_usage_by_model),
            "engineer_cost_usd": usd_for_tokens(
                self.engineer_model, self.engineer_input_tokens,
                self.engineer_cached_input_tokens, self.engineer_output_tokens,
                reasoning_output_tokens=self.engineer_reasoning_output_tokens,
                price_lookup=price_for,
            ),
            "reviewer_cost_usd": usd_for_tokens(
                self.reviewer_model, self.reviewer_input_tokens,
                self.reviewer_cached_input_tokens, self.reviewer_output_tokens,
                reasoning_output_tokens=self.reviewer_reasoning_output_tokens,
                price_lookup=price_for,
            ),
            "util_cost_usd": price_by_model(self.util_usage_by_model),
            "copilot_cost_usd": copilot_usd_for_premium_requests(self.copilot_premium_requests),
        }

    def completion_usage(self) -> tuple[UsageSummary, dict[str, Any]]:
        """Build completion totals and role details from the same ledger read."""
        if self._usage_ledger is None:
            summary = self.usage_summary()
            costs = self._event_costs()
            scientist = {
                model: list(values) for model, values in self.scientist_usage_by_model.items()
            }
        else:
            records = self._usage_ledger.records(mission_id=self._mission_id)
            summary = summarize_usage(records)
            costs = dict.fromkeys(
                (f"{role}_cost_usd" for role in ("engineer", "reviewer", "scientist", "util", "copilot")),
                0.0,
            )
            scientist = {}
            for record in records:
                role = self._role_for_record(record)
                if record.cost_usd is not None:
                    if record.cost_basis == "premium_request":
                        costs["copilot_cost_usd"] += record.cost_usd
                    elif record.cost_basis == "token":
                        costs[f"{role}_cost_usd"] += record.cost_usd
                if role == "scientist":
                    bucket = scientist.setdefault(record.model or "unknown", [0, 0, 0, 0])
                    for index, field in enumerate(_TOKEN_FIELDS):
                        bucket[index] += getattr(record, field) or 0
        return summary, {
            **costs,
            **{
                f"scientist_{field}": sum(values[index] for values in scientist.values())
                for index, field in enumerate(_TOKEN_FIELDS)
            },
            "scientist_usage_by_model": {
                model: dict(zip(_TOKEN_FIELDS, values)) for model, values in scientist.items()
            },
        }

    @staticmethod
    def _role_for_record(record: UsageRecord) -> str:
        label = record.run_label.strip().lower()
        if label == "matcher" or label.startswith(
            ("scientist", "skill.compaction", "wiki.compaction")
        ):
            return "scientist"
        if label.startswith("engineer"):
            return "engineer"
        if label.startswith("reviewer"):
            return "reviewer"
        return "util"

    def _record_scientist_usage(self, event: dict[str, Any]) -> None:
        for phase in ("matcher", "distiller"):
            nested = event.get(phase)
            if isinstance(nested, dict):
                model = str(nested.get("model") or event.get(f"{phase}_model") or "")
                raw = {field: nested.get(field, 0) for field in _TOKEN_FIELDS}
            else:
                model = str(event.get(f"{phase}_model") or "")
                raw = {field: event.get(f"{phase}_{field}", 0) for field in _TOKEN_FIELDS}
            in_tok, cached_tok, out_tok, reasoning_out_tok = self._usage_delta(
                raw,
                layer=f"scientist:{phase}",
            )
            self.scientist_input_tokens += in_tok
            self.scientist_cached_input_tokens += cached_tok
            self.scientist_output_tokens += out_tok
            self.scientist_reasoning_output_tokens += reasoning_out_tok
            if not any((in_tok, cached_tok, out_tok, reasoning_out_tok)):
                continue
            key = model or self.engineer_model
            bucket = self.scientist_usage_by_model.setdefault(key, [0, 0, 0, 0])
            bucket[0] += in_tok
            bucket[1] += cached_tok
            bucket[2] += out_tok
            bucket[3] += reasoning_out_tok

    def _premium_delta(self, event: dict[str, Any]) -> float:
        """Copilot premium-request count on a round event (already a per-round
        delta from the backend adapter; fail-soft to 0.0).
        取轮次事件里的 copilot 高级请求数(适配层已给出单轮增量；失败回退 0.0)。"""
        try:
            val = float(event.get("premium_requests", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return val if val > 0.0 else 0.0

    def _usage_delta(
        self,
        event: dict[str, Any],
        *,
        layer: str,
    ) -> tuple[int, int, int, int]:
        raw = (
            int(event.get("input_tokens", 0) or 0),
            int(event.get("cached_input_tokens", 0) or 0),
            int(event.get("output_tokens", 0) or 0),
            int(event.get("reasoning_output_tokens", 0) or 0),
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
            raw[3] - previous[3],
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

from __future__ import annotations

import logging
from typing import Any

from ...core.event_catalog import EventType
from ...core.ports import EventSink
from ...core.usage import UsageLedger, UsageRecord, UsageSummary, summarize_usage

log = logging.getLogger(__name__)
_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


class _CostTrackingSink:
    """Forward events while exposing mission usage from the call ledger.

    Runners without a call ledger (the deterministic test runners) report no
    usage; the runner's own calls are what the ledger meters.
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
        self._on_phase_change = on_phase_change
        self._reviewer_notified = False
        self._engineer_round_count = 0
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
            if kind == EventType.ROUND_MAIN_COMPLETED:
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
        if self._usage_ledger is None:
            return summarize_usage(())
        return self._usage_ledger.summary(mission_id=self._mission_id)

    def completion_usage(self) -> tuple[UsageSummary, dict[str, Any]]:
        """Build completion totals and role details from the same ledger read."""
        records = (
            self._usage_ledger.records(mission_id=self._mission_id)
            if self._usage_ledger is not None
            else []
        )
        summary = summarize_usage(records)
        costs = dict.fromkeys(
            (f"{role}_cost_usd" for role in ("engineer", "reviewer", "scientist", "util", "copilot")),
            0.0,
        )
        scientist: dict[str, list[int]] = {}
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

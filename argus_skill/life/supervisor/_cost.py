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
from ...core.usage import UsageLedger, UsageRecord, UsageSummary

log = logging.getLogger(__name__)


def _copilot_usd_per_premium_request() -> float:
    """USD per copilot PREMIUM REQUEST over the included monthly allowance.

    EN: copilot bills flat premium-requests, not tokens. To make copilot spend
    visible in the SAME ``total_usd`` meter the F3 breaker / daily cap already
    enforce (no new control surface), we price each premium request. Configurable
    via ``ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST``; defaults to GitHub's
    published $0.04 overage rate. Fail-soft to the default on bad/negative input.
    中文：copilot 按「高级请求数」定额计费而非 token。为让 copilot 花费进入 F3 熔断/
    日额度已在用的同一个 ``total_usd`` 表（不新增控制面），给每个高级请求定价。可用
    ``ARGUS_SKILL_COPILOT_USD_PER_PREMIUM_REQUEST`` 覆盖，默认 GitHub 公布的 $0.04；
    非法/负值回退默认。
    """
    return copilot_usd_per_premium_request()


def copilot_usd_for_premium_requests(value: float) -> float:
    try:
        count = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        count = 0.0
    return count * _copilot_usd_per_premium_request()


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

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == EventType.ROUND_MAIN_COMPLETED:
                in_tok, cached_tok, out_tok, reasoning_out_tok = self._usage_delta(
                    event,
                    layer="engineer",
                )
                self.engineer_input_tokens += in_tok
                self.engineer_cached_input_tokens += cached_tok
                self.engineer_output_tokens += out_tok
                self.engineer_reasoning_output_tokens += reasoning_out_tok
                self.copilot_premium_requests += self._premium_delta(event)
                self._engineer_round_count += 1
            elif kind == EventType.ENGINEER_SKILL_MAINTENANCE_COMPLETED:
                in_tok, cached_tok, out_tok, reasoning_out_tok = self._usage_delta(
                    event,
                    layer="engineer_skill_maintenance",
                )
                self.engineer_input_tokens += in_tok
                self.engineer_cached_input_tokens += cached_tok
                self.engineer_output_tokens += out_tok
                self.engineer_reasoning_output_tokens += reasoning_out_tok
                self.copilot_premium_requests += self._premium_delta(event)
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
        if self._usage_ledger is not None:
            return self._ledger_summary().known_cost_usd
        return (
            self.scientist_usd()
            + self.engineer_usd()
            + self.reviewer_usd()
            + self.util_usd()
            + self.copilot_usd()
        )

    def copilot_usd(self) -> float:
        """USD-equivalent of accumulated copilot premium requests (0.0 off
        copilot). Priced so copilot spend flows through the existing breaker.
        累计 copilot 高级请求的美元等值(非 copilot 时为 0.0)。"""
        if self._usage_ledger is not None:
            return self._role_cost(None, cost_basis="premium_request")
        return copilot_usd_for_premium_requests(self.copilot_premium_requests)

    def util_usd(self) -> float:
        if self._usage_ledger is not None:
            return self._role_cost("util", cost_basis="token")
        total = 0.0
        for model, values in self.util_usage_by_model.items():
            (
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
            ) = values
            total += usd_for_tokens(
                model,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens=reasoning_output_tokens,
                price_lookup=price_for,
            )
        return total

    def scientist_usd(self) -> float:
        if self._usage_ledger is not None:
            return self._role_cost("scientist", cost_basis="token")
        total = 0.0
        for model, values in self.scientist_usage_by_model.items():
            (
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens,
            ) = values
            total += usd_for_tokens(
                model,
                input_tokens,
                cached_input_tokens,
                output_tokens,
                reasoning_output_tokens=reasoning_output_tokens,
                price_lookup=price_for,
            )
        return total

    def engineer_usd(self) -> float:
        if self._usage_ledger is not None:
            return self._role_cost("engineer", cost_basis="token")
        return usd_for_tokens(
            self.engineer_model,
            self.engineer_input_tokens,
            self.engineer_cached_input_tokens,
            self.engineer_output_tokens,
            reasoning_output_tokens=self.engineer_reasoning_output_tokens,
            price_lookup=price_for,
        )

    def reviewer_usd(self) -> float:
        if self._usage_ledger is not None:
            return self._role_cost("reviewer", cost_basis="token")
        return usd_for_tokens(
            self.reviewer_model,
            self.reviewer_input_tokens,
            self.reviewer_cached_input_tokens,
            self.reviewer_output_tokens,
            reasoning_output_tokens=self.reviewer_reasoning_output_tokens,
            price_lookup=price_for,
        )

    def total_input_tokens(self) -> int:
        if self._usage_ledger is not None:
            return self._ledger_summary().input_tokens
        return (
            self.scientist_input_tokens
            + self.engineer_input_tokens
            + self.reviewer_input_tokens
            + self.util_input_tokens
        )

    def total_output_tokens(self) -> int:
        if self._usage_ledger is not None:
            return self._ledger_summary().output_tokens
        return (
            self.scientist_output_tokens
            + self.engineer_output_tokens
            + self.reviewer_output_tokens
            + self.util_output_tokens
        )

    def total_reasoning_output_tokens(self) -> int:
        if self._usage_ledger is not None:
            return self._ledger_summary().reasoning_output_tokens
        return (
            self.scientist_reasoning_output_tokens
            + self.engineer_reasoning_output_tokens
            + self.reviewer_reasoning_output_tokens
            + self.util_reasoning_output_tokens
        )

    def total_cached_input_tokens(self) -> int:
        if self._usage_ledger is not None:
            return self._ledger_summary().cached_input_tokens
        return (
            self.scientist_cached_input_tokens
            + self.engineer_cached_input_tokens
            + self.reviewer_cached_input_tokens
            + self.util_cached_input_tokens
        )

    def total_cache_write_tokens(self) -> int:
        if self._usage_ledger is not None:
            return self._ledger_summary().cache_write_tokens
        return 0

    def pricing_status(self) -> str:
        if self._usage_ledger is None:
            return "priced"
        return self._ledger_summary().pricing_status

    def usage_summary(self) -> UsageSummary:
        if self._usage_ledger is None:
            return UsageSummary(
                call_count=0,
                known_cost_usd=self.total_usd(),
                cost_usd=self.total_usd(),
                pricing_status="priced",
                priced_calls=0,
                partial_calls=0,
                unpriced_calls=0,
                not_billed_calls=0,
                input_tokens=self.total_input_tokens(),
                cached_input_tokens=self.total_cached_input_tokens(),
                output_tokens=self.total_output_tokens(),
                reasoning_output_tokens=self.total_reasoning_output_tokens(),
                premium_requests=self.copilot_premium_requests,
            )
        return self._ledger_summary()

    def scientist_totals(self) -> tuple[int, int, int, int]:
        if self._usage_ledger is None:
            return (
                self.scientist_input_tokens,
                self.scientist_cached_input_tokens,
                self.scientist_output_tokens,
                self.scientist_reasoning_output_tokens,
            )
        return self._token_totals_for_role("scientist")

    def scientist_usage_by_model_snapshot(self) -> dict[str, list[int]]:
        if self._usage_ledger is None:
            return {
                model: list(values)
                for model, values in self.scientist_usage_by_model.items()
            }
        out: dict[str, list[int]] = {}
        for record in self._ledger_records():
            if self._role_for_record(record) != "scientist":
                continue
            bucket = out.setdefault(record.model or "unknown", [0, 0, 0, 0])
            bucket[0] += record.input_tokens or 0
            bucket[1] += record.cached_input_tokens or 0
            bucket[2] += record.output_tokens or 0
            bucket[3] += record.reasoning_output_tokens or 0
        return out

    def copilot_premium_request_total(self) -> float:
        if self._usage_ledger is not None:
            return self._ledger_summary().premium_requests
        return self.copilot_premium_requests

    def _ledger_summary(self) -> UsageSummary:
        assert self._usage_ledger is not None
        return self._usage_ledger.summary(mission_id=self._mission_id)

    def _ledger_records(self) -> list[UsageRecord]:
        assert self._usage_ledger is not None
        return self._usage_ledger.records(mission_id=self._mission_id)

    def _role_cost(self, role: str | None, *, cost_basis: str) -> float:
        total = 0.0
        for record in self._ledger_records():
            if record.cost_basis != cost_basis:
                continue
            if role is not None and self._role_for_record(record) != role:
                continue
            if record.cost_usd is not None:
                total += record.cost_usd
        return total

    def _token_totals_for_role(self, role: str) -> tuple[int, int, int, int]:
        totals = [0, 0, 0, 0]
        for record in self._ledger_records():
            if self._role_for_record(record) != role:
                continue
            totals[0] += record.input_tokens or 0
            totals[1] += record.cached_input_tokens or 0
            totals[2] += record.output_tokens or 0
            totals[3] += record.reasoning_output_tokens or 0
        return totals[0], totals[1], totals[2], totals[3]

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
                raw = {
                    "input_tokens": nested.get("input_tokens", 0),
                    "cached_input_tokens": nested.get("cached_input_tokens", 0),
                    "output_tokens": nested.get("output_tokens", 0),
                    "reasoning_output_tokens": nested.get("reasoning_output_tokens", 0),
                }
            else:
                model = str(event.get(f"{phase}_model") or "")
                raw = {
                    "input_tokens": event.get(f"{phase}_input_tokens", 0),
                    "cached_input_tokens": event.get(
                        f"{phase}_cached_input_tokens", 0
                    ),
                    "output_tokens": event.get(f"{phase}_output_tokens", 0),
                    "reasoning_output_tokens": event.get(
                        f"{phase}_reasoning_output_tokens", 0
                    ),
                }
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

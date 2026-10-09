"""Spend awareness: what a mission has spent, what the account has left.

Three readers share these helpers:

* the roles (Planner, Manager), which receive one compact line describing how
  the active account is billed and how much of the month remains, so they can
  prefer fewer, larger calls on request-billed plans and shorter contexts on
  credit-billed ones. The line is information for their judgement, never a
  limit;
* the round loop, which pauses a mission and asks the operator once the
  mission's own spend reaches an operator-configured budget. Both budget
  knobs default to 0 (off), so nothing ever pauses unless the operator asked;
* the web cockpit, which shows spend per mission and per project.

The account quota itself is read in the providers layer
(``provider_integrations.account_budget``); this kernel module only formats it.

A usage record's ``mission_id`` is the attempt key
``<item_id>:attempt:<n>``; a mission's spend is the sum over its attempts.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .knob_store import persisted_knob
from .usage import UsageLedger, UsageRecord, UsageSummary, summarize_usage

MISSION_BUDGET_REQUESTS_KNOB = "ARGUS_SKILL_MISSION_BUDGET_REQUESTS"
MISSION_BUDGET_USD_KNOB = "ARGUS_SKILL_MISSION_BUDGET_USD"
MISSION_BUDGET_DECISION_KIND = "mission_budget"
_ATTEMPT_SEPARATOR = ":attempt:"
# 1e9 nano-AIU is one AI credit.
NANO_AIU_PER_CREDIT = 1_000_000_000


def mission_item_id(usage_mission_id: str | None) -> str:
    """The backlog item a usage record's mission id belongs to."""
    text = str(usage_mission_id or "")
    return text.split(_ATTEMPT_SEPARATOR, 1)[0]


def _belongs(record: UsageRecord, item_id: str) -> bool:
    return bool(item_id) and mission_item_id(record.mission_id) == item_id


def mission_usage_summary(ledger_root: Path | str, item_id: str) -> UsageSummary:
    records = UsageLedger(ledger_root, migrate_legacy=False).records()
    return summarize_usage(record for record in records if _belongs(record, item_id))


def usage_by_mission(records: Iterable[UsageRecord]) -> dict[str, dict[str, Any]]:
    """Compact spend per backlog item, for the cockpit."""
    grouped: dict[str, list[UsageRecord]] = defaultdict(list)
    for record in records:
        item_id = mission_item_id(record.mission_id)
        if item_id:
            grouped[item_id].append(record)
    return {item_id: compact_usage(summarize_usage(rows)) for item_id, rows in grouped.items()}


def compact_usage(summary: UsageSummary) -> dict[str, Any]:
    return {
        "calls": summary.call_count,
        "premium_requests": round(float(summary.premium_requests or 0.0), 2),
        "credits": round((summary.total_nano_aiu or 0) / NANO_AIU_PER_CREDIT, 2),
        "known_cost_usd": round(float(summary.known_cost_usd or 0.0), 4),
        "pricing_status": summary.pricing_status,
        "input_tokens": int(summary.input_tokens or 0),
        "cached_input_tokens": int(summary.cached_input_tokens or 0),
        "output_tokens": int(summary.output_tokens or 0),
    }


@dataclass(frozen=True)
class MissionBudget:
    requests: float = 0.0
    usd: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.requests > 0 or self.usd > 0

    def to_jsonable(self) -> dict[str, float]:
        return {"requests": self.requests, "usd": self.usd}


def _non_negative(name: str, env: Mapping[str, str] | None) -> float:
    source = os.environ if env is None else env
    raw = str(source.get(name) or "").strip() or persisted_knob(name, env=source).strip()
    try:
        value = float(raw.removeprefix("$")) if raw else 0.0
    except ValueError:
        return 0.0
    return value if value > 0 and value == value else 0.0


def mission_budget(env: Mapping[str, str] | None = None) -> MissionBudget:
    """The operator's per-mission budget; both parts default to off."""
    return MissionBudget(
        requests=_non_negative(MISSION_BUDGET_REQUESTS_KNOB, env),
        usd=_non_negative(MISSION_BUDGET_USD_KNOB, env),
    )


def mission_budget_reached(summary: UsageSummary, budget: MissionBudget) -> str:
    """Plain description of the reached limit, or "" while under budget."""
    reached: list[str] = []
    requests = float(summary.premium_requests or 0.0)
    if budget.requests > 0 and requests >= budget.requests:
        reached.append(f"{requests:g} of {budget.requests:g} premium requests")
    usd = float(summary.known_cost_usd or 0.0)
    if budget.usd > 0 and usd >= budget.usd:
        reached.append(f"${usd:.2f} of ${budget.usd:.2f}")
    return " and ".join(reached)


def _amount(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}" if abs(value) >= 10 else f"{value:g}"


def account_summary_line(quota: Any) -> str:
    """One plain clause: billing mode and what is left this month.

    ``quota`` is a ``provider_integrations.copilot_account_quota.AccountQuota``.
    """
    who = f"{quota.provider} account {quota.login}".strip() if quota.login else f"{quota.provider} account"
    reset = f", resets {quota.reset_date}" if quota.reset_date else ""
    if quota.error:
        return f"{who}: quota could not be read ({quota.error}); billing mode unknown"
    if quota.unlimited or quota.billing_mode == "unlimited":
        return f"{who}: premium usage is unlimited on this plan"
    if quota.billing_mode == "request":
        left = _amount(quota.remaining)
        total = _amount(quota.entitlement)
        return (
            f"{who}: request-billed (every model call costs a premium request, "
            f"whatever its size); {left} of {total} left this month{reset}"
        )
    if quota.billing_mode == "credit":
        percent = "?" if quota.percent_remaining is None else f"{quota.percent_remaining:.0f}%"
        return (
            f"{who}: credit-billed (calls cost by tokens); {percent} of the monthly "
            f"credits left ({_amount(quota.remaining)} of {_amount(quota.entitlement)}){reset}"
        )
    return f"{who}: billing mode unknown"


def budget_signal(
    quota: Any,
    *,
    mission: UsageSummary | None = None,
    budget: MissionBudget | None = None,
) -> str:
    """The compact line given to roles; "" when there is nothing to say."""
    parts: list[str] = []
    if quota is not None:
        parts.append(account_summary_line(quota))
        if not quota.error:
            if quota.billing_mode == "request":
                parts.append(
                    "prefer fewer, larger calls: fold related checks into one turn "
                    "rather than many small ones"
                )
            elif quota.billing_mode == "credit":
                parts.append(
                    "prefer shorter contexts: cite paths and excerpts instead of "
                    "rereading or pasting large files and logs"
                )
            if quota.low:
                parts.append(
                    f"LOW: under {quota.to_jsonable()['warn_percent']:g}% of the month "
                    "is left and the operator has been warned"
                )
    if mission is not None and mission.call_count:
        spent = f"{float(mission.premium_requests or 0.0):g} premium requests"
        credits = (mission.total_nano_aiu or 0) / NANO_AIU_PER_CREDIT
        if credits > 0:
            spent += f", {credits:,.1f} credits"
        if mission.known_cost_usd:
            spent += f", ${mission.known_cost_usd:.2f}"
        parts.append(f"this mission has spent {spent} over {mission.call_count} calls")
    if budget is not None and budget.enabled:
        limits = []
        if budget.requests > 0:
            limits.append(f"{budget.requests:g} premium requests")
        if budget.usd > 0:
            limits.append(f"${budget.usd:.2f}")
        parts.append(
            "the operator set a per-mission budget of " + " / ".join(limits)
            + "; at that point Argus pauses and asks the operator"
        )
    if not parts:
        return ""
    return (
        "Account budget (information for your judgement, not a limit on what to do): "
        + "; ".join(parts) + "."
    )




# The round loop only sees the project state root and item id; the supervisor
# settles the pause. This small marker carries "the pause was the mission
# budget, not a Manager WAIT" across that boundary.
_PAUSE_MARKER_FILE = "mission-budget-pauses.json"


def _marker_path(root: Path | str) -> Path:
    return Path(root) / _PAUSE_MARKER_FILE


def _read_markers(root: Path | str) -> dict[str, Any]:
    try:
        value = json.loads(_marker_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_markers(root: Path | str, markers: dict[str, Any]) -> None:
    path = _marker_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(markers, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def record_mission_budget_pause(
    root: Path | str,
    item_id: str,
    *,
    reached: str,
    summary: UsageSummary,
    budget: MissionBudget,
) -> dict[str, Any]:
    marker = {
        "reached": reached,
        "budget": budget.to_jsonable(),
        "spent": compact_usage(summary),
        "at": time.time(),
    }
    markers = _read_markers(root)
    markers[item_id] = marker
    _write_markers(root, markers)
    return marker


def take_mission_budget_pause(root: Path | str, item_id: str) -> dict[str, Any] | None:
    """Consume the marker the round loop left for ``item_id``."""
    markers = _read_markers(root)
    marker = markers.pop(item_id, None)
    if marker is None:
        return None
    try:
        _write_markers(root, markers)
    except OSError:
        pass
    return marker if isinstance(marker, dict) else None




__all__ = [
    "MISSION_BUDGET_DECISION_KIND",
    "MISSION_BUDGET_REQUESTS_KNOB",
    "MISSION_BUDGET_USD_KNOB",
    "MissionBudget",
    "account_summary_line",
    "budget_signal",
    "compact_usage",
    "mission_budget",
    "mission_budget_reached",
    "mission_item_id",
    "mission_usage_summary",
    "record_mission_budget_pause",
    "take_mission_budget_pause",
    "usage_by_mission",
]

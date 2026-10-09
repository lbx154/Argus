"""Account budget signal for roles and the cockpit (providers layer).

Reads the active account's quota where the backend exposes one and combines it
with the mission spend helpers in ``core.budget_signal``. Everything here is
advisory: it never raises into a caller and never pauses work.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

from ..core.budget_signal import budget_signal, mission_budget, mission_usage_summary
from .copilot_account_quota import AccountQuota, account_quota


def active_account_quota(
    *,
    role: str = "engineer",
    blocking: bool = True,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AccountQuota | None:
    """Quota of the account the given role's backend bills, when detectable."""
    from ..core.knobs import resolve_role_backend

    backends: list[str] = []
    for name in dict.fromkeys((role, "engineer")):
        try:
            backends.append(str(resolve_role_backend(name, env=env, default="") or "").strip().lower())
        except Exception:  # noqa: BLE001 - advisory signal
            continue
    if "copilot" not in backends:
        return None
    try:
        return account_quota(root=root, env=env, blocking=blocking)
    except Exception:  # noqa: BLE001 - advisory signal
        return None


def role_budget_signal(
    *,
    ledger_root: Path | str | None = None,
    item_id: str = "",
    role: str = "engineer",
) -> str:
    """Budget line for a role prompt. Never raises."""
    try:
        quota = active_account_quota(role=role)
        mission = (
            mission_usage_summary(ledger_root, item_id)
            if ledger_root is not None and item_id
            else None
        )
        return budget_signal(quota, mission=mission, budget=mission_budget())
    except Exception:  # noqa: BLE001 - advisory signal
        return ""


_LOW_QUOTA_MARKER_FILE = "account-quota-warnings.json"


def low_quota_warning_text(quota: AccountQuota, *, chinese: bool = False) -> str:
    left = (
        f"{quota.percent_remaining:.0f}%" if quota.percent_remaining is not None else "?"
    )
    reset = quota.reset_date or ("下个周期" if chinese else "the next cycle")
    account = quota.login or quota.provider
    if chinese:
        unit = "次高级请求" if quota.billing_mode == "request" else "额度"
        return (
            f"账号 {account} 本月{unit}只剩 {left}（{reset} 重置）。"
            "Argus 不会因此停下；如需控制花费，可在设置里设单任务预算或换账号。"
        )
    unit = "premium requests" if quota.billing_mode == "request" else "credits"
    return (
        f"Account {account} has {left} of this month's {unit} left (resets {reset}). "
        "Argus keeps working; set a per-mission budget or switch accounts in "
        "settings if you want to limit spend."
    )


def maybe_warn_low_account_quota(
    marker_root: Path | str,
    emit: Any,
    *,
    chinese: bool = False,
    quota: AccountQuota | None = None,
) -> bool:
    """Emit one operator alert per account and quota period when quota is low."""
    try:
        quota = quota if quota is not None else active_account_quota(blocking=False)
        if quota is None or quota.error or not quota.low:
            return False
        key = f"{quota.provider}:{quota.login}:{quota.reset_date}"
        path = Path(marker_root) / _LOW_QUOTA_MARKER_FILE
        try:
            seen = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            seen = {}
        if not isinstance(seen, dict):
            seen = {}
        if key in seen:
            return False
        seen[key] = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(seen, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        from ..core.event_catalog import EventType

        emit({
            "type": EventType.OPERATOR_ALERT,
            "operator_alert": True,
            "reason": "account_quota_low",
            "text": low_quota_warning_text(quota, chinese=chinese),
            "account": quota.login,
            "billing_mode": quota.billing_mode,
            "percent_remaining": quota.percent_remaining,
        })
        return True
    except Exception:  # noqa: BLE001 - a warning must never stop work
        return False


__all__ = [
    "active_account_quota",
    "low_quota_warning_text",
    "maybe_warn_low_account_quota",
    "role_budget_signal",
]

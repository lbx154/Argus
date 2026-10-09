"""What the active Copilot account has left this month, and how it is billed.

Copilot accounts are billed in one of two ways. A request-billed plan charges
one premium request per model call whatever its size, so fifteen small calls
cost fifteen times one large call. A credit-billed seat charges by tokens
against a monthly credit entitlement, so a long context costs more than a
short one. Argus's own ledger records what each call spent but cannot know
which of the two the account pays in, nor how much of the month is left.

GitHub reports both on ``/copilot_internal/user`` under
``quota_snapshots.premium_interactions`` (``entitlement``, ``remaining``,
``credits_used``, ``percent_remaining``). This module reads that endpoint with
the token of the account the Copilot workers actually use, keeps a small cache
next to the rest of Argus state, and never writes or returns the token.

The result is advisory: it informs the roles and the operator, it is never an
admission check.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from ..core.knob_store import persisted_knob
from ..core.paths import global_root

BillingMode = Literal["request", "credit", "unlimited", "unknown"]

_CACHE_FILE = "copilot-account-quota.json"
_PROBE_KNOB = "ARGUS_SKILL_ACCOUNT_QUOTA_PROBE"
_TTL_KNOB = "ARGUS_SKILL_ACCOUNT_QUOTA_TTL_SECONDS"
_MODE_KNOB = "ARGUS_SKILL_ACCOUNT_BILLING_MODE"
_WARN_KNOB = "ARGUS_SKILL_ACCOUNT_QUOTA_WARN_PERCENT"
_DEFAULT_TTL_SECONDS = 600.0
_FAILURE_TTL_SECONDS = 300.0
_DEFAULT_WARN_PERCENT = 10.0
_HTTP_TIMEOUT_SECONDS = 5.0
# No request-billed plan grants anywhere near this many premium requests a
# month; credit entitlements are counted in hundredths of a dollar and start
# far above it. Only used when the endpoint does not say which unit it means.
_CREDIT_ENTITLEMENT_FLOOR = 10_000.0
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})

_REFRESH_LOCK = threading.Lock()
_REFRESHING: set[str] = set()


@dataclass(frozen=True)
class AccountQuota:
    """The monthly premium quota of one account, without any credential."""

    provider: str
    login: str
    plan: str
    billing_mode: BillingMode
    entitlement: float | None
    remaining: float | None
    used: float | None
    percent_remaining: float | None
    reset_date: str
    overage_permitted: bool
    unlimited: bool
    fetched_at: float
    mode_source: str = "detected"
    error: str = ""

    @property
    def low(self) -> bool:
        if self.unlimited or self.percent_remaining is None:
            return False
        return self.percent_remaining < warn_percent()

    def to_jsonable(self) -> dict[str, Any]:
        row = asdict(self)
        row["low"] = self.low
        row["warn_percent"] = warn_percent()
        return row


def _setting(name: str, default: str, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    raw = str(source.get(name) or "").strip()
    if raw:
        return raw
    persisted = persisted_knob(name, env=source).strip()
    return persisted or default


def probe_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    explicit = str(source.get(_PROBE_KNOB) or "").strip().lower()
    if not explicit and source.get("PYTEST_CURRENT_TEST"):
        # Unit tests must never reach the network with the operator's login.
        return False
    value = explicit or _setting(_PROBE_KNOB, "on", env).lower()
    return value not in _FALSY


def warn_percent(env: Mapping[str, str] | None = None) -> float:
    try:
        return max(0.0, min(100.0, float(_setting(_WARN_KNOB, str(_DEFAULT_WARN_PERCENT), env))))
    except ValueError:
        return _DEFAULT_WARN_PERCENT


def _ttl_seconds(env: Mapping[str, str] | None = None) -> float:
    try:
        return max(30.0, float(_setting(_TTL_KNOB, str(_DEFAULT_TTL_SECONDS), env)))
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # drop NaN


def classify_billing(user: Mapping[str, Any]) -> tuple[BillingMode, Mapping[str, Any]]:
    """Billing mode from a ``/copilot_internal/user`` payload.

    Returns the mode and the premium quota row it was read from.
    """
    snapshots = user.get("quota_snapshots")
    premium = snapshots.get("premium_interactions") if isinstance(snapshots, Mapping) else None
    if not isinstance(premium, Mapping):
        return "unknown", {}
    if premium.get("unlimited") is True:
        return "unlimited", premium
    token_billed = bool(user.get("token_based_billing") or premium.get("token_based_billing"))
    entitlement = _number(premium.get("entitlement")) or 0.0
    # The endpoint marks newer plans token-billed even when their premium
    # entitlement is still a count of requests, so the size of the entitlement
    # decides which unit it is.
    if token_billed and entitlement >= _CREDIT_ENTITLEMENT_FLOOR:
        return "credit", premium
    return "request", premium


def parse_account_quota(
    user: Mapping[str, Any],
    *,
    provider: str = "copilot",
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> AccountQuota:
    mode, premium = classify_billing(user)
    mode_source = "detected"
    override = _setting(_MODE_KNOB, "auto", env).lower()
    if override in {"request", "credit"} and mode in {"request", "credit", "unknown"}:
        mode = override  # type: ignore[assignment]
        mode_source = "operator"
    entitlement = _number(premium.get("entitlement"))
    remaining = _number(premium.get("quota_remaining"))
    if remaining is None:
        remaining = _number(premium.get("remaining"))
    used = _number(premium.get("credits_used"))
    if (used is None or used == 0) and entitlement is not None and remaining is not None:
        used = max(0.0, entitlement - remaining)
    percent = _number(premium.get("percent_remaining"))
    if percent is None and entitlement and remaining is not None:
        percent = max(0.0, min(100.0, 100.0 * remaining / entitlement))
    return AccountQuota(
        provider=provider,
        login=str(user.get("login") or ""),
        plan=str(user.get("copilot_plan") or user.get("access_type_sku") or ""),
        billing_mode=mode,
        entitlement=entitlement,
        remaining=remaining,
        used=used,
        percent_remaining=percent,
        reset_date=str(user.get("quota_reset_date") or ""),
        overage_permitted=bool(premium.get("overage_permitted")),
        unlimited=bool(premium.get("unlimited")),
        fetched_at=time.time() if now is None else float(now),
        mode_source=mode_source,
    )


def _read_config(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # The CLI prefixes its managed config with // comment lines.
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    try:
        value = json.loads(body)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _config_token(config: Mapping[str, Any]) -> tuple[str, str, str]:
    """(token, login, host) of the CLI's last logged-in user."""
    user = config.get("lastLoggedInUser")
    if not isinstance(user, Mapping):
        users = config.get("loggedInUsers")
        user = users[0] if isinstance(users, list) and users and isinstance(users[0], Mapping) else {}
    login = str(user.get("login") or "").strip()
    host = str(user.get("host") or "https://github.com").strip().rstrip("/")
    if not login:
        return "", "", host
    tokens = config.get("authTokens")
    if isinstance(tokens, Mapping):
        entry = tokens.get(f"{host}:{login}")
        if isinstance(entry, Mapping) and str(entry.get("token") or "").strip():
            return str(entry["token"]).strip(), login, host
    legacy = config.get("copilotTokens")
    if isinstance(legacy, Mapping):
        entry = legacy.get(f"{host}:{login}")
        if isinstance(entry, str) and entry.strip():
            return entry.strip(), login, host
    return "", login, host


def active_copilot_credential(
    env: Mapping[str, str] | None = None,
) -> tuple[str, str, str] | None:
    """(token, login, host) the Copilot workers authenticate with, or None.

    Mirrors ``copilot_home``'s account selection: an opted-in environment
    token, then a dedicated account home, then the Argus home (which mirrors
    the operator's login), then the operator's own home.
    """
    from ..agent_cli.copilot_home import (
        argus_copilot_home,
        copilot_account_home,
        copilot_env_token,
        copilot_hosted_trial,
    )

    source = os.environ if env is None else env
    if copilot_hosted_trial(source):
        return None
    token = copilot_env_token(source)
    if token:
        return token, "", "https://github.com"
    homes: list[Path] = []
    account = copilot_account_home(source)
    if account is not None:
        homes.append(account)
    else:
        configured = str(source.get("COPILOT_HOME") or "").strip()
        if configured:
            homes.append(Path(configured).expanduser())
        homes.append(argus_copilot_home(source))
        homes.append(Path(str(source.get("HOME") or Path.home())) / ".copilot")
    for home in homes:
        config = _read_config(home / "config.json")
        if config is None:
            continue
        token, login, host = _config_token(config)
        if token:
            return token, login, host
    return None


def _api_base(host: str) -> str:
    host = host.rstrip("/")
    if host in {"https://github.com", "http://github.com", "github.com"}:
        return "https://api.github.com"
    bare = host.split("://", 1)[-1]
    return f"https://api.{bare}"


def fetch_copilot_user(token: str, host: str = "https://github.com") -> dict[str, Any]:
    request = urllib.request.Request(
        _api_base(host) + "/copilot_internal/user",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "User-Agent": "argus-account-quota",
        },
    )
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https host
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("unexpected quota payload")
    return payload


def _cache_path(root: Path | None) -> Path:
    return (root or global_root()) / _CACHE_FILE


def _read_cache(root: Path | None) -> dict[str, Any] | None:
    try:
        value = json.loads(_cache_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_cache(root: Path | None, row: dict[str, Any]) -> None:
    path = _cache_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _from_cache(row: Mapping[str, Any]) -> AccountQuota | None:
    try:
        fields = {name: row[name] for name in AccountQuota.__dataclass_fields__ if name in row}
        return AccountQuota(**fields)
    except TypeError:
        return None


def _fresh(row: Mapping[str, Any], now: float, env: Mapping[str, str] | None) -> bool:
    fetched = _number(row.get("fetched_at")) or 0.0
    ttl = _FAILURE_TTL_SECONDS if row.get("error") else _ttl_seconds(env)
    return 0.0 <= now - fetched < ttl


def refresh_account_quota(
    *,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> AccountQuota | None:
    """Query the provider now and cache the result (failures cached briefly)."""
    if not probe_enabled(env):
        return None
    credential = active_copilot_credential(env)
    if credential is None:
        return None
    token, login, host = credential
    now = time.time()
    try:
        quota = parse_account_quota(fetch_copilot_user(token, host), now=now, env=env)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        # Keep only the exception type: a provider message could echo headers.
        quota = AccountQuota(
            provider="copilot", login=login, plan="", billing_mode="unknown",
            entitlement=None, remaining=None, used=None, percent_remaining=None,
            reset_date="", overage_permitted=False, unlimited=False,
            fetched_at=now, error=type(exc).__name__,
        )
    _write_cache(root, asdict(quota))
    return quota


def account_quota(
    *,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
    blocking: bool = True,
) -> AccountQuota | None:
    """The cached account quota, refreshed when stale.

    ``blocking=False`` never waits on the network: it returns whatever is
    cached and refreshes in a background thread.
    """
    if not probe_enabled(env):
        return None
    cached = _read_cache(root)
    now = time.time()
    if cached is not None and _fresh(cached, now, env):
        return _from_cache(cached)
    if blocking:
        return refresh_account_quota(root=root, env=env) or (
            _from_cache(cached) if cached is not None else None
        )
    key = str(_cache_path(root))
    with _REFRESH_LOCK:
        start = key not in _REFRESHING
        if start:
            _REFRESHING.add(key)
    if start:
        def _run() -> None:
            try:
                refresh_account_quota(root=root, env=env)
            except Exception:  # noqa: BLE001 - advisory background refresh
                pass
            finally:
                with _REFRESH_LOCK:
                    _REFRESHING.discard(key)

        threading.Thread(target=_run, name="argus-account-quota", daemon=True).start()
    return _from_cache(cached) if cached is not None else None


__all__ = [
    "AccountQuota",
    "BillingMode",
    "account_quota",
    "active_copilot_credential",
    "classify_billing",
    "fetch_copilot_user",
    "parse_account_quota",
    "probe_enabled",
    "refresh_account_quota",
    "warn_percent",
]

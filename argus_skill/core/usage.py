"""Call-level, append-only usage accounting.

``usage.jsonl`` is the sole cost aggregation source.  Lifecycle events remain a
human-readable timeline, but are never summed for spend because one call can be
represented by several overlapping events.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal

from .codex_usage import TokenUsage, extract_token_usage
from .copilot_usage import NANO_AIU_PER_USD, find_copilot_usage_near
from .event_catalog import CALL_SCOPED_EVENT_TYPES, EventType, canonical_event_type
from .pricing import PricingStatus, quote_copilot_usage, quote_token_usage
from .runner_errors import is_pre_provider_refusal_error

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

USAGE_FILE = "usage.jsonl"
USAGE_LOCK_FILE = "usage.lock"
USAGE_MIGRATION_FILE = "usage.migration-v1.json"
USAGE_COPILOT_RECONCILE_FILE = "usage.copilot-token-v1.json"
EVENT_MIGRATION_FILE = "events.migration-v2.json"
EVENT_MIGRATION_LOCK_FILE = "events.migration-v2.lock"
_COPILOT_RECONCILE_VERSION = 3
UsageSource = Literal["run_exec", "legacy.events"]
CallStatus = Literal["completed", "error", "denied"]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_CALL_ID_CACHE: dict[str, tuple[tuple[int, int, int] | None, set[str]]] = {}
_CALL_ID_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class UsageRecord:
    call_id: str
    project_id: str
    mission_id: str | None
    provider: str
    model: str
    run_label: str
    started_at: float
    completed_at: float
    status: CallStatus
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    premium_requests: float | None
    pricing_status: PricingStatus
    pricing_tier: str
    cost_usd: float | None
    cost_basis: str
    thread_id: str | None = None
    duration_ms: int = 0
    model_usage: tuple[dict[str, Any], ...] = ()
    cache_write_tokens: int | None = None
    total_nano_aiu: int | None = None
    premium_request_cost_usd: float | None = None
    error: str = ""
    source: UsageSource = "run_exec"
    schema_version: int = 1

    def to_jsonable(self) -> dict[str, Any]:
        row = asdict(self)
        row["model_usage"] = [dict(item) for item in self.model_usage]
        return row

    @classmethod
    def from_jsonable(cls, row: dict[str, Any]) -> "UsageRecord":
        cost = _optional_float(row.get("cost_usd"))
        pricing_status = _pricing_status(row.get("pricing_status"))
        pricing_tier = str(row.get("pricing_tier") or "unknown")
        error = str(row.get("error") or "")
        if (
            is_pre_provider_refusal_error(error)
            and cost is None
            and row.get("total_nano_aiu") is None
            and not row.get("model_usage")
            and not (_optional_float(row.get("premium_requests")) or 0.0)
            and not (
                _optional_float(row.get("premium_request_cost_usd")) or 0.0
            )
            and all(
                row.get(field) is None
                for field in (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                )
            )
        ):
            # Copilot rejects an unknown local resume ID before starting a
            # provider turn. Older ledgers called this "partial", which made
            # strict cost control permanently block every subsequent call.
            pricing_status = "not_billed"
            pricing_tier = "not_started"
            cost = 0.0
        started_at = _float(row.get("started_at"), _float(row.get("ts"), 0.0))
        completed_at = _float(
            row.get("completed_at"),
            _float(row.get("ts"), 0.0),
        )
        return cls(
            call_id=str(row.get("call_id") or ""),
            project_id=str(row.get("project_id") or ""),
            mission_id=_optional_text(row.get("mission_id")),
            provider=str(row.get("provider") or ""),
            model=str(row.get("model") or ""),
            run_label=str(row.get("run_label") or ""),
            started_at=started_at,
            completed_at=completed_at,
            status=_call_status(row.get("status")),
            input_tokens=_optional_int(row.get("input_tokens")),
            cached_input_tokens=_optional_int(row.get("cached_input_tokens")),
            cache_write_tokens=_optional_int(row.get("cache_write_tokens")),
            output_tokens=_optional_int(row.get("output_tokens")),
            reasoning_output_tokens=_optional_int(
                row.get("reasoning_output_tokens")
            ),
            premium_requests=_optional_float(row.get("premium_requests")),
            pricing_status=pricing_status,
            pricing_tier=pricing_tier,
            cost_usd=cost,
            cost_basis=str(row.get("cost_basis") or ""),
            thread_id=_optional_text(row.get("thread_id")),
            duration_ms=_duration_ms(
                started_at,
                completed_at,
                recorded=row.get("duration_ms"),
            ),
            model_usage=_normalize_model_usage(row.get("model_usage")),
            total_nano_aiu=_optional_int(row.get("total_nano_aiu")),
            premium_request_cost_usd=_optional_float(
                row.get("premium_request_cost_usd")
            ),
            error=error,
            source=(
                "legacy.events"
                if row.get("source") == "legacy.events"
                else "run_exec"
            ),
            schema_version=max(1, _optional_int(row.get("schema_version")) or 1),
        )


@dataclass(frozen=True)
class UsageSummary:
    call_count: int
    known_cost_usd: float
    cost_usd: float | None
    pricing_status: str
    priced_calls: int
    partial_calls: int
    unpriced_calls: int
    not_billed_calls: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    premium_requests: float
    cache_write_tokens: int = 0
    total_nano_aiu: int = 0
    premium_request_cost_usd: float = 0.0

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


def build_usage_record(
    *,
    call_id: str,
    project_root: Path,
    mission_id: str | None,
    provider: str,
    model: str,
    run_label: str,
    started_at: float,
    completed_at: float,
    status: CallStatus,
    token_usage: TokenUsage | None = None,
    premium_requests: float | None = None,
    total_nano_aiu: int | None = None,
    provider_cost_usd: float | None = None,
    thread_id: str | None = None,
    model_usage: Iterable[dict[str, Any]] | None = None,
    error: str = "",
    source: UsageSource = "run_exec",
) -> UsageRecord:
    usage = token_usage or TokenUsage()
    normalized_model_usage = _normalize_model_usage(model_usage)
    normalized_provider = str(provider or "").strip().lower()
    premium_quote = quote_copilot_usage(premium_requests)
    missing_resume_target = (
        is_pre_provider_refusal_error(error)
        and total_nano_aiu is None
        and not normalized_model_usage
        and not usage.observed
        and not (premium_requests or 0.0)
    )
    if status == "denied" or missing_resume_target:
        pricing_status: PricingStatus = "not_billed"
        pricing_tier = "not_started"
        cost_usd: float | None = 0.0
        cost_basis = "none"
    elif normalized_provider == "copilot" and total_nano_aiu is not None:
        pricing_status = "priced"
        pricing_tier = "copilot_token"
        cost_usd = max(0, int(total_nano_aiu)) / NANO_AIU_PER_USD
        cost_basis = "token"
    elif normalized_provider == "copilot":
        pricing_status = premium_quote.status
        pricing_tier = premium_quote.tier
        cost_usd = premium_quote.cost_usd
        cost_basis = (
            "premium_request" if premium_quote.cost_usd is not None else "none"
        )
    elif provider_cost_usd is not None:
        pricing_status = "priced"
        pricing_tier = "provider_reported"
        cost_usd = max(0.0, float(provider_cost_usd))
        cost_basis = "provider_reported"
    else:
        quote = quote_token_usage(
            model,
            input_tokens=usage.input_tokens if usage.input_tokens_present else None,
            cached_input_tokens=(
                usage.cached_input_tokens
                if usage.cached_input_tokens_present
                else None
            ),
            cache_write_tokens=(
                usage.cache_write_tokens
                if usage.cache_write_tokens_present
                else None
            ),
            output_tokens=usage.output_tokens if usage.output_tokens_present else None,
            reasoning_output_tokens=(
                usage.reasoning_output_tokens
                if usage.reasoning_output_tokens_present
                else None
            ),
        )
        pricing_status = quote.status
        pricing_tier = quote.tier
        cost_usd = quote.cost_usd
        cost_basis = "token"
    return UsageRecord(
        call_id=str(call_id),
        project_id=Path(project_root).name,
        mission_id=_optional_text(mission_id),
        provider=normalized_provider,
        model=str(model or ""),
        run_label=str(run_label or ""),
        started_at=float(started_at),
        completed_at=float(completed_at),
        status=status,
        input_tokens=usage.input_tokens if usage.input_tokens_present else None,
        cached_input_tokens=(
            usage.cached_input_tokens if usage.cached_input_tokens_present else None
        ),
        cache_write_tokens=(
            usage.cache_write_tokens if usage.cache_write_tokens_present else None
        ),
        output_tokens=usage.output_tokens if usage.output_tokens_present else None,
        reasoning_output_tokens=(
            usage.reasoning_output_tokens
            if usage.reasoning_output_tokens_present
            else None
        ),
        premium_requests=premium_requests,
        pricing_status=pricing_status,
        pricing_tier=pricing_tier,
        cost_usd=cost_usd,
        cost_basis=cost_basis,
        thread_id=_optional_text(thread_id),
        duration_ms=_duration_ms(started_at, completed_at),
        model_usage=normalized_model_usage,
        total_nano_aiu=total_nano_aiu,
        premium_request_cost_usd=premium_quote.cost_usd,
        error=str(error or "")[:2000],
        source=source,
        schema_version=2,
    )


def usage_recorded_event(record: UsageRecord) -> dict[str, Any]:
    """Return the canonical self-contained ``usage.recorded`` v2 event."""
    models = [dict(item) for item in record.model_usage]
    usage = {
        "input_tokens": record.input_tokens,
        "cached_input_tokens": record.cached_input_tokens,
        "cache_write_tokens": record.cache_write_tokens,
        "output_tokens": record.output_tokens,
        "reasoning_output_tokens": record.reasoning_output_tokens,
        "premium_requests": record.premium_requests,
        "total_nano_aiu": record.total_nano_aiu,
        "models": models,
    }
    pricing = {
        "status": record.pricing_status,
        "tier": record.pricing_tier,
        "cost_basis": record.cost_basis,
        "cost_usd": record.cost_usd,
        "premium_request_cost_usd": record.premium_request_cost_usd,
    }
    return {
        "type": EventType.USAGE_RECORDED,
        "schema_version": 2,
        "call_id": record.call_id,
        "project_id": record.project_id,
        "mission_id": record.mission_id,
        "thread_id": record.thread_id,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "duration_ms": record.duration_ms,
        "provider": record.provider,
        "model": record.model,
        "run_label": record.run_label,
        "status": record.status,
        "source": record.source,
        "error": record.error,
        "usage": usage,
        "pricing": pricing,
        # Flat compatibility fields for existing event consumers.
        "input_tokens": record.input_tokens,
        "cached_input_tokens": record.cached_input_tokens,
        "cache_write_tokens": record.cache_write_tokens,
        "output_tokens": record.output_tokens,
        "reasoning_output_tokens": record.reasoning_output_tokens,
        "premium_requests": record.premium_requests,
        "total_nano_aiu": record.total_nano_aiu,
        "premium_request_cost_usd": record.premium_request_cost_usd,
        "pricing_status": record.pricing_status,
        "pricing_tier": record.pricing_tier,
        "cost_basis": record.cost_basis,
        "cost_usd": record.cost_usd,
        "ts": record.completed_at,
    }


class UsageLedger:
    """Project-local ledger with cross-process idempotent append."""

    def __init__(self, project_root: Path | str, *, migrate_legacy: bool = True) -> None:
        self.project_root = Path(project_root).expanduser()
        self.path = self.project_root / USAGE_FILE
        self.lock_path = self.project_root / USAGE_LOCK_FILE
        self.migration_path = self.project_root / USAGE_MIGRATION_FILE
        self.copilot_reconcile_path = (
            self.project_root / USAGE_COPILOT_RECONCILE_FILE
        )
        self._migrate_legacy = bool(migrate_legacy)

    def append(self, record: UsageRecord) -> bool:
        return bool(self.append_many([record]))

    def append_many(self, records: Iterable[UsageRecord]) -> int:
        pending = [record for record in records if record.call_id]
        if not pending:
            return 0
        self.project_root.mkdir(parents=True, exist_ok=True)
        appended = 0
        with self._locked():
            known = self._call_ids_unlocked()
            with self.path.open("a", encoding="utf-8") as handle:
                for record in pending:
                    if record.call_id in known:
                        continue
                    handle.write(
                        json.dumps(
                            record.to_jsonable(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    known.add(record.call_id)
                    appended += 1
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            self._cache_call_ids(known)
        return appended

    def records(
        self,
        *,
        since: float = 0.0,
        mission_id: str | None = None,
    ) -> list[UsageRecord]:
        if self._migrate_legacy:
            self.ensure_legacy_migrated()
            self.ensure_copilot_usage_reconciled()
        out: list[UsageRecord] = []
        seen: set[str] = set()
        try:
            handle = self.path.open("r", encoding="utf-8")
        except OSError:
            return out
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                record = UsageRecord.from_jsonable(row)
                if not record.call_id or record.call_id in seen:
                    continue
                seen.add(record.call_id)
                if record.completed_at < since:
                    continue
                if mission_id is not None and record.mission_id != mission_id:
                    continue
                out.append(record)
        return out

    def summary(
        self,
        *,
        since: float = 0.0,
        mission_id: str | None = None,
        run_labels: set[str] | None = None,
        run_label_prefixes: tuple[str, ...] = (),
        cost_basis: str | None = None,
    ) -> UsageSummary:
        records = self.records(since=since, mission_id=mission_id)
        if run_labels is not None or run_label_prefixes or cost_basis is not None:
            records = [
                record
                for record in records
                if (
                    run_labels is None
                    or record.run_label in run_labels
                )
                and (
                    not run_label_prefixes
                    or record.run_label.startswith(run_label_prefixes)
                )
                and (cost_basis is None or record.cost_basis == cost_basis)
            ]
        return summarize_usage(records)

    def ensure_legacy_migrated(self) -> int:
        if self.migration_path.exists():
            return 0
        records = list(
            _legacy_event_records(
                self.project_root,
                covered_mission_ids=self._existing_mission_ids(),
            )
        )
        if not _event_history_paths(self.project_root / "events.jsonl"):
            records.extend(_legacy_journal_records(self.project_root))
        appended = self.append_many(records)
        _write_json_atomic(
            self.migration_path,
            {
                "version": 1,
                "completed_at": time.time(),
                "records_seen": len(records),
                "records_appended": appended,
            },
        )
        return appended

    def ensure_copilot_usage_reconciled(self) -> int:
        if not _copilot_reconcile_enabled_for(self.project_root):
            return 0
        signature = _path_signature(self.path)
        if signature is None and self.copilot_reconcile_path.exists():
            return 0
        if _reconcile_marker_signature(self.copilot_reconcile_path) == signature:
            return 0
        if signature is None:
            _write_json_atomic(
                self.copilot_reconcile_path,
                {
                    "version": _COPILOT_RECONCILE_VERSION,
                    "usage_signature": None,
                    "updated": 0,
                },
            )
            return 0
        call_threads = _legacy_call_threads(self.project_root)
        updated = 0
        with self._locked():
            rows = _read_usage_json_rows(self.path)
            not_billed = {
                "input_tokens": None,
                "cached_input_tokens": None,
                "cache_write_tokens": None,
                "output_tokens": None,
                "reasoning_output_tokens": None,
                "premium_requests": None,
                "premium_request_cost_usd": 0.0,
                "total_nano_aiu": None,
                "model_usage": [],
                "cost_usd": 0.0,
                "cost_basis": "none",
                "pricing_status": "not_billed",
                "pricing_tier": "not_started",
            }
            for row in rows:
                if (
                    str(row.get("provider") or "").lower() == "copilot"
                    and str(row.get("status") or "").lower() == "denied"
                    and any(row.get(key) != value for key, value in not_billed.items())
                ):
                    row.update(not_billed)
                    updated += 1

            used_usage_events = {
                (str(item.get("session_id") or ""), event_id)
                for row in rows
                for item in _normalize_model_usage(row.get("model_usage"))
                if (event_id := _optional_int(item.get("usage_event_id"))) is not None
                and str(item.get("session_id") or "")
            }
            for row in rows:
                if str(row.get("provider") or "").lower() != "copilot":
                    continue
                if (
                    str(row.get("status") or "").lower() == "denied"
                    or str(row.get("pricing_status") or "").lower() == "not_billed"
                ):
                    continue
                if _optional_int(row.get("total_nano_aiu")) is not None:
                    continue
                call_id = str(row.get("call_id") or "")
                completed_at = _float(row.get("completed_at"), 0.0)
                started_at = _float(row.get("started_at"), completed_at)
                session_id = call_threads.get(call_id)
                found = find_copilot_usage_near(
                    completed_at=completed_at,
                    started_at=started_at,
                    session_id=session_id,
                )
                usage = found[1] if found is not None else None
                available = (
                    tuple(
                        item
                        for item in usage.rows
                        if (item.session_id, item.row_id) not in used_usage_events
                    )
                    if usage is not None
                    else ()
                )
                if (
                    usage is not None
                    and available
                    and (session_id is not None or len(available) == 1)
                ):
                    usage = type(usage)(available)
                    for item in available:
                        used_usage_events.add((item.session_id, item.row_id))
                    previous_cost = _optional_float(row.get("cost_usd"))
                    if row.get("premium_request_cost_usd") is None:
                        row["premium_request_cost_usd"] = previous_cost
                    row.update(
                        {
                            "model": usage.model,
                            "thread_id": session_id,
                            "input_tokens": usage.input_tokens,
                            "cached_input_tokens": usage.cache_read_tokens,
                            "cache_write_tokens": usage.cache_write_tokens,
                            "output_tokens": usage.output_tokens,
                            "reasoning_output_tokens": usage.reasoning_tokens,
                            "total_nano_aiu": usage.total_nano_aiu,
                            "model_usage": list(usage.model_usage),
                            "duration_ms": _duration_ms(
                                started_at,
                                completed_at,
                                recorded=row.get("duration_ms"),
                            ),
                            "cost_usd": usage.cost_usd,
                            "cost_basis": "token",
                            "pricing_status": (
                                "priced" if usage.cost_usd is not None else "partial"
                            ),
                            "pricing_tier": "copilot_token",
                            "schema_version": max(
                                2, _optional_int(row.get("schema_version")) or 1
                            ),
                        }
                    )
                    updated += 1
                    if usage.cost_usd is not None:
                        continue

                # Copilot CLI versions that expose the complete billable
                # premium-request count but no local token/AIU row still give us
                # a definitive charge.  Settle that billing unit rather than
                # leaving the call permanently partial.  Missing premium usage
                # remains fail-closed.
                premium_quote = quote_copilot_usage(
                    _optional_float(row.get("premium_requests"))
                )
                existing_pricing_status = str(
                    row.get("pricing_status") or ""
                ).lower()
                existing_cost = _optional_float(row.get("cost_usd"))
                if (
                    premium_quote.cost_usd is not None
                    and (
                        existing_cost is None
                        or existing_pricing_status in {"partial", "unpriced"}
                    )
                ):
                    row.update(
                        {
                            "premium_request_cost_usd": premium_quote.cost_usd,
                            "cost_usd": premium_quote.cost_usd,
                            "cost_basis": "premium_request",
                            "pricing_status": premium_quote.status,
                            "pricing_tier": premium_quote.tier,
                            "schema_version": max(
                                2, _optional_int(row.get("schema_version")) or 1
                            ),
                        }
                    )
                    updated += 1
            if updated:
                _rewrite_usage_rows(self.path, rows)
                self._cache_call_ids(
                    {
                        str(row.get("call_id"))
                        for row in rows
                        if row.get("call_id")
                    }
                )
        _write_json_atomic(
            self.copilot_reconcile_path,
            {
                "version": _COPILOT_RECONCILE_VERSION,
                "usage_signature": list(_path_signature(self.path) or ()),
                "updated": updated,
                "completed_at": time.time(),
            },
        )
        return updated

    def _existing_mission_ids(self) -> set[str]:
        mission_ids: set[str] = set()
        try:
            handle = self.path.open("r", encoding="utf-8")
        except OSError:
            return mission_ids
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                mission_id = _optional_text(row.get("mission_id"))
                if mission_id:
                    mission_ids.add(mission_id)
        return mission_ids

    @contextmanager
    def _locked(self) -> Iterator[None]:
        key = str(self.lock_path.resolve())
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
        self.project_root.mkdir(parents=True, exist_ok=True)
        with thread_lock:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(fd)

    def _call_ids_unlocked(self) -> set[str]:
        key = str(self.path.resolve())
        signature = _path_signature(self.path)
        with _CALL_ID_CACHE_LOCK:
            cached = _CALL_ID_CACHE.get(key)
            if cached is not None and cached[0] == signature:
                return set(cached[1])
        ids: set[str] = set()
        try:
            handle = self.path.open("r", encoding="utf-8")
        except OSError:
            return ids
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict) and row.get("call_id"):
                    ids.add(str(row["call_id"]))
        with _CALL_ID_CACHE_LOCK:
            _CALL_ID_CACHE[key] = (signature, set(ids))
        return ids

    def _cache_call_ids(self, ids: set[str]) -> None:
        key = str(self.path.resolve())
        with _CALL_ID_CACHE_LOCK:
            _CALL_ID_CACHE[key] = (_path_signature(self.path), set(ids))


def summarize_usage(records: Iterable[UsageRecord]) -> UsageSummary:
    rows = list(records)
    priced = sum(record.pricing_status == "priced" for record in rows)
    partial = sum(record.pricing_status == "partial" for record in rows)
    unpriced = sum(record.pricing_status == "unpriced" for record in rows)
    not_billed = sum(record.pricing_status == "not_billed" for record in rows)
    contributions = _deduplicated_usage_contributions(rows)
    known_costs = [
        float(item["cost_usd"])
        for item in contributions
        if item.get("cost_usd") is not None
    ]
    known_cost = sum(known_costs)
    if partial:
        aggregate_status = "partial"
    elif unpriced:
        aggregate_status = "unpriced"
    elif rows and not_billed == len(rows):
        aggregate_status = "not_billed"
    elif rows:
        aggregate_status = "priced"
    else:
        aggregate_status = "empty"
    incomplete_without_positive_cost = (
        aggregate_status in {"partial", "unpriced"} and known_cost <= 0.0
    )
    return UsageSummary(
        call_count=len(rows),
        known_cost_usd=known_cost,
        cost_usd=(
            known_cost
            if known_costs and not incomplete_without_positive_cost
            else None
        ),
        pricing_status=aggregate_status,
        priced_calls=priced,
        partial_calls=partial,
        unpriced_calls=unpriced,
        not_billed_calls=not_billed,
        input_tokens=sum(item.get("input_tokens") or 0 for item in contributions),
        cached_input_tokens=sum(
            item.get("cached_input_tokens") or 0 for item in contributions
        ),
        output_tokens=sum(item.get("output_tokens") or 0 for item in contributions),
        reasoning_output_tokens=sum(
            item.get("reasoning_output_tokens") or 0 for item in contributions
        ),
        premium_requests=sum(record.premium_requests or 0.0 for record in rows),
        cache_write_tokens=sum(
            item.get("cache_write_tokens") or 0 for item in contributions
        ),
        total_nano_aiu=sum(
            item.get("total_nano_aiu") or 0 for item in contributions
        ),
        premium_request_cost_usd=sum(
            record.premium_request_cost_usd or 0.0 for record in rows
        ),
    )


def _deduplicated_usage_contributions(
    records: Iterable[UsageRecord],
) -> list[dict[str, Any]]:
    contributions: list[dict[str, Any]] = []
    seen_copilot_events: set[tuple[str, int]] = set()
    for record in records:
        if not record.model_usage:
            contributions.append({
                "input_tokens": record.input_tokens,
                "cached_input_tokens": record.cached_input_tokens,
                "cache_write_tokens": record.cache_write_tokens,
                "output_tokens": record.output_tokens,
                "reasoning_output_tokens": record.reasoning_output_tokens,
                "total_nano_aiu": record.total_nano_aiu,
                "cost_usd": record.cost_usd,
            })
            continue
        for item in record.model_usage:
            session_id = _optional_text(item.get("session_id"))
            usage_event_id = _optional_int(item.get("usage_event_id"))
            if session_id is not None and usage_event_id is not None:
                identity = (session_id, usage_event_id)
                if identity in seen_copilot_events:
                    continue
                seen_copilot_events.add(identity)
            contributions.append(dict(item))
    return contributions


def project_usage_summary(
    project_root: Path | str,
    *,
    since: float = 0.0,
    mission_id: str | None = None,
) -> UsageSummary:
    return UsageLedger(project_root).summary(since=since, mission_id=mission_id)


def ensure_project_events_standardized(project_root: Path | str) -> int:
    """Merge the legacy worktree event log into the canonical project log once.

    Call-scoped lifecycle rows are de-duplicated by ``type`` + ``call_id``.
    Repeated stream rows and non-call events use their complete normalized JSON
    payload so migration never collapses distinct output fragments.
    """
    root = Path(project_root).expanduser()
    marker_path = root / EVENT_MIGRATION_FILE
    if marker_path.exists():
        return 0
    legacy_path = root / ".argus" / "events.jsonl"
    source_paths = _event_history_paths(legacy_path)
    if not source_paths:
        return 0

    canonical_path = root / "events.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    rows_seen = 0
    rows_appended = 0
    malformed_rows = 0
    duplicate_rows = 0
    with _exclusive_file_lock(root / EVENT_MIGRATION_LOCK_FILE):
        if marker_path.exists():
            return 0
        identities = _event_identities(_event_history_paths(canonical_path))
        try:
            handle = canonical_path.open("a", encoding="utf-8")
        except OSError:
            return 0
        with handle:
            for source_path in source_paths:
                try:
                    source = source_path.open("r", encoding="utf-8")
                except OSError:
                    continue
                with source:
                    for raw in source:
                        rows_seen += 1
                        try:
                            row = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            malformed_rows += 1
                            continue
                        if not isinstance(row, dict):
                            malformed_rows += 1
                            continue
                        identity = _event_identity(row)
                        if identity in identities:
                            duplicate_rows += 1
                            continue
                        handle.write(
                            json.dumps(
                                row,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                            + "\n"
                        )
                        identities.add(identity)
                        rows_appended += 1
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        _write_json_atomic(
            marker_path,
            {
                "version": 2,
                "completed_at": time.time(),
                "source_files": [str(path) for path in source_paths],
                "rows_seen": rows_seen,
                "rows_appended": rows_appended,
                "duplicate_rows": duplicate_rows,
                "malformed_rows": malformed_rows,
            },
        )
    return rows_appended


def format_usage_cost(summary: UsageSummary, *, decimals: int = 2) -> str:
    """Human-readable cost that never renders unknown usage as ``$0.00``."""
    status = summary.pricing_status
    if summary.cost_usd is None:
        if status == "partial":
            return "partial"
        if status == "unpriced":
            return "unpriced"
        return f"${0.0:.{decimals}f}"
    rendered = f"${summary.cost_usd:.{decimals}f}"
    if status == "partial":
        return f"{rendered}+ (partial)"
    if status == "unpriced":
        return f"{rendered}+ (unpriced)"
    return rendered


def _legacy_event_records(
    project_root: Path,
    *,
    covered_mission_ids: set[str] | None = None,
) -> Iterator[UsageRecord]:
    current_mission: str | None = None
    call_missions: dict[str, str | None] = {}
    starts: dict[str, dict[str, Any]] = {}
    emitted: set[str] = set()
    missions_with_calls: set[str] = set(covered_mission_ids or ())
    legacy_missions: list[dict[str, Any]] = []
    for path in _event_history_paths(project_root / "events.jsonl"):
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                kind = canonical_event_type(
                    row.get("canonical_type") or row.get("type")
                )
                if kind == EventType.LIFE_MISSION_STARTED:
                    current_mission = _optional_text(row.get("item_id"))
                    continue
                if kind == EventType.LIFE_MISSION_COMPLETED:
                    item_id = _optional_text(row.get("item_id"))
                    cost = _optional_float(row.get("cost_usd"))
                    if cost is not None:
                        legacy_missions.append({
                            "item_id": item_id,
                            "ts": _float(row.get("ts"), 0.0),
                            "cost_usd": cost,
                            "status": str(row.get("pricing_status") or "priced"),
                        })
                    if item_id is None or item_id == current_mission:
                        current_mission = None
                    continue
                call_id = str(row.get("call_id") or "")
                if not call_id:
                    continue
                if kind == EventType.AGENT_IO_START:
                    starts[call_id] = row
                    call_missions[call_id] = current_mission
                    continue
                if call_id in emitted:
                    continue
                if kind == EventType.AGENT_IO_COMPLETE:
                    token_usage = _legacy_token_usage(row)
                    premium = _legacy_premium_usage(row)
                    fatal = str(row.get("fatal_error") or "")
                    exit_code = _optional_int(row.get("exit_code"))
                    failed = bool(fatal or (exit_code is not None and exit_code != 0))
                    started = starts.get(call_id, {})
                    mission_id = call_missions.get(call_id, current_mission)
                    yield build_usage_record(
                        call_id=call_id,
                        project_root=project_root,
                        mission_id=mission_id,
                        provider=str(
                            row.get("backend")
                            or started.get("backend")
                            or ""
                        ),
                        model=str(row.get("model") or started.get("model") or ""),
                        run_label=str(
                            row.get("run_label")
                            or started.get("run_label")
                            or ""
                        ),
                        started_at=_float(
                            started.get("ts"),
                            _float(row.get("ts"), 0.0),
                        ),
                        completed_at=_float(row.get("ts"), 0.0),
                        status="error" if failed else "completed",
                        token_usage=token_usage,
                        premium_requests=premium,
                        thread_id=_optional_text(row.get("thread_id")),
                        error=fatal,
                        source="legacy.events",
                    )
                    if mission_id:
                        missions_with_calls.add(mission_id)
                    emitted.add(call_id)
                elif kind == EventType.AGENT_IO_ERROR:
                    started = starts.get(call_id, {})
                    error = str(row.get("error") or "")
                    denied = "binary not found" in error.lower()
                    mission_id = call_missions.get(call_id, current_mission)
                    yield build_usage_record(
                        call_id=call_id,
                        project_root=project_root,
                        mission_id=mission_id,
                        provider=str(
                            row.get("backend")
                            or started.get("backend")
                            or ""
                        ),
                        model=str(started.get("model") or ""),
                        run_label=str(
                            row.get("run_label")
                            or started.get("run_label")
                            or ""
                        ),
                        started_at=_float(
                            started.get("ts"),
                            _float(row.get("ts"), 0.0),
                        ),
                        completed_at=_float(row.get("ts"), 0.0),
                        status="denied" if denied else "error",
                        error=error,
                        source="legacy.events",
                    )
                    if mission_id:
                        missions_with_calls.add(mission_id)
                    emitted.add(call_id)
                elif kind == EventType.PROVIDER_REQUEST_DENIED:
                    yield build_usage_record(
                        call_id=call_id,
                        project_root=project_root,
                        mission_id=current_mission,
                        provider=str(row.get("provider") or ""),
                        model="",
                        run_label=str(row.get("run_label") or ""),
                        started_at=_float(row.get("ts"), 0.0),
                        completed_at=_float(row.get("ts"), 0.0),
                        status="denied",
                        error=str(row.get("reason") or ""),
                        source="legacy.events",
                    )
                    emitted.add(call_id)
    for index, row in enumerate(legacy_missions):
        mission_id = _optional_text(row.get("item_id"))
        if mission_id and mission_id in missions_with_calls:
            continue
        yield _legacy_aggregate_record(
            project_root=project_root,
            call_id=(
                f"legacy-mission:{mission_id or 'unknown'}:"
                f"{int(_float(row.get('ts'), 0.0) * 1_000_000)}:{index}"
            ),
            mission_id=mission_id,
            completed_at=_float(row.get("ts"), 0.0),
            cost_usd=_float(row.get("cost_usd"), 0.0),
            run_label="legacy.mission.aggregate",
        )


def _legacy_journal_records(project_root: Path) -> Iterator[UsageRecord]:
    path = project_root / "journal.jsonl"
    for history_path in _event_history_paths(path):
        try:
            handle = history_path.open("r", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for index, raw in enumerate(handle):
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                cost = _optional_float(row.get("cost_usd"))
                if cost is None:
                    continue
                ts = _float(row.get("ts"), 0.0)
                identity = str(row.get("id") or f"{history_path.name}:{index}")
                yield _legacy_aggregate_record(
                    project_root=project_root,
                    call_id=f"legacy-journal:{identity}:{int(ts * 1_000_000)}",
                    mission_id=_optional_text(row.get("id")),
                    completed_at=ts,
                    cost_usd=cost,
                    run_label="legacy.journal.aggregate",
                )


def _legacy_aggregate_record(
    *,
    project_root: Path,
    call_id: str,
    mission_id: str | None,
    completed_at: float,
    cost_usd: float,
    run_label: str,
) -> UsageRecord:
    return UsageRecord(
        call_id=call_id,
        project_id=project_root.name,
        mission_id=mission_id,
        provider="legacy",
        model="",
        run_label=run_label,
        started_at=completed_at,
        completed_at=completed_at,
        status="completed",
        input_tokens=None,
        cached_input_tokens=None,
        output_tokens=None,
        reasoning_output_tokens=None,
        premium_requests=None,
        pricing_status="priced",
        pricing_tier="legacy_aggregate",
        cost_usd=max(0.0, float(cost_usd)),
        cost_basis="legacy_aggregate",
        source="legacy.events",
    )


def _legacy_token_usage(row: dict[str, Any]) -> TokenUsage:
    events = row.get("json_events")
    if isinstance(events, list):
        extracted = extract_token_usage(events)
        # Copilot's camelCase message fields were the production bug: the old
        # translated top-level values are zero, so use the newly extracted sum.
        if extracted.source == "per_event":
            return extracted
        if extracted.observed:
            return TokenUsage(
                input_tokens=_optional_int(row.get("input_tokens")) or 0,
                cached_input_tokens=(
                    _optional_int(row.get("cached_input_tokens")) or 0
                ),
                cache_write_tokens=(
                    _optional_int(row.get("cache_write_tokens")) or 0
                ),
                output_tokens=_optional_int(row.get("output_tokens")) or 0,
                reasoning_output_tokens=(
                    _optional_int(row.get("reasoning_output_tokens")) or 0
                ),
                input_tokens_present=extracted.input_tokens_present,
                cached_input_tokens_present=(
                    extracted.cached_input_tokens_present
                ),
                cache_write_tokens_present=(
                    extracted.cache_write_tokens_present
                ),
                output_tokens_present=extracted.output_tokens_present,
                reasoning_output_tokens_present=(
                    extracted.reasoning_output_tokens_present
                ),
                source="recorded_delta",
            )
    names = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    values = [_optional_int(row.get(name)) for name in names]
    present = [value is not None and value > 0 for value in values]
    return TokenUsage(
        input_tokens=values[0] or 0,
        cached_input_tokens=values[1] or 0,
        cache_write_tokens=values[2] or 0,
        output_tokens=values[3] or 0,
        reasoning_output_tokens=values[4] or 0,
        input_tokens_present=present[0],
        cached_input_tokens_present=present[1],
        cache_write_tokens_present=present[2],
        output_tokens_present=present[3],
        reasoning_output_tokens_present=present[4],
        source="recorded" if any(present) else "missing",
    )


def _legacy_premium_usage(row: dict[str, Any]) -> float | None:
    if row.get("premium_requests_present") is False:
        return None
    events = row.get("json_events")
    if isinstance(events, list):
        seen = False
        last = 0.0
        for event in events:
            if not isinstance(event, dict):
                continue
            usage = event.get("usage")
            if not isinstance(usage, dict) or "premiumRequests" not in usage:
                continue
            value = _optional_float(usage.get("premiumRequests"))
            if value is not None:
                seen = True
                last = value
        if seen:
            translated = _optional_float(row.get("premium_requests"))
            return translated if translated is not None else last
    return None


def _event_history_paths(path: Path) -> list[Path]:
    older: list[tuple[int, Path]] = []
    recent: Path | None = None
    prefix = path.name + "."
    try:
        candidates = list(path.parent.glob(prefix + "*"))
    except OSError:
        candidates = []
    for candidate in candidates:
        suffix = candidate.name[len(prefix) :]
        if not suffix.isdigit() or not candidate.is_file():
            continue
        index = int(suffix)
        if index == 1:
            recent = candidate
        elif index >= 2:
            older.append((index, candidate))
    paths = [candidate for _index, candidate in sorted(older, reverse=True)]
    if recent is not None:
        paths.append(recent)
    if path.is_file():
        paths.append(path)
    return paths


def _event_identities(paths: Iterable[Path]) -> set[str]:
    identities: set[str] = set()
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(row, dict):
                    identities.add(_event_identity(row))
    return identities


def _event_identity(row: dict[str, Any]) -> str:
    kind = str(row.get("type") or "")
    call_id = str(row.get("call_id") or "")
    if kind in CALL_SCOPED_EVENT_TYPES and call_id:
        return f"call:{kind}:{call_id}"
    return "row:" + json.dumps(
        row,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _legacy_call_threads(project_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for event_path in (
        project_root / "events.jsonl",
        project_root / ".argus" / "events.jsonl",
    ):
        for path in _event_history_paths(event_path):
            try:
                handle = path.open("r", encoding="utf-8")
            except OSError:
                continue
            with handle:
                for raw in handle:
                    try:
                        row = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if (
                        not isinstance(row, dict)
                        or row.get("type") != EventType.AGENT_IO_COMPLETE
                    ):
                        continue
                    call_id = str(row.get("call_id") or "")
                    thread_id = str(row.get("thread_id") or "")
                    if call_id and thread_id:
                        out[call_id] = thread_id
    return out


def _copilot_reconcile_enabled_for(project_root: Path) -> bool:
    if os.environ.get("COPILOT_HOME", "").strip():
        return True
    from .paths import session_states_root

    try:
        return project_root.resolve().parent == session_states_root().resolve()
    except OSError:
        return False


def _read_usage_json_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError:
        return rows
    with handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _rewrite_usage_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _reconcile_marker_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _COPILOT_RECONCILE_VERSION
    ):
        return None
    raw = payload.get("usage_signature")
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    try:
        return int(raw[0]), int(raw[1]), int(raw[2])
    except (TypeError, ValueError):
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _path_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (
        int(getattr(stat, "st_ino", 0) or 0),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _duration_ms(
    started_at: float,
    completed_at: float,
    *,
    recorded: Any = None,
) -> int:
    explicit = _optional_int(recorded)
    if explicit is not None:
        return explicit
    return max(0, int(round((float(completed_at) - float(started_at)) * 1000)))


def _normalize_model_usage(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None or isinstance(value, (str, bytes)):
        return ()
    if isinstance(value, dict):
        raw_items = [value]
    else:
        try:
            raw_items = list(value)
        except TypeError:
            return ()
    items: list[dict[str, Any]] = []
    seen_copilot_events: set[tuple[str, int]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        session_id = _optional_text(raw.get("session_id"))
        usage_event_id = _optional_int(raw.get("usage_event_id"))
        if session_id is not None and usage_event_id is not None:
            identity = (session_id, usage_event_id)
            if identity in seen_copilot_events:
                continue
            seen_copilot_events.add(identity)
        total_nano_aiu = _optional_int(raw.get("total_nano_aiu"))
        cost_usd = _optional_float(raw.get("cost_usd"))
        if cost_usd is None and total_nano_aiu is not None:
            cost_usd = total_nano_aiu / NANO_AIU_PER_USD
        items.append({
            "usage_event_id": usage_event_id,
            "session_id": session_id,
            "model": str(raw.get("model") or ""),
            "turn_index": _optional_int(raw.get("turn_index")),
            "input_tokens": _optional_int(raw.get("input_tokens")),
            "cached_input_tokens": _optional_int(
                raw.get("cached_input_tokens")
            ),
            "cache_write_tokens": _optional_int(raw.get("cache_write_tokens")),
            "output_tokens": _optional_int(raw.get("output_tokens")),
            "reasoning_output_tokens": _optional_int(
                raw.get("reasoning_output_tokens")
            ),
            "total_nano_aiu": total_nano_aiu,
            "cost_usd": cost_usd,
            "request_multiplier": _optional_float(raw.get("request_multiplier")),
            "created_at": str(raw.get("created_at") or ""),
        })
    return tuple(items)


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _pricing_status(value: Any) -> PricingStatus:
    status = str(value or "")
    if status in {"priced", "partial", "unpriced", "not_billed"}:
        return status  # type: ignore[return-value]
    return "partial"


def _call_status(value: Any) -> CallStatus:
    status = str(value or "")
    if status in {"completed", "error", "denied"}:
        return status  # type: ignore[return-value]
    return "error"


__all__ = [
    "CallStatus",
    "EVENT_MIGRATION_FILE",
    "USAGE_FILE",
    "UsageLedger",
    "UsageRecord",
    "UsageSummary",
    "build_usage_record",
    "ensure_project_events_standardized",
    "format_usage_cost",
    "project_usage_summary",
    "summarize_usage",
    "usage_recorded_event",
]

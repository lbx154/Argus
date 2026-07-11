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
from .pricing import PricingStatus, quote_copilot_usage, quote_token_usage

try:  # pragma: no cover - production daemons are POSIX
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

USAGE_FILE = "usage.jsonl"
USAGE_LOCK_FILE = "usage.lock"
USAGE_MIGRATION_FILE = "usage.migration-v1.json"
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
    error: str = ""
    source: UsageSource = "run_exec"
    schema_version: int = 1

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_jsonable(cls, row: dict[str, Any]) -> "UsageRecord":
        cost = _optional_float(row.get("cost_usd"))
        return cls(
            call_id=str(row.get("call_id") or ""),
            project_id=str(row.get("project_id") or ""),
            mission_id=_optional_text(row.get("mission_id")),
            provider=str(row.get("provider") or ""),
            model=str(row.get("model") or ""),
            run_label=str(row.get("run_label") or ""),
            started_at=_float(row.get("started_at"), _float(row.get("ts"), 0.0)),
            completed_at=_float(row.get("completed_at"), _float(row.get("ts"), 0.0)),
            status=_call_status(row.get("status")),
            input_tokens=_optional_int(row.get("input_tokens")),
            cached_input_tokens=_optional_int(row.get("cached_input_tokens")),
            output_tokens=_optional_int(row.get("output_tokens")),
            reasoning_output_tokens=_optional_int(
                row.get("reasoning_output_tokens")
            ),
            premium_requests=_optional_float(row.get("premium_requests")),
            pricing_status=_pricing_status(row.get("pricing_status")),
            pricing_tier=str(row.get("pricing_tier") or "unknown"),
            cost_usd=cost,
            cost_basis=str(row.get("cost_basis") or ""),
            error=str(row.get("error") or ""),
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
    error: str = "",
    source: UsageSource = "run_exec",
) -> UsageRecord:
    usage = token_usage or TokenUsage()
    normalized_provider = str(provider or "").strip().lower()
    if status == "denied":
        pricing_status: PricingStatus = "not_billed"
        pricing_tier = "not_started"
        cost_usd: float | None = 0.0
        cost_basis = "none"
    elif normalized_provider == "copilot":
        quote = quote_copilot_usage(premium_requests)
        pricing_status = quote.status
        pricing_tier = quote.tier
        cost_usd = quote.cost_usd
        cost_basis = "premium_request"
    else:
        quote = quote_token_usage(
            model,
            input_tokens=usage.input_tokens if usage.input_tokens_present else None,
            cached_input_tokens=(
                usage.cached_input_tokens
                if usage.cached_input_tokens_present
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
        error=str(error or "")[:2000],
        source=source,
    )


class UsageLedger:
    """Project-local ledger with cross-process idempotent append."""

    def __init__(self, project_root: Path | str, *, migrate_legacy: bool = True) -> None:
        self.project_root = Path(project_root).expanduser()
        self.path = self.project_root / USAGE_FILE
        self.lock_path = self.project_root / USAGE_LOCK_FILE
        self.migration_path = self.project_root / USAGE_MIGRATION_FILE
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
    known_costs = [
        float(record.cost_usd)
        for record in rows
        if record.cost_usd is not None
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
        input_tokens=sum(record.input_tokens or 0 for record in rows),
        cached_input_tokens=sum(
            record.cached_input_tokens or 0 for record in rows
        ),
        output_tokens=sum(record.output_tokens or 0 for record in rows),
        reasoning_output_tokens=sum(
            record.reasoning_output_tokens or 0 for record in rows
        ),
        premium_requests=sum(record.premium_requests or 0.0 for record in rows),
    )


def project_usage_summary(
    project_root: Path | str,
    *,
    since: float = 0.0,
    mission_id: str | None = None,
) -> UsageSummary:
    return UsageLedger(project_root).summary(since=since, mission_id=mission_id)


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
                kind = str(row.get("type") or "")
                if kind == "life.mission.started":
                    current_mission = _optional_text(row.get("item_id"))
                    continue
                if kind == "life.mission.completed":
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
                if kind == "agent.io.start":
                    starts[call_id] = row
                    call_missions[call_id] = current_mission
                    continue
                if call_id in emitted:
                    continue
                if kind == "agent.io.complete":
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
                        error=fatal,
                        source="legacy.events",
                    )
                    if mission_id:
                        missions_with_calls.add(mission_id)
                    emitted.add(call_id)
                elif kind == "agent.io.error":
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
                elif kind == "provider.request.denied":
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
                output_tokens=_optional_int(row.get("output_tokens")) or 0,
                reasoning_output_tokens=(
                    _optional_int(row.get("reasoning_output_tokens")) or 0
                ),
                input_tokens_present=extracted.input_tokens_present,
                cached_input_tokens_present=(
                    extracted.cached_input_tokens_present
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
        "output_tokens",
        "reasoning_output_tokens",
    )
    values = [_optional_int(row.get(name)) for name in names]
    present = [value is not None and value > 0 for value in values]
    return TokenUsage(
        input_tokens=values[0] or 0,
        cached_input_tokens=values[1] or 0,
        output_tokens=values[2] or 0,
        reasoning_output_tokens=values[3] or 0,
        input_tokens_present=present[0],
        cached_input_tokens_present=present[1],
        output_tokens_present=present[2],
        reasoning_output_tokens_present=present[3],
        source="recorded" if any(present) else "missing",
    )


def _legacy_premium_usage(row: dict[str, Any]) -> float | None:
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
    "USAGE_FILE",
    "UsageLedger",
    "UsageRecord",
    "UsageSummary",
    "build_usage_record",
    "format_usage_cost",
    "project_usage_summary",
    "summarize_usage",
]

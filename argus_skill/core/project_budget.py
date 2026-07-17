"""Stable per-project USD budget configuration."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

BUDGET_FILENAME = "budget.json"
GLOBAL_BUDGET_FILENAME = "global_budget.json"
SCHEMA_VERSION = 1
DEFAULT_PER_MISSION_CAP_USD = 30.0
DEFAULT_DAILY_CAP_USD = 180.0
DEFAULT_GLOBAL_DAILY_CAP_USD = 10_000.0

_LEGACY_NAMES = {
    "per_mission_cap_usd": "ARGUS_SKILL_PER_MISSION_CAP_USD",
    "daily_cap_usd": "ARGUS_SKILL_DAILY_CAP_USD",
}
_THREAD_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_THREAD_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class ProjectBudget:
    per_mission_cap_usd: float = DEFAULT_PER_MISSION_CAP_USD
    daily_cap_usd: float = DEFAULT_DAILY_CAP_USD


@dataclass(frozen=True)
class GlobalBudget:
    global_daily_cap_usd: float = DEFAULT_GLOBAL_DAILY_CAP_USD


def budget_path(project_state_dir: Path | str) -> Path:
    return Path(project_state_dir).expanduser() / BUDGET_FILENAME


def global_budget_path(global_root: Path | str) -> Path:
    return Path(global_root).expanduser() / GLOBAL_BUDGET_FILENAME


def _number(name: str, value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed


def _from_mapping(data: Mapping[str, object]) -> ProjectBudget:
    return ProjectBudget(
        per_mission_cap_usd=_number(
            "per_mission_cap_usd",
            data.get("per_mission_cap_usd", DEFAULT_PER_MISSION_CAP_USD),
        ),
        daily_cap_usd=_number(
            "daily_cap_usd",
            data.get("daily_cap_usd", DEFAULT_DAILY_CAP_USD),
        ),
    )


def _legacy_initial_values(env: Mapping[str, str] | None = None) -> ProjectBudget:
    """One-time migration from the old env/global-knob budget layers."""
    env_map = env if env is not None else os.environ
    try:
        from .knob_store import read_persisted_knobs

        persisted = read_persisted_knobs()
    except Exception:  # noqa: BLE001
        persisted = {}
    values: dict[str, object] = {}
    defaults = asdict(ProjectBudget())
    for field, legacy_name in _LEGACY_NAMES.items():
        raw = str(env_map.get(legacy_name, "") or "").strip()
        if not raw:
            raw = str(persisted.get(legacy_name, "") or "").strip()
        values[field] = raw or defaults[field]
    return _from_mapping(values)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
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


def _atomic_write(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_project_unlocked(path: Path, value: ProjectBudget) -> ProjectBudget:
    _atomic_write(path, {"schema_version": SCHEMA_VERSION, **asdict(value)})
    return value


def _write_global_unlocked(path: Path, value: GlobalBudget) -> GlobalBudget:
    _atomic_write(path, {"schema_version": SCHEMA_VERSION, **asdict(value)})
    return value


def _load_project(path: Path) -> ProjectBudget:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError
    return _from_mapping(raw)


def _load_global(path: Path) -> GlobalBudget:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError
    return GlobalBudget(
        global_daily_cap_usd=_number(
            "global_daily_cap_usd",
            raw.get("global_daily_cap_usd", DEFAULT_GLOBAL_DAILY_CAP_USD),
        )
    )


def _initial_global_budget(env: Mapping[str, str] | None = None) -> GlobalBudget:
    env_map = env if env is not None else os.environ
    legacy_name = "ARGUS_SKILL_GLOBAL_DAILY_CAP_USD"
    value = str(env_map.get(legacy_name, "") or "").strip()
    if not value:
        try:
            from .knob_store import read_persisted_knobs

            value = str(read_persisted_knobs().get(legacy_name, "") or "").strip()
        except Exception:  # noqa: BLE001
            value = ""
    return GlobalBudget(
        _number(
            "global_daily_cap_usd",
            value or DEFAULT_GLOBAL_DAILY_CAP_USD,
        )
    )


def write_project_budget(
    project_state_dir: Path | str,
    budget: ProjectBudget | Mapping[str, object],
) -> ProjectBudget:
    """Validate and atomically replace this project's budget file."""
    value = budget if isinstance(budget, ProjectBudget) else _from_mapping(budget)
    path = budget_path(project_state_dir)
    with _locked(path):
        return _write_project_unlocked(path, value)


def write_global_budget(
    global_root: Path | str,
    budget: GlobalBudget | Mapping[str, object],
) -> GlobalBudget:
    value = (
        budget
        if isinstance(budget, GlobalBudget)
        else GlobalBudget(
            global_daily_cap_usd=_number(
                "global_daily_cap_usd",
                budget.get("global_daily_cap_usd", DEFAULT_GLOBAL_DAILY_CAP_USD),
            )
        )
    )
    path = global_budget_path(global_root)
    with _locked(path):
        return _write_global_unlocked(path, value)


def read_project_budget(
    project_state_dir: Path | str,
    *,
    migrate_env: Mapping[str, str] | None = None,
) -> ProjectBudget:
    """Read the project budget; initialize/migrate only when the file is absent."""
    path = budget_path(project_state_dir)
    try:
        return _load_project(path)
    except FileNotFoundError:
        with _locked(path):
            try:
                return _load_project(path)
            except FileNotFoundError:
                return _write_project_unlocked(
                    path,
                    _legacy_initial_values(migrate_env),
                )
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                raise ValueError(f"invalid project budget file: {path}") from None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        # A malformed file must not be silently replaced by startup defaults.
        raise ValueError(f"invalid project budget file: {path}") from None


def read_global_budget(
    global_root: Path | str,
    *,
    migrate_env: Mapping[str, str] | None = None,
) -> GlobalBudget:
    path = global_budget_path(global_root)
    try:
        return _load_global(path)
    except FileNotFoundError:
        with _locked(path):
            try:
                return _load_global(path)
            except FileNotFoundError:
                return _write_global_unlocked(
                    path,
                    _initial_global_budget(migrate_env),
                )
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                raise ValueError(f"invalid global budget file: {path}") from None
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        raise ValueError(f"invalid global budget file: {path}") from None


def update_project_budget(
    project_state_dir: Path | str,
    **changes: object,
) -> ProjectBudget:
    path = budget_path(project_state_dir)
    with _locked(path):
        try:
            current = _load_project(path)
        except FileNotFoundError:
            current = _legacy_initial_values()
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            raise ValueError(f"invalid project budget file: {path}") from None
        data = asdict(current)
        unknown = set(changes) - set(data)
        if unknown:
            raise ValueError(
                f"unknown project budget field(s): {', '.join(sorted(unknown))}"
            )
        data.update(changes)
        return _write_project_unlocked(path, _from_mapping(data))


def update_global_budget(global_root: Path | str, **changes: object) -> GlobalBudget:
    path = global_budget_path(global_root)
    with _locked(path):
        try:
            current = _load_global(path)
        except FileNotFoundError:
            current = _initial_global_budget()
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            raise ValueError(f"invalid global budget file: {path}") from None
        data = asdict(current)
        unknown = set(changes) - set(data)
        if unknown:
            raise ValueError(
                f"unknown global budget field(s): {', '.join(sorted(unknown))}"
            )
        data.update(changes)
        return _write_global_unlocked(
            path,
            GlobalBudget(
                _number("global_daily_cap_usd", data["global_daily_cap_usd"])
            ),
        )


__all__ = [
    "BUDGET_FILENAME",
    "GLOBAL_BUDGET_FILENAME",
    "GlobalBudget",
    "ProjectBudget",
    "budget_path",
    "global_budget_path",
    "read_global_budget",
    "read_project_budget",
    "update_global_budget",
    "update_project_budget",
    "write_global_budget",
    "write_project_budget",
]

"""Durable circuit breaker for failures more missions cannot repair.

Two failures open it. An exception that escapes Argus orchestration itself:
more missions cannot repair the same loaded code. And a model service that
turned work away past its automatic retries, so a task parked on the provider
fence: every other call would be turned away too. A single provider failure is
a normal mission outcome and does not belong here. While the circuit is open,
Planner turns and task dispatch (and with it every role's model calls) are
held until the runtime, relevant source, checkpoint contract, or an explicit
canary result changes. A provider outage ends sooner: on the operator's Run,
or on any call a half-open window lets through that the model service
answers, the canary's or the Planner's.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..core.file_facts import file_facts
from ..core.file_lock import exclusive_file_lock
from ..core.secret_guard import known_secret_values, redact_secrets_text
from ..core.stop_kinds import OPERATOR_RESUMED_PARKS
from .context_packet import CHECKPOINT_CONTRACT_VERSION

CIRCUIT_FILENAME = "runtime-failure-circuit.json"
# The fingerprint of a provider outage: one outage is one circuit, whatever
# words each failed call used.
PROVIDER_OUTAGE = "provider_outage"
# Half-open recovery. A window admits one canary mission (and, while nothing is
# queued, one Planner turn to produce work for it); a reviewed canary pass
# clears the circuit, and for a provider outage so does any answer. A window
# opens when a fact the caller watches changes (a backend switch, new operator
# input). A new Argus runtime needs no window: it clears the circuit outright.
# For a recovery nothing announces (a provider or disk coming back) a timed
# window is the fallback, its wait doubling after each window that ends
# without a pass. Nothing used to produce a canary, so
# only a changed runtime could close a circuit.
CANARY_COOLDOWN_KNOB = "ARGUS_SKILL_RUNTIME_CANARY_COOLDOWN_MIN"
CANARY_COOLDOWN_DEFAULT_MINUTES = 30.0
CANARY_TAG = "runtime_failure_canary"
_CANARY_FIELDS = (
    "canary_window",
    "canary_due_at",
    "canary_facts",
    "canary_item_id",
    "canary_admitted_at",
    "canary_mission_window",
    "canary_planner_window",
)
_CIRCUIT_LOCK_FILENAME = "runtime-failure-circuit.lock"
_SCHEMA_VERSION = 1
_HANDOFF_CHECKPOINT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Za-z]:|/)[^\n\r'\"]*?[\\/]handoffs[\\/][^\\/\n\r'\"]+"
    r"[\\/]CHECKPOINT\.md"
)
_HANDOFF_ID_RE = re.compile(r"(?i)(handoffs[\\/])[^\\/\s'\"]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / _CIRCUIT_LOCK_FILENAME
    with lock_path.open("a+", encoding="utf-8") as handle:
        with exclusive_file_lock(
            handle,
            lock_name=f"runtime failure circuit {lock_path}",
        ):
            yield


def _read_unlocked(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _relevant_source() -> list[int]:
    """The checkpoint settlement code's ``file_facts``: they move when that code does."""
    from ..core import role_session

    return file_facts(str(role_session.__file__))


def runtime_failure_identity() -> dict[str, Any]:
    """Facts that are allowed to close an existing runtime-failure circuit."""
    try:
        from ..core.runtime_identity import source_root
        from ..release import release_identity

        identity = release_identity(source_root())
    except Exception:  # noqa: BLE001 - identity failure must fail closed
        identity = {}
    return {
        "release": identity,
        "relevant_source": _relevant_source(),
        "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
    }


def normalize_runtime_failure_message(message: object) -> str:
    """Remove per-mission/path noise while retaining the causal exception."""
    text = redact_secrets_text(
        str(message or ""),
        known_values=known_secret_values(),
    )
    text = _HANDOFF_CHECKPOINT_RE.sub(
        "<state-root>/handoffs/<mission-id>/CHECKPOINT.md",
        text,
    )
    text = _HANDOFF_ID_RE.sub(r"\1<mission-id>", text)
    return _WHITESPACE_RE.sub(" ", text.replace("\\", "/")).strip()[:2000]


# The package directory as imported (and symlink-resolved): frames below it are
# Argus's own code. Fingerprints persisted before the 2026-09-14 rename spell
# the callsite ``argus_skill/...``; new ones spell it ``argus/...``, so a
# circuit that tripped on the old tree re-arms once on the same failure.
_PACKAGE_ROOTS = tuple(
    dict.fromkeys(
        str(root).replace("\\", "/").rstrip("/") + "/"
        for root in (Path(__file__).parents[1], Path(__file__).resolve().parents[1])
    )
)
_LEGACY_PACKAGE_MARKER = "/argus_skill/"


def _package_members() -> frozenset[str]:
    """Top-level subpackages and modules of ``argus``: ``core``, ``life``, ..., ``loop.py``."""
    try:
        return frozenset(
            entry.name for entry in Path(__file__).parents[1].iterdir()
            if (entry.is_dir() and not entry.name.startswith((".", "_"))) or entry.suffix == ".py"
        )
    except OSError:
        return frozenset()


# A frozen (PyInstaller) build records build-time absolute paths in its code
# objects, so frames need not start with the runtime package root; any
# ``/argus/<member>/`` where ``<member>`` is one of this package's own
# subpackages or root modules is still Argus's code.
_PACKAGE_MEMBERS = _package_members()
# Lookahead so consecutive markers may overlap (``/home/argus/argus/core/x.py``).
_PACKAGE_MARKER = re.compile(r"(?=/argus/([^/]+))")


def _package_relative_filename(filename: str) -> str | None:
    """``argus/<path>`` for a frame inside this package (either spelling), else ``None``."""
    normalized = filename.replace("\\", "/")
    for root in _PACKAGE_ROOTS:
        if normalized.startswith(root):
            return "argus/" + normalized[len(root):]
    for match in reversed(list(_PACKAGE_MARKER.finditer(normalized))):
        if match.group(1) in _PACKAGE_MEMBERS:
            return "argus/" + normalized[match.start(1):]
    marker = normalized.rfind(_LEGACY_PACKAGE_MARKER)
    if marker >= 0:
        return "argus/" + normalized[marker + len(_LEGACY_PACKAGE_MARKER):]
    return None


def _exception_callsite(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    selected = frames[-1] if frames else None
    filename = selected.filename.replace("\\", "/") if selected is not None else ""
    for frame in reversed(frames):
        relative = _package_relative_filename(frame.filename)
        if relative is not None:
            selected, filename = frame, relative
            break
    if selected is None:
        return "unknown"
    return f"{filename}:{selected.name}"


def runtime_failure_fingerprint(exc: BaseException) -> dict[str, str]:
    exception_type = type(exc).__name__
    callsite = _exception_callsite(exc)
    normalized_error = normalize_runtime_failure_message(exc)
    return {
        "fingerprint": f"{exception_type} at {callsite}: {normalized_error}",
        "exception_type": exception_type,
        "callsite": callsite,
        "normalized_error": normalized_error,
    }


def provider_outage_failure(cause: object) -> dict[str, str]:
    """The circuit's record of a model service that turned work away."""
    return {
        "fingerprint": PROVIDER_OUTAGE,
        "exception_type": "",
        "callsite": "",
        "normalized_error": normalize_runtime_failure_message(cause),
    }


def record_runtime_failure_circuit(
    root: Path | str,
    failure: BaseException | dict[str, str],
    *,
    item_id: str = "",
) -> dict[str, Any]:
    """Open or increment the durable circuit for an uncaught exception or an outage."""
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    if isinstance(failure, BaseException):
        failure = runtime_failure_fingerprint(failure)
    identity = runtime_failure_identity()
    now = time.time()
    with _locked(state_root):
        previous = _read_unlocked(path)
        same = bool(
            previous.get("active")
            and previous.get("fingerprint") == failure["fingerprint"]
            and previous.get("runtime_identity") == identity
        )
        item_ids = [
            str(value)
            for value in (previous.get("item_ids") if same else []) or []
            if str(value)
        ]
        if item_id:
            item_ids.append(str(item_id))
        state = {
            "schema_version": _SCHEMA_VERSION,
            "active": True,
            **failure,
            "runtime_identity": identity,
            "occurrence_count": int(previous.get("occurrence_count") or 0) + 1 if same else 1,
            "first_observed_at": (
                float(previous.get("first_observed_at") or now) if same else now
            ),
            "last_observed_at": now,
            "item_ids": list(dict.fromkeys(item_ids))[-20:],
            "clear_conditions": [
                "release_changed",
                "relevant_source_changed",
                "checkpoint_contract_version_changed",
                "reviewed_canary_passed",
            ],
            "cleared_at": 0.0,
            "cleared_reason": "",
            "newly_opened": not same,
        }
        if previous.get("active"):
            # A failure while the circuit is already open (a canary that
            # raised, or a new fingerprint) keeps the recovery schedule, so
            # the next half-open window still waits its doubled cooldown.
            state.update({
                key: previous[key] for key in _CANARY_FIELDS if key in previous
            })
        _atomic_json(path, state)
    return state


def runtime_failure_canary_cooldown_seconds(
    window: int, *, env: Any = None,
) -> float:
    """Timed fallback before half-open window ``window`` (1-based) opens.

    ``ARGUS_SKILL_RUNTIME_CANARY_COOLDOWN_MIN`` sets the first wait (default
    30 minutes); each later window waits twice the one before. 0 or less
    leaves windows to changed facts alone (``math.inf``).
    """
    default = str(CANARY_COOLDOWN_DEFAULT_MINUTES)
    try:
        from ..core.knobs import resolve_knob

        raw = resolve_knob(CANARY_COOLDOWN_KNOB, default, env=env).value
    except Exception:  # noqa: BLE001 - an unreadable knob store keeps the default
        raw = default
    try:
        minutes = float(raw)
    except (TypeError, ValueError):
        minutes = CANARY_COOLDOWN_DEFAULT_MINUTES
    if not minutes > 0:  # also NaN
        return math.inf
    return minutes * 60.0 * 2.0 ** min(max(0, int(window) - 1), 64)


def claim_runtime_failure_half_open(
    root: Path | str,
    *,
    slot: str,
    item_id: str = "",
    expected_canary_item_id: str = "",
    facts: dict[str, Any] | None = None,
    now: float | None = None,
) -> bool:
    """Take the current half-open window's single ``mission`` or ``planner`` slot.

    The caller has already established that the previous canary (named by
    ``expected_canary_item_id``) is no longer in flight; a different value on
    disk means another worker raced ahead, and nothing is granted. ``facts``
    is what the caller watches, stored as given; facts that differ from those
    the last window (or the first claim) saw open a window at once. Mechanical
    only: facts, time, a window counter, and one slot of each kind per window.
    """
    if slot not in {"mission", "planner"}:
        raise ValueError(f"unknown half-open slot: {slot!r}")
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    now = time.time() if now is None else float(now)
    with _locked(state_root):
        state = _read_unlocked(path)
        if not state.get("active"):
            return False
        if str(state.get("canary_item_id") or "") != str(expected_canary_item_id or ""):
            return False
        window = int(state.get("canary_window") or 0)
        due = float(state.get("canary_due_at") or 0.0) or (
            float(state.get("first_observed_at") or now)
            + runtime_failure_canary_cooldown_seconds(window + 1)
        )
        seen = state.get("canary_facts") or {}
        facts = dict(facts or {})
        changed = bool(facts and seen and facts != seen)
        dirty = bool(facts and not seen)
        if dirty:
            state["canary_facts"] = facts
        opened = changed or now >= due
        if opened:
            window += 1
            state["canary_window"] = window
            state["canary_facts"] = facts or seen
            next_due = now + runtime_failure_canary_cooldown_seconds(window + 1)
            # Unset (0) while no timed window is configured, so a later
            # setting is measured from the circuit's opening instead.
            state["canary_due_at"] = next_due if math.isfinite(next_due) else 0.0
        slot_key = f"canary_{slot}_window"
        if window <= 0 or int(state.get(slot_key) or 0) >= window:
            if opened or dirty:
                _atomic_json(path, state)
            return False
        state[slot_key] = window
        if slot == "mission":
            state["canary_item_id"] = str(item_id)
            state["canary_admitted_at"] = now
        _atomic_json(path, state)
    return True


def active_runtime_failure_circuit(root: Path | str) -> dict[str, Any] | None:
    """Return the active circuit, clearing it after an allowed fact change."""
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    with _locked(state_root):
        state = _read_unlocked(path)
        if not state.get("active"):
            return None
        current_identity = runtime_failure_identity()
        if state.get("runtime_identity") != current_identity:
            state.update(
                active=False,
                cleared_at=time.time(),
                cleared_reason="runtime_identity_changed",
                superseded_runtime_identity=current_identity,
                newly_opened=False,
            )
            _atomic_json(path, state)
            return None
        state.pop("newly_opened", None)
        return state


def clear_runtime_failure_circuit(
    root: Path | str,
    *,
    reason: str,
    fingerprint: str = "",
    canary_item_id: str = "",
) -> bool:
    """Close a circuit after a reviewed canary or another explicit fact change.

    With ``canary_item_id``, only the canary this circuit admitted closes it.
    """
    state_root = Path(root)
    path = state_root / CIRCUIT_FILENAME
    with _locked(state_root):
        state = _read_unlocked(path)
        if not state.get("active"):
            return False
        if fingerprint and str(state.get("fingerprint") or "") != fingerprint:
            return False
        # Only the canary a half-open window admitted closes the circuit; a
        # tag it never handed out is not a reviewed canary of this failure.
        if canary_item_id and str(state.get("canary_item_id") or "") != str(
            canary_item_id
        ):
            return False
        state.update(
            active=False,
            cleared_at=time.time(),
            cleared_reason=str(reason or "reviewed_canary_passed")[:500],
            newly_opened=False,
        )
        _atomic_json(path, state)
    return True


def operator_resume(root: Path | str) -> list[Any]:
    """The operator's Run: end a provider outage's circuit, then every park the Planner does not own.

    Never automatic. The circuit goes first: the backlog write wakes a sleeping daemon.
    """
    from .memory import LifeMemory

    clear_runtime_failure_circuit(root, reason="operator Run", fingerprint=PROVIDER_OUTAGE)
    return LifeMemory.open(root).backlog.resume_parked(OPERATOR_RESUMED_PARKS)


__all__ = [
    "CANARY_COOLDOWN_KNOB",
    "CANARY_TAG",
    "CIRCUIT_FILENAME",
    "PROVIDER_OUTAGE",
    "active_runtime_failure_circuit",
    "claim_runtime_failure_half_open",
    "clear_runtime_failure_circuit",
    "normalize_runtime_failure_message",
    "operator_resume",
    "provider_outage_failure",
    "record_runtime_failure_circuit",
    "runtime_failure_canary_cooldown_seconds",
    "runtime_failure_fingerprint",
    "runtime_failure_identity",
]

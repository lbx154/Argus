"""In-process bridge for in-container advisory verifier results.

The Harbor benchmark adapter (``benchmarks/harbor_adapter.py``) wires
``ContainerCodexRunner`` to run the official ``bash /tests/test.sh``
inside the agent container after each engineer round, as advisory
evidence for the reviewer. When that advisory verifier ran, we want to
adopt its result as the trial's *final* reward instead of letting Harbor
re-upload ``/tests`` and re-run, because the second run on a mutated
``/app`` (after the first run already executed test side-effects) can
disagree with the in-container result and create false negatives /
positives in scoring.

This cache is the side-channel between the runner (which sets the
result) and the adapter's monkey-patched ``Verifier.verify`` (which
consumes it). Keyed on ``id(environment)`` because both sides see the
same Harbor environment object during a single trial.

If no entry exists for the trial's environment, the patched
``Verifier.verify`` falls back to Harbor's original behaviour.
"""
from __future__ import annotations

from threading import Lock
from typing import TypedDict


class VerifyResult(TypedDict):
    exit_code: int
    stdout: str
    stderr: str
    cmd: str


_lock = Lock()
_cache: dict[int, VerifyResult] = {}


def register_verify_result(
    env_id: int,
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    cmd: str,
) -> None:
    """Record the latest advisory verifier outcome for an environment.

    Subsequent rounds overwrite earlier entries so the LAST round's
    result wins, mirroring Harbor's "verify after the agent finishes"
    semantics.
    """
    with _lock:
        _cache[env_id] = {
            "exit_code": int(exit_code),
            "stdout": stdout or "",
            "stderr": stderr or "",
            "cmd": cmd or "",
        }


def pop_verify_result(env_id: int) -> VerifyResult | None:
    """Consume the cached advisory result for an environment, if any."""
    with _lock:
        return _cache.pop(env_id, None)


def peek_verify_result(env_id: int) -> VerifyResult | None:
    with _lock:
        return _cache.get(env_id)


def clear_verify_result(env_id: int) -> None:
    with _lock:
        _cache.pop(env_id, None)

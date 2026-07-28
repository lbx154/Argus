"""Protocol metadata written by daemon workers into their status sidecar."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..core.runtime_identity import runtime_identity

DAEMON_PROTOCOL_NAME = "argus.daemon"
DAEMON_PROTOCOL_MAJOR = 1
DAEMON_PROTOCOL_MINOR = 1
DAEMON_CAPABILITIES = (
    "budget.status.v1",
    "events.jsonl.v1",
    "manager.directive.v1",
    "mission.abort.v2",
    "release.identity.v1",
    "usage.ledger.v1",
)


def daemon_protocol_metadata() -> dict[str, Any]:
    runtime = runtime_identity()
    runtime["self_managed_source"] = str(
        os.environ.get("ARGUS_SKILL_SELF_MANAGED_SOURCE", "")
    ).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "protocol": {
            "name": DAEMON_PROTOCOL_NAME,
            "major": DAEMON_PROTOCOL_MAJOR,
            "minor": DAEMON_PROTOCOL_MINOR,
        },
        "capabilities": list(DAEMON_CAPABILITIES),
        "runtime": runtime,
    }


def daemon_protocol_compatibility(status: Any) -> tuple[bool | None, str]:
    if not bool(getattr(status, "alive", False)):
        return None, ""
    name = str(getattr(status, "protocol_name", "") or "")
    major = getattr(status, "protocol_major", None)
    minor = getattr(status, "protocol_minor", None)
    if not name or major is None or minor is None:
        return False, "running daemon has no protocol metadata; restart it with the current checkout"
    if name != DAEMON_PROTOCOL_NAME or int(major) != DAEMON_PROTOCOL_MAJOR:
        return (
            False,
            f"daemon protocol {name}/{major} is incompatible with "
            f"{DAEMON_PROTOCOL_NAME}/{DAEMON_PROTOCOL_MAJOR}",
        )
    capabilities = set(getattr(status, "capabilities", ()) or ())
    missing = [item for item in DAEMON_CAPABILITIES if item not in capabilities]
    if missing:
        return False, f"daemon capabilities missing: {', '.join(missing)}"
    runtime = getattr(status, "runtime", None)
    if isinstance(runtime, dict) and runtime.get("source_root_matches_config") is False:
        return (
            False,
            "daemon loaded source "
            f"{runtime.get('source_root')} but ARGUS_SKILL_SOURCE_ROOT points to "
            f"{runtime.get('configured_source_root')}",
        )
    if isinstance(runtime, dict) and runtime.get("release_matches_source") is False:
        return False, "daemon release manifest does not match its loaded source"
    worktree = runtime.get("worktree") if isinstance(runtime, dict) else None
    require_clean = str(os.environ.get("ARGUS_SKILL_REQUIRE_CLEAN_SOURCE", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    if require_clean and isinstance(worktree, dict) and (
        worktree.get("dirty") is True or worktree.get("detached") is True
    ):
        return False, "daemon loaded a dirty or detached source checkout"
    expected_runtime = runtime_identity()
    if isinstance(runtime, dict) and runtime.get("self_managed_source") is True:
        return True, ""
    expected_release = str(expected_runtime.get("release_id") or "")
    actual_release = str((runtime or {}).get("release_id") or "")
    if expected_release and actual_release and expected_release != actual_release:
        return (
            False,
            f"daemon release {actual_release} is incompatible with WebAPI release "
            f"{expected_release}",
        )
    expected_digest = str(expected_runtime.get("runtime_source_digest") or "")
    actual_digest = str((runtime or {}).get("runtime_source_digest") or "")
    if expected_digest and actual_digest and expected_digest != actual_digest:
        return (
            False,
            f"daemon process source {actual_digest[:16]} is incompatible with "
            f"WebAPI source {expected_digest[:16]}",
        )
    return True, ""


def daemon_runtime_owned_by_current_source(status: Any) -> bool:
    """Prove that a daemon was launched from this WebAPI installation."""
    runtime = getattr(status, "runtime", None)
    if not isinstance(runtime, dict):
        return False
    expected = str(runtime_identity().get("source_root") or "").strip()
    if not expected:
        return False
    try:
        expected_path = Path(expected).expanduser().resolve()
    except OSError:
        return False
    candidate = str(runtime.get("source_root") or "").strip()
    if not candidate:
        return False
    try:
        return Path(candidate).expanduser().resolve() == expected_path
    except OSError:
        return False


__all__ = [
    "DAEMON_CAPABILITIES",
    "DAEMON_PROTOCOL_MAJOR",
    "DAEMON_PROTOCOL_MINOR",
    "DAEMON_PROTOCOL_NAME",
    "daemon_protocol_compatibility",
    "daemon_runtime_owned_by_current_source",
    "daemon_protocol_metadata",
]

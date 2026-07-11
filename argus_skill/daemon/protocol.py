"""Protocol metadata written by daemon workers into their status sidecar."""

from __future__ import annotations

from typing import Any

from ..core.runtime_identity import runtime_identity

DAEMON_PROTOCOL_NAME = "argus.daemon"
DAEMON_PROTOCOL_MAJOR = 1
DAEMON_PROTOCOL_MINOR = 1
DAEMON_CAPABILITIES = (
    "budget.status.v1",
    "events.jsonl.v1",
    "release.identity.v1",
    "usage.ledger.v1",
)


def daemon_protocol_metadata() -> dict[str, Any]:
    return {
        "protocol": {
            "name": DAEMON_PROTOCOL_NAME,
            "major": DAEMON_PROTOCOL_MAJOR,
            "minor": DAEMON_PROTOCOL_MINOR,
        },
        "capabilities": list(DAEMON_CAPABILITIES),
        "runtime": runtime_identity(),
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
    expected_release = str(runtime_identity().get("release_id") or "")
    actual_release = str((runtime or {}).get("release_id") or "")
    if expected_release and actual_release and expected_release != actual_release:
        return (
            False,
            f"daemon release {actual_release} is incompatible with WebAPI release "
            f"{expected_release}",
        )
    return True, ""


__all__ = [
    "DAEMON_CAPABILITIES",
    "DAEMON_PROTOCOL_MAJOR",
    "DAEMON_PROTOCOL_MINOR",
    "DAEMON_PROTOCOL_NAME",
    "daemon_protocol_compatibility",
    "daemon_protocol_metadata",
]

"""Versioned wire contract shared by the WebAPI and its frontends."""

from __future__ import annotations

from typing import Any

from ..core.runtime_identity import runtime_identity

API_SERVICE = "argus-skill-webapi"
API_PROTOCOL_NAME = "argus.webapi"
API_PROTOCOL_MAJOR = 1
API_PROTOCOL_MINOR = 8
SNAPSHOT_SCHEMA_VERSION = 5
API_CAPABILITIES = (
    "daemon.admission.v1",
    "daemon.status.protocol.v1",
    "daemon.command.v1",
    "cost.reservation.v1",
    "event.catalog.v1",
    "event.payload-schema.v1",
    "manager.sse.v1",
    "metrics.slo.v1",
    "mission.view.v1",
    "mission.abort.v1",
    "project.git-diff.v1",
    "research.events.v1",
    "release.identity.v1",
    "snapshot.budget.v1",
    "snapshot.schema.v1",
    "usage.recorded.v2",
)

def build_api_meta() -> dict[str, Any]:
    return {
        "service": API_SERVICE,
        "protocol": {
            "name": API_PROTOCOL_NAME,
            "major": API_PROTOCOL_MAJOR,
            "minor": API_PROTOCOL_MINOR,
        },
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "capabilities": list(API_CAPABILITIES),
        "runtime": runtime_identity(),
    }


def protocol_header() -> str:
    return f"{API_PROTOCOL_NAME}/{API_PROTOCOL_MAJOR}.{API_PROTOCOL_MINOR}"


__all__ = [
    "API_CAPABILITIES",
    "API_PROTOCOL_MAJOR",
    "API_PROTOCOL_MINOR",
    "API_PROTOCOL_NAME",
    "API_SERVICE",
    "SNAPSHOT_SCHEMA_VERSION",
    "build_api_meta",
    "protocol_header",
]

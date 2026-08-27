"""Versioned wire contract shared by the WebAPI and its frontends."""

from __future__ import annotations

import os
from typing import Any

from ..core.runtime_identity import runtime_identity

API_SERVICE = "argus-skill-webapi"
API_PROTOCOL_NAME = "argus.webapi"
API_PROTOCOL_MAJOR = 1
API_PROTOCOL_MINOR = 15
SNAPSHOT_SCHEMA_VERSION = 7
API_CAPABILITIES = (
    "daemon.admission.v1",
    "daemon.status.protocol.v1",
    "daemon.command.v1",
    "daemon.upgrade-schedule.v1",
    "cost.admission.v1",
    "event.catalog.v1",
    "event.payload-schema.v1",
    "manager.sse.v1",
    "metrics.slo.v2",
    "mission.view.v1",
    "mission.abort.v1",
    "project.attachments.v1",
    "project.git-diff.v1",
    "project.cost-feed.v1",
    "project.counterexamples.v1",
    "project.workdir.v1",
    "research.events.v1",
    "release.identity.v1",
    "snapshot.budget.v1",
    "snapshot.schema.v1",
    "source.update.v1",
    "usage.recorded.v2",
)

def build_api_meta() -> dict[str, Any]:
    runtime = runtime_identity()
    # The nonce is needed only for the first authenticated Desktop handshake.
    # Consume it before the WebAPI spawns daemons or model CLIs so the proof is
    # not inherited by unrelated descendants.
    desktop_launch_nonce = os.environ.pop("ARGUS_DESKTOP_LAUNCH_NONCE", "").strip()
    if desktop_launch_nonce:
        runtime["desktop_launch_nonce"] = desktop_launch_nonce
    return {
        "service": API_SERVICE,
        "protocol": {
            "name": API_PROTOCOL_NAME,
            "major": API_PROTOCOL_MAJOR,
            "minor": API_PROTOCOL_MINOR,
        },
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "capabilities": list(API_CAPABILITIES),
        "runtime": runtime,
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

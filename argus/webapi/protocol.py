"""Versioned wire contract shared by the WebAPI and its frontends."""

from __future__ import annotations

import json
import os
from typing import Any

from ..core.contract_resources import contract_schema_path
from ..core.runtime_identity import runtime_identity

_CONTRACT = json.loads(contract_schema_path("api_protocol.json").read_text(encoding="utf-8"))
API_SERVICE = _CONTRACT["API_SERVICE"]
API_PROTOCOL_NAME = _CONTRACT["API_PROTOCOL_NAME"]
API_PROTOCOL_MAJOR = _CONTRACT["API_PROTOCOL_MAJOR"]
API_PROTOCOL_MINOR = _CONTRACT["API_PROTOCOL_MINOR"]
SNAPSHOT_SCHEMA_VERSION = _CONTRACT["SNAPSHOT_SCHEMA_VERSION"]
API_CAPABILITIES = tuple(_CONTRACT["API_CAPABILITIES"])

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

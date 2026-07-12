from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.daemon.life_worker import (
    DaemonStatus,
    LifeWorkerConfig,
    _daemon_status_payload,
    read_daemon_status,
)
from argus_skill.daemon.protocol import (
    DAEMON_CAPABILITIES,
    DAEMON_PROTOCOL_MAJOR,
    DAEMON_PROTOCOL_NAME,
    daemon_protocol_compatibility,
)


def test_daemon_status_sidecar_carries_protocol_and_runtime_identity(
    tmp_path: Path,
) -> None:
    payload = _daemon_status_payload(
        LifeWorkerConfig(life_dir=tmp_path, backend="memory"),
        started_at_iso="2026-07-11T00:00:00+00:00",
    )
    (tmp_path / "daemon.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (tmp_path / "daemon.status.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    status = read_daemon_status(tmp_path)

    assert status.protocol_name == DAEMON_PROTOCOL_NAME
    assert status.protocol_major == DAEMON_PROTOCOL_MAJOR
    assert status.capabilities == DAEMON_CAPABILITIES
    assert status.runtime is not None
    assert status.runtime["source_root"]
    assert daemon_protocol_compatibility(status) == (True, "")


def test_running_legacy_daemon_is_explicitly_incompatible(tmp_path: Path) -> None:
    status = DaemonStatus(
        alive=True,
        pid=os.getpid(),
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
    )

    compatible, error = daemon_protocol_compatibility(status)

    assert compatible is False
    assert "no protocol metadata" in error


def test_daemon_loaded_from_wrong_configured_checkout_is_incompatible(
    tmp_path: Path,
) -> None:
    status = DaemonStatus(
        alive=True,
        pid=os.getpid(),
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
        protocol_name=DAEMON_PROTOCOL_NAME,
        protocol_major=DAEMON_PROTOCOL_MAJOR,
        protocol_minor=0,
        capabilities=DAEMON_CAPABILITIES,
        runtime={
            "source_root": "/loaded/argus-skill",
            "configured_source_root": "/configured/argus-skill",
            "source_root_matches_config": False,
        },
    )

    compatible, error = daemon_protocol_compatibility(status)

    assert compatible is False
    assert "/loaded/argus-skill" in error
    assert "/configured/argus-skill" in error


def test_daemon_from_different_release_is_incompatible(tmp_path: Path) -> None:
    status = DaemonStatus(
        alive=True,
        pid=os.getpid(),
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
        protocol_name=DAEMON_PROTOCOL_NAME,
        protocol_major=DAEMON_PROTOCOL_MAJOR,
        protocol_minor=1,
        capabilities=DAEMON_CAPABILITIES,
        runtime={
            "source_root": str(tmp_path),
            "configured_source_root": str(tmp_path),
            "source_root_matches_config": True,
            "release_id": "0.1.0+stale",
            "release_matches_source": True,
        },
    )

    compatible, error = daemon_protocol_compatibility(status)

    assert compatible is False
    assert "incompatible with WebAPI release" in error

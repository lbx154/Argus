from __future__ import annotations

import json
import os
from pathlib import Path

from argus_skill.daemon.config import config_from_payload, config_payload
from argus_skill.daemon.life_worker import (
    DaemonStatus,
    LifeWorkerConfig,
    _daemon_status_payload,
    read_daemon_status,
)
from argus_skill.daemon.protocol import (
    DAEMON_CAPABILITIES,
    DAEMON_PROTOCOL_MAJOR,
    DAEMON_PROTOCOL_MINOR,
    DAEMON_PROTOCOL_NAME,
    daemon_protocol_compatibility,
    daemon_runtime_owned_by_current_source,
)


def test_daemon_status_sidecar_carries_protocol_and_runtime_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from argus_skill.core.daemon_lock import acquire_global_daemon_lock

    monkeypatch.delenv("ARGUS_SKILL_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_REQUIRE_CLEAN_SOURCE", raising=False)
    payload = _daemon_status_payload(
        LifeWorkerConfig(life_dir=tmp_path, backend="memory"),
        started_at_iso="2026-07-11T00:00:00+00:00",
    )
    # This test exercises protocol serialization rather than the repository's
    # in-progress release manifest while the test suite itself edits files.
    payload["runtime"]["release_matches_source"] = None
    with acquire_global_daemon_lock(pid_path=tmp_path / "daemon.pid"):
        (tmp_path / "daemon.status.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        status = read_daemon_status(tmp_path)

    assert status.protocol_name == DAEMON_PROTOCOL_NAME
    assert status.protocol_major == DAEMON_PROTOCOL_MAJOR
    assert status.capabilities == DAEMON_CAPABILITIES
    assert status.runtime is not None
    assert status.mission_width == 2
    assert status.runtime["source_root"]
    assert daemon_protocol_compatibility(status) == (True, "")


def test_mission_width_round_trips_and_supports_zero_one_or_many(
    tmp_path: Path,
) -> None:
    for width in (0, 1, 2, 12):
        config = LifeWorkerConfig(
            life_dir=tmp_path,
            backend="memory",
            mission_width=width,
        )
        assert config_from_payload(config_payload(config)).mission_width == width


def test_family_failure_breaker_defaults_track_supervisor_config(
    tmp_path: Path,
) -> None:
    from argus_skill.life.supervisor._config import LifeSupervisorConfig

    config = LifeWorkerConfig(life_dir=tmp_path, backend="memory")

    assert (
        config.subagent_family_failure_streak_limit
        == LifeSupervisorConfig.subagent_family_failure_streak_limit
    )
    assert (
        config.subagent_family_failure_window_hours
        == LifeSupervisorConfig.subagent_family_failure_window_hours
    )


def test_family_failure_breaker_explicit_zero_survives_round_trip(
    tmp_path: Path,
) -> None:
    # 0 is the documented "breaker off" value; ``or``-style fallbacks used to
    # revert it to the default after a payload round trip.
    config = LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        subagent_family_failure_streak_limit=0,
        subagent_family_failure_window_hours=0.0,
    )

    restored = config_from_payload(config_payload(config))

    assert restored.subagent_family_failure_streak_limit == 0
    assert restored.subagent_family_failure_window_hours == 0.0


def test_family_failure_breaker_invalid_payload_values_fall_back_to_default(
    tmp_path: Path,
) -> None:
    # The daemon recovery path deserializes handoff payloads it did not write;
    # a non-numeric field must fall back to the factory default, not raise and
    # keep the daemon from booting.
    from argus_skill.life.supervisor._config import LifeSupervisorConfig

    payload = config_payload(LifeWorkerConfig(life_dir=tmp_path, backend="memory"))
    payload["subagent_family_failure_streak_limit"] = ""
    payload["subagent_family_failure_window_hours"] = "abc"

    restored = config_from_payload(payload)

    assert restored.subagent_family_failure_streak_limit == 3
    assert (
        restored.subagent_family_failure_streak_limit
        == LifeSupervisorConfig.subagent_family_failure_streak_limit
    )
    assert (
        restored.subagent_family_failure_window_hours
        == LifeSupervisorConfig.subagent_family_failure_window_hours
    )


def test_continuous_objective_file_round_trips(tmp_path: Path) -> None:
    objective = tmp_path / "OBJECTIVE.md"
    config = LifeWorkerConfig(
        life_dir=tmp_path,
        backend="memory",
        continuous=True,
        continuous_objective="keep exploring",
        continuous_objective_file=objective,
    )

    restored = config_from_payload(config_payload(config))

    assert restored.continuous_objective_file == objective


def test_daemon_status_reports_copilot_when_codex_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    copilot = tmp_path / ("copilot.cmd" if os.name == "nt" else "copilot")
    copilot.write_text(
        "@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    copilot.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_ENGINEER_BACKEND", raising=False)
    monkeypatch.setattr(
        "argus_skill.core.knob_store.read_persisted_knobs",
        lambda: {},
    )

    payload = _daemon_status_payload(
        LifeWorkerConfig(life_dir=tmp_path, backend="codex"),
        started_at_iso="2026-07-20T00:00:00+00:00",
    )

    assert payload["backend"] == "copilot"


def test_clean_source_policy_rejects_dirty_runtime(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_CLEAN_SOURCE", "1")
    status = SimpleNamespace(
        alive=True,
        protocol_name=DAEMON_PROTOCOL_NAME,
        protocol_major=DAEMON_PROTOCOL_MAJOR,
        protocol_minor=DAEMON_PROTOCOL_MINOR,
        capabilities=DAEMON_CAPABILITIES,
        runtime={
            "source_root_matches_config": None,
            "release_matches_source": None,
            "release_id": "",
            "worktree": {"dirty": True, "detached": False},
        },
    )
    assert daemon_protocol_compatibility(status) == (
        False,
        "daemon loaded a dirty or detached source checkout",
    )


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


def test_manifest_drift_is_warning_when_strict_release_gate_is_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", raising=False)
    monkeypatch.setattr(
        "argus_skill.daemon.protocol.runtime_identity",
        lambda: {
            "release_id": "0.1.1+same",
            "runtime_source_digest": "same-source",
        },
    )
    status = DaemonStatus(
        alive=True,
        pid=os.getpid(),
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
        protocol_name=DAEMON_PROTOCOL_NAME,
        protocol_major=DAEMON_PROTOCOL_MAJOR,
        protocol_minor=DAEMON_PROTOCOL_MINOR,
        capabilities=DAEMON_CAPABILITIES,
        runtime={
            "source_root_matches_config": True,
            "release_id": "0.1.1+same",
            "release_matches_source": False,
            "runtime_source_digest": "same-source",
        },
    )

    assert daemon_protocol_compatibility(status) == (True, "")


def test_manifest_drift_is_incompatible_when_strict_release_gate_is_on(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REQUIRE_RELEASE_MATCH", "1")
    status = DaemonStatus(
        alive=True,
        pid=os.getpid(),
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
        protocol_name=DAEMON_PROTOCOL_NAME,
        protocol_major=DAEMON_PROTOCOL_MAJOR,
        protocol_minor=DAEMON_PROTOCOL_MINOR,
        capabilities=DAEMON_CAPABILITIES,
        runtime={
            "source_root_matches_config": True,
            "release_matches_source": False,
        },
    )

    assert daemon_protocol_compatibility(status) == (
        False,
        "daemon release manifest does not match its loaded source",
    )


def test_clean_handoff_candidate_may_differ_from_webapi_release(
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
        protocol_minor=DAEMON_PROTOCOL_MINOR,
        capabilities=DAEMON_CAPABILITIES,
        runtime={
            "source_root": str(tmp_path),
            "configured_source_root": str(tmp_path),
            "source_root_matches_config": True,
            "release_id": "0.1.0+self-reviewed",
            "release_matches_source": True,
            "self_managed_source": True,
            "worktree": {"dirty": False, "detached": False},
        },
    )

    assert daemon_protocol_compatibility(status) == (True, "")


def test_daemon_from_stale_process_source_is_incompatible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "argus_skill.daemon.protocol.runtime_identity",
        lambda: {
            "release_id": "0.1.1+same",
            "runtime_source_digest": "new-source-digest",
        },
    )
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
            "source_root_matches_config": True,
            "release_id": "0.1.1+same",
            "release_matches_source": True,
            "runtime_source_digest": "old-source-digest",
        },
    )

    compatible, error = daemon_protocol_compatibility(status)

    assert compatible is False
    assert "daemon process source" in error
    assert "WebAPI source" in error


def test_daemon_source_ownership_requires_same_installation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    current = tmp_path / "current"
    other = tmp_path / "other"
    current.mkdir()
    other.mkdir()
    monkeypatch.setattr(
        "argus_skill.daemon.protocol.runtime_identity",
        lambda: {"source_root": str(current)},
    )

    owned = DaemonStatus(
        alive=True,
        pid=1,
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
        runtime={"source_root": str(current)},
    )
    foreign = DaemonStatus(
        alive=True,
        pid=2,
        started_at_iso=None,
        uptime_seconds=1.0,
        life_dir=tmp_path,
        runtime={"source_root": str(other)},
    )

    assert daemon_runtime_owned_by_current_source(owned) is True
    assert daemon_runtime_owned_by_current_source(foreign) is False

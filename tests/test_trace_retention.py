from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
import zipfile
from pathlib import Path

import pytest

from argus.agent_cli import copilot_home
from argus.core import trace_archive
from argus.core.knob_store import write_persisted_knob
from argus.core.knobs import normalize_cockpit_knob_value

RETENTION = "ARGUS_SKILL_COPILOT_SESSION_RETENTION_DAYS"


def old_session(home: Path, name="session-1") -> Path:
    session = home / "session-state" / name
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text('{"type":"session.start"}\n', encoding="utf-8")
    stamp = time.time() - 30 * 86400
    for path in (session / "events.jsonl", session):
        os.utime(path, (stamp, stamp))
    return session


def archives(home: Path, name="session-1") -> list[Path]:
    return list((home / "session-archives" / name).glob("*.zip"))


def test_default_preserves_old_research_session(tmp_path):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    assert copilot_home.prune_copilot_sessions(home, env={}) == 0
    assert (session / "events.jsonl").exists()


def test_recent_append_in_old_directory_is_not_reclaimed(tmp_path):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    with (session / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"type":"assistant.message"}\n')
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert (session / "events.jsonl").exists()


def test_nested_recent_trace_is_protected(tmp_path):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    (session / "files").mkdir()
    (session / "files" / "recent.txt").write_text("recent tool work", encoding="utf-8")
    stamp = time.time() - 30 * 86400
    os.utime(session / "files", (stamp, stamp))
    os.utime(session, (stamp, stamp))
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0


def test_session_archive_restores_exact_trace_before_resume(tmp_path):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    original = (session / "events.jsonl").read_bytes()
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 1
    assert not session.exists()
    assert len(archives(home)) == 1
    env = {"ARGUS_SKILL_HOME": str(tmp_path), RETENTION: "7"}
    with copilot_home.copilot_session_use(env, session_id="session-1"):
        assert (session / "events.jsonl").read_bytes() == original
        assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0


def test_idle_worker_use_lock_protects_all_sessions(tmp_path):
    home = tmp_path / "copilot-home"
    env = {"ARGUS_SKILL_HOME": str(tmp_path)}
    with copilot_home.copilot_session_use(env):
        session = old_session(home)
        assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
        assert session.exists()
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 1


def test_protected_resume_survives_startup_sweep(tmp_path):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    other = old_session(home, "other")
    env = {"ARGUS_SKILL_HOME": str(tmp_path), RETENTION: "7"}
    with copilot_home.copilot_session_use(env, session_id="session-1"):
        assert session.exists()
        assert not other.exists()


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "garbage"])
def test_invalid_retention_preserves_evidence(tmp_path, value):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: value}) == 0
    assert session.exists()
    with pytest.raises(ValueError):
        normalize_cockpit_knob_value(RETENTION, value)


def test_persisted_retention_and_explicit_disable(tmp_path):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    assert write_persisted_knob(RETENTION, "7")
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "0"}) == 0
    assert session.exists()
    assert copilot_home.prune_copilot_sessions(home, env={}) == 1


def test_terminal_storage_settings_persist_and_validate():
    from argus.apps._life_actions import render_config_cmd
    from argus.core.knob_store import read_persisted_knobs

    reply = render_config_cmd(["copilot_session_retention_days=7", "agent_io_mode=full", "agent_io_keep=0"], {})
    assert "saved; host-wide" in reply
    assert read_persisted_knobs()[RETENTION] == "7"
    assert read_persisted_knobs()["ARGUS_SKILL_AGENT_IO_KEEP"] == "0"
    reply = render_config_cmd(["copilot_session_retention_days=nan", "agent_io_keep=-1"], {})
    assert "bad value" in reply
    assert read_persisted_knobs()[RETENTION] == "7"
    assert read_persisted_knobs()["ARGUS_SKILL_AGENT_IO_KEEP"] == "0"


def test_acp_resume_restores_archive_before_session_load(tmp_path, monkeypatch):
    from argus.agent_cli.copilot_acp import CopilotAcpClient

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    home = tmp_path / "copilot-home"
    session = old_session(home)
    original = (session / "events.jsonl").read_bytes()
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 1
    client = CopilotAcpClient("unused-offline")
    client._session_events_root = home / "session-state"
    client._agent_caps = {"loadSession": True}
    requested = []
    def request(method, params, **kwargs):
        requested.append(method)
        assert (session / "events.jsonl").read_bytes() == original
        return {"result": {}}
    monkeypatch.setattr(client, "_request", request)
    with copilot_home.copilot_session_use({"ARGUS_SKILL_HOME": str(tmp_path)}):
        assert client._session_for("session-1", str(tmp_path), None) == "session-1"
    assert requested == ["session/load"]


def test_corrupt_archive_cannot_silently_start_fresh_session(tmp_path, monkeypatch):
    from argus.agent_cli.copilot_acp import CopilotAcpClient

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    home = tmp_path / "copilot-home"
    old_session(home)
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 1
    archives(home)[0].write_bytes(b"broken zip")
    client = CopilotAcpClient("unused-offline")
    client._session_events_root = home / "session-state"
    client._agent_caps = {"loadSession": True}
    requested = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: requested.append(args))
    with pytest.raises(zipfile.BadZipFile):
        client._session_for("session-1", str(tmp_path), None)
    assert requested == []


@pytest.mark.parametrize("stage", ["write", "verify", "publish"])
def test_archive_failure_preserves_session(tmp_path, monkeypatch, stage):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    original = (session / "events.jsonl").read_bytes()
    def fail(*args, **kwargs):
        raise OSError("injected archive failure")
    if stage == "write":
        monkeypatch.setattr(trace_archive.zipfile.ZipFile, "open", fail)
    elif stage == "verify":
        monkeypatch.setattr(trace_archive, "verify_archive", fail)
    else:
        monkeypatch.setattr(trace_archive.os, "replace", fail)
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert (session / "events.jsonl").read_bytes() == original


def test_scan_error_cannot_produce_partial_archive(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    def broken_walk(source, *, followlinks, onerror):
        onerror(PermissionError("cannot read a nested trace directory"))
        return iter(())
    monkeypatch.setattr(trace_archive.os, "walk", broken_walk)
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert (session / "events.jsonl").exists()
    assert not archives(home)


def test_partial_reclamation_restores_complete_archive(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    original = (session / "events.jsonl").read_bytes()
    def partial_remove(path):
        (path / "events.jsonl").unlink()
        raise OSError("interrupted directory removal")
    with monkeypatch.context() as patch:
        patch.setattr(copilot_home.shutil, "rmtree", partial_remove)
        assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert session.exists() and not (session / "events.jsonl").exists()
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    with copilot_home.copilot_session_use({"ARGUS_SKILL_HOME": str(tmp_path)}, session_id="session-1"):
        assert (session / "events.jsonl").read_bytes() == original


def test_changed_source_is_not_published(tmp_path, monkeypatch):
    source = tmp_path / "trace.jsonl"
    source.write_text("original\n", encoding="utf-8")
    verify = trace_archive.verify_archive
    def mutate_after_verify(path):
        result = verify(path)
        source.write_text("new trace\n", encoding="utf-8")
        return result
    monkeypatch.setattr(trace_archive, "verify_archive", mutate_after_verify)
    with pytest.raises(OSError, match="changed"):
        trace_archive.archive_trace(source, tmp_path / "archives")
    assert source.read_text(encoding="utf-8") == "new trace\n"
    assert not list((tmp_path / "archives").glob("*.zip"))


def test_archive_syncs_parent_chain_before_allowing_reclamation(tmp_path, monkeypatch):
    source = tmp_path / "trace.jsonl"
    source.write_text("evidence\n", encoding="utf-8")
    destination = tmp_path / "new" / "nested" / "archives"
    synced = []
    monkeypatch.setattr(trace_archive, "sync_directory", lambda path: synced.append(path))
    trace_archive.archive_trace(source, destination)
    assert synced[:4] == [destination, destination.parent, destination.parent.parent, tmp_path]


def test_failed_parent_sync_preserves_source(tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    def fail_parent(path):
        if path == home / "session-archives":
            raise OSError("parent directory sync failed")
    monkeypatch.setattr(trace_archive, "sync_directory", fail_parent)
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert (session / "events.jsonl").exists()


def test_linked_home_stays_usable_but_is_not_reclaimed(tmp_path, require_symlink_support):
    real = tmp_path / "real-home"
    real.mkdir()
    home = tmp_path / "copilot-home"
    home.symlink_to(real, target_is_directory=True)
    session = old_session(home)
    with copilot_home.copilot_session_use({"ARGUS_SKILL_HOME": str(tmp_path)}, session_id="session-1"):
        assert session.exists()
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert session.exists()


def test_nested_link_aborts_reclamation(tmp_path, require_symlink_support):
    home = tmp_path / "copilot-home"
    session = old_session(home)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    (session / "linked.txt").symlink_to(outside)
    stamp = time.time() - 30 * 86400
    os.utime(session, (stamp, stamp))
    assert copilot_home.prune_copilot_sessions(home, env={RETENTION: "7"}) == 0
    assert outside.read_text(encoding="utf-8") == "keep"


def test_linked_project_keeps_raw_trace_writes(tmp_path, require_symlink_support):
    from argus.adapters.agent_cli_backend import _io_log

    actual = tmp_path / "actual-project"
    actual.mkdir()
    linked = tmp_path / "linked-project"
    linked.symlink_to(actual, target_is_directory=True)
    path = linked / "agent_io.jsonl"
    _io_log._jsonl_append_lines(path, ['{"index":0}'], threading.Lock())
    _io_log._jsonl_append(path, {"index": 1}, threading.Lock())
    assert [json.loads(line)["index"] for line in (actual / path.name).read_text(encoding="utf-8").splitlines()] == [0, 1]
    assert not (actual / "trace-archives" / "pending").exists()


def test_windows_junction_project_keeps_trace_writes(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows junction")
    import subprocess

    from argus.adapters.agent_cli_backend import _io_log

    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "junction"
    # Both explicit, resolved targets are inside this test's private tmp root.
    assert actual.resolve().is_relative_to(tmp_path.resolve())
    assert linked.resolve().is_relative_to(tmp_path.resolve())
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(linked), str(actual)], capture_output=True)
    if result.returncode:
        pytest.skip("host cannot create junction")
    try:
        path = linked / "agent_io.jsonl"
        _io_log._jsonl_append_lines(path, ['{"index":0}'], threading.Lock())
        _io_log._jsonl_append(path, {"index": 1}, threading.Lock())
        assert [json.loads(line)["index"] for line in (actual / path.name).read_text(encoding="utf-8").splitlines()] == [0, 1]
    finally:
        linked.rmdir()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_restored_trace_stays_private_and_owner_executable(tmp_path):
    import stat
    source = tmp_path / "session-1"
    source.mkdir(mode=0o700)
    private = source / "events.jsonl"
    private.write_text("private\n", encoding="utf-8")
    private.chmod(0o600)
    executable = source / "tool.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o700)
    archived = trace_archive.archive_trace(source, tmp_path / "archives")
    restored_parent = tmp_path / "restored"
    restored_parent.mkdir()
    target = restored_parent / source.name
    previous_umask = os.umask(0o022)
    try:
        trace_archive.restore_trace(archived.path, target)
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / "events.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE((target / "tool.sh").stat().st_mode) == 0o700


def _writer(path, home, ordinal, count):
    from argus.adapters.agent_cli_backend import _io_log
    os.environ["ARGUS_SKILL_HOME"] = home
    os.environ["ARGUS_SKILL_AGENT_IO_MAX_BYTES"] = "100"
    os.environ["ARGUS_SKILL_AGENT_IO_KEEP"] = "1"
    for index in range(count):
        _io_log._jsonl_append_lines(Path(path), [json.dumps({"writer": ordinal, "index": index})], threading.Lock())


def collect_trace(path):
    rows = []
    for candidate in path.parent.glob(path.name + "*"):
        if candidate.name == path.name or candidate.name[len(path.name) + 1:].isdigit():
            rows.extend(json.loads(line) for line in candidate.read_text(encoding="utf-8").splitlines())
    for archived in (path.parent / "trace-archives" / "agent_io").glob("*.zip"):
        manifest = trace_archive.verify_archive(archived)
        with zipfile.ZipFile(archived) as archive:
            for entry in manifest["entries"]:
                rows.extend(json.loads(line) for line in archive.read("payload/" + entry["name"]).decode().splitlines())
    return rows


def test_concurrent_loggers_keep_every_line_once(tmp_path):
    path = tmp_path / "agent_io.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_writer, args=(str(path), str(tmp_path / "home"), ordinal, 16)) for ordinal in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(25)
        if process.is_alive():
            process.terminate()
            process.join(5)
        assert process.exitcode == 0
    rows = collect_trace(path)
    assert len(rows) == 32
    assert {(row["writer"], row["index"]) for row in rows} == {(ordinal, index) for ordinal in range(2) for index in range(16)}


@pytest.mark.parametrize("keep", ["0", "1", "2"])
def test_rotation_archives_all_generations_even_when_keep_shrinks(tmp_path, monkeypatch, keep):
    from argus.adapters.agent_cli_backend import _io_log
    path = tmp_path / "agent_io.jsonl"
    for index in range(4):
        target = path if index == 0 else path.with_name(path.name + f".{index}")
        target.write_text(json.dumps({"index": index}) + "\n", encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_MAX_BYTES", "1")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_KEEP", keep)
    _io_log._jsonl_append_lines(path, [json.dumps({"index": 4})], threading.Lock())
    assert sorted(row["index"] for row in collect_trace(path)) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("stage", ["write", "verify", "publish"])
def test_io_archive_failure_keeps_previous_and_new_lines(tmp_path, monkeypatch, stage):
    from argus.adapters.agent_cli_backend import _io_log
    path = tmp_path / "agent_io.jsonl"
    path.write_text('{"index":0}\n', encoding="utf-8")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_MAX_BYTES", "1")
    monkeypatch.setenv("ARGUS_SKILL_AGENT_IO_KEEP", "0")
    def fail(*args, **kwargs):
        raise OSError("injected failure")
    if stage == "write":
        monkeypatch.setattr(trace_archive.zipfile.ZipFile, "open", fail)
    elif stage == "verify":
        monkeypatch.setattr(trace_archive, "verify_archive", fail)
    else:
        monkeypatch.setattr(trace_archive.os, "replace", fail)
    _io_log._jsonl_append_lines(path, ['{"index":1}'], threading.Lock())
    assert [json.loads(line)["index"] for line in path.read_text(encoding="utf-8").splitlines()] == [0, 1]


def test_failed_trace_lock_preserves_pending_batch(tmp_path, monkeypatch):
    from argus.adapters.agent_cli_backend import _io_log
    path = tmp_path / "agent_io.jsonl"
    def fail(*args, **kwargs):
        raise OSError("lock unavailable")
    monkeypatch.setattr(_io_log.portalocker, "Lock", fail)
    _io_log._jsonl_append_lines(path, ['{"index":1}'], threading.Lock())
    pending = list((tmp_path / "trace-archives" / "pending").glob("*.jsonl"))
    assert len(pending) == 1
    assert pending[0].read_text(encoding="utf-8") == '{"index":1}\n'

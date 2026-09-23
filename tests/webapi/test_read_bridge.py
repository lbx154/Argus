"""The Node read surface invokes only these bounded Python query operations."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import project_state, read_bridge


def request(method: str, params: dict | None = None) -> bytes:
    return json.dumps({
        "protocol": read_bridge.CONTRACT["bridge_protocol"],
        "version": read_bridge.CONTRACT["bridge_version"],
        "id": "query-1", "method": method, "params": params or {},
    }).encode()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    workdir = tmp_path / "work"
    workdir.mkdir()
    write_session_meta(tmp_path, SessionMeta(
        id="s-read", display_name="Read fixture", objective="Inspect this project",
        created=100, last_active=100, cwd=str(workdir), workdir=str(workdir),
    ))
    life_dir = tmp_path / "projects/s-read"
    LifeMemory.open(life_dir).backlog.add(BacklogItem.new(title="Pending task", objective="Preserve pending work"))
    return tmp_path, life_dir


def test_read_bridge_uses_existing_projections_and_preserves_authoritative_files(project) -> None:
    root, life_dir = project
    # Existing stores may initialize locks/indexes or recover a pending commit.
    # Warm that path, then verify the authoritative inputs remain byte-identical.
    baseline = project_state.build_snapshot("s-read", global_root=root)
    watched = [life_dir / "session.json", life_dir / "backlog.jsonl"]
    before = {path: path.read_bytes() for path in watched}
    listed = read_bridge.reply(request("projects", {"include_empty": True}), global_root=root)
    assert listed["ok"] is True
    assert listed["result"]["projects"][0]["id"] == "s-read"
    snapshot = read_bridge.reply(request("snapshot", {"sid": "s-read"}), global_root=root)
    assert snapshot["ok"] is True
    assert snapshot["result"]["session"] == baseline["session"]
    assert snapshot["result"]["backlog"] == baseline["backlog"]
    frames = []
    costs = read_bridge.reply(request("costs"), global_root=root, emit_cost_frame=frames.append)
    assert costs["ok"] is True
    assert costs["result"]["project_count"] == 1
    assert [frame["kind"] for frame in frames] == ["cost_project", "cost_project_end"]
    assert all(frame["project_id"] == "s-read" for frame in frames)
    assert {path: path.read_bytes() for path in watched} == before


@pytest.mark.parametrize("method,params", [
    ("stop_daemon", {}), ("projects", {"global_root": "/other"}),
    ("projects", {"limit": True}), ("projects", {"limit": 0}),
    ("projects", {"limit": 2001}), ("projects", {"include_empty": "true"}),
    ("snapshot", {"sid": "../other"}), ("snapshot", {"sid": "x/y"}),
    ("snapshot", {"sid": "x\\y"}), ("snapshot", {"sid": "\ud800"}),
    ("snapshot", {"sid": "s-read", "prewarm": True}),
    ("snapshot", {"sid": "s-read", "events_limit": 501}),
])
def test_invalid_queries_never_return_success(method: str, params: dict, tmp_path: Path) -> None:
    response = read_bridge.reply(request(method, params), global_root=tmp_path)
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_query"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("raw", [b"null", b"[]", b"{broken", b'{"value":NaN}', b"x" * 65537])
def test_malformed_or_oversized_envelopes_are_rejected(raw: bytes, tmp_path: Path) -> None:
    assert read_bridge.reply(raw, global_root=tmp_path)["ok"] is False


def test_outside_project_symlink_cannot_be_read(tmp_path: Path) -> None:
    root = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    projects = root / "projects"
    projects.mkdir(parents=True)
    try:
        (projects / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    response = read_bridge.reply(request("snapshot", {"sid": "escape"}), global_root=root)
    assert response["ok"] is True
    assert response["result"] is None


def test_library_diagnostics_do_not_corrupt_the_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    def query(*args, **kwargs):
        print("library diagnostic")
        return []
    monkeypatch.setattr(project_state, "list_projects", query)
    result = read_bridge.reply(request("projects"), global_root=tmp_path)
    captured = capsys.readouterr()
    assert result["ok"] is True
    assert captured.out == ""
    assert "library diagnostic" in captured.err


def test_cost_stream_exports_only_normalized_inputs(project, monkeypatch) -> None:
    from argus.core.usage import UsageLedger, UsageRecord

    root, life_dir = project
    record = UsageRecord.from_jsonable({
        "call_id": "private-call", "project_id": "s-read", "error": "private diagnostic",
        "model": "private-model", "thread_id": "private-thread", "cost_basis": "provider_reported",
        "cost_usd": 0.2, "pricing_status": "priced", "input_tokens": 100,
    })
    UsageLedger(life_dir, migrate_legacy=False).append(record)
    before = (life_dir / "usage.jsonl").read_bytes()
    monkeypatch.setattr(project_state, "list_project_costs", lambda **_: pytest.fail("Python must not aggregate Node cost queries"))
    frames = []
    result = read_bridge.reply(request("costs"), global_root=root, emit_cost_frame=frames.append)
    assert result["ok"] is True
    assert [frame["kind"] for frame in frames] == ["cost_project", "cost_record", "cost_project_end"]
    assert frames[1]["record"]["cost_usd"] == 0.2
    assert "private" not in json.dumps(frames)
    assert (life_dir / "usage.jsonl").read_bytes() == before


@pytest.mark.parametrize("bound,value", [("max_cost_records", 0), ("max_cost_frame_bytes", 40), ("max_response_bytes", 80)])
def test_cost_stream_limits_return_a_failed_receipt(project, monkeypatch, bound, value) -> None:
    from argus.core.usage import UsageLedger, UsageRecord

    root, life_dir = project
    UsageLedger(life_dir, migrate_legacy=False).append(UsageRecord.from_jsonable({"call_id": "one"}))
    monkeypatch.setitem(read_bridge.CONTRACT, bound, value)
    result = read_bridge.reply(request("costs"), global_root=root, emit_cost_frame=lambda _: None)
    assert result["ok"] is False
    assert result["error"]["code"] == "response_too_large"


def test_corrupt_ledger_does_not_emit_success_or_empty_spending(project) -> None:
    root, life_dir = project
    path = life_dir / "usage.jsonl"
    path.write_bytes(b'{"call_id": "valid", "cost_usd": 1}\n{broken\n')
    before = path.read_bytes()
    frames = []
    result = read_bridge.reply(request("costs"), global_root=root, emit_cost_frame=frames.append)
    assert result["ok"] is False
    assert result["error"]["code"] == "query_failed"
    assert [frame["kind"] for frame in frames] == ["cost_project"]
    assert path.read_bytes() == before


def test_cost_stream_retains_python_token_price_reconciliation(project) -> None:
    from argus.core.usage import UsageLedger, UsageRecord

    root, life_dir = project
    ledger = UsageLedger(life_dir, migrate_legacy=False)
    ledger.append(UsageRecord.from_jsonable({
        "call_id": "late-price", "project_id": "s-read", "model": "gpt-5.5",
        "input_tokens": 1000, "output_tokens": 100, "pricing_status": "partial", "cost_basis": "token",
    }))
    frames = []
    result = read_bridge.reply(request("costs"), global_root=root, emit_cost_frame=frames.append)
    assert result["ok"] is True
    assert frames[1]["record"]["pricing_status"] == "priced"
    assert frames[1]["record"]["cost_usd"] == pytest.approx(0.00225)
    assert ledger.records()[0].cost_usd == pytest.approx(0.00225)

"""Exercise the built Node HTTP entrypoint against real isolated Python stores."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import portalocker
import pytest

from argus.core.session import SessionMeta, write_session_meta
from argus.life.memory import BacklogItem, LifeMemory
from argus.webapi import project_state

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "packages/api/dist/cli.js"
TOKEN = "read-api-integration-fixture"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ENTRY.is_file() or not shutil.which("node"), reason="run npm ci && npm run build for Node/Python integration"),
]


@pytest.fixture
def running_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "state"
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    write_session_meta(root, SessionMeta(
        id="s-read", display_name="研究项目", objective="Inspect a pending task",
        created=100, last_active=100, cwd=str(workdir), workdir=str(workdir),
    ))
    life_dir = root / "projects/s-read"
    LifeMemory.open(life_dir).backlog.add(BacklogItem.new(title="Pending fixture", objective="Keep this pending"))
    project_state.build_snapshot("s-read", global_root=root)
    before = {path: path.read_bytes() for path in (life_dir / "session.json", life_dir / "backlog.jsonl")}
    process = subprocess.Popen(
        [shutil.which("node"), str(ENTRY), "--python", sys.executable,
         "--source-root", str(ROOT), "--global-root", str(root)],
        cwd=ROOT, env={**os.environ, "ARGUS_READ_API_TOKEN": TOKEN},
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    ready: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        line = ready.get(timeout=30)
        if not line:
            raise AssertionError(f"Node read API failed to start: {process.stderr.read()}")
        address = json.loads(line)["url"]
        yield address, before
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        reader.join(timeout=2)


def fetch(url: str, path: str, *, token: str = TOKEN, method: str = "GET") -> tuple[int, dict]:
    request = Request(url + path, headers={"Authorization": f"Bearer {token}"}, method=method)
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


def test_node_http_reads_real_python_state_without_advancing_work(running_api) -> None:
    url, before = running_api
    status, meta = fetch(url, "/api/meta")
    assert status == 200
    assert meta["service"] == "argus-read-api"
    assert meta["read_only"] is True
    assert meta["runtime"]["implementation"] == "node"
    assert meta["backend"]["runtime"]["source_root"] == str(ROOT)
    status, projects = fetch(url, "/api/projects?include_empty=true")
    assert status == 200
    assert projects["projects"][0]["display_name"] == "研究项目"
    status, snapshot = fetch(url, "/api/projects/s-read/snapshot")
    assert status == 200
    assert snapshot["session"]["id"] == "s-read"
    assert snapshot["backlog"][0]["status"] == "pending"
    assert snapshot["backlog"][0]["objective"] == "Keep this pending"
    status, compact = fetch(url, "/api/projects/s-read/snapshot?compact=true&events_limit=0")
    assert status == 200
    assert compact["backlog"][0]["objective"] == ""
    status, costs = fetch(url, "/api/projects/costs")
    assert status == 200
    assert costs["projects"][0]["id"] == "s-read"
    assert {path: path.read_bytes() for path in before} == before


def test_node_http_rejects_authentication_writes_prewarm_and_unknown_projects(running_api) -> None:
    url, before = running_api
    assert fetch(url, "/api/projects", token="wrong")[0] == 401
    assert fetch(url, "/api/projects/s-read/tasks", method="POST")[0] == 405
    assert fetch(url, "/api/projects/s-read/snapshot?prewarm=true")[0] == 422
    assert fetch(url, "/api/projects/missing/snapshot")[0] == 404
    assert fetch(url, "/api/projects/%2fetc%2fpasswd/snapshot")[0] == 422
    assert {path: path.read_bytes() for path in before} == before


def test_node_costs_match_real_ledgers_with_cross_call_model_deduplication(running_api) -> None:
    from argus.core.usage import UsageLedger, UsageRecord

    url, before = running_api
    life_dir = next(iter(before)).parent
    root = life_dir.parent.parent
    cases = json.loads((ROOT / "packages/runtime/fixtures/usage-summary.json").read_text())
    rows = next(case["records"] for case in cases if case["id"] == "copilot-overlapping-receipts")
    paths = []
    for sid in ["s-read", "s-second"]:
        if sid != "s-read":
            write_session_meta(root, SessionMeta(id=sid, display_name="Second", workdir=str(root)))
        ledger = UsageLedger(root / "projects" / sid, migrate_legacy=False)
        for i, row in enumerate(rows):
            ledger.append(UsageRecord.from_jsonable({**row, "call_id": f"call-{i}", "cost_basis": "provider_reported"}))
        # The storage owner removes duplicate call IDs before Node sees them.
        with ledger.path.open("a") as handle:
            handle.write(json.dumps({"call_id": "call-0", "cost_usd": 99, "pricing_status": "priced"}) + "\n")
        paths.append(ledger.path)
    write_session_meta(root, SessionMeta(id="s-empty", display_name="Empty", workdir=str(root)))
    expected = project_state.list_project_costs(global_root=root)
    ledger_before = {path: path.read_bytes() for path in paths}
    status, result = fetch(url, "/api/projects/costs")
    assert status == 200
    assert result["projects"] == expected
    assert {row["id"]: row["spend_usd"] for row in result["projects"]} == {
        "s-read": 0.02, "s-second": 0.02, "s-empty": None,
    }
    assert {path: path.read_bytes() for path in paths} == ledger_before
    assert {path: path.read_bytes() for path in before} == before
    limited = fetch(url, "/api/projects/costs?limit=1")[1]
    assert limited["projects"] == expected[:1]


@pytest.mark.parametrize("suffix,code", [
    (b'{broken\n', "query_failed"),
    (b'{"call_id":"unsafe","input_tokens":9007199254740993,"pricing_status":"priced"}\n', "invalid_response"),
])
def test_node_costs_reject_corruption_or_unsafe_counts_without_partial_success(running_api, suffix, code) -> None:
    url, before = running_api
    ledger = next(iter(before)).parent / "usage.jsonl"
    contents = b'{"call_id":"settled","cost_usd":0.2,"pricing_status":"priced"}\n' + suffix
    ledger.write_bytes(contents)
    status, result = fetch(url, "/api/projects/costs")
    assert status == 502
    assert result["code"] == code
    assert "projects" not in result
    assert ledger.read_bytes() == contents


def test_node_costs_preserve_python_active_writer_tail_rules(running_api) -> None:
    url, before = running_api
    life_dir = next(iter(before)).parent
    ledger = life_dir / "usage.jsonl"
    contents = b'{"call_id":"settled","cost_usd":0.2,"pricing_status":"priced"}\n{"call_id":'
    with portalocker.Lock(str(life_dir / "usage.lock"), mode="a", timeout=1):
        ledger.write_bytes(contents)
        status, result = fetch(url, "/api/projects/costs")
        assert status == 200
        assert result["projects"][0]["spend_usd"] == 0.2
        assert result["projects"][0]["usage_calls"] == 1
    # The identical tail without a live writer is corruption, not missing spend.
    assert fetch(url, "/api/projects/costs")[0] == 502
    assert ledger.read_bytes() == contents

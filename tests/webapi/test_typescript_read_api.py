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

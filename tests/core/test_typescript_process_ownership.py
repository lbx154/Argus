"""Client disconnect != daemon death: verify both with real owned process trees."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from argus.core.cost_control import cost_admission_reason, cost_control_snapshot
from argus.core.usage import UsageLedger

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "packages/runtime/fixtures/guarded-owner.mjs"
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not shutil.which("node") or not (ROOT / "packages/runtime/dist/budgetedPi.js").is_file(),
    reason="build Node packages for native process ownership checks",
)]


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def wait_until(predicate, *, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "process ownership condition did not become true"
        time.sleep(0.02)


@pytest.fixture
def launch(tmp_path, monkeypatch):
    root = tmp_path / "state"
    (root / "projects/p").mkdir(parents=True)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_CAP_USD", "10")
    monkeypatch.setenv("ARGUS_SKILL_GLOBAL_DAILY_TOKEN_CAP", "0")
    monkeypatch.setenv("ARGUS_SKILL_UNPRICED_COST_POLICY", "block")
    retained = []
    subprocesses = []
    config = {"python": sys.executable, "sourceRoot": str(ROOT), "root": str(root),
              **{key: str(tmp_path / key) for key in ("ready", "result", "pids", "release")}}
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def start(mode="wait", detached=False):
        config["mode"] = mode
        path = tmp_path / "configuration.json"
        path.write_text(json.dumps(config))
        command = [shutil.which("node"), str(ENTRY), str(path)]
        if detached:
            client = subprocess.run([*command, "launch"], text=True, capture_output=True, check=True, timeout=10)
            owner = psutil.Process(int(client.stdout.strip()))
        else:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocesses.append(child)
            owner = psutil.Process(child.pid)
        retained.append(owner)
        wait_until(lambda: Path(config["ready"]).exists() or Path(config["result"]).exists())
        if not Path(config["ready"]).exists():
            pytest.fail(Path(config["result"]).read_text())
        rows = [json.loads(line) for line in Path(config["pids"]).read_text().splitlines()]
        assert {row["role"] for row in rows} == {"provider", "child", "grandchild"}
        tree = []
        for row in rows:
            try:
                process = psutil.Process(row["pid"])
                if not any("guarded-provider.mjs" in argument for argument in process.cmdline()):
                    continue  # The fixture already exited and its PID was reused.
                retained.append(process)
                tree.append(process)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                # Natural-exit cases can finish before the observer polls.
                assert mode != "wait"
        return owner, tree

    yield start, config, root, unrelated
    for process in retained:
        try:
            if alive(process):
                for child in process.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
        except psutil.NoSuchProcess:
            pass
    unrelated.kill()
    unrelated.wait(timeout=5)
    for child in subprocesses:
        child.wait(timeout=5)


def test_detached_owner_keeps_working_after_launcher_and_terminal_disconnect(launch):
    start, config, root, unrelated = launch
    owner, tree = start(detached=True)
    if os.name != "nt":
        owner.send_signal(signal.SIGHUP)
    assert alive(owner) and all(alive(child) for child in tree)
    assert not Path(config["result"]).exists()
    Path(config["release"]).touch()
    wait_until(lambda: Path(config["result"]).exists())
    result = json.loads(Path(config["result"]).read_text())
    assert result["settlement"] == "settled", json.dumps(result)
    wait_until(lambda: all(not alive(child) for child in tree))
    row, = UsageLedger(root / "projects/p", migrate_legacy=False).records()
    assert row.cost_usd == pytest.approx(0.1)
    assert unrelated.poll() is None


@pytest.mark.parametrize("victim", ["owner", "guard", "budget"])
def test_execution_owner_failure_reclaims_temporary_children_and_preserves_cost(launch, victim):
    start, config, root, unrelated = launch
    owner, tree = start()
    children = owner.children()
    guard, = [child for child in children if "argus.agent_cli.process_guard" in child.cmdline()]
    budget, = [child for child in children if "argus.adapters.budget_bridge" in child.cmdline()]
    {"owner": owner, "guard": guard, "budget": budget}[victim].kill()
    wait_until(lambda: all(not alive(child) for child in [*tree, guard, budget]))
    if victim != "owner":
        wait_until(lambda: Path(config["result"]).exists())
        result = json.loads(Path(config["result"]).read_text())
        assert result["settlement"] in {"unresolved", "failed"}, result
        assert result["runner"] is None or not result["runner"]["turnCompleted"]
    assert "unresolved provider cost" in cost_admission_reason(global_root=root)
    snapshot = cost_control_snapshot(global_root=root)
    assert snapshot["blocking_unresolved_calls"] == 1
    known = sum(row.cost_usd or 0 for row in UsageLedger(root / "projects/p", migrate_legacy=False).records())
    assert known + snapshot["unacknowledged_observed_cost_usd"] == pytest.approx(0.1)
    assert unrelated.poll() is None


@pytest.mark.parametrize("mode", ["root-exit", "inherited-pipe"])
def test_provider_parent_exit_cannot_abandon_children(launch, mode):
    start, config, _root, unrelated = launch
    _owner, tree = start(mode)
    wait_until(lambda: Path(config["result"]).exists())
    result = json.loads(Path(config["result"]).read_text())
    assert result["settlement"] == ("settled" if mode == "root-exit" else "unresolved"), json.dumps(result)
    wait_until(lambda: all(not alive(child) for child in tree))
    assert unrelated.poll() is None

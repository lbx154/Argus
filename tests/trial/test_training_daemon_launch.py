"""Real fresh-interpreter/double-fork tests; no models, tasks, or live tenants.

Only the container/read-only-image check is replaced for the temporary host
fixture. Unix peer credentials, exact helper argv, every fork parent/start-time
binding, ticket consumption, daemon hooks and persistent registration are real.
"""
from __future__ import annotations

import hashlib
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_training_runtime import training as training

from argus_skill.daemon import _life_worker_admission as admission
from argus_skill.daemon.config import LifeWorkerConfig
from argus_skill.trial import training_runtime as runtime
from argus_skill.trial.analytics import AnalyticsError
from argus_skill.trial.training_bridge import (
    PeerVerifier,
    TrainingBridge,
    _Handler,
    _process,
    _Server,
)


class _LocalImageVerifier(PeerVerifier):
    def _image_identity(self, proc, uid):
        # Test processes live outside the production container and their source
        # files are owned by the test user. Keep every OS lineage check intact.
        return {"isolated_test_source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


class _WebEndpoint(socketserver.BaseRequestHandler):
    def handle(self):
        pass


@pytest.mark.skipif(os.name != "posix" or not Path("/proc").is_dir(), reason="Linux peer credentials and fork are required")
def test_actual_fresh_helper_double_fork_registration_replay_and_restart(training, tmp_path, monkeypatch):
    web_path, bridge_path = tmp_path / "web.sock", tmp_path / "training.sock"
    web = socketserver.UnixStreamServer(str(web_path), _WebEndpoint)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    verifier = _LocalImageVerifier("tenant-one", tmp_path / "tenant", web_path)
    bridge = TrainingBridge(training, "tenant-one", verifier)
    server = _Server(str(bridge_path), _Handler)
    server.bridge = bridge
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = training.analytics.tenants["tenant-one"]["data_dir"] / "home/.argus-skill"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (root / "projects/s-project/session.json").write_text(json.dumps({"id": "s-project", "workdir": str(workspace)}))
    config = LifeWorkerConfig(life_dir=root / "projects/s-project", global_root=root,
                              project_workdir=workspace, backend="pi")
    receipt, repeat, stop = (tmp_path / name for name in ("receipt.json", "repeat", "stop"))
    shim = tmp_path / "test-only-python-site"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(r'''
import json, os, time
from pathlib import Path
from argus_skill.daemon import life_worker
class IsolatedNoModelWorker:
    def __init__(self, config): self.config=config
    def run_forever(self):
        from argus_skill.trial import training_runtime as runtime
        from argus_skill.trial.training_bridge import IMAGE_PACKAGE, EXTENSION_NAME
        time.sleep(0.15)
        path=os.environ[runtime.SOCKET_ENV]
        value={'sid':self.config.life_dir.name,'call_id':'first-real-call','run_label':'engineer-test',
               'mission_id':None,'command':['argus-pi','--no-extensions','--no-context-files',
               '--extension',str(IMAGE_PACKAGE / EXTENSION_NAME)]}
        reply=runtime._request(path,'register',value)
        record={'pid':os.getpid(),'parent':os.getppid(),'registered':reply.get('enabled') is True,
                'ticket_cleared':runtime._daemon_launch is None,
                'no_ticket_env':not any('CAPABILITY' in key or 'LAUNCH_TICKET' in key for key in os.environ)}
        try:
            runtime._request(path,'register',{**value,'sid':'different-project'})
            record['wrong_project_denied']=False
        except ValueError: record['wrong_project_denied']=True
        receipt=Path(os.environ['ARGUS_ISOLATED_TEST_RECEIPT'])
        receipt.write_text(json.dumps(record))
        deadline=time.monotonic()+15
        repeated=False
        while time.monotonic()<deadline and not Path(os.environ['ARGUS_ISOLATED_TEST_STOP']).exists():
            if not repeated and Path(os.environ['ARGUS_ISOLATED_TEST_REPEAT']).exists():
                later=runtime._request(path,'register',{**value,'call_id':'after-portal-restart'})
                record['registered_after_restart']=later.get('enabled') is True
                receipt.write_text(json.dumps(record))
                repeated=True
            time.sleep(0.05)
        return 0
life_worker.LifeWorker=IsolatedNoModelWorker
''')
    monkeypatch.setenv("ARGUS_TRIAL_HARNESS", "argus-pi")
    monkeypatch.setenv(runtime.SOCKET_ENV, str(bridge_path))
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    monkeypatch.setenv("PYTHONPATH", str(shim))
    monkeypatch.setenv("ARGUS_ISOLATED_TEST_RECEIPT", str(receipt))
    monkeypatch.setenv("ARGUS_ISOLATED_TEST_REPEAT", str(repeat))
    monkeypatch.setenv("ARGUS_ISOLATED_TEST_STOP", str(stop))
    tickets = []
    original = runtime.daemon_launch_payload

    def observe(config):
        payload = original(config)
        tickets.append(payload)
        return payload

    monkeypatch.setattr(runtime, "daemon_launch_payload", observe)
    daemon_pid = None
    try:
        assert admission.spawn_detached_daemon_clean(config, quiet=True) == 0, config.last_spawn_error
        deadline = time.monotonic() + 5
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        observed = json.loads(receipt.read_text())
        daemon_pid = observed["pid"]
        assert observed["registered"] and observed["ticket_cleared"] and observed["no_ticket_env"]
        assert observed["wrong_project_denied"]
        # The bug being fixed: the detached worker cannot prove a surviving
        # Python ancestry path back to the current web server.
        with pytest.raises(AnalyticsError, match="runtime_parent_untrusted"):
            verifier._runtime_parent(_process(daemon_pid))
        assert tickets[0] is not None
        with pytest.raises(AnalyticsError, match="capability_invalid"):
            bridge.dispatch({"action": "daemon_helper", "lease": tickets[0]["ticket"], "value": {"sid": "s-project"}},
                            (os.getpid(), os.getuid(), os.getgid()))
        assert not bridge.daemon_tickets
        with training.analytics._db() as db:
            row = db.execute("SELECT * FROM training_runtime_workers").fetchone()
        metadata = json.loads(row["metadata"])
        assert metadata["pid"] == daemon_pid and metadata["sid"] == "s-project"
        assert [part["kind"] for part in metadata["chain"]] == ["runtime_parent", "helper", "fork", "claim"]
        assert len({part["pid"] for part in metadata["chain"]}) == 4
        assert tickets[0]["ticket"] not in row["metadata"]
        assert tickets[0]["ticket"] not in json.dumps(bridge.status())
        for log in config.life_dir.rglob("*.log"):
            assert tickets[0]["ticket"] not in log.read_text(errors="replace")
        # Simulate a portal/bridge restart: capabilities and verifier caches are
        # gone, but the same living pid/start/boot registration remains usable.
        replacement = TrainingBridge(training, "tenant-one", _LocalImageVerifier("tenant-one", tmp_path / "tenant", web_path))
        server.bridge = replacement
        repeat.touch()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if json.loads(receipt.read_text()).get("registered_after_restart"):
                break
            time.sleep(0.05)
        assert json.loads(receipt.read_text())["registered_after_restart"]
        counts = bridge.status()["counts"]
        assert all(counts[action + "_completed"] == 1 for action in ("daemon_prepare", "daemon_helper", "daemon_fork", "daemon_claim"))
        # A stolen ticket cannot be claimed by its own issuer or an unrelated
        # process: the next actual direct child is part of the capability.
        fresh = replacement.dispatch({"action": "daemon_prepare", "lease": None, "value": {"sid": "s-project"}},
                                     (os.getpid(), os.getuid(), os.getgid()))["ticket"]
        with pytest.raises(AnalyticsError, match="peer_parent_mismatch"):
            replacement.dispatch({"action": "daemon_helper", "lease": fresh, "value": {"sid": "s-project"}},
                                 (os.getpid(), os.getuid(), os.getgid()))
        # Nor does becoming a Python orphan under init/tini grant trust.
        rejected = tmp_path / "unregistered-orphan.json"
        script = r'''
import json, os, sys, time
from pathlib import Path
if os.fork(): raise SystemExit(0)
os.setsid()
if os.fork(): raise SystemExit(0)
time.sleep(0.15)
from argus_skill.trial import training_runtime as runtime
from argus_skill.trial.training_bridge import IMAGE_PACKAGE, EXTENSION_NAME
value={'sid':'s-project','call_id':'forged-orphan-call','run_label':'engineer-test','mission_id':None,
       'command':['argus-pi','--no-extensions','--no-context-files','--extension',str(IMAGE_PACKAGE/EXTENSION_NAME)]}
try:
    runtime._request(os.environ[runtime.SOCKET_ENV],'register',value)
    denied=False
except ValueError: denied=True
Path(sys.argv[1]).write_text(json.dumps({'denied':denied}))
'''
        source_root = str(Path(admission.__file__).resolve().parents[2])
        rejected_process = subprocess.run(
            [sys.executable, "-c", script, str(rejected)], capture_output=True, text=True, check=True, timeout=10,
            cwd=source_root, env={**os.environ, "PYTHONPATH": os.pathsep.join((source_root, str(shim)))},
        )
        deadline = time.monotonic() + 3
        while not rejected.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert rejected.exists(), rejected_process.stderr[-1500:]
        assert json.loads(rejected.read_text())["denied"]
    finally:
        stop.touch()
        if daemon_pid:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (config.life_dir / "daemon.status.json").exists():
                time.sleep(0.05)
        server.shutdown()
        server.server_close()
        web.shutdown()
        web.server_close()


def test_forged_ticket_and_wrong_stage_never_create_worker_registration(training):
    bridge = TrainingBridge(training, "tenant-one", lambda peer, **kwargs: {
        "pid": peer[0], "started": "synthetic-start", "source_sha256": {"fixture": "a" * 64}})
    peer = (os.getpid(), os.getuid(), os.getgid())
    for action in ("daemon_helper", "daemon_fork", "daemon_claim"):
        with pytest.raises(AnalyticsError, match="capability_invalid"):
            bridge.dispatch({"action": action, "lease": "forged", "value": {"sid": "s-project"}}, peer)
    ticket = bridge.dispatch({"action": "daemon_prepare", "lease": None, "value": {"sid": "s-project"}}, peer)["ticket"]
    for action, sid in (("daemon_claim", "s-project"), ("daemon_helper", "different-project")):
        with pytest.raises(AnalyticsError, match="capability_invalid"):
            bridge.dispatch({"action": action, "lease": ticket, "value": {"sid": sid}}, peer)
    with training.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_runtime_workers").fetchone()[0] == 0

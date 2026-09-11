"""Initialization/late-ACK behavior on isolated real Unix sockets, without models."""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from test_training_runtime import registration, verifier
from test_training_runtime import training as training

from argus_skill.trial import training_runtime as runtime
from argus_skill.trial.analytics import AnalyticsError
from argus_skill.trial.training_bridge import TrainingBridge, _Handler, _Server


def _run_extension(path, lease):
    script = r'''
import extension from __EXTENSION__;
const handlers = new Map();
extension({on:(name,handler)=>handlers.set(name,handler),getActiveTools:()=>['bash'],
 getAllTools:()=>[{name:'bash',description:'Execute a command.',parameters:{type:'object'}}]});
await handlers.get('agent_start')({}, {model:{provider:'argus',api:'openai-completions'},
 sessionManager:{getSessionId:()=> 'synthetic-initialization-session'}});
await handlers.get('context')({messages:[{role:'user',content:'Public isolated socket test.',timestamp:Date.now()}]});
console.log(JSON.stringify({finished:true,capabilityRemoved:!process.env.ARGUS_TRAINING_LEASE_TOKEN}));
'''.replace("__EXTENSION__", json.dumps(Path(runtime.EXTENSION).as_uri()))
    result = subprocess.run(["node", "--input-type=module"], input=script, text=True,
                            capture_output=True, timeout=25,
                            env={**os.environ, runtime.SOCKET_ENV: str(path), runtime.LEASE_ENV: lease})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"finished": True, "capabilityRemoved": True}
    assert lease not in result.stdout + result.stderr


@pytest.mark.parametrize("mode,reason", [
    ("slow", None),
    ("timeout", "capture_init_begin_timeout"),
    ("profile", "runtime_profile_changed"),
    ("receipt", "capture_init_reply_invalid"),
])
def test_real_socket_slow_begin_and_late_or_invalid_ack(training, tmp_path, monkeypatch, mode, reason):
    original = training.capture.begin

    def begin(*args, **kwargs):
        # A genuine >10s outstanding request races its client's init_failed
        # request; the bridge lock must quarantine the eventual late insert.
        if mode in {"slow", "timeout"}:
            time.sleep(1.25 if mode == "slow" else 10.25)
        result = original(*args, **kwargs)
        if mode == "profile":
            result["profile"] = "synthetic-wrong-profile"
        elif mode == "receipt":
            result["allowed_tools"] = None
        return result

    monkeypatch.setattr(training.capture, "begin", begin)
    bridge = TrainingBridge(training, "tenant-one", lambda peer, **kw: {
        "pid": peer[0], "started": "synthetic-fixture", "source_sha256": {"fixture": "a" * 64},
    })
    lease = bridge.dispatch(registration(), (os.getpid(), os.getuid(), os.getgid()))["lease"]
    server = _Server(str(tmp_path / "capture.sock"), _Handler)
    server.bridge = bridge
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _run_extension(server.server_address, lease)
        with training.analytics._db() as db:
            row = db.execute("SELECT state,reason,record FROM training_tool_episodes").fetchone()
        status = bridge.status()
        if reason is None:
            assert row["state"] == "capturing" and row["reason"] is None
            assert [item["kind"] for item in json.loads(row["record"])] == ["context"]
            assert status["counts"]["events_received"] == 1
            assert status["counts"].get("initialization_failed", 0) == 0
        else:
            assert dict(row) == {"state": "quarantined", "reason": reason, "record": "[]"}
            assert status["counts"]["initialization_failed"] == status["counts"]["episodes_quarantined"] == 1
            assert status["last_error_code"] == reason
            assert status["counts"].get("events_received", 0) == 0
    finally:
        server.shutdown()
        server.server_close()


def test_initialization_diagnosis_authenticates_seals_and_counts_once(training):
    bridge = TrainingBridge(training, "tenant-one", verifier)
    lease = bridge.dispatch(registration(), (10, 0, 0))["lease"]
    failed = {"action": "init_failed", "lease": lease, "value": {"reason": "capture_init_begin_failed"}}
    with pytest.raises(AnalyticsError, match="peer_parent_mismatch"):
        bridge.dispatch(failed, (99, 0, 0))
    with pytest.raises(ValueError, match="initialization diagnosis"):
        bridge.dispatch({**failed, "value": {"reason": "PRIVATE_ERROR_VALUE"}}, (20, 0, 0))
    assert bridge.dispatch(failed, (20, 0, 0))["state"] == "disabled"
    assert bridge.dispatch(failed, (20, 0, 0))["already_failed"]
    for action, value in [("authorize", {}), ("begin", {"session_id": "late-session"}),
                          ("event", {"episode_id": 1, "kind": "settled", "payload": {}})]:
        with pytest.raises(AnalyticsError, match="training_initialization_failed"):
            bridge.dispatch({"action": action, "lease": lease, "value": value}, (20, 0, 0))
    status = bridge.status()
    assert status["counts"]["initialization_failed"] == 1
    assert status["counts"].get("episodes_quarantined", 0) == 0
    assert "PRIVATE_ERROR_VALUE" not in json.dumps(status) and lease not in json.dumps(status)
    with training.analytics._db() as db:
        assert db.execute("SELECT count(*) FROM training_tool_episodes").fetchone()[0] == 0
    assert bridge.dispatch({"action": "close", "lease": lease, "value": {}}, (10, 0, 0))["closed"]


@pytest.mark.parametrize("terminal", ["complete", "quarantined", "deleted"])
def test_failed_initialization_does_not_overwrite_terminal_or_deleted_episode(training, terminal):
    bridge = TrainingBridge(training, "tenant-one", verifier)
    lease = bridge.dispatch(registration(), (10, 0, 0))["lease"]
    bridge.dispatch({"action": "begin", "lease": lease, "value": {"session_id": "synthetic-terminal"}}, (20, 0, 0))
    with training.analytics._db() as db:
        if terminal == "deleted":
            db.execute("DELETE FROM training_tool_episodes")
        else:
            db.execute("UPDATE training_tool_episodes SET state=?,reason='synthetic-existing-reason'", (terminal,))
    bridge.dispatch({"action": "init_failed", "lease": lease,
                     "value": {"reason": "capture_init_begin_failed"}}, (20, 0, 0))
    with training.analytics._db() as db:
        rows = db.execute("SELECT state,reason FROM training_tool_episodes").fetchall()
    assert not rows if terminal == "deleted" else dict(rows[0]) == {"state": terminal, "reason": "synthetic-existing-reason"}
    assert bridge.status()["counts"].get("episodes_quarantined", 0) == 0


@pytest.mark.parametrize("failure_action", ["authorize", "begin"])
def test_initializer_diagnoses_only_fixed_code_without_error_or_capability(failure_action):
    script = r'''
import {trainingExtension} from __EXTENSION__;
const handlers=new Map(), receipts=[];
trainingExtension(async(action,value)=>{
 if(action===__FAILURE__) throw Error('PRIVATE_ERROR_WITH_SYNTHETIC_CAPABILITY');
 receipts.push({action,value});return {enabled:true};
})({on:(name,handler)=>handlers.set(name,handler)});
await handlers.get('agent_start')({}, {model:{provider:'argus',api:'openai-completions'},
 sessionManager:{getSessionId:()=> 'synthetic-init-failure'}});
console.log(JSON.stringify(receipts));
'''.replace("__EXTENSION__", json.dumps(Path(runtime.EXTENSION).as_uri())).replace("__FAILURE__", json.dumps(failure_action))
    result = subprocess.run(["node", "--input-type=module"], input=script, text=True,
                            capture_output=True, check=True, timeout=10)
    assert "PRIVATE_ERROR" not in result.stdout + result.stderr
    assert json.loads(result.stdout)[-1] == {
        "action": "init_failed", "value": {"reason": "capture_init_" + failure_action + "_failed"},
    }

"""Real-size synthetic public episodes, isolated from all deployed/user state."""
import json
import socket
import threading

from test_training_runtime import training as training

from argus_skill.trial.training_bridge import _Handler, _Server
from argus_skill.trial.training_capture import (
    HOSTED_EPISODE_BYTES,
    HOSTED_PAYLOAD_BYTES,
    HOSTED_PROFILE,
)
from argus_skill.trial.training_validate import validate_package

TOOLS = [{"name": "bash", "description": "Produce bounded public fixture output.", "parameters": {
    "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"],
}}]
PROVIDER_TOOLS = [{"type": "function", "function": TOOLS[0]}]


def _begin(training, session):
    return training.capture.begin("tenant-one", "s-project", session, observer_verified=True,
                                  allowed_tools=["bash"], runtime_profile=HOSTED_PROFILE, runtime_metadata={
                                      "profile": HOSTED_PROFILE, "capture_id": "a" * 32, "call_id": "capacity-fixture",
                                      "mission_id": "capacity-task", "source_sha256": {"fixture": "b" * 64},
                                      "launch_sha256": "c" * 64,
                                  })["episode_id"]


def _large_episode(training, turns):
    episode = _begin(training, "synthetic-large-" + str(turns))
    stamp = training.analytics.clock() * 1000
    user_text = "Public capacity fixture. " + "x" * (64 * 1024 - 32)
    messages = [{"role": "user", "content": [{"type": "text", "text": user_text}], "timestamp": stamp}]
    wire = [{"role": "user", "content": user_text}]

    def event(kind, payload):
        # Every event remains much smaller than the original 4 MiB RPC limit.
        assert len(json.dumps(payload)) < HOSTED_PAYLOAD_BYTES
        return training.capture.event("tenant-one", "s-project", episode, kind, payload)

    def context():
        result = event("context", {"messages": messages, "tools": TOOLS})
        if result["state"] == "quarantined":
            return result
        return event("provider_request", {"messages": wire, "tools": PROVIDER_TOOLS, "model": "synthetic-capacity"})

    result = context()
    for index in range(turns):
        if result["state"] == "quarantined":
            return episode, result
        call_id, arguments = "call-" + str(index), {"command": "printf public_fixture_" + str(index)}
        text = f"Public output {index}.\n" + "r" * (48 * 1024 - 32)
        messages.append({"role": "assistant", "content": [{"type": "toolCall", "id": call_id,
                         "name": "bash", "arguments": arguments}], "stopReason": "toolUse", "timestamp": stamp})
        wire.append({"role": "assistant", "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": "bash", "arguments": arguments}}]})
        for kind, payload in (
            ("tool_call", {"toolCallId": call_id, "toolName": "bash", "input": arguments}),
            ("tool_result", {"toolCallId": call_id, "toolName": "bash", "input": arguments,
                             "content": [{"type": "text", "text": text}], "isError": False, "output_complete": True}),
        ):
            result = event(kind, payload)
            if result["state"] == "quarantined":
                return episode, result
        messages.append({"role": "toolResult", "toolName": "bash", "toolCallId": call_id,
                         "content": [{"type": "text", "text": text}], "isError": False, "timestamp": stamp})
        wire.append({"role": "tool", "tool_call_id": call_id, "name": "bash", "content": text})
        result = context()
    if result["state"] == "quarantined":
        return episode, result
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "All bounded public fixture checks completed."}],
                     "stopReason": "stop", "timestamp": stamp})
    result = event("agent_end", {"messages": messages, "private_blocks_excluded": False})
    return episode, result if result["state"] == "quarantined" else event("settled", {})


def test_over_eight_mib_complete_episode_previews_exports_and_validates(training):
    episode, result = _large_episode(training, 12)
    assert result["state"] == "complete", result
    with training.analytics._db() as db:
        size = db.execute("SELECT length(record) FROM training_tool_episodes WHERE id=?", (episode,)).fetchone()[0]
    assert 8 * 1024 * 1024 < size < HOSTED_EPISODE_BYTES
    projects = [{"tenant_id": "tenant-one", "sid": "s-project"}]
    preview = training.preview("internal_training", projects)
    assert preview["counts"]["tool_candidates"] == 1
    blob, _ = training.export("internal_training", projects, review={
        "content_approved": True, "tool_context_approved": True,
        "approved_event_ids": [preview["candidates"][0]["event_id"]],
    })
    validation = validate_package(blob)
    assert validation["valid"], validation


def test_real_size_cumulative_limit_quarantines_instead_of_truncating(training):
    episode, result = _large_episode(training, 20)
    assert result["state"] == "quarantined" and result["reason"] == "capture_episode_oversized"
    with training.analytics._db() as db:
        assert db.execute("SELECT record FROM training_tool_episodes WHERE id=?", (episode,)).fetchone()[0] == "[]"


def test_single_event_over_four_mib_stays_rejected_below_episode_budget(training):
    episode = _begin(training, "synthetic-payload-boundary")
    payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": "x" * (256 * 1024)}],
                              "timestamp": training.analytics.clock() * 1000} for _ in range(17)], "tools": TOOLS}
    assert HOSTED_PAYLOAD_BYTES < len(json.dumps(payload)) < HOSTED_EPISODE_BYTES
    result = training.capture.event("tenant-one", "s-project", episode, "context", payload)
    assert result["state"] == "quarantined" and result["reason"] == "capture_payload_oversized"


def test_bridge_rejects_oversized_rpc_before_dispatch(tmp_path):
    dispatched = []

    class Bridge:
        def dispatch(self, *_args):
            dispatched.append(True)
            return {"unexpected_dispatch": True}

    server = _Server(str(tmp_path / "bounded.sock"), _Handler)
    server.bridge = Bridge()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(server.server_address)
            try:
                client.sendall(json.dumps({"padding": "x" * (HOSTED_PAYLOAD_BYTES + 8193)}).encode() + b"\n")
                with client.makefile("rb") as stream:
                    response = json.loads(stream.readline())
                assert response == {"error": "training_bridge_request_rejected"}
            except (BrokenPipeError, ConnectionResetError):
                pass
        assert not dispatched
    finally:
        server.shutdown()
        server.server_close()

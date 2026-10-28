"""Offline validator tests use only newly generated synthetic dataset packages."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from test_training_runtime import training as training

from argus_trial_server.interaction_capture import Capture
from argus_trial_server.training_capture import HOSTED_PROFILE
from argus_trial_server.training_schema import pi_strict_schema
from argus_trial_server.training_validate import (
    InvalidPackage,
    _check_public_sources,
    main,
    validate_package,
)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


@pytest.fixture
def package(training, tmp_path):
    """A synthetic parallel-tool episode passed through the actual exporter."""
    evidence = tmp_path / "acceptance.json"
    evidence.write_text('{"synthetic_unit_test":true,"checks_passed":true}\n')
    evidence_bytes = len(evidence.read_bytes())
    tools = [{"name": "bash", "description": "Execute a workspace command.", "parameters": {
        "type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
        "required": ["command"],
    }}]
    provider_tools = [{"type": "function", "function": {
        **tools[0], "parameters": pi_strict_schema(tools[0]["parameters"]), "strict": True,
    }}]
    stamp = training.analytics.clock() * 1000
    user = {"role": "user", "content": [{"type": "text", "text": "Run both public synthetic checks."}], "timestamp": stamp}
    calls = [{"type": "toolCall", "id": f"real-call-{number}", "name": "bash",
              "arguments": {"command": f"printf {number}", "timeout": None}} for number in (1, 2)]
    assistant = {"role": "assistant", "content": calls, "timestamp": stamp, "stopReason": "toolUse"}
    results = [{"role": "toolResult", "toolCallId": f"real-call-{number}", "toolName": "bash",
                "content": [{"type": "text", "text": str(number)}], "isError": False, "timestamp": stamp}
               for number in (2, 1)]
    final = {"role": "assistant", "content": [{"type": "text", "text": "Both synthetic checks completed."}],
             "stopReason": "stop", "timestamp": stamp}
    prefix = [{"role": "user", "content": user["content"][0]["text"]}, {"role": "assistant", "tool_calls": [
        {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}}
        for call in calls]}, *[{"role": "tool", "name": "bash", "tool_call_id": result["toolCallId"],
                               "content": result["content"][0]["text"]} for result in results]]
    observations = [("context", {"messages": [user], "tools": tools}),
                    ("provider_request", {"messages": prefix[:1], "tools": provider_tools, "model": "synthetic-model"})]
    for number in (1, 2):
        observations.append(("tool_call", {"toolCallId": f"real-call-{number}", "toolName": "bash", "input": {"command": f"printf {number}"}}))
    for number in (2, 1):
        observations.append(("tool_result", {"toolCallId": f"real-call-{number}", "toolName": "bash", "input": {"command": f"printf {number}"},
                                               "content": [{"type": "text", "text": str(number)}], "isError": False, "output_complete": True}))
    observations += [("context", {"messages": [user, assistant, *results], "tools": tools}),
                     ("provider_request", {"messages": prefix, "tools": provider_tools, "model": "synthetic-model"}),
                     ("agent_end", {"messages": [user, assistant, *results, final], "private_blocks_excluded": True}),
                     ("settled", {})]
    episode = training.capture.begin("tenant-one", "s-project", "synthetic-session", observer_verified=True,
                                     allowed_tools=["bash"], runtime_profile=HOSTED_PROFILE, runtime_metadata={
                                         "profile": HOSTED_PROFILE, "capture_id": "a" * 32, "call_id": "actual-call", "mission_id": "actual-task"})["episode_id"]
    for kind, payload in observations:
        result = training.capture.event("tenant-one", "s-project", episode, kind, payload)
        assert result["state"] != "quarantined", result
    projects = [{"tenant_id": "tenant-one", "sid": "s-project"}]
    sample = training.preview("internal_training", projects)["candidates"][0]
    blob, _ = training.export("internal_training", projects, review={
        "content_approved": True, "tool_context_approved": True, "approved_event_ids": [sample["event_id"]],
        "reviewer_kind": "automated_acceptance", "evidence_bytes": evidence_bytes})
    return blob, evidence, training, projects


def rewrite(blob, mutate):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    parsed = {name: [json.loads(line) for line in raw.splitlines()] if name.endswith(".jsonl") else json.loads(raw)
              for name, raw in files.items() if name.endswith((".json", ".jsonl"))}
    mutate(parsed)
    for name, value in parsed.items():
        files[name] = ("".join(canonical(row) + "\n" for row in value) if name.endswith(".jsonl") else canonical(value)).encode()
    manifest = parsed["manifest.json"]
    manifest["files"] = {name: {"bytes": len(raw)} for name, raw in files.items() if name != "manifest.json"}
    files["manifest.json"] = canonical(manifest).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def test_chat_review_package_is_valid_but_not_agentic(training):
    capture = Capture(training.analytics, "tenant-one", "s-project", "/api/projects/s-project/message", {"text": "Explain sorting."})
    capture.feed(json.dumps({"kind": "chat", "reply": "Sorting orders items."}).encode())
    capture.finish(200, True, "application/json")
    training.journal.poll("tenant-one")
    projects = [{"tenant_id": "tenant-one", "sid": "s-project"}]
    candidate = training.preview("internal_training", projects)["candidates"][0]
    blob, _ = training.export("internal_training", projects, review={"content_approved": True,
                             "reviewer_kind": "human_operator", "approved_event_ids": [candidate["event_id"]]})
    report = validate_package(blob)
    assert report["valid"] and report["counts"]["sft"] == 1, report
    assert not report["agentic_training_ready"]


def test_persisted_sample_review_keeps_its_own_reviewer_and_evidence(package):
    _, evidence, training, projects = package
    blob, _ = training.export("internal_training", projects, review={"content_approved": True, "reviewer_kind": "human_operator"})
    report = validate_package(blob, evidence_path=evidence)
    assert report["valid"], report
    assert report["reviewer_kind"] == "human_operator"
    assert report["automated_sample_evidence_verified"] == 1 and report["agentic_training_ready"]


def test_cli_writes_content_free_structured_report(package, tmp_path, capsys):
    blob, evidence, _, _ = package
    source, report = tmp_path / "synthetic.zip", tmp_path / "validation.json"
    source.write_bytes(blob)
    assert main([str(source), "--evidence", str(evidence), "--report", str(report), "--require-agentic"]) == 0
    saved = json.loads(report.read_text())
    assert saved == json.loads(capsys.readouterr().out)
    assert saved["package_bytes"] == len(blob)
    assert "printf" not in report.read_text()


def test_duplicate_json_keys_and_nonfinite_numbers_fail_strictly():
    from argus_trial_server.training_validate import InvalidPackage, _json

    for raw, code in ((b'{"a":1,"a":2}', "duplicate_json_key"), (b'{"a":NaN}', "nonfinite_json_number"),
                      (b'{"a":1e999}', "nonfinite_json_number")):
        with pytest.raises(InvalidPackage, match=code):
            _json(raw, "synthetic")


def test_offline_public_skill_read_rejects_modified_body_at_valid_published_path():
    relative = "verticals/software/skills/engineer/software-change-implementation.md"
    body = (Path(__file__).resolve().parents[2] / "argus" / relative).read_text()
    payload = {
        "toolCallId": "synthetic-public-read", "toolName": "read",
        "input": {"path": "/tenant/home/.argus-skill/skills/_shared_verticals/software/engineer/software-change-implementation.md"},
        "content": [{"type": "text", "text": body}], "isError": False, "output_complete": True,
    }
    source = {
        "kind": "pi.training_episode", "sid": "s-a1b2c3d4", "runtime_profile": HOSTED_PROFILE,
        "runtime": {"profile": HOSTED_PROFILE, "mission_id": "a1b2c3d4e5f6"},
        "events": [{"id": "synthetic-public-read", "sequence": 0, "kind": "tool_result",
                    "observed_at": 1, "payload": payload}],
    }
    _check_public_sources([source])
    payload["content"][0]["text"] = body + "\nA modified local skill instruction.\n"
    with pytest.raises(InvalidPackage) as error:
        _check_public_sources([source])
    assert error.value.code == "unverified_public_skill_content"
    assert error.value.location == "trajectories.jsonl:1"
    assert "modified local skill" not in str(error.value)


@pytest.mark.parametrize("required,code", [
    (["timeout", "command"], None),
    (["command"], "runtime_provider_schema_mismatch"),
    (["command", "timeout", "timeout"], "invalid_tool_schema"),
])
def test_provider_required_order_is_irrelevant_but_membership_is_exact(package, required, code):
    blob, evidence, _, _ = package

    def mutate(parsed):
        for split in ("train", "validation"):
            for filename in (f"hf_trl_{split}.jsonl", f"sft_{split}.jsonl"):
                for sample in parsed[filename]:
                    sample["tools"][0]["function"]["parameters"]["required"] = list(required)
        for source in parsed["trajectories.jsonl"]:
            for event in source["events"]:
                if event["kind"] == "provider_request":
                    event["payload"]["tools"][0]["function"]["parameters"]["required"] = list(required)

    changed = rewrite(blob, mutate)
    report = validate_package(changed, evidence_path=evidence)
    if code is None:
        assert report["valid"], report
        with zipfile.ZipFile(io.BytesIO(changed)) as archive:
            sample = json.loads(archive.read("hf_trl_train.jsonl").splitlines()[0])
        assert sample["tools"][0]["function"]["parameters"]["required"] == required
    else:
        assert not report["valid"] and report["errors"][0]["code"] == code, report

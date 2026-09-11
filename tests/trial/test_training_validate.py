"""Offline validator tests use only newly generated synthetic dataset packages."""
import hashlib
import io
import json
import zipfile

import pytest
from test_training_runtime import training as training

from argus_skill.trial.interaction_capture import Capture
from argus_skill.trial.training_capture import HOSTED_PROFILE
from argus_skill.trial.training_schema import pi_strict_schema
from argus_skill.trial.training_validate import _sample, main, validate_package


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def package(training, tmp_path):
    """A synthetic parallel-tool episode passed through the actual exporter."""
    evidence = tmp_path / "acceptance.json"
    evidence.write_text('{"synthetic_unit_test":true,"checks_passed":true}\n')
    evidence_hash = digest(evidence.read_bytes())
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
                                         "profile": HOSTED_PROFILE, "capture_id": "a" * 32, "call_id": "actual-call", "mission_id": "actual-task",
                                         "source_sha256": {"synthetic_fixture": "b" * 64}, "launch_sha256": "c" * 64})["episode_id"]
    for kind, payload in observations:
        result = training.capture.event("tenant-one", "s-project", episode, kind, payload)
        assert result["state"] != "quarantined", result
    projects = [{"tenant_id": "tenant-one", "sid": "s-project"}]
    sample = training.preview("internal_training", projects)["candidates"][0]
    blob, _ = training.export("internal_training", projects, review={
        "content_approved": True, "tool_context_approved": True, "approved_event_ids": [sample["event_id"]],
        "reviewer_kind": "automated_acceptance", "evidence_sha256": evidence_hash})
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
    manifest["files"] = {name: {"sha256": digest(raw), "bytes": len(raw)} for name, raw in files.items() if name != "manifest.json"}
    files["manifest.json"] = canonical(manifest).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return stream.getvalue()


def test_actual_export_parallel_calls_reversed_results_and_evidence(package):
    blob, evidence, _, _ = package
    report = validate_package(blob, evidence_path=evidence)
    assert report["valid"], report
    assert report["agentic_training_ready"] and report["counts"]["tool_sft"] == 1
    assert report["evidence_file_verified"] and not report["human_reviewed"]
    assert report["automated_sample_evidence_verified"] == 1
    assert "Run both public synthetic checks" not in json.dumps(report)


def test_automated_acceptance_requires_exact_external_evidence(package, tmp_path):
    blob, _, _, _ = package
    assert validate_package(blob)["errors"][0]["code"] == "automated_review_evidence_file_required"
    wrong = tmp_path / "wrong.json"
    wrong.write_text("unrelated synthetic evidence")
    assert validate_package(blob, evidence_path=wrong)["errors"][0]["code"] == "evidence_file_hash_mismatch"


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


@pytest.mark.parametrize("mutation,code", [
    (lambda p: p["sft_train.jsonl"][0]["messages"][1]["tool_calls"][0]["function"].update(arguments={"command": "printf 1"}),
     "portable_arguments_not_json_string"),
    (lambda p: p["hf_trl_train.jsonl"][0]["messages"][1]["tool_calls"][1].update(id="real-call-1"),
     "invalid_or_duplicate_tool_call_id"),
    (lambda p: p["hf_trl_train.jsonl"][0]["messages"][2].update(tool_call_id="nonexistent-call"), "orphan_or_duplicate_tool_result"),
    (lambda p: p["hf_trl_train.jsonl"][0]["messages"].pop(2), "missing_tool_result_before_next_turn"),
    (lambda p: p["hf_trl_train.jsonl"][0]["messages"][1]["tool_calls"][0]["function"]["arguments"].update(command=42),
     "tool_arguments_schema_mismatch"),
    (lambda p: p["hf_trl_train.jsonl"][0]["messages"][-1].update(reasoning_content="PRIVATE_SYNTHETIC_NEVER_REPORT"),
     "private_or_unknown_message_field"),
    (lambda p: p["samples.jsonl"][0].update(sample_id="f" * 64), "sample_content_hash_mismatch"),
    (lambda p: p["samples.jsonl"][0]["quality_evidence"].update(human_reviewed=True), "sample_reviewer_evidence_mismatch"),
    (lambda p: p["trajectories.jsonl"][0]["events"][2]["payload"]["input"].update(command="printf changed"),
     "executed_arguments_do_not_match_model_call"),
    (lambda p: p["provenance.jsonl"][0]["consent"].update(notice_version="unrelated-notice"), "source_consent_scope_mismatch"),
    (lambda p: p["manifest.json"]["counts"].update(sft=42), "manifest_quality_actual_counts_mismatch"),
])
def test_rehashed_but_semantically_invalid_package_is_rejected(package, mutation, code):
    blob, evidence, _, _ = package
    report = validate_package(rewrite(blob, mutation), evidence_path=evidence)
    assert not report["valid"] and report["errors"][0]["code"] == code, report
    assert "PRIVATE_SYNTHETIC" not in json.dumps(report)


def test_empty_tool_output_and_portable_parameter_string_are_legal():
    sample = {"tools": [{"type": "function", "function": {"name": "read", "description": "Read a file.",
               "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}],
              "messages": [{"role": "user", "content": "Read an empty file."},
                           {"role": "assistant", "tool_calls": [{"id": "empty-file-call", "type": "function", "function": {
                               "name": "read", "arguments": '{"path":"empty.txt"}'}}]},
                           {"role": "tool", "tool_call_id": "empty-file-call", "content": ""},
                           {"role": "assistant", "content": "The file is empty."}]}
    normalized, _, results = _sample(sample, "synthetic", portable=True)
    assert results == {"empty-file-call": ""}
    assert normalized["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"path": "empty.txt"}


def test_cli_writes_content_free_structured_report(package, tmp_path, capsys):
    blob, evidence, _, _ = package
    source, report = tmp_path / "synthetic.zip", tmp_path / "validation.json"
    source.write_bytes(blob)
    assert main([str(source), "--evidence", str(evidence), "--report", str(report), "--require-agentic"]) == 0
    saved = json.loads(report.read_text())
    assert saved == json.loads(capsys.readouterr().out)
    assert saved["package_sha256"] == digest(blob)
    assert "printf" not in report.read_text()


def test_unlisted_duplicate_zip_entries_are_rejected(package):
    blob, evidence, _, _ = package
    stream = io.BytesIO(blob)
    with zipfile.ZipFile(stream, "a") as archive:
        archive.writestr("unexpected.txt", "synthetic-only")
    assert validate_package(stream.getvalue(), evidence_path=evidence)["errors"][0]["code"] == "zip_file_set_mismatch"


def test_file_integrity_failure_is_detected_before_semantics(package):
    blob, evidence, _, _ = package
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["README.txt"] += b"changed"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    assert validate_package(stream.getvalue(), evidence_path=evidence)["errors"][0]["code"] == "manifest_hash_or_size_mismatch"


def test_duplicate_json_keys_and_nonfinite_numbers_fail_strictly():
    from argus_skill.trial.training_validate import InvalidPackage, _json

    for raw, code in ((b'{"a":1,"a":2}', "duplicate_json_key"), (b'{"a":NaN}', "nonfinite_json_number"),
                      (b'{"a":1e999}', "nonfinite_json_number")):
        with pytest.raises(InvalidPackage, match=code):
            _json(raw, "synthetic")

"""Offline validation of an Argus dataset ZIP; never runs tools or training.

The report establishes file integrity, training format, source correspondence,
and review-evidence consistency. It does not prove scientific conclusions,
mathematical correctness, performance claims, licensing, or genuine human review.
Error reports contain locations and fixed codes, never sample text or arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

from jsonschema import Draft202012Validator, SchemaError

from argus_skill.core.secret_guard import redact_secrets_record

from .analytics import _sanitize
from .training_capture import HOSTED_PROFILE, _hosted_sensitive
from .training_public_assets import check_public_skill_event
from .training_schema import pi_execution_arguments, pi_schema_equal, pi_strict_schema

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
FILES = frozenset({"manifest.json", "quality_report.json", "README.txt", "trajectories.jsonl",
                   "provenance.jsonl", "samples.jsonl", "hf_trl_train.jsonl", "hf_trl_validation.jsonl",
                   "sft_train.jsonl", "sft_validation.jsonl"})
REVIEWERS = {"unspecified", "human_operator", "automated_acceptance"}


class InvalidPackage(ValueError):
    def __init__(self, code, location="package"):
        self.code, self.location = code, location
        super().__init__(code)


def _require(condition, code, location):
    if not condition:
        raise InvalidPackage(code, location)


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _digest(value):
    return _hash(_canonical(value).encode())


def _is_hash(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _json(raw, location):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate_json_key", location)
            result[key] = value
        return result

    def constant(_value):
        raise InvalidPackage("nonfinite_json_number", location)

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise InvalidPackage("invalid_json", location) from None
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(depth <= 60 and nodes <= 1_000_000, "json_complexity_limit", location)
        _require(not isinstance(item, float) or math.isfinite(item), "nonfinite_json_number", location)
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def _rows(raw, location):
    if not raw:
        return []
    rows = []
    for index, line in enumerate(raw.splitlines(), 1):
        spot = f"{location}:{index}"
        _require(bool(line.strip()), "blank_jsonl_row", spot)
        row = _json(line, spot)
        _require(isinstance(row, dict), "jsonl_row_not_object", spot)
        rows.append(row)
    return rows


def _schema(schema, location):
    _require(isinstance(schema, dict) and schema.get("type") == "object", "invalid_tool_schema", location)
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            _require(not node.keys() & {"$ref", "$dynamicRef", "$recursiveRef"}, "schema_reference_not_supported", location)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise InvalidPackage("invalid_tool_schema", location) from None
    return Draft202012Validator(schema)


def _public_binding(source):
    if (isinstance(source, dict) and source.get("kind") == "pi.training_episode"
            and source.get("runtime_profile") == HOSTED_PROFILE):
        runtime = source.get("runtime")
        if isinstance(runtime, dict) and runtime.get("profile") == HOSTED_PROFILE:
            return {"sid": source.get("sid"), "mission_id": runtime.get("mission_id")}
    return {}


def _sample(sample, location, *, portable=False, source=None):
    _require(isinstance(sample, dict) and not sample.keys() - {"messages", "tools"}, "invalid_sample_fields", location)
    tools = sample.get("tools", [])
    _require(isinstance(tools, list), "invalid_tools", location)
    schemas = {}
    for index, tool in enumerate(tools):
        spot = f"{location}/tools/{index}"
        _require(isinstance(tool, dict) and set(tool) == {"type", "function"} and tool["type"] == "function",
                 "invalid_tool_definition", spot)
        function = tool["function"]
        _require(isinstance(function, dict) and not function.keys() - {"name", "description", "parameters", "strict"},
                 "invalid_tool_definition", spot)
        name = function.get("name")
        _require(isinstance(name, str) and NAME.fullmatch(name) and name not in schemas, "invalid_or_duplicate_tool_name", spot)
        _require(isinstance(function.get("description"), str), "missing_tool_description", spot)
        _require("strict" not in function or type(function["strict"]) is bool, "invalid_tool_strict_flag", spot)
        schemas[name] = _schema(function.get("parameters"), spot)
    messages = sample.get("messages")
    _require(isinstance(messages, list) and 2 <= len(messages) <= 4096, "invalid_messages", location)
    _require(messages[0].get("role") == "user" if isinstance(messages[0], dict) else False, "first_message_not_user", location)
    canonical, calls, pending, results = [], {}, set(), {}
    for index, message in enumerate(messages):
        spot = f"{location}/messages/{index}"
        _require(isinstance(message, dict), "invalid_message", spot)
        role = message.get("role")
        fields = {"user": {"role", "content"}, "assistant": {"role", "content", "tool_calls"},
                  "tool": {"role", "content", "tool_call_id", "name"}}
        _require(role in fields, "private_or_unknown_message_role", spot)
        _require(not message.keys() - fields[role], "private_or_unknown_message_field", spot)
        _require("content" not in message or isinstance(message["content"], str), "nontext_message_content", spot)
        item = dict(message)
        if role == "tool":
            key = message.get("tool_call_id")
            _require(isinstance(key, str) and key in pending, "orphan_or_duplicate_tool_result", spot)
            _require("content" in message, "missing_tool_output", spot)
            name = calls[key]["name"]
            _require(message.get("name", name) == name, "tool_result_name_mismatch", spot)
            _require(not portable or "name" not in message, "portable_tool_name_not_removed", spot)
            item["name"] = name
            pending.remove(key)
            results[key] = message["content"]
        else:
            _require(not pending, "missing_tool_result_before_next_turn", spot)
            if role == "user":
                _require(isinstance(message.get("content"), str) and bool(message["content"].strip()), "empty_user_text", spot)
            else:
                values = message.get("tool_calls", [])
                _require(isinstance(values, list), "invalid_tool_calls", spot)
                _require(bool(values) or bool(message.get("content", "").strip()), "empty_assistant_message", spot)
                if "tool_calls" in message:
                    _require(bool(values), "empty_tool_calls", spot)
                    item["tool_calls"] = []
                for call_index, call in enumerate(values):
                    call_spot = f"{spot}/tool_calls/{call_index}"
                    _require(isinstance(call, dict) and set(call) == {"id", "type", "function"}
                             and call["type"] == "function", "invalid_tool_call", call_spot)
                    key, function = call["id"], call["function"]
                    _require(isinstance(key, str) and 1 <= len(key) <= 160 and key not in calls,
                             "invalid_or_duplicate_tool_call_id", call_spot)
                    _require(isinstance(function, dict) and set(function) == {"name", "arguments"}, "invalid_tool_call", call_spot)
                    _require(isinstance(function["name"], str) and function["name"] in schemas, "undefined_tool", call_spot)
                    arguments = function["arguments"]
                    if portable:
                        _require(isinstance(arguments, str), "portable_arguments_not_json_string", call_spot)
                        arguments = _json(arguments.encode(), call_spot)
                    _require(isinstance(arguments, dict), "hf_arguments_not_object", call_spot)
                    _require(schemas[function["name"]].is_valid(arguments), "tool_arguments_schema_mismatch", call_spot)
                    calls[key] = {"name": function["name"], "arguments": arguments}
                    pending.add(key)
                    item["tool_calls"].append({"id": key, "type": "function", "function": calls[key]})
        canonical.append(item)
    _require(not pending, "missing_tool_results", location)
    _require(canonical[-1]["role"] == "assistant" and not canonical[-1].get("tool_calls")
             and bool(canonical[-1].get("content", "").strip()), "missing_final_public_assistant_output", location)
    _require(bool(calls) == bool(tools), "tools_without_tool_episode", location)
    normalized = {"messages": canonical}
    if tools:
        normalized["tools"] = tools
    # Portable arguments are JSON strings: escaped newlines after a code colon
    # can look like a Windows drive, while Unicode escapes can hide a secret.
    # Scan the strictly decoded semantic object. The original rows and bytes
    # remain untouched for the format correspondence and manifest hash checks.
    _require(not _hosted_sensitive(normalized, **_public_binding(source))
             and _sanitize(redact_secrets_record(normalized)) == normalized,
             "sensitive_training_content", location)
    return normalized, calls, results


def _public_messages(messages, location):
    _require(isinstance(messages, list), "invalid_source_messages", location)
    result = []
    for message in messages:
        _require(isinstance(message, dict) and not message.keys() - {
            "role", "content", "timestamp", "stopReason", "toolCallId", "toolName", "isError",
        }, "private_or_unknown_source_field", location)
        role = message.get("role")
        _require(role in {"user", "assistant", "toolResult"}, "private_source_role", location)
        blocks = message.get("content")
        if isinstance(blocks, str) and role == "user":
            blocks = [{"type": "text", "text": blocks}]
        _require(isinstance(blocks, list), "invalid_source_content", location)
        texts, calls = [], []
        for block in blocks:
            _require(isinstance(block, dict), "invalid_source_block", location)
            if block.get("type") == "text" and set(block) == {"type", "text"}:
                _require(isinstance(block["text"], str), "invalid_source_text", location)
                texts.append(block["text"])
            else:
                _require(role == "assistant" and set(block) == {"type", "id", "name", "arguments"}
                         and block["type"] == "toolCall", "private_or_unknown_source_block", location)
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": block["arguments"]}})
        item = {"role": "tool" if role == "toolResult" else role}
        if texts:
            item["content"] = ("\n" if role == "toolResult" else "").join(texts)
        if calls:
            item["tool_calls"] = calls
        if role == "toolResult":
            _require(type(message.get("isError")) is bool, "missing_tool_error_state", location)
            item.update(tool_call_id=message.get("toolCallId"), name=message.get("toolName"))
        if role == "assistant":
            _require(message.get("stopReason") == ("toolUse" if calls else "stop"), "incomplete_source_assistant", location)
        result.append(item)
    return result


def _wire_messages(messages):
    """The one documented Pi text normalization for a truly empty tool output."""
    return [{**message, "content": "(no tool output)"}
            if message.get("role") == "tool" and message.get("content") == "" else message
            for message in messages]


def _check_public_sources(trajectories):
    """Even unapproved diagnostic episodes must not carry private message fields."""
    for index, source in enumerate(trajectories, 1):
        if source.get("kind") != "pi.training_episode":
            continue
        spot = f"trajectories.jsonl:{index}"
        events = source.get("events")
        _require(isinstance(events, list), "invalid_episode_events", spot)
        for event in events:
            _require(isinstance(event, dict) and set(event) == {"id", "sequence", "kind", "observed_at", "payload"},
                     "private_or_unknown_episode_field", spot)
            payload = event["payload"]
            _require(isinstance(payload, dict) and not _hosted_sensitive(payload, **_public_binding(source))
                     and _sanitize(redact_secrets_record(payload)) == payload, "sensitive_source_content", spot)
            kind = event["kind"]
            try:
                check_public_skill_event(kind, payload)
            except ValueError:
                raise InvalidPackage("unverified_public_skill_content", spot) from None
            if kind in {"context", "agent_end"}:
                _public_messages(payload.get("messages"), spot)
            elif kind == "provider_request":
                _require(set(payload) == {"messages", "tools", "model"} and isinstance(payload["messages"], list),
                         "invalid_provider_request_fields", spot)
                for message in payload["messages"]:
                    _require(isinstance(message, dict) and message.get("role") in {"user", "assistant", "tool"}
                             and not message.keys() - {"role", "content", "tool_calls", "tool_call_id", "name"},
                             "private_provider_message_field", spot)
                    _require("content" not in message or isinstance(message["content"], str), "nontext_provider_message", spot)
            elif kind == "tool_result":
                content = payload.get("content")
                _require(isinstance(content, list) and all(isinstance(block, dict) and set(block) == {"type", "text"}
                         and block["type"] == "text" and isinstance(block["text"], str) for block in content),
                         "private_or_nontext_tool_receipt", spot)
            elif kind != "tool_call":
                raise InvalidPackage("unknown_episode_event_kind", spot)


def _tool_source(metadata, sample, calls, results, source, origin, location, exported_at):
    _require(source.get("kind") == "pi.training_episode" and source.get("public_episode_complete") is True,
             "tool_source_not_complete_episode", location)
    _require(source.get("complete") is False, "source_claims_global_completeness", location)
    _require(source.get("session_id") == origin.get("session_id") and bool(source.get("session_id")),
             "source_session_mismatch", location)
    episode_id = origin.get("episode_id")
    _require(type(episode_id) is int and episode_id > 0, "missing_source_episode_id", location)
    _require(metadata["event_id"] == _digest(["pi_episode", metadata["tenant_id"], metadata["sid"], episode_id]),
             "episode_identity_hash_mismatch", location)
    events = source.get("events")
    _require(isinstance(events, list) and bool(events), "missing_episode_events", location)
    _require(metadata.get("event_ids") == [event.get("id") for event in events if isinstance(event, dict)],
             "source_event_ids_mismatch", location)
    contexts, requests, endings, observed_calls, observed_results = [], [], [], {}, {}
    prefixes = [sample["messages"][:index] for index, message in enumerate(sample["messages"]) if message["role"] == "assistant"]
    hosted = source.get("runtime_profile") == "pi-0.85.1-hosted-workspace-v1"
    allowed_calls, provider_seen = set(), False
    grant = origin.get("consent", {}).get("granted_at")
    _require(type(grant) in (float, int), "missing_source_grant_time", location)
    schema_by_name = {}
    for index, event in enumerate(events):
        spot = f"{location}/source/{index}"
        _require(isinstance(event, dict) and event.get("id") == _digest(["pi_capture", episode_id, index])
                 and event.get("sequence") == index, "source_event_hash_or_sequence_mismatch", spot)
        observed = event.get("observed_at")
        _require(type(observed) in (float, int) and grant <= observed <= exported_at, "source_precedes_authorization_or_follows_export", spot)
        kind, payload = event.get("kind"), event.get("payload")
        _require(isinstance(payload, dict), "invalid_source_payload", spot)
        if kind == "context":
            _require(set(payload) == {"messages", "tools"}, "invalid_context_fields", spot)
            contexts.append(payload)
            _require(allowed_calls <= observed_results.keys() and len(contexts) <= len(prefixes), "source_turn_order_mismatch", spot)
            following = sample["messages"][len(prefixes[len(contexts) - 1])]
            allowed_calls = {call["id"] for call in following.get("tool_calls", [])}
            provider_seen = False
            _require(isinstance(payload["tools"], list), "missing_runtime_tools", spot)
            if not schema_by_name:
                for tool in payload["tools"]:
                    _require(isinstance(tool, dict) and set(tool) == {"name", "description", "parameters"},
                             "invalid_runtime_tool_schema", spot)
                    name = tool.get("name")
                    _require(isinstance(name, str) and NAME.fullmatch(name) and name not in schema_by_name
                             and isinstance(tool.get("description"), str),
                             "invalid_or_duplicate_runtime_tool_name", spot)
                    _schema(tool["parameters"], spot)
                    schema_by_name[name] = tool
        elif kind == "provider_request":
            _require(set(payload) == {"messages", "tools", "model"} and isinstance(payload["model"], str),
                     "invalid_provider_request_fields", spot)
            requests.append(payload)
            _require(bool(contexts) and not provider_seen, "source_provider_order_mismatch", spot)
            provider_seen = True
        elif kind == "agent_end":
            _require(not payload.keys() - {"messages", "private_blocks_excluded"}, "invalid_agent_end_fields", spot)
            endings.append(_public_messages(payload.get("messages"), spot))
        elif kind in {"tool_call", "tool_result"}:
            expected_fields = {"toolCallId", "toolName", "input"}
            if kind == "tool_result":
                expected_fields |= {"content", "isError", "output_complete"}
            _require(set(payload) == expected_fields, "invalid_tool_receipt_fields", spot)
            key = payload.get("toolCallId")
            _require(isinstance(key, str) and key in calls and key in allowed_calls
                     and (provider_seen or not hosted) and payload["toolName"] == calls[key]["name"],
                     "tool_receipt_identity_mismatch", spot)
            target = observed_calls if kind == "tool_call" else observed_results
            _require(key not in target, "duplicate_tool_receipt", spot)
            runtime_schema = schema_by_name.get(calls[key]["name"], {}).get("parameters")
            _require(runtime_schema is not None, "missing_runtime_tool_definition", spot)
            try:
                expected = pi_execution_arguments(calls[key]["arguments"], runtime_schema)
            except ValueError:
                raise InvalidPackage("unsupported_runtime_argument_conversion", spot) from None
            _require(payload["input"] == expected, "executed_arguments_do_not_match_model_call", spot)
            if kind == "tool_result":
                _require(key in observed_calls and type(payload["isError"]) is bool and payload["output_complete"] is True,
                         "incomplete_or_unordered_tool_receipt", spot)
                content = payload["content"]
                _require(isinstance(content, list) and all(isinstance(block, dict) and set(block) == {"type", "text"}
                         and block["type"] == "text" and isinstance(block["text"], str) for block in content),
                         "private_or_nontext_tool_receipt", spot)
                text = "\n".join(block["text"] for block in content)
                # Pi's provider adapter makes this explicit placeholder for an
                # empty tool result. Preserve the raw receipt; never invent data.
                _require(text == results[key] or (text == "" and results[key] == "(no tool output)"),
                         "tool_output_does_not_match_receipt", spot)
            target[key] = True
        else:
            raise InvalidPackage("unknown_episode_event_kind", spot)
    _require(set(observed_calls) == set(calls) == set(observed_results), "missing_tool_execution_receipts", location)
    _require(len(endings) == 1 and events[-1]["kind"] == "agent_end", "missing_terminal_agent_end", location)
    _require(_wire_messages(endings[0]) == _wire_messages(sample["messages"]), "sample_does_not_match_agent_end", location)
    _require(len(contexts) == len(prefixes), "missing_context_observations", location)
    first = contexts[0]["messages"]
    _require(isinstance(first, list) and len(first) == 1 and isinstance(first[0], dict), "source_not_fresh_public_episode", location)
    stamp = first[0].get("timestamp")
    _require(type(stamp) in (float, int) and grant <= stamp / 1000 <= events[0]["observed_at"],
             "first_user_message_precedes_authorization", location)
    for context, prefix in zip(contexts, prefixes):
        _require(_wire_messages(_public_messages(context["messages"], location)) == _wire_messages(prefix)
                 and context["tools"] == contexts[0]["tools"],
                 "source_context_changed", location)
    if hosted:
        _require(source.get("model_context_complete") is False and source.get("private_blocks_excluded") is True,
                 "missing_private_context_exclusion_label", location)
        _require(len(requests) == len(prefixes), "missing_actual_provider_requests", location)
        for request, prefix in zip(requests, prefixes):
            _require(_wire_messages(request["messages"]) == _wire_messages(prefix) and request["tools"] == sample["tools"],
                     "sample_does_not_match_provider_request", location)
        provider_names = [tool["function"]["name"] for tool in sample["tools"]]
        _require(len(provider_names) == len(set(provider_names)) and set(provider_names) == set(schema_by_name),
                 "runtime_provider_tool_identity_mismatch", location)
        for provider_tool in sample["tools"]:
            function = provider_tool["function"]
            runtime_tool = schema_by_name.get(function["name"])
            _require(runtime_tool is not None and function["description"] == runtime_tool["description"],
                     "runtime_provider_tool_identity_mismatch", location)
            try:
                expected = pi_strict_schema(runtime_tool["parameters"]) if function.get("strict") else runtime_tool["parameters"]
            except ValueError:
                raise InvalidPackage("unsupported_provider_schema_conversion", location) from None
            _require(pi_schema_equal(function["parameters"], expected), "runtime_provider_schema_mismatch", location)
        runtime_metadata = source.get("runtime")
        _require(isinstance(runtime_metadata, dict) and runtime_metadata == metadata.get("runtime") == origin.get("runtime"),
                 "runtime_attribution_mismatch", location)
        _require(runtime_metadata.get("profile") == source["runtime_profile"] == metadata.get("runtime_profile"),
                 "runtime_profile_mismatch", location)
        _require(isinstance(runtime_metadata.get("capture_id"), str) and bool(runtime_metadata["capture_id"])
                 and isinstance(runtime_metadata.get("call_id"), str) and bool(runtime_metadata["call_id"]),
                 "missing_runtime_call_attribution", location)
        _require(metadata.get("task_id") == runtime_metadata.get("mission_id"), "task_attribution_mismatch", location)
        hashes = runtime_metadata.get("source_sha256")
        _require(isinstance(hashes, dict) and bool(hashes) and all(_is_hash(value) for value in hashes.values())
                 and _is_hash(runtime_metadata.get("launch_sha256")), "missing_runtime_source_hashes", location)


def _validate(files, evidence_by_hash):
    manifest = _json(files["manifest.json"], "manifest.json")
    quality = _json(files["quality_report.json"], "quality_report.json")
    _require(isinstance(manifest, dict) and manifest.get("format_version") == "argus-training-package-v1",
             "unsupported_manifest_format", "manifest.json")
    exported_at = manifest.get("created_at")
    _require(type(exported_at) in (float, int), "missing_export_timestamp", "manifest.json")
    listed = manifest.get("files")
    _require(isinstance(listed, dict) and set(listed) == FILES - {"manifest.json"}, "manifest_file_set_mismatch", "manifest.json")
    for name, data in files.items():
        if name == "manifest.json":
            continue
        entry = listed[name]
        _require(isinstance(entry, dict) and entry.get("sha256") == _hash(data) and entry.get("bytes") == len(data),
                 "manifest_hash_or_size_mismatch", name)
    _require(isinstance(quality, dict), "invalid_quality_report", "quality_report.json")
    tables = {name: _rows(data, name) for name, data in files.items() if name.endswith(".jsonl")}
    metadata, origins, trajectories = tables["samples.jsonl"], tables["provenance.jsonl"], tables["trajectories.jsonl"]
    _check_public_sources(trajectories)
    source_by_id, origin_by_id, samples_by_id = {}, {}, {}
    for rows, index, field, name in ((trajectories, source_by_id, "id", "trajectories.jsonl"),
                                     (origins, origin_by_id, "event_id", "provenance.jsonl"),
                                     (metadata, samples_by_id, "event_id", "samples.jsonl")):
        for offset, row in enumerate(rows, 1):
            key = row.get(field)
            _require(_is_hash(key) and key not in index, "missing_or_duplicate_source_identity", f"{name}:{offset}")
            index[key] = row
    _require(all(row.get("disposition") in {"diagnostic", "quarantined"} for row in origins),
             "invalid_provenance_disposition", "provenance.jsonl")
    review = quality.get("review")
    _require(isinstance(review, dict) and review.get("content_approved") is True and manifest.get("content_approved") is True,
             "content_review_missing", "quality_report.json")
    reviewer = review.get("reviewer_kind", "unspecified")
    _require(reviewer in REVIEWERS and manifest.get("reviewer_kind") == reviewer
             and manifest.get("human_reviewed") is (reviewer == "human_operator"), "reviewer_attribution_mismatch", "manifest.json")
    evidence_hash = review.get("evidence_sha256")
    _require(manifest.get("evidence_sha256") == evidence_hash and (evidence_hash is None or _is_hash(evidence_hash)),
             "review_evidence_hash_mismatch", "manifest.json")
    evidence_verified = False
    if reviewer == "automated_acceptance":
        _require(_is_hash(evidence_hash), "automated_review_evidence_missing", "quality_report.json")
        _require(bool(evidence_by_hash), "automated_review_evidence_file_required", "evidence")
        _require(evidence_hash in evidence_by_hash, "evidence_file_hash_mismatch", "evidence")
    if evidence_hash in evidence_by_hash:
        evidence_verified = True
    approved = review.get("approved_event_ids", [])
    _require(isinstance(approved, list) and all(_is_hash(key) and key in samples_by_id for key in approved),
             "invalid_reviewed_event_selection", "quality_report.json")
    purpose = manifest.get("purpose")
    _require(purpose in {"internal_training", "external_sharing"}, "invalid_training_purpose", "manifest.json")
    _require(purpose != "external_sharing" or review.get("rights_reviewed") is True,
             "external_sharing_review_missing", "quality_report.json")
    projects = quality.get("projects")
    _require(isinstance(projects, list), "invalid_project_counts", "quality_report.json")
    project_keys = {(row.get("tenant_id"), row.get("sid")) for row in projects if isinstance(row, dict)}
    expected = Counter(projects=len(projects), eligible_projects=sum(row.get("eligible") is True for row in projects),
                       events=len(trajectories), candidates=len(metadata),
                       quarantined=sum(row.get("disposition") == "quarantined" for row in origins),
                       duplicates=sum(bool(row.get("duplicate_of")) for row in metadata))
    accepted_ids, split_by_tenant, split_by_prompt = set(), {}, {}
    reviewed_tool_samples, automated_sample_evidence_verified = 0, 0
    for index, row in enumerate(metadata, 1):
        spot = f"samples.jsonl:{index}"
        _require(type(row.get("quality_approved")) is bool and _is_hash(row.get("sample_id"))
                 and _is_hash(row.get("split_group")) and row.get("split") in {"train", "validation"},
                 "invalid_sample_metadata", spot)
        _require((row.get("tenant_id"), row.get("sid")) in project_keys, "sample_project_not_selected", spot)
        _require(row.get("sample_complete") is True and row.get("global_complete") is False,
                 "invalid_sample_completeness_claim", spot)
        ids = row.get("event_ids")
        _require(isinstance(ids, list) and bool(ids) and len(ids) == len(set(ids)) and all(_is_hash(key) for key in ids),
                 "invalid_source_event_ids", spot)
        is_tool = source_by_id.get(row["event_id"], {}).get("kind") == "pi.training_episode"
        expected["tool_candidates"] += int(is_tool)
        if row.get("duplicate_of"):
            _require(row["duplicate_of"] in samples_by_id and not row["quality_approved"]
                     and samples_by_id[row["duplicate_of"]]["sample_id"] == row["sample_id"], "invalid_duplicate_reference", spot)
        if row["quality_approved"]:
            _require(row["sample_id"] not in accepted_ids, "duplicate_accepted_sample", spot)
            accepted_ids.add(row["sample_id"])
            expected["sft"] += 1
            expected["tool_sft"] += int(is_tool)
    for split in ("train", "validation"):
        selected = [row for row in metadata if row["quality_approved"] and row["split"] == split]
        hf, portable = tables[f"hf_trl_{split}.jsonl"], tables[f"sft_{split}.jsonl"]
        _require(len(hf) == len(portable) == len(selected) == quality.get(split), "split_row_count_mismatch", f"hf_trl_{split}.jsonl")
        for index, (row, native, standard) in enumerate(zip(selected, hf, portable), 1):
            spot = f"hf_trl_{split}.jsonl:{index}"
            source = source_by_id.get(row["event_id"])
            normalized, calls, results = _sample(native, spot, source=source)
            converted, _, _ = _sample(standard, f"sft_{split}.jsonl:{index}", portable=True, source=source)
            _require(native == normalized and normalized == converted, "portable_hf_semantic_mismatch", spot)
            _require(_digest(native) == row["sample_id"], "sample_content_hash_mismatch", spot)
            evidence_row = row.get("quality_evidence")
            _require(isinstance(evidence_row, dict), "sample_quality_evidence_missing", spot)
            kind = evidence_row.get("kind")
            _require(kind in {"operator_event_review", "operator_tool_context_review", "explicit_task_feedback"},
                     "unknown_quality_evidence_kind", spot)
            if kind == "explicit_task_feedback":
                _require(not calls and type(evidence_row.get("feedback_id")) is int and evidence_row["feedback_id"] > 0,
                         "invalid_task_feedback_evidence", spot)
            else:
                persisted = evidence_row.get("persisted") is True
                _require(evidence_row.get("event_id") == row["event_id"] and (persisted or row["event_id"] in approved),
                         "sample_review_event_mismatch", spot)
                actual_reviewer = evidence_row.get("reviewer_kind")
                actual_evidence = evidence_row.get("evidence_sha256")
                _require(actual_reviewer in REVIEWERS and evidence_row.get("human_reviewed") is (actual_reviewer == "human_operator")
                         and (actual_evidence is None or _is_hash(actual_evidence)), "sample_reviewer_evidence_mismatch", spot)
                if persisted:
                    _require(evidence_row.get("sample_sha256") == row["sample_id"] and type(evidence_row.get("reviewed_at")) in (float, int),
                             "persisted_review_content_binding_mismatch", spot)
                else:
                    _require(actual_reviewer == reviewer and actual_evidence == evidence_hash,
                             "sample_reviewer_evidence_mismatch", spot)
                if actual_reviewer == "automated_acceptance":
                    _require(_is_hash(actual_evidence) and actual_evidence in evidence_by_hash,
                             "sample_acceptance_evidence_file_required", spot)
                    automated_sample_evidence_verified += 1
                if calls and actual_reviewer != "unspecified":
                    reviewed_tool_samples += 1
            origin = origin_by_id.get(row["event_id"])
            _require(isinstance(origin, dict) and origin.get("disposition") != "quarantined"
                     and origin.get("tenant_id") == row["tenant_id"] and origin.get("sid") == row["sid"],
                     "sample_provenance_mismatch", spot)
            consent = origin.get("consent", {})
            _require(consent.get("purpose") == purpose and consent.get("notice_version") == manifest.get("notice_version"),
                     "source_consent_scope_mismatch", spot)
            if evidence_row.get("persisted") is True:
                reviewed_at = evidence_row["reviewed_at"]
                _require(type(consent.get("granted_at")) in (float, int)
                         and consent["granted_at"] <= reviewed_at <= exported_at, "persisted_review_time_mismatch", spot)
            if calls:
                _require(kind == "operator_tool_context_review"
                         and (review.get("tool_context_approved") is True or evidence_row.get("persisted") is True),
                         "tool_context_review_missing", spot)
                source = source_by_id.get(row["event_id"])
                _require(isinstance(source, dict) and source.get("tenant_id") == row["tenant_id"]
                         and source.get("sid") == row["sid"], "tool_source_project_mismatch", spot)
                _tool_source(row, native, calls, results, source, origin, spot, exported_at)
            else:
                source = [source_by_id.get(key) for key in row["event_ids"]]
                _require(len(source) == 2 and all(isinstance(item, dict) for item in source), "chat_source_missing", spot)
                request = next((item for item in source if item.get("kind") == "http.request"), {})
                response = next((item for item in source if item.get("kind") == "http.response"), {})
                _require(request.get("payload", {}).get("input", {}).get("text") == native["messages"][0]["content"]
                         and response.get("payload", {}).get("result", {}).get("reply") == native["messages"][-1]["content"],
                         "chat_text_does_not_match_source", spot)
            tenant, prompt = row["tenant_id"], native["messages"][0]["content"]
            _require(split_by_tenant.setdefault(tenant, split) == split and split_by_prompt.setdefault(prompt, split) == split,
                     "train_validation_group_leakage", spot)
    keys = ("projects", "eligible_projects", "events", "candidates", "sft", "quarantined", "duplicates", "tool_candidates", "tool_sft")
    actual_counts = {key: expected[key] for key in keys}
    _require(isinstance(manifest.get("counts"), dict) and all(type(value) is int and value >= 0 for value in manifest["counts"].values()),
             "invalid_count_types", "manifest.json")
    _require(manifest.get("counts") == quality.get("counts") == actual_counts, "manifest_quality_actual_counts_mismatch", "manifest.json")
    ready = actual_counts["tool_sft"] > 0
    _require(manifest.get("agentic_tool_training_ready") is ready and quality.get("agentic_tool_training_ready") is ready,
             "agentic_status_count_mismatch", "manifest.json")
    status = "reviewed_public_tool_episodes" if ready else "reviewed_chat_samples_only" if actual_counts["sft"] else "no_eligible_sft_samples"
    _require(manifest.get("dataset_status") == quality.get("dataset_status") == status,
             "dataset_status_count_mismatch", "manifest.json")
    return {"counts": actual_counts, "reviewer_kind": reviewer, "human_reviewed": reviewer == "human_operator",
            "evidence_sha256": evidence_hash, "evidence_file_verified": evidence_verified,
            "agentic_training_ready": ready and reviewed_tool_samples == actual_counts["tool_sft"],
            "automated_sample_evidence_verified": automated_sample_evidence_verified,
            "provided_evidence_sha256": sorted(evidence_by_hash),
            "train": quality["train"], "validation": quality["validation"],
            "warnings": ["reviewer_kind_unspecified"] if reviewer == "unspecified" else []}


def validate_package(package, *, evidence_path=None, evidence_paths=()):
    """Validate bytes or a local ZIP path. Return a content-free JSON report."""
    report = {"validator": "argus-training-validator-v1", "valid": False, "agentic_training_ready": False,
              "errors": [], "limitations": [
                  "Checks format, hashes, observed source correspondence and declared review attribution only.",
                  "Does not verify scientific conclusions, mathematical proofs, benchmark claims or licensing.",
                  "Public episodes omit system/private reasoning and are not complete model contexts.",
                  "A self-consistent ZIP is not a cryptographic proof of an untampered runtime or actual human review.",
              ]}
    try:
        if isinstance(package, bytes):
            raw = package
        else:
            with Path(package).open("rb") as stream:
                raw = stream.read(MAX_PACKAGE_BYTES + 1)
        _require(len(raw) <= MAX_PACKAGE_BYTES, "package_size_limit", "package")
        report["package_sha256"] = _hash(raw)
        evidence_by_hash = {}
        paths = [*evidence_paths, *([evidence_path] if evidence_path is not None else [])]
        _require(len(paths) <= 256, "evidence_file_count_limit", "evidence")
        for path in paths:
            with Path(path).open("rb") as stream:
                evidence = stream.read(MAX_EVIDENCE_BYTES + 1)
            _require(len(evidence) <= MAX_EVIDENCE_BYTES, "evidence_size_limit", "evidence")
            evidence_by_hash[_hash(evidence)] = True
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            _require(len(names) == len(set(names)) and set(names) == FILES, "zip_file_set_mismatch", "package")
            _require(sum(entry.file_size for entry in entries) <= MAX_PACKAGE_BYTES
                     and all(not entry.flag_bits & 1 and not entry.is_dir()
                             and stat.S_IFMT(entry.external_attr >> 16) in {0, stat.S_IFREG}
                             and entry.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                             for entry in entries), "zip_capacity_encryption_or_entry_type", "package")
            files = {name: archive.read(name) for name in names}
        report.update(_validate(files, evidence_by_hash))
        report["valid"] = True
    except InvalidPackage as exc:
        report["errors"].append({"code": exc.code, "location": exc.location})
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, zipfile.BadZipFile, zipfile.LargeZipFile):
        report["errors"].append({"code": "malformed_or_unreadable_package", "location": "package"})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence", type=Path, action="append", default=[], help="Exact reviewed evidence file; repeat for samples reviewed against different reports")
    parser.add_argument("--require-agentic", action="store_true", help="Fail if no reviewed tool sample is ready")
    args = parser.parse_args(argv)
    if args.report and any(args.report.resolve() == path.resolve() for path in (args.package, *args.evidence)):
        parser.error("--report must not overwrite an input package or evidence file")
    report = validate_package(args.package, evidence_paths=args.evidence)
    output = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.report.parent, delete=False) as stream:
            stream.write(output)
            temporary = stream.name
        os.replace(temporary, args.report)
    print(output, end="")
    return 0 if report["valid"] and (not args.require_agentic or report["agentic_training_ready"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

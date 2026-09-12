"""Translate the trial's text/tool chat contract to Copilot Responses."""
from __future__ import annotations

import json

from . import MODEL, REASONING_EFFORT
from .store import TrialError


def request_payload(chat: dict) -> dict:
    items = []
    custom_calls = set()
    for message in chat["messages"]:
        role, content = message["role"], message.get("content")
        if isinstance(content, list):
            content = "\n".join(part["text"] for part in content)
        if role == "tool":
            call_id = message["tool_call_id"]
            kind = "custom_tool_call_output" if call_id in custom_calls else "function_call_output"
            items.append({"type": kind, "call_id": call_id,
                          "output": content or ""})
            continue
        if content:
            items.append({"role": role, "content": content})
        for tool in message.get("tool_calls", []):
            if tool["type"] == "custom":
                custom_calls.add(tool["id"])
                items.append({"type": "custom_tool_call", "call_id": tool["id"], **tool["custom"]})
            else:
                items.append({"type": "function_call", "call_id": tool["id"], **tool["function"]})
    payload = {
        "model": chat["model"], "input": items, "stream": chat["stream"],
        "max_output_tokens": chat["max_tokens"], "store": False,
        # Completion validates the requested effort before this wire conversion.
        "reasoning": {"effort": chat.get("reasoning_effort") or REASONING_EFFORT},
    }
    if chat.get("tools"):
        payload["tools"] = [{**tool[tool["type"]], "type": tool["type"]} for tool in chat["tools"]]
        for tool in payload["tools"]:
            if tool["type"] == "custom" and tool.get("format", {}).get("type") == "grammar":
                tool["format"] = {**tool["format"]["grammar"], "type": "grammar"}
    if "tool_choice" in chat:
        choice = chat["tool_choice"]
        payload["tool_choice"] = (
            {"type": choice["type"], "name": choice[choice["type"]]["name"]}
            if isinstance(choice, dict) else choice
        )
    if "parallel_tool_calls" in chat:
        payload["parallel_tool_calls"] = chat["parallel_tool_calls"]
    # Retain the trial's omission of chat sampling/penalty parameters.
    # Model and storage remain server-controlled.
    return payload


def completion(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("status") not in {"completed", "incomplete"}:
        raise TrialError(502, "provider_protocol_error", "Invalid provider completion response.")
    if not isinstance(data.get("output"), list):
        raise TrialError(502, "provider_protocol_error", "Invalid provider completion output.")
    text, calls, refusal = [], [], []
    for item in data["output"]:
        if item.get("type") == "message":
            for part in item["content"]:
                if part["type"] == "output_text":
                    text.append(part["text"])
                elif part["type"] == "refusal":
                    refusal.append(part["refusal"])
        elif item.get("type") == "function_call":
            calls.append({"id": item["call_id"], "type": "function",
                          "function": {"name": item["name"], "arguments": item["arguments"]}})
        elif item.get("type") == "custom_tool_call":
            calls.append({"id": item["call_id"], "type": "custom",
                          "custom": {"name": item["name"], "input": item["input"]}})
    message = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = calls
    if refusal:
        message["refusal"] = "".join(refusal)
    finish = "length" if data["status"] == "incomplete" else "tool_calls" if calls else "stop"
    result = {
        "id": data["id"], "object": "chat.completion", "created": data["created_at"], "model": MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    usage = data.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {
            "prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "prompt_tokens_details": usage.get("input_tokens_details", {}),
            "completion_tokens_details": usage.get("output_tokens_details", {}),
        }
    return result


async def chat_chunks(response, limit: int):
    """Stream text immediately; deliver complete local tool calls at completion.

    Only terminal Responses events produce [DONE]. Interrupted/error streams
    cannot release token reservations, even if an earlier event reported usage.
    """
    lines = []
    identity = {}
    sent_text = False
    async for line in response.aiter_lines():
        if len(line) + sum(map(len, lines)) > limit:
            raise TrialError(502, "provider_protocol_error", "Provider event is too large.")
        if line.startswith("data:"):
            lines.append(line[5:].lstrip())
        elif not line and lines:
            raw, lines = "\n".join(lines), []
            if raw == "[DONE]":
                break  # A terminal response with usage is required first.
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "response.created":
                data = event["response"]
                identity = {"id": data["id"], "created": data["created_at"]}
            elif kind == "response.output_text.delta":
                sent_text = True
                yield {**identity, "object": "chat.completion.chunk", "model": MODEL,
                       "choices": [{"index": 0, "delta": {"content": event["delta"]}, "finish_reason": None}]}
            elif kind in {"response.completed", "response.incomplete"}:
                result = completion(event["response"])
                choice = result["choices"][0]
                delta = choice.pop("message")
                if sent_text:
                    delta.pop("content", None)
                for index, tool in enumerate(delta.get("tool_calls", [])):
                    tool["index"] = index
                choice["delta"] = delta
                result["object"] = "chat.completion.chunk"
                yield result
                yield None
                return
            elif kind in {"error", "response.failed"}:
                raise TrialError(502, "provider_stream_failed", "Trial provider stream failed.")
    raise TrialError(502, "provider_stream_incomplete", "Provider stream ended early; reservation retained.")

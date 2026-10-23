"""A local model endpoint that answers with a fixed list of tool calls.

Real agent CLIs (Pi, Copilot, Claude Code, Codex) are pointed at it so a test
can make them call a tool with chosen arguments and then read back what the
tool returned, without credentials or network. Each request answers with the
next scripted call, counted by the tool results already in the conversation;
when the script is spent it answers ``finished``. Three wire formats:
OpenAI chat completions (Pi, Copilot), OpenAI Responses (Codex) and Anthropic
Messages (Claude Code).
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class ScriptedProvider:
    def __init__(self, script: list[tuple[str, dict[str, Any]]]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_GET(self) -> None:  # model listing
                self._json({"data": [{"id": "scripted", "object": "model"}]})

            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                provider.requests.append({"path": self.path, "body": body})
                path = self.path.partition("?")[0]
                if path.endswith("/count_tokens"):
                    self._json({"input_tokens": 1})
                elif path.endswith("/messages"):
                    provider._anthropic(self, body)
                elif path.endswith("/responses"):
                    provider._responses(self, body)
                else:
                    provider._chat(self, body)

            def _json(self, value: Any) -> None:
                data = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self) -> "ScriptedProvider":
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def tool_results(self) -> list[str]:
        """Every tool result the CLI sent back, as text, in order (the longest
        conversation carries them all)."""
        best: list[str] = []
        for request in self.requests:
            body = request["body"]
            items: list[Any] = []
            for message in body.get("messages") or []:
                if message.get("role") == "tool":
                    items.append(message.get("content"))
                elif isinstance(message.get("content"), list):
                    items += [c.get("content") for c in message["content"] if c.get("type") == "tool_result"]
            items += [i.get("output") for i in body.get("input") or [] if isinstance(i, dict)
                      and i.get("type") in {"function_call_output", "custom_tool_call_output"}]
            if len(items) > len(best):
                best = [item if isinstance(item, str) else json.dumps(item) for item in items]
        return best

    def _next(self, done: int) -> tuple[str, dict[str, Any]] | None:
        return self.script[done] if done < len(self.script) else None

    @staticmethod
    def _sse(handler: BaseHTTPRequestHandler, events: list[tuple[str | None, dict[str, Any]]]) -> None:
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.end_headers()
        for name, data in events:
            prefix = f"event: {name}\n" if name else ""
            handler.wfile.write(f"{prefix}data: {json.dumps(data)}\n\n".encode())
        if not events or events[0][0] is None:
            handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()

    def _chat(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> None:
        done = sum(1 for m in body.get("messages") or [] if m.get("role") == "tool")
        call = self._next(done)
        base = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "scripted"}
        if call:
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call_{done}", "type": "function",
                                                          "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}
            finish = "tool_calls"
        else:
            delta, finish = {"role": "assistant", "content": "finished"}, "stop"
        self._sse(handler, [
            (None, {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
            (None, {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
        ])

    def _responses(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> None:
        done = sum(1 for i in body.get("input") or [] if isinstance(i, dict)
                   and i.get("type") in {"function_call_output", "custom_tool_call_output"})
        call = self._next(done)
        if call:
            item = {"type": "function_call", "id": f"fc_{done}", "call_id": f"call_{done}",
                    "name": call[0], "arguments": json.dumps(call[1]), "status": "completed"}
        else:
            item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": "finished", "annotations": []}]}
        response = {"id": "resp_1", "object": "response", "status": "in_progress", "output": [], "model": "scripted"}
        usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                 "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
        self._sse(handler, [
            ("response.created", {"type": "response.created", "response": response}),
            ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": item}),
            ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
            ("response.completed", {"type": "response.completed",
                                    "response": {**response, "status": "completed", "output": [item], "usage": usage}}),
        ])

    def _anthropic(self, handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> None:
        done = sum(1 for m in body.get("messages") or [] if isinstance(m.get("content"), list)
                   for c in m["content"] if c.get("type") == "tool_result")
        # Side requests (titles, summaries) carry no tools; they get plain text.
        call = self._next(done) if body.get("tools") else None
        message = {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"),
                   "content": [], "stop_reason": None, "stop_sequence": None,
                   "usage": {"input_tokens": 1, "output_tokens": 1}}
        if call:
            block = {"type": "tool_use", "id": f"toolu_{done}", "name": call[0], "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(call[1])}
            stop = "tool_use"
        else:
            block = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": "finished"}
            stop = "end_turn"
        self._sse(handler, [
            ("message_start", {"type": "message_start", "message": message}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": block}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": delta}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ])

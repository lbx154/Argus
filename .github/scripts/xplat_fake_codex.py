"""A credential-free stand-in for the ``codex`` CLI (xplat-dogfood workflow).

Argus drives a real backend CLI as a child process: ``codex exec --json ... -``
with the prompt on stdin, JSONL events on stdout. This script speaks that
protocol so Argus can be used end to end on CI machines without a model
account. It recognises the contract a prompt asks for (Manager front door,
chat reply, planner, engineer, reviewer, ...) by marker text and returns a
plausible reply in that contract; when the role has tools it also does the
work (writes and runs ``hello.py``) in the working directory.

Every call is logged to ``$FAKE_CODEX_LOG_DIR`` (prompt, argv, reply) so the
run can be inspected afterwards. Never raises: an unknown prompt still gets a
short reply so the caller's parser, not this stub, decides what happens.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

VERSION = "codex-cli 0.144.5"


def _log_dir() -> Path | None:
    raw = os.environ.get("FAKE_CODEX_LOG_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def _emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _option(argv: list[str], *names: str) -> str:
    for index, value in enumerate(argv):
        if value in names and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def _schema_example(schema: dict, defs: dict | None = None) -> object:
    """Build a small instance that satisfies a JSON Schema (best effort)."""
    defs = defs if defs is not None else schema.get("$defs", {}) or schema.get("definitions", {})
    if "$ref" in schema:
        ref = str(schema["$ref"]).rsplit("/", 1)[-1]
        return _schema_example(defs.get(ref, {}), defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            return _schema_example(schema[key][0], defs)
    if schema.get("allOf"):
        merged: dict = {}
        for part in schema["allOf"]:
            merged.update(part)
        return _schema_example(merged, defs)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0] if kind else "string")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {}) or {}
        return {name: _schema_example(sub, defs) for name, sub in props.items()}
    if kind == "array":
        items = schema.get("items", {}) or {}
        count = max(1, int(schema.get("minItems", 1) or 1))
        return [_schema_example(items, defs) for _ in range(count)]
    if kind == "integer":
        return int(schema.get("minimum", 1) or 1)
    if kind == "number":
        return float(schema.get("minimum", 1) or 1)
    if kind == "boolean":
        return True
    text = "fake summary"
    min_len = int(schema.get("minLength", 0) or 0)
    if min_len > len(text):
        text = text + "." * (min_len - len(text))
    return text


def _message_after(prompt: str, marker: str) -> str:
    index = prompt.rfind(marker)
    if index < 0:
        return ""
    tail = prompt[index + len(marker):]
    return tail.split("\n\nDecide now.", 1)[0].strip()


def _run_python(target: Path) -> str:
    try:
        proc = subprocess.run([sys.executable, str(target)], capture_output=True, text=True,
                              timeout=60, cwd=str(target.parent))
        return (proc.stdout or proc.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return f"run failed: {exc!r}"


def _do_hello_work(workdir: Path, name: str = "hello.py", text: str = "hi") -> str:
    """The engineer actually writes and runs the script when asked to."""
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / name
    target.write_text(f"print({text!r})\n", encoding="utf-8")
    return _run_python(target)


def _workdir_from_prompt(prompt: str, fallback: Path) -> Path:
    match = re.search(r"^- Workdir: `([^`]+)`", prompt, re.M)
    return Path(match.group(1)) if match else fallback


def _mcp_servers(argv: list[str]) -> dict[str, dict]:
    """Collect ``-c mcp_servers.<name>.<key>=<toml>`` overrides like codex does."""
    import tomllib

    servers: dict[str, dict] = {}
    for index, value in enumerate(argv[:-1]):
        if value != "-c":
            continue
        raw = argv[index + 1]
        if not raw.startswith("mcp_servers.") or "=" not in raw:
            continue
        key, _, toml_value = raw.partition("=")
        parts = key.split(".")
        if len(parts) < 3:
            continue
        try:
            parsed = tomllib.loads(f"v = {toml_value}")["v"]
        except Exception:  # noqa: BLE001
            continue
        entry = servers.setdefault(parts[1], {})
        if parts[2] == "env" and len(parts) >= 4:
            entry.setdefault("env", {})[parts[3]] = parsed
        else:
            entry[parts[2]] = parsed
    return servers


def _mcp_call(server: dict, tool: str, arguments: dict, trace: list) -> dict:
    """Minimal stdio MCP client: initialize, list tools, call one tool."""
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (server.get("env") or {}).items()})
    cmd = [str(server.get("command") or ""), *[str(a) for a in server.get("args") or []]]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, text=True, encoding="utf-8", errors="replace")
    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
    def receive(want: int) -> dict:
        while True:
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server closed stdout; stderr={proc.stderr.read()[-2000:]!r}")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == want:
                return message
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "fake-codex", "version": "0"}}})
        trace.append({"initialize": receive(1).get("result", {}).get("serverInfo")})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = receive(2).get("result", {}).get("tools", [])
        trace.append({"tools": [t.get("name") for t in tools]})
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
        result = receive(3)
        trace.append({"call": tool, "result": result})
        return result
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


def _review(prompt: str, argv: list[str], workdir: Path) -> tuple[str, str]:
    servers = _mcp_servers(argv)
    server = servers.get("argus_review_actions")
    workdir = _workdir_from_prompt(prompt, workdir)
    contract = prompt.split("Task objective:", 1)[-1][:1500]
    named = re.search(r"\b(\w+)\.py\b", contract)
    hello = workdir / (named.group(1) + ".py" if named else "hello.py")
    if hello.exists():
        output = _run_python(hello)
        ok = bool(output) and "Traceback" not in output
        review = (f"I ran {hello} myself and it printed {output!r}; the request is met."
                  if ok else f"{hello} exists but printed {output!r}; fix it.")
    else:
        ok = named is None
        review = ("The work described in the task is in place; nothing else is required." if ok
                  else f"{hello.name} was not found in {workdir}; write it and run it.")
    tool = "approve_review" if ok else "revise_review"
    _TRACE.append({"tool_call": tool, "review": review})
    if server is None:
        return "reviewer-no-mcp", review
    try:
        result = _mcp_call(server, tool, {"review": review, "forward_progress": ok}, _TRACE)
    except Exception as exc:  # noqa: BLE001
        return "reviewer-mcp-error", f"{review} (review tool failed: {exc!r})"
    return "reviewer", f"Submitted {tool}. {json.dumps(result.get('result', result))[:300]}"


def _engineer(prompt: str, argv: list[str], workdir: Path) -> tuple[str, str]:
    workdir = _workdir_from_prompt(prompt, workdir)
    contract = ""
    for marker in ("## Mission contract", "## Current mission task", "## Original operator request"):
        if marker in prompt:
            contract = prompt.split(marker, 1)[1][:2000]
            break
    named = re.search(r"\b(\w+)\.py\b", contract)
    if named:
        name = named.group(1) + ".py"
        request = prompt.split("## Original operator request", 1)[-1][:2000]
        pattern = re.compile(r"\bprints? [\"'`]?([\w ]+?)[\"'`]?(?:[,.;]| and | then |$)", re.I | re.M)
        said = pattern.search(request) or pattern.search(contract)
        text = said.group(1).strip() if said else "hi"
        output = _do_hello_work(workdir, name, text)
        _TRACE.append({"command": f"python {name} -> {output!r}", "workdir": str(workdir)})
        result = f"Wrote {workdir / name}; running it printed {output!r}."
    else:
        result = "Inspected the workspace; no file change was needed for this step."
    footer = _decision_footer(prompt)
    lines = [line for line in footer.splitlines() if not line.startswith("RESULT=")] if footer else [
        "MILESTONE_STATUS=done", "NEXT_OWNER=reviewer"]
    lines.insert(1, f"RESULT={result}")
    return "engineer", f"{result}\nDecision:\n" + "\n".join(lines)


_TRACE: list = []


def _reply_for(prompt: str, argv: list[str], workdir: Path) -> tuple[str, str]:
    """Return (kind, reply text) for the contract the prompt asks for."""
    schema_path = _option(argv, "--output-schema")
    if schema_path:
        try:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            return "native-schema", json.dumps(_schema_example(schema), ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return "native-schema-error", json.dumps({"error": repr(exc)})

    tools_on = "read-only" not in argv and "--ephemeral" not in argv
    if prompt.startswith("Classify only.") or "Classify only.\nINTAKE_TYPE" in prompt:
        message = _message_after(prompt, "Message:\n")
        is_task = bool(re.search(r"hello\.py|write|implement|create|build|run it|写|实现", message, re.I))
        greeting = not is_task and bool(re.search(r"^(你好|hello|hi|hey)\b", message.strip(), re.I))
        if is_task:
            route, mode, reply, lifetime, name = "TEAM", "NONE", "NONE", "BOUNDED", "hello.py demo"
        else:
            route, mode, lifetime, name = "SELF", "REPLY", "NONE", "NONE"
            reply = ("你好！我是 Argus（离线测试替身）。可以试试：1) 写一个 hello.py；"
                     "2) 看看项目状态；3) 调整设置。") if greeting else "Fake backend reply: noted."
        lines = [
            "INTAKE_TYPE: EPHEMERAL", "INTAKE_SCOPE: PROJECT", "INTAKE_ROLES: ALL",
            "PREFERENCE_KIND: NONE", "PREFERENCE_VALUE: NONE", "REVOKE_REVISION: NONE",
            "CONFIG: NONE", "CONTROL: NONE", "AUTHORIZATION: NONE", "STEER_DIRECTIVE: NONE",
            "OPERATOR_QUESTION_POLICY: unchanged", f"ROUTE: {route}", f"SELF_MODE: {mode}",
            f"REPLY: {reply}", f"LIFETIME: {lifetime}",
            f"GREETING: {'GREETING' if greeting else 'NONE'}", f"NAME: {name}",
            "DOMAIN_ACTION: NONE", "SKILL_VERTICAL: NONE", "LOOKUP_SUBJECT: NONE",
        ]
        return "front-door", "\n".join(lines)
    if "Reply with exactly one word: STEER or SELF" in prompt:
        return "steer-confirm", "SELF"
    if "mcp_servers.argus_review_actions.command" in " ".join(argv) or "## Submit your review" in prompt:
        return _review(prompt, argv, workdir)
    if prompt.startswith("You are Argus Manager reporting an already-completed project"):
        return "completion-report", ("The work is finished: the script was written in the project folder, "
                                     "the reviewer ran it and saw the expected output.")
    if prompt.startswith("You are Argus Manager"):
        return "manager-chat", ("Hello! This is Argus answering through an offline test backend. "
                                "I can write and run a small script, show project status, or change settings.")
    if prompt.startswith("You are the persistent project Manager, supervising"):
        refs = re.findall(r'"path": "([^"]+)"', prompt.split('"evidence_refs"', 1)[-1])[:1]
        return "supervision", ("ACTION: CONTINUE\nREASON: The team is working on the requested change and "
                               "the next round should show the result.\nEVIDENCE_REFS: " + "; ".join(refs))
    if "looking back on a task that just finished" in prompt:
        return "reflection", "Nothing new worth keeping from this small task.\nWROTE: nothing"
    if "Manager chooses ADVANCE, HOLD, ROLLBACK, or COMPLETE" in prompt:
        return "stage-decision", ("The reviewer ran the program and it met the request.\nDecision:\n"
                                  "ACTION=complete\nTARGET_STAGE=delivery\n"
                                  "REASON=The reviewer confirmed the program prints the expected text, "
                                  "so the request is finished.")
    if prompt.startswith("Plan the Manager's brief as a small executable DAG"):
        brief = _message_after(prompt, "Manager's brief:\n").split("\n\n", 1)[0].strip()
        brief = " ".join(brief.split()) or "the requested change"
        name = (re.search(r"\b(\w+\.py)\b", brief) or [None, "hello.py"])[1]
        return "planner", (
            "Two steps: write the script, then run it and check the output.\nDecision:\n"
            f"PLAN_REASON=I split the request into writing {name} and then running it to check the output.\n"
            f"TASK_KEY=write\nTASK_DEPS=\nTASK_TITLE=Write {name}\n"
            f"TASK_OBJECTIVE=Create {name} in the workspace as asked: {brief}. Check: the file exists.\n"
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true\n"
            f"TASK_KEY=run\nTASK_DEPS=write\nTASK_TITLE=Run {name}\n"
            f"TASK_OBJECTIVE=Run {name} with Python and confirm its output matches the request. "
            "Check: compare the output with the request.\n"
            "TASK_REQUIRE_INDEPENDENT_REVIEW=true")
    if ("You are resuming your own Planner session" in prompt or prompt.startswith("## Reality check")
            or "could not act on your previous Planner conclusion" in prompt):
        named = re.search(r"\b(\w+\.py)\b", prompt)
        workdir = _workdir_from_prompt(prompt, workdir)
        if named is None or (workdir / named.group(1)).exists():
            return "campaign-planner", ("The requested script is written and was run successfully.\nDecision:\n"
                                        "PROJECT_DONE=true\nREASON=The script exists and prints the requested "
                                        "text, so the request is finished.")
        name = named.group(1)
        return "campaign-planner", ("One step remains.\nDecision:\nPROJECT_DONE=false\n"
                                    f"REASON=I will have {name} written and run next.\n"
                                    f"TASK_KEY=k1\nTASK_DEPS=\nTASK_TITLE=Write and run {name}\n"
                                    f"TASK_OBJECTIVE=Write {name} as the operator asked and run it.")
    if tools_on and prompt.startswith("## Task authority") and "## Submit your review" not in prompt:
        return _engineer(prompt, argv, workdir)
    footer = _decision_footer(prompt)
    if footer and prompt.startswith("Choose VERTICAL and, independently, WORKFLOW"):
        task = _message_after(prompt, "[CURRENT OPERATOR MESSAGE]\n").split("\n\n", 1)[0].strip()
        if not task:
            task = _message_after(prompt, "## Task\n").split("\n\n", 1)[0].strip()
        footer += "\nEXECUTION_TASK=" + " ".join(task.split())
    if "WORKFLOW_MODE=direct" in footer:
        task = (_message_after(prompt, "[CURRENT OPERATOR MESSAGE]\n")
                or _message_after(prompt, "## Task\n")).split("\n\n", 1)[0]
        if re.search(r"\bplan\b|staged|分步|计划", task, re.I):
            footer = footer.replace("WORKFLOW_MODE=direct", "WORKFLOW_MODE=staged")
    if footer:
        return "decision-footer", "Fake backend reasoning: following the requested shape.\nDecision:\n" + footer
    return "unknown", "Fake backend reply: acknowledged."


_FOOTER_MARK = "(replace examples; omit unused lines):\nDecision:\n"
_KEY_LINE = re.compile(r"^[A-Z][A-Z0-9_]*\s*[:=]")


def _decision_footer(prompt: str) -> str:
    """Echo the example decision block the prompt itself shows."""
    index = prompt.rfind(_FOOTER_MARK)
    if index < 0:
        return ""
    lines = []
    for line in prompt[index + len(_FOOTER_MARK):].splitlines():
        if not _KEY_LINE.match(line.strip()):
            break
        lines.append(line.strip())
    return "\n".join(lines)


def _exec(argv: list[str]) -> int:
    prompt = sys.stdin.read() if "-" in argv else ""
    workdir = Path(_option(argv, "-C") or os.getcwd())
    started = time.time()
    try:
        kind, reply = _reply_for(prompt, argv, workdir)
    except Exception as exc:  # noqa: BLE001
        kind, reply = "stub-error", f"Fake backend error: {exc!r}"
    thread_id = str(uuid.uuid4())
    _emit({"type": "thread.started", "thread_id": thread_id})
    _emit({"type": "turn.started"})
    for number, entry in enumerate(_TRACE, start=1):
        if "command" in entry:
            item = {"id": f"cmd_{number}", "type": "command_execution", "command": entry["command"],
                    "aggregated_output": "", "exit_code": 0, "status": "completed"}
            _emit({"type": "item.started", "item": {**item, "status": "in_progress"}})
            _emit({"type": "item.completed", "item": item})
    _emit({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": reply}})
    _emit({"type": "turn.completed", "usage": {"input_tokens": len(prompt) // 4, "cached_input_tokens": 0,
                                               "output_tokens": len(reply) // 4}})
    log_dir = _log_dir()
    if log_dir is not None:
        stamp = f"{time.strftime('%H%M%S')}-{os.getpid()}-{kind}"
        try:
            (log_dir / f"{stamp}.json").write_text(json.dumps({
                "kind": kind, "argv": argv, "cwd": os.getcwd(), "workdir": str(workdir),
                "seconds": round(time.time() - started, 3), "reply": reply, "trace": _TRACE,
                "prompt": prompt,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("--version", "-V", "version"):
        print(VERSION)
        return 0
    if argv[:2] == ["login", "status"]:
        print("Logged in using an API key - fake")
        return 0
    if argv[0] == "exec":
        return _exec(argv[1:])
    print(f"fake codex: unsupported command {argv!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

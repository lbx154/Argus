#!/usr/bin/env python3
"""LIVE evaluation harness for fiction_writing (P3).

Real model calls via the local coproxy endpoint ($ANTHROPIC_BASE_URL). Measures
the intelligence layer; deterministic guarantees live in the pytest suite.

Fidelity (why this is the REAL chain, not a bypass prototype):
  ROUTING reuses Argus's real `Manager.decide_vertical` →
  `build_vertical_decision_prompt` → `parse_vertical_decision` → `VerticalDecision`.
  Only the TRANSPORT is swapped: `CoproxyRunner.run_exec` drives a bounded,
  READ-ONLY tool-using loop (Anthropic tools API: a whitelisted `bash` the model
  uses to investigate the REAL repo) against coproxy — mirroring what
  AgentCliBackend does with a real CLI, minus the heavyweight subprocess. This is
  necessary because the decision prompt is a GROUNDED call: a plain completion
  emits tool calls it cannot run and never reaches a parseable decision.

Reporting discipline: temperature pinned to 0; every case's input, raw output,
parsed value and pass/fail saved to reports/. Reported as "fixed-sample
single-run k/N", never generalized to an accuracy.

Usage:
  python -m argus_skill.verticals.fiction_writing.evaluations.run_evals all|routing|reviewer|demo
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from argus_skill.core.models import RunnerResult
from argus_skill.manager import Manager
from argus_skill.skills.vertical_select import VERTICAL_PURPOSES
from argus_skill.verticals.fiction_writing.state import apply_patch, validate_state

BASE = os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8536").rstrip("/")
ROUTER_MODEL = os.environ.get("FW_EVAL_ROUTER_MODEL", "claude-haiku-4.5")
JUDGE_MODEL = os.environ.get("FW_EVAL_JUDGE_MODEL", "claude-sonnet-4.6")
TEMPERATURE = 0
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]          # …/argus-skill (for grounded investigation)
REPORTS = HERE / "reports"
REPORTS.mkdir(exist_ok=True)


def _artifacts_dir() -> Path:
    """FAILED-demo artifact dir, resolved at CALL time so FW_EVAL_ARTIFACTS set at run
    time is honored. Repo-external by default so it never enters the fiction PR diff."""
    return Path(os.environ.get("FW_EVAL_ARTIFACTS") or (Path(tempfile.gettempdir()) / "fw_eval_artifacts"))
CASES = json.loads((HERE / "continuation_cases.json").read_text(encoding="utf-8"))

_ALLOWED_VERBS = {
    "ls", "find", "cat", "head", "tail", "grep", "pwd", "wc", "tree", "sed",
    "awk", "echo", "test", "file", "stat", "sort", "uniq", "cut", "basename",
    "dirname", "rg",
}
_FORBIDDEN = ("rm", "mv ", " cp ", "chmod", "chown", "dd ", "mkfifo", "curl",
              "wget", " nc ", "python", "perl", "ruby", "node", "eval", "tee",
              "truncate", "-i ", "git ", "pip", "apt", "sudo", "ln ", "touch",
              "mkdir", ">>", ":(){")


def safe_bash(cmd: str, cwd: Path) -> str:
    """Run a READ-ONLY command for the model's repo investigation, whitelisted."""
    cmd = (cmd or "").strip()
    if not cmd:
        return "blocked: empty"
    first = cmd.split()[0]
    if first not in _ALLOWED_VERBS:
        return f"blocked: '{first}' not in read-only allowlist"
    scan = cmd.replace("2>/dev/null", "").replace(">/dev/null", "")
    for bad in _FORBIDDEN:
        if bad in scan:
            return f"blocked: contains {bad!r}"
    if ">" in scan:
        return "blocked: output redirection not allowed"
    try:
        out = subprocess.run(
            cmd, shell=True, cwd=str(cwd), capture_output=True, text=True
        )
        return (out.stdout + out.stderr)[:4000] or "(no output)"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def _post_raw(model: str, messages: list[dict], *, system: str | None = None,
              tools: list | None = None, tool_choice: dict | None = None,
              max_tokens: int = 1200) -> dict:
    payload = {"model": model, "max_tokens": max_tokens,
               "temperature": TEMPERATURE, "messages": messages}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice
    req = urllib.request.Request(
        f"{BASE}/v1/messages", data=json.dumps(payload).encode(),
        headers={"content-type": "application/json",
                 "anthropic-version": "2023-06-01",
                 "authorization": "Bearer placeholder"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e), "content": [{"type": "text", "text": f"<transport-error: {e}>"}]}


def call(model: str, system: str, user: str, max_tokens: int = 1600) -> str:
    d = _post_raw(model, [{"role": "user", "content": user}], system=system, max_tokens=max_tokens)
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")


_BASH_TOOL = {
    "name": "bash",
    "description": "Run a READ-ONLY shell command (ls/find/cat/grep/head/wc) to "
                   "investigate the repository before deciding.",
    "input_schema": {"type": "object",
                     "properties": {"command": {"type": "string"}},
                     "required": ["command"]},
}


class CoproxyRunner:
    """Transport-only backend satisfying RunnerBackend.run_exec, but driving a
    bounded READ-ONLY tool loop against coproxy (real prompt/parser stay Argus).
    ``investigate_dir`` is where the model's bash runs (the real repo).

    The LAST round is a forced FINISH step: the bash tool is withdrawn and the
    model is told its investigation budget is spent, so it must emit the decision
    JSON from what it already learned instead of investigating forever. Without
    this a model that keeps calling tools until ``max_rounds`` leaves the loop
    mid-investigation, and the "final message" is tool chatter with no decision —
    a harness dead-end, not a real routing failure. A real CLI backend likewise
    stops and answers; this mirrors that finish.
    """

    def __init__(self, model: str, investigate_dir: Path, max_rounds: int = 8) -> None:
        self.model = model
        self.investigate_dir = investigate_dir
        self.max_rounds = max_rounds
        self.last_raw = ""
        self.rounds_used = 0

    def run_exec(self, *, prompt: str, options, run_label: str,
                 resume_thread_id: str | None = None) -> RunnerResult:
        model = getattr(options, "model", None) or self.model
        messages: list[dict] = [{"role": "user", "content": prompt}]
        final_text = ""
        for rnd in range(1, self.max_rounds + 1):
            self.rounds_used = rnd
            last_round = rnd == self.max_rounds
            # Final round: withdraw the tool so the model MUST decide now.
            tools = None if last_round else [_BASH_TOOL]
            resp = _post_raw(model, messages, tools=tools, max_tokens=1500)
            content = resp.get("content", [])
            messages.append({"role": "assistant", "content": content})
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            if text:
                final_text = text  # keep the last NON-empty text (the decision)
            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if last_round or resp.get("stop_reason") != "tool_use" or not tool_uses:
                break
            results: list[dict] = []
            for tu in tool_uses:
                out = safe_bash(str(tu.get("input", {}).get("command", "")), self.investigate_dir)
                results.append({"type": "tool_result", "tool_use_id": tu.get("id"),
                                "content": out})
            # If the NEXT round is the forced-finish round, tell the model to stop.
            if rnd + 1 == self.max_rounds:
                results.append({"type": "text",
                                "text": "Investigation budget is exhausted. In your next "
                                        "message reply with ONLY the JSON decision object "
                                        "(no tool calls, no prose)."})
            messages.append({"role": "user", "content": results})
        self.last_raw = final_text or json.dumps(messages[-1])[:1500]
        return RunnerResult(exit_code=0, agent_messages=[final_text], thread_id="coproxy-eval")


# --------------------------------------------------------------------------- #
# routing eval (issue #2) — REAL Manager.decide_vertical chain w/ grounded tools
# --------------------------------------------------------------------------- #
_ROUTING_TASKS = [
    ("写一个中文都市悬疑短篇", "fiction_writing"),
    ("Continue this English fantasy chapter: the gate had not been opened in a hundred years.", "fiction_writing"),
    ("根据已有设定续写下一章", "fiction_writing"),
    ("把这段故事重写得更紧张一些", "fiction_writing"),
    ("做一份中国网络文学研究综述", "research"),
    ("总结五篇关于叙事视角的论文", "research"),
    ("调研生成式 AI 对出版行业的影响并写成报告", "research"),
]


def eval_routing() -> None:
    print(f"\n=== ROUTING EVAL — REAL chain + grounded tools "
          f"(model={ROUTER_MODEL}, temp={TEMPERATURE}) ===")
    print("    Manager.decide_vertical + build_vertical_decision_prompt + "
          "parse_vertical_decision; transport=coproxy; read-only bash on the real repo")
    records, ok = [], 0
    for task, expected in _ROUTING_TASKS:
        runner = CoproxyRunner(ROUTER_MODEL, investigate_dir=REPO_ROOT)
        with tempfile.TemporaryDirectory() as d:   # throwaway: catches any writes
            mgr = Manager(project_root=d, runner=runner)
            got, err = None, None
            try:
                got = mgr.decide_vertical(task).vertical
            except Exception as e:  # noqa: BLE001 — real fail-hard path
                err = str(e)
        good = (got == expected)
        ok += good
        records.append({"task": task, "expected": expected, "parsed_vertical": got,
                        "rounds": runner.rounds_used, "error": err,
                        "raw_output": runner.last_raw[:1500], "pass": good})
        print(f"  [{'PASS' if good else 'FAIL'}] want={expected:<15} got={str(got):<15} "
              f"rounds={runner.rounds_used} :: {task[:36]}")
    (REPORTS / "routing_run.json").write_text(
        json.dumps({"model": ROUTER_MODEL, "temperature": TEMPERATURE,
                    "ts": int(time.time()), "records": records}, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    print(f"  fixed-sample single run: {ok}/{len(_ROUTING_TASKS)} "
          f"(NOT an overall accuracy). raw+parsed saved -> reports/routing_run.json")


def eval_routing_menu() -> None:
    """MENU-DISCRIMINATION PROBE (explicitly PARTIAL). Uses Argus's REAL
    VERTICAL_PURPOSES menu but a direct single-shot decision (NO repo grounding,
    NO tool loop, NOT the production decide_vertical chain). It answers only:
    given the real menu text, does a model separate fiction from a
    literature-review/research task? Do not read this as production routing
    accuracy — the full grounded chain is measured (and shown to need the heavy
    backend) in eval_routing()."""
    menu = "\n".join(f"  - {k}: {v}" for k, v in VERTICAL_PURPOSES.items())
    system = ("You are Argus's Manager. From the menu, pick the single best "
              'vertical id for the task. Return ONLY JSON: {"vertical":"<id>"}.')
    print(f"\n=== ROUTING — MENU-DISCRIMINATION PROBE (PARTIAL; model={ROUTER_MODEL}, "
          f"temp={TEMPERATURE}) ===")
    print("    real VERTICAL_PURPOSES menu; direct decision; NO tools / NO grounding")
    records, ok = [], 0
    for task, expected in _ROUTING_TASKS:
        raw = call(ROUTER_MODEL, system, f"MENU:\n{menu}\n\nTASK: {task}\n\nJSON only.", 120)
        got = str((extract_json(raw) or {}).get("vertical", "")).strip() or None
        good = (got == expected)
        ok += good
        records.append({"task": task, "expected": expected, "parsed_vertical": got,
                        "raw_output": raw[:400], "pass": good})
        print(f"  [{'PASS' if good else 'FAIL'}] want={expected:<15} got={str(got):<15} "
              f":: {task[:40]}")
    (REPORTS / "routing_menu_probe.json").write_text(
        json.dumps({"model": ROUTER_MODEL, "temperature": TEMPERATURE, "kind": "menu_probe_partial",
                    "ts": int(time.time()), "records": records}, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    print(f"  fixed-sample single run: {ok}/{len(_ROUTING_TASKS)} "
          f"(PARTIAL menu-discrimination signal, NOT production routing). "
          f"saved -> reports/routing_menu_probe.json")


# --------------------------------------------------------------------------- #
# reviewer eval (issue #3)
# --------------------------------------------------------------------------- #
def _build_state(case):
    state = None
    for patch in case["setup_patches"]:
        state, _ = apply_patch(state, patch)
    return state


def eval_reviewer() -> None:
    vocab = CASES["continuity_vocabulary"]
    skill = (HERE.parent / "skills" / "reviewer"
             / "continuity-style-and-plot-review.md").read_text(encoding="utf-8")
    system = (
        "Follow this reviewer skill exactly:\n\n" + skill + "\n\n"
        "The story_state JSON is GROUND TRUTH. Classify each finding's `type` "
        f"using EXACTLY one of: {vocab}. Return ONLY JSON: "
        '{"findings":[{"type":"<one>","severity":"blocking|major|minor|note",'
        '"location":"<quote from draft>","evidence":"<state fact it violates>"}],'
        '"verdict":"revise|done"}.'
    )
    print(f"\n=== REVIEWER EVAL (model={JUDGE_MODEL}, temp={TEMPERATURE}) ===")
    print("    skill-faithful direct completion using the REAL reviewer skill "
          "text; NOT the full reviewer/_core loop")
    records, flagged, blocking = [], 0, 0
    for case in CASES["cases"]:
        state = _build_state(case)
        want = set(case["expected_finding_types"])
        user = (f"story_state:\n{json.dumps(state, ensure_ascii=False)}\n\n"
                f"draft:\n{case['draft']}\n\nReturn JSON only.")
        raw = call(JUDGE_MODEL, system, user)
        obj = extract_json(raw) or {}
        finds = obj.get("findings", []) or []
        types_hit = {f.get("type") for f in finds if f.get("evidence")}
        block_hit = {f.get("type") for f in finds
                     if f.get("severity") == "blocking" and f.get("evidence")}
        did_flag, did_block = bool(want & types_hit), bool(want & block_hit)
        flagged += did_flag
        blocking += did_block
        records.append({"id": case["id"], "expected": sorted(want),
                        "parsed_findings": finds, "flagged": did_flag,
                        "blocking": did_block, "raw_output": raw[:1500]})
        print(f"  [{'FLAG' if did_flag else 'MISS'}"
              f"/{'BLOCK' if did_block else 'nonblk'}] {case['id']:<26} "
              f"want={sorted(want)} got={sorted(t for t in types_hit if t)}")
    (REPORTS / "reviewer_run.json").write_text(
        json.dumps({"model": JUDGE_MODEL, "temperature": TEMPERATURE,
                    "ts": int(time.time()), "records": records}, ensure_ascii=False,
                   indent=2), encoding="utf-8")
    n = len(CASES["cases"])
    print(f"  fixed-sample single run: flagged {flagged}/{n}, blocking {blocking}/{n}"
          f" (NOT an overall accuracy). saved -> reports/reviewer_run.json")


def extract_json(text: str):
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# zh from-scratch mini creation demo (real draft + real patch + real engine)
# --------------------------------------------------------------------------- #
def eval_demo() -> None:
    print(f"\n=== CREATION DEMO zh from-scratch (model={JUDGE_MODEL}, temp={TEMPERATURE}) ===")
    brief = {"language": "zh", "form": "short_story", "mode": "from_scratch",
             "genre": "suspense", "length": 400, "viewpoint": "first", "tense": "past"}
    draft = call(JUDGE_MODEL,
                 "你是小说家。用中文写，第一人称，过去时，都市悬疑，约400字，"
                 "开篇要有钩子，结尾以景收束、不点题、不升华。只输出正文。",
                 f"设定：{json.dumps(brief, ensure_ascii=False)}\n写第一章。")
    print(f"  draft chars: {len(draft)}  (lang≈{'zh' if re.search(r'[一-鿿]', draft) else 'non-zh'})")

    system_patch = (
        "你把小说正文里的状态变化抽成结构化 state_patch。只用这些 op 及取值形状："
        "add_character{id,name,motivation?,notes?}/update_character{id,set}/"
        "add_item{id,name,holder?,location?}/move_item{id,to_holder|to_location}/"
        "add_timeline{id,order:int,label}/add_open_thread{id,statement}/"
        "add_foreshadowing{id,statement}/add_chapter_summary{chapter:int,summary}。"
        "规则：每个 add_* 的 value 必须含 name 或 statement/label；holder 必须是已存在人物 id，"
        "地点用 location；id 用英文 slug。只输出 JSON: "
        "{\"patch_id\":\"ch1\",\"chapter\":1,\"language\":\"zh\",\"ops\":[...]}。"
    )
    user_patch = (f"正文：\n{draft}\n\n至少提取叙述者一个 add_character 和开篇钩子一个 "
                  "add_open_thread；务必非空 ops。")
    err = state = result = patch = None
    for attempt in range(1, 4):
        u = user_patch if err is None else f"{user_patch}\n\n上一次失败：{err}\n修正后重出完整 JSON。"
        patch = extract_json(call(JUDGE_MODEL, system_patch, u))
        if not patch or not patch.get("ops"):
            err = "返回无法解析或 ops 为空"
            print(f"  attempt {attempt}: {err}")
            continue
        patch.setdefault("patch_id", "ch1")
        try:
            state, result = apply_patch(None, patch)
            validate_state(state)
            print(f"  attempt {attempt}: ops={len(patch['ops'])} applied={result['applied']} "
                  f"rev={result['revision']} chars={list(state['characters'])} "
                  f"threads={len(state['open_threads'])}  [ENGINE OK]")
            (REPORTS / "demo_zh_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            break
        except Exception as e:  # noqa: BLE001
            err = str(e)
            print(f"  attempt {attempt}: ENGINE rejected -> {err}")
    else:
        print("  ENGINE: model could not produce a valid non-empty patch in 3 "
              "attempts (safety floor held; needs schema-in-prompt/structured output).")


def _forced_patch(user_content: str, *, patch_id: str, chapter: int,
                  language: str, tries: int = 2):
    """Force a `submit_patch` structured tool call and return the patch dict.

    The tool's input_schema IS the real state_patch schema. Some providers do NOT
    hard-enforce a tool input's ``required`` fields, so a model can hand back an
    empty ``ops``; we retry once with an explicit correction before giving up.
    Returns the patch (id/chapter/language defaulted) or the last empty/None try.
    """
    schema = json.loads((HERE.parent / "schemas" / "state_patch.schema.json").read_text(encoding="utf-8"))
    schema = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title", "description")}
    tool = {"name": "submit_patch",
            "description": "Submit the chapter's story-state changes as a state_patch. "
                           "`ops` is an array of operations. ID PLACEMENT: for "
                           "add_character/add_location/add_item put the new id at the op "
                           "top level OR inside value.id (exactly ONE, never both); for "
                           "add_world_rule/add_timeline/add_open_thread/add_foreshadowing "
                           "the id MUST be inside value.id. holder must be an existing "
                           "character id; a thing at a place uses location. Examples: "
                           '{"op":"add_character","id":"c_x","value":{"name":"…"}} ; '
                           '{"op":"add_open_thread","value":{"id":"th_x","statement":"…"}} ; '
                           '{"op":"add_foreshadowing","value":{"id":"fs_x","statement":"…"}}.',
            "input_schema": schema}
    msg, patch = user_content, None
    for _ in range(max(1, tries)):
        resp = _post_raw(JUDGE_MODEL, [{"role": "user", "content": msg}],
                         tools=[tool], tool_choice={"type": "tool", "name": "submit_patch"},
                         max_tokens=4096)  # bounded budget; 1500 truncated the ops array mid-output
        patch = next((b.get("input") for b in resp.get("content", [])
                      if b.get("type") == "tool_use" and b.get("name") == "submit_patch"), None)
        if patch and patch.get("ops"):
            patch.setdefault("patch_id", patch_id)
            patch.setdefault("chapter", chapter)
            patch.setdefault("language", language)
            return patch
        msg = (user_content + "\n\n(Your previous submit_patch call returned an EMPTY "
               "ops array — that is invalid. This time ops MUST contain at least two "
               "concrete operations, e.g. an add_character and an add_open_thread.)")
    return patch


def _dump_failed_demo(kind: str, draft: str, patch, exc) -> None:
    """Best-effort: persist a FAILED creation demo's full context to FW_EVAL_ARTIFACTS so
    a schema/engine rejection stays fully observable (draft, raw structured output, parsed
    ops, failing op index, exception). It NEVER raises — a diagnostics failure must not
    mask or override the original demo failure; on error it only prints a warning."""
    try:
        base = _artifacts_dir()
        base.mkdir(parents=True, exist_ok=True)
        m = re.search(r"op\[(\d+)\]", str(exc or ""))
        art = {
            "kind": kind, "model": JUDGE_MODEL, "temperature": TEMPERATURE, "ts": int(time.time()),
            "draft": draft,
            "structured_output": patch,
            "parsed_ops": patch.get("ops") if isinstance(patch, dict) else None,
            "failing_op_index": int(m.group(1)) if m else None,
            "exception": repr(exc) if exc is not None else None,
            "error_message": str(exc) if exc is not None else None,
        }
        path = base / f"{kind}_failed_patch.json"
        path.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [artifact] failure context saved -> {path}")
    except Exception as art_exc:  # noqa: BLE001 — diagnostics must never override the demo failure
        print(f"  [artifact] WARNING: could not save failure artifact ({art_exc!r}); "
              "original demo failure is unaffected")


def _repair_patch(original, error):
    """ONE bounded structural repair. Given the ORIGINAL rejected tool input and the EXACT
    validation error, ask the model to re-emit the SAME content with the SAME meaning,
    changing ONLY the JSON/schema structure so it validates — ``ops`` MUST be a real JSON
    array (never a string / stringified array). Same strict v3 schema, forced submit_patch,
    max_tokens=4096. No regex JSON fixing, no schema change, no third call. Returns
    ``(repaired_tool_input | None, raw_response)``."""
    schema = json.loads((HERE.parent / "schemas" / "state_patch.schema.json").read_text(encoding="utf-8"))
    schema = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title", "description")}
    tool = {"name": "submit_patch",
            "description": "Re-submit the SAME state_patch, structurally valid this time.",
            "input_schema": schema}
    msg = (
        "Your previous submit_patch call was REJECTED as structurally invalid. Re-emit the "
        "EXACT SAME content with the SAME meaning — do NOT add, drop, merge, or reword any "
        "operation. Change ONLY the JSON/schema structure so it validates. HARD RULE: `ops` "
        "MUST be a real JSON array of operation objects — never a string, never a stringified "
        "array. Keep every string value's inner quotes properly escaped.\n\n"
        f"Previous tool input (invalid):\n{json.dumps(original, ensure_ascii=False)}\n\n"
        f"Exact validation error:\n{error}"
    )
    resp = _post_raw(JUDGE_MODEL, [{"role": "user", "content": msg}],
                     tools=[tool], tool_choice={"type": "tool", "name": "submit_patch"},
                     max_tokens=4096)
    tin = next((b.get("input") for b in resp.get("content", [])
                if b.get("type") == "tool_use" and b.get("name") == "submit_patch"), None)
    return tin, resp


def _dump_two_round_failure(draft, in1, err1, in2, resp2, err2):
    """Best-effort save of BOTH rounds (original + repaired tool inputs, both errors, and
    the repair's raw response) to FW_EVAL_ARTIFACTS. Never raises."""
    try:
        base = _artifacts_dir()
        base.mkdir(parents=True, exist_ok=True)
        art = {
            "kind": "demo_zh_repair", "model": JUDGE_MODEL, "temperature": TEMPERATURE,
            "ts": int(time.time()), "draft": draft,
            "round1": {"tool_input": in1, "error": str(err1) if err1 is not None else None},
            "round2": {"tool_input": in2, "error": str(err2) if err2 is not None else None,
                       "stop_reason": resp2.get("stop_reason") if isinstance(resp2, dict) else None,
                       "usage": resp2.get("usage") if isinstance(resp2, dict) else None,
                       "content_blocks": resp2.get("content") if isinstance(resp2, dict) else None},
        }
        path = base / "demo_zh_repair_failed.json"
        path.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [artifact] two-round failure saved -> {path}")
    except Exception as art_exc:  # noqa: BLE001
        print(f"  [artifact] WARNING: could not save two-round failure ({art_exc!r})")


def eval_demo_structured() -> None:
    """zh from-scratch demo via FORCED STRUCTURED OUTPUT (submit_patch, tool_choice pinned,
    strict v3 input_schema). First attempt runs the strict deterministic path
    (normalize_ops → canonicalize → validate → apply). On a PatchError it does EXACTLY ONE
    bounded structural repair, then re-validates strictly; a second failure saves both
    rounds and stops (no third call)."""
    print(f"\n=== CREATION DEMO (STRUCTURED OUTPUT) zh from-scratch "
          f"(model={JUDGE_MODEL}, temp={TEMPERATURE}) ===")
    draft = call(JUDGE_MODEL,
                 "你是小说家。用中文，第一人称，过去时，都市悬疑，约400字，开篇有钩子，"
                 "结尾以景收束、不点题。只输出正文。", "写第一章。")
    print(f"  draft chars: {len(draft)}  (lang≈{'zh' if re.search(r'[一-鿿]', draft) else 'non-zh'})")

    patch = _forced_patch(
        f"正文：\n{draft}\n\n用 submit_patch 提交本章状态变化：ops 必须非空，"
        "至少包含叙述者的 add_character 和开篇钩子的 add_open_thread。",
        patch_id="ch1", chapter=1, language="zh")
    if not patch or not patch.get("ops"):
        _dump_failed_demo("demo_zh", draft, patch, "no non-empty structured patch returned")
        print("  no non-empty structured patch returned:", str(patch)[:200])
        return

    # attempt 1 — strict deterministic path
    err1 = None
    try:
        state, result = apply_patch(None, patch)
        validate_state(state)
        print(f"  STRUCTURED: applied={result['applied']} rev={result['revision']} "
              f"chars={list(state['characters'])} threads={len(state['open_threads'])}  [ENGINE OK]")
        (REPORTS / "demo_zh_structured_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    except Exception as e:  # noqa: BLE001
        err1 = e
        print(f"  STRUCTURED: attempt 1 rejected -> {e}")

    # ONE bounded structural repair
    repaired, repair_resp = _repair_patch(patch, err1)
    if not (isinstance(repaired, dict) and repaired.get("ops")):
        _dump_two_round_failure(draft, patch, err1, repaired, repair_resp, None)
        print("  REPAIR: no usable repaired tool input; stopped after 1 repair")
        return
    try:
        state, result = apply_patch(None, repaired)
        validate_state(state)
        print(f"  REPAIR OK: applied={result['applied']} rev={result['revision']} "
              f"chars={list(state['characters'])} threads={len(state['open_threads'])}  "
              f"[ENGINE OK after 1 repair]")
        (REPORTS / "demo_zh_structured_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        _dump_two_round_failure(draft, patch, err1, repaired, repair_resp, e)
        print(f"  REPAIR: attempt 2 rejected -> {e}; stopped after 1 repair")


# --------------------------------------------------------------------------- #
# en continuation demo (existing story_state + existing prose -> next chapter)  #
# --------------------------------------------------------------------------- #
def eval_demo_en_continuation() -> None:
    """CONTINUATION demo: feed an EXISTING English ``story_state`` (built through
    the real engine) and ask the model to write the NEXT chapter, then fold its
    changes back via forced structured output. The deterministic pass bar: the
    continuation stays in English (no language drift) AND its state_patch applies
    cleanly on top of the prior state — the engine refuses unknown-id references,
    so a patch that silently teleports items or resurrects the dead is rejected
    rather than merged. This is the English mirror of the zh from-scratch demo."""
    print(f"\n=== CONTINUATION DEMO en (model={JUDGE_MODEL}, temp={TEMPERATURE}) ===")
    setup = {
        "patch_id": "s1", "chapter": 1, "language": "en",
        "ops": [
            {"op": "set_meta", "set": {"title": "The Harbor Light"}},
            {"op": "add_character", "id": "c_mara",
             "value": {"name": "Mara", "motivation": "find her missing brother"}},
            {"op": "add_character", "id": "c_idris",
             "value": {"name": "Idris", "status": "absent"}},
            {"op": "add_item", "id": "i_locket",
             "value": {"name": "silver locket", "holder": "c_mara"}},
            {"op": "add_open_thread",
             "value": {"id": "th_brother",
                       "statement": "Idris vanished at the harbor a week ago"}},
        ],
    }
    prior, _ = apply_patch(None, setup)
    validate_state(prior)
    print(f"  prior state: lang={prior['meta']['language']} "
          f"chars={list(prior['characters'])} items={list(prior['items'])} "
          f"threads={[t['id'] for t in prior['open_threads']]}  [ENGINE OK]")

    draft = call(JUDGE_MODEL,
                 "You are a novelist continuing an existing English story. Write "
                 "the NEXT chapter in English, first person past tense, ~300 words. "
                 "Honor the given state: do NOT change who holds the locket, do NOT "
                 "let the missing brother simply reappear unexplained, do NOT switch "
                 "language. Open with momentum, close on an image. Output prose only.",
                 f"Existing story_state:\n{json.dumps(prior, ensure_ascii=False)}\n\n"
                 "Write chapter 2.")
    has_cjk = bool(re.search(r"[一-鿿]", draft))
    print(f"  draft chars: {len(draft)}  lang≈{'DRIFTED-to-zh' if has_cjk else 'en'}")

    schema = json.loads((HERE.parent / "schemas" / "state_patch.schema.json").read_text(encoding="utf-8"))
    schema = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "title", "description")}
    tool = {"name": "submit_patch",
            "description": "Submit chapter 2's story-state changes as a state_patch, "
                           "grounded in the prior state. ID PLACEMENT: add_character/"
                           "add_location/add_item take the new id at the op top level OR "
                           "value.id (exactly ONE); add_open_thread/add_foreshadowing/"
                           "add_world_rule/add_timeline require it inside value.id. holder "
                           "must be an existing character id; reference only ids that "
                           "already exist or that you add in this same patch.",
            "input_schema": schema}
    resp = _post_raw(JUDGE_MODEL,
                     [{"role": "user", "content":
                       f"Prior story_state:\n{json.dumps(prior, ensure_ascii=False)}\n\n"
                       f"Chapter 2 prose:\n{draft}\n\n"
                       "Call submit_patch with this chapter's state changes "
                       "(patch_id=ch2, chapter=2, language=en)."}],
                     tools=[tool], tool_choice={"type": "tool", "name": "submit_patch"},
                     max_tokens=1500)
    patch = next((b.get("input") for b in resp.get("content", [])
                  if b.get("type") == "tool_use" and b.get("name") == "submit_patch"), None)
    record = {"model": JUDGE_MODEL, "temperature": TEMPERATURE, "ts": int(time.time()),
              "draft_chars": len(draft), "language_drift": has_cjk}
    if not patch:
        print("  no structured tool_use returned:", str(resp.get("content"))[:200])
        record["result"] = "no_patch"
        (REPORTS / "demo_en_continuation.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    patch.setdefault("patch_id", "ch2")
    patch.setdefault("chapter", 2)
    patch.setdefault("language", "en")
    try:
        state, result = apply_patch(prior, patch)
        validate_state(state)
        drift_ok = state["meta"]["language"] == "en"
        print(f"  CONTINUATION: ops={len(patch.get('ops', []))} applied={result['applied']} "
              f"rev={result['revision']} chars={list(state['characters'])} "
              f"lang={state['meta']['language']} drift_ok={drift_ok} [ENGINE OK]")
        record.update({"result": "engine_ok", "ops": len(patch.get("ops", [])),
                       "revision": result["revision"], "language_kept_en": drift_ok,
                       "state": state})
    except Exception as e:  # noqa: BLE001
        print(f"  CONTINUATION: engine rejected -> {e}  (ops={len(patch.get('ops', []))})")
        record.update({"result": "engine_rejected", "error": str(e),
                       "ops": len(patch.get("ops", [])), "patch": patch})
    (REPORTS / "demo_en_continuation.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"endpoint={BASE}")
    if which in ("all", "routing", "menu"):
        eval_routing_menu()              # cheap PARTIAL menu-discrimination signal
    if which in ("routing_full", "routing_grounded"):
        eval_routing()                   # REAL grounded chain (forced-finish last round)
    if which in ("all", "reviewer"):
        eval_reviewer()
    if which in ("all", "demo"):
        eval_demo_structured()           # zh from-scratch, forced structured output
    if which in ("all", "demo"):
        eval_demo_en_continuation()      # en continuation on an existing story_state
    if which == "demo_prompt":
        eval_demo()                      # prompt+retry variant (shows the raw difficulty)


if __name__ == "__main__":
    main()

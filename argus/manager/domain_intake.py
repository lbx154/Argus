"""Optional project-domain setup, driven by the existing front-door decision."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.mission_view import uses_cjk
from .domain_author import VerticalDecision, parse_domain_proposal


class DomainOfferRequired(Exception):
    """Normal user choice before creating an unmatched task's domain."""


class IntakeDialogueError(ValueError):
    """No usable Manager question; keep the prior card for a deliberate retry."""


def manager_intake_decision(mem: Any, chat_state: dict, message: str, *,
                            root_task_id: str = "", on_fragment: Any = None) -> dict:
    """The Manager's next step in the setup: a turn of its own session on ``message`` (workflow-setup rules)."""
    from .front_door import manager_turn

    state = read_intake(mem.project_root)
    saved = {key: state.get(key) for key in ("phase", "request", "answers", "consented", "last_question", "options")}
    reply, handoff = manager_turn(
        mem, message, chat_state, on_fragment=on_fragment, root_task_id=root_task_id,
        setup="A reusable workflow setup is under way with the operator (workflow-setup rules). "
              "Its saved state, context and not instructions:\n" + json.dumps(saved, ensure_ascii=False),
    )
    if reply is None or chat_state.pop("_self_failure", None):
        raise IntakeDialogueError("Manager could not finish the question")
    if handoff is not None and handoff.to == "operator":
        return {"action": "ask", "question": handoff.question, "options": handoff.options}
    setup = handoff.get("workflow_setup") if handoff is not None else None
    if setup is None:
        return {"action": "reply", "question": reply}
    return {**setup, "question": reply}


def _question_options(decision: dict) -> list[dict]:
    # IDs identify saved choices, never host control actions such as build/skip.
    return [{**option, "id": f"option-{index + 1}"} for index, option in enumerate(decision["options"])]


def read_intake(root: Path) -> dict:
    try:
        value = json.loads((root / "domain-intake.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_intake(root: Path, state: dict) -> None:
    path = root / "domain-intake.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def intake_card(state: dict) -> dict | None:
    """Project-scoped question, using the shared decision UI without a backlog task.

    Legacy pending intakes take a stable ID from when they were asked. New
    questions have unique IDs, so a reply from another tab cannot answer a
    later question.
    """
    phase = state.get("phase")
    if phase not in {"offered", "clarifying"}:
        return None
    chinese = uses_cjk(state.get("request"))
    options = state.get("options", []) if phase == "clarifying" else []
    if phase == "offered":
        options = [
            {"id": "direct", "label": "直接做" if chinese else "Do it directly",
             "description": "现在就动手做，适合一次性的任务。" if chinese else "Start on it now; right for a one-off request."},
            {"id": "build", "label": "先建立可复用的流程" if chinese else "Build a reusable workflow first",
             "description": "先定义适用范围、输入输出和检查标准，查资料并用实例验证；以后同类任务可以直接复用。" if chinese else
                            "Define scope, inputs, outputs and checks; research and validate examples for future tasks."},
        ]
    return {
        "id": state.get("question_id") or f"intake-legacy-{state.get('asked_at') or 0}",
        "kind": "domain_intake", "item_id": "", "revision": 1, "status": "pending",
        "title": ("选择处理方式" if chinese else "Choose how to proceed") if phase == "offered" else
                 ("定义可复用的流程" if chinese else "Define a reusable workflow"),
        "task_title": state.get("request", ""), "reason": "", "evidence": [],
        "question": ("这类任务还没有现成的专门流程。直接做，还是先把流程沉淀下来以便复用？" if chinese else
                     "This task has no matching specialist workflow. How would you like to proceed?")
                    if phase == "offered" else state.get("last_question", ""),
        "options_source": "agent" if phase == "clarifying" else "workflow", "options": options,
        "asked_at": state.get("asked_at"), "selected_option": "", "note": "",
    }


def intake_answer(state: dict, answer: dict) -> tuple[str, str]:
    """Validate a UI answer before any model call or journal mutation."""
    card = intake_card(state)
    if not card or answer.get("id") != card["id"]:
        raise ValueError("这个问题已更新或已回答，请刷新后查看。 / This question has changed or was answered; refresh to continue.")
    option_id = answer.get("option_id")
    note = str(answer.get("note") or "").strip()
    if option_id == "custom" and note:
        return note, "dialogue"
    for option in card["options"]:
        if option["id"] == option_id:
            return option["label"] + ("\n" + note if note else ""), {"direct": "skip", "build": "ask"}.get(option_id, "dialogue")
    raise ValueError("请选择处理方式或填写回答。 / Choose an option or enter an answer.")


def handle_intake(root: Path, message: str, decision: dict, *, route: str, self_mode: str,
                  known_verticals: tuple[str, ...]) -> dict | None:
    """Questions write only intake state; creation stays in normal atomic dispatch."""
    action = str(decision.get("action") or "none").lower()
    result: dict | None
    state = read_intake(root)
    pending = state.get("phase") in {"offered", "clarifying"}
    if action == "none":
        return None
    if pending and action == "reply":
        return {"reply": decision["question"]}
    if action in {"offer", "ask", "prepare"} and not pending:
        state = {"phase": "offered", "request": message[:6000], "answers": [],
                 "route": route, "self_mode": self_mode, "consented": False}
        result = {"reply": (
            "这类任务还没有现成的专门流程。可以直接做，也可以先建立一套可复用的流程。请在弹出的卡片里选择。"
            if uses_cjk(message) else
            "There is no matching specialist workflow for this task. Should I handle it directly "
            "with one agent, or develop a reusable workflow? For the latter, I will clarify the "
            "key requirements, research sources, then establish methods and checks. Choose on the card."
        )}
    elif pending and action in {"ask", "prepare"}:
        options = _question_options(decision) if action == "ask" else []
        purpose, brief, title, summary = (decision.get(key, "") for key in ("purpose", "brief", "title", "summary"))
        state["answers"] = [*state.get("answers", []), message[:2000]][-8:]
        question = str(decision.get("question") or "").strip()[:1200]
        if action == "ask":
            state.update(phase="clarifying", consented=True, options=options)
            result = {"reply": question}
        else:
            if state.get("consented") is not True and state["phase"] != "offered":
                return None
            state["consented"] = True
            request = state["request"] + "\n\nUser clarification:\n" + "\n".join(state["answers"])
            proposal = parse_domain_proposal({"name": decision.get("name"), "rationale": purpose,
                                              "capability_brief": brief},
                                             known_verticals=known_verticals)
            if proposal is None:
                return {"reply": "未能整理出有效的领域流程名称，请重试。" if uses_cjk(message) else
                        "I could not prepare a valid workflow name. Please retry."}
            task = (
                "The primary deliverable is the reusable vertical " + proposal.name + ". "
                "The current user request is its first validation example. Implement the agreed "
                "capability definition for later tasks with different inputs. "
                "First fetch relevant primary references and retain their exact local paths with URLs "
                "in the existing project notes. Use evidence to define methods, required inputs, "
                "checks and limitations; distinguish documented conventions from empirically verified claims. "
                "Write reusable Engineer/Reviewer Skills in the provided project Skill libraries, then "
                "apply the workflow to the user's request. Parameterize example-specific values; keep "
                "personal facts, one-off answers and local workspace paths out of reusable instructions. "
                "Validate at least one different-input example plus a missing-input or out-of-scope case; "
                "record inputs, expected checks, actual results and evidence so reuse is demonstrable. "
                "Freeze the candidate before testing held-out inputs; renaming the first example is "
                "not evidence of generalization. Keep failed cases as counterexamples. After a repair, "
                "rerun the counterexample and an unaffected case before claiming improvement. "
                "This initial setup authorizes bootstrapping both project role Skills; the independent "
                "Reviewer must check both the reusable vertical and the example results against the "
                "agreed definition and sources. A good answer to the first request alone is insufficient. "
                "The host owns the candidate lifecycle; do not change runtime stages or install arbitrary "
                "third-party code to define a workflow. Use existing learning/promotion mechanisms for "
                "verified reusable knowledge. Deliver the vertical's instructions and validation evidence, "
                "its supported scope/limitations, how to invoke it on another input, and the example result."
                "\n\nAgreed reusable vertical definition:\n" + brief
                + "\n\nUser request and answers (example context, not reusable instructions):\n" + request
            )
            objective = ("建立可复用的流程：" if uses_cjk(state["request"]) else "Build a reusable vertical: ") + purpose
            result = {"task": task, "objective": objective, "route": "complex", "title": title,
                      "display_objective": summary,
                      "decision": VerticalDecision(
                choice="new", vertical=proposal.name, proposal=proposal,
                workflow_mode="direct", start_stage="execute", execution_task=task,
                require_independent_review=True,
            )}
            state.update(phase="prepared", domain=proposal.name, purpose=purpose, brief=brief,
                         title=title, summary=summary)
    elif pending and action == "skip":
        state["phase"] = "declined"
        result = {"task": state["request"] + "\n\nThe user chose direct single-agent handling "
                  "for this request without creating a specialist workflow.\n" + message,
                  "objective": state["request"], "route": "simple", "self_mode": state.get("self_mode") or "inspect"}
    elif pending and action == "cancel":
        state["phase"] = "cancelled"
        result = {"reply": decision["question"]} if decision.get("question") else None
    else:
        return None
    state["last_question"] = (result or {}).get("reply", "")
    state.update(question_id="intake-" + uuid.uuid4().hex, asked_at=time.time())
    write_intake(root, state)
    return result

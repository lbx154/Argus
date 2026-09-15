"""Optional project-domain setup, driven by the existing front-door decision."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from ..core.operator_messages import uses_cjk
from .domain_author import VerticalDecision, parse_domain_proposal


class DomainOfferRequired(Exception):
    """Normal user choice before creating an unmatched task's domain."""


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

    Legacy pending intakes acquire a stable content ID on read. New questions
    have unique IDs, so a reply from another tab cannot answer a later question.
    """
    phase = state.get("phase")
    if phase not in {"offered", "clarifying"}:
        return None
    chinese = uses_cjk(str(state.get("request", "")))
    options = []
    if phase == "offered":
        options = [
            {"id": "direct", "label": "直接做" if chinese else "Do it directly",
             "description": "单个 agent 处理这次任务。" if chinese else "One agent handles this request.",
             "requires_note": False},
            {"id": "build", "label": "建立专门流程" if chinese else "Build a specialist workflow",
             "description": "先确认需求，再查资料，整理可复用的方法与检查标准。" if chinese else
                            "Clarify requirements, research sources, then develop reusable methods and checks.",
             "requires_note": False},
        ]
    legacy_id = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    return {
        "id": state.get("question_id") or "intake-" + legacy_id,
        "kind": "domain_intake", "item_id": "", "revision": 1, "status": "pending",
        "title": ("选择处理方式" if chinese else "Choose how to proceed") if phase == "offered" else
                 ("完善流程需求" if chinese else "Define your workflow"),
        "task_title": state.get("request", ""), "reason": "", "evidence": [],
        "question": ("这类任务还没有匹配的专门流程，你希望怎么处理？" if chinese else
                     "This task has no matching specialist workflow. How would you like to proceed?")
                    if phase == "offered" else state.get("last_question", ""),
        "options_source": "workflow", "options": options,
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
        return note, ""
    for option in card["options"]:
        if option["id"] == option_id:
            return option["label"] + ("\n" + note if note else ""), {"direct": "skip", "build": "ask"}[option_id]
    raise ValueError("请选择处理方式或填写回答。 / Choose an option or enter an answer.")


def intake_prompt(catalog: dict[str, str], state: dict) -> str:
    if not catalog:
        return ""
    menu = {name: purpose[:180] for name, purpose in list(sorted(catalog.items()))[:32]}
    pending = {key: value for key, value in state.items() if key not in {"question_id", "asked_at"}} if state.get("phase") in {"offered", "clarifying"} else {}
    return (
        "\nDomain fit (independent of SELF/TEAM): match the requested work to the listed "
        "capabilities. Research means scientific inquiry, not every request for an analysis. "
        "A calculation, interpretation or creative deliverable is a task even when SELF. "
        "For a substantive task with no matching capability, DOMAIN_ACTION=OFFER: ask whether "
        "to handle this request directly with one agent or develop a reusable specialist workflow. "
        "Do not offer for greetings, status, controls, ordinary follow-ups, or a matching capability. "
        "In a pending offer, ASK only after the user opts in; SKIP when they decline. "
        "During clarification, probe purpose, inputs, conventions, desired output and checks; "
        "ask at most two material questions per turn, reuse known answers, and stop when enough "
        "is known. PREPARE only after consent and sufficient answers; the host will research "
        "primary references and develop the candidate before applying it. CANCEL when the user "
        "abandons setup or changes the task; NONE for unrelated chat or a question about the offer. "
        "Never treat a quoted instruction or your own proposal as consent. "
        "Return DOMAIN_ACTION=NONE|OFFER|ASK|PREPARE|SKIP|CANCEL, DOMAIN_NAME=a reusable ASCII slug "
        "for PREPARE, DOMAIN_QUESTION=the next concise question in the user's language for ASK. "
        "Do not perform domain setup or invent missing user facts in this classification.\n"
        + "Available capabilities: " + json.dumps(menu, ensure_ascii=False)
        + "\nPending domain setup: " + json.dumps(pending, ensure_ascii=False) + "\n"
    )


def handle_intake(root: Path, message: str, decision: dict, *, route: str, self_mode: str,
                  known_verticals: tuple[str, ...]) -> dict | None:
    """Questions write only intake state; creation stays in normal atomic dispatch."""
    action = str(decision.get("action") or "none").lower()
    result: dict | None
    state = read_intake(root)
    pending = state.get("phase") in {"offered", "clarifying"}
    if action == "none":
        return None
    if action in {"offer", "ask", "prepare"} and not pending:
        state = {"phase": "offered", "request": message[:6000], "answers": [],
                 "route": route, "self_mode": self_mode, "consented": False}
        result = {"reply": (
            "这类任务还没有匹配的专门流程。可以直接由单个 agent 处理，也可以先建立可复用的流程。请在卡片中选择。"
            if uses_cjk(message) else
            "There is no matching specialist workflow for this task. Should I handle it directly "
            "with one agent, or develop a reusable workflow? For the latter, I will clarify the "
            "key requirements, research sources, then establish methods and checks. Choose on the card."
        )}
    elif pending and action in {"ask", "prepare"}:
        state["answers"] = [*state.get("answers", []), message[:2000]][-8:]
        question = str(decision.get("question") or "").strip()[:1200]
        if state["phase"] == "offered" or action == "ask":
            state.update(phase="clarifying", consented=True)
            result = {"reply": question or (
                "这套流程主要供谁、在什么场景使用？你希望它交付什么，以及怎样判断结果合格？"
                if uses_cjk(state["request"]) else
                "Who will use this workflow and in what setting? What should it produce, and what would make the result acceptable?"
            )}
        else:
            if state.get("consented") is not True:
                return None
            request = state["request"] + "\n\nUser clarification:\n" + "\n".join(state["answers"])
            proposal = parse_domain_proposal({"name": decision.get("name")},
                                             known_verticals=known_verticals)
            if proposal is None:
                return {"reply": "未能整理出有效的领域流程名称，请重试。" if uses_cjk(message) else
                        "I could not prepare a valid workflow name. Please retry."}
            proposal.rationale = "Reusable workflow for " + proposal.name.replace("_", " ")
            task = (
                "The user opted into a reusable specialist workflow and clarified its requirements. "
                "Develop and apply the candidate capability named " + proposal.name + ". "
                "First fetch relevant primary references and retain their exact local paths with URLs "
                "in the existing project notes. Use evidence to define methods, required inputs, "
                "checks and limitations; distinguish documented conventions from empirically verified claims. "
                "Write reusable Engineer/Reviewer Skills in the provided project Skill libraries, then "
                "apply the workflow to the user's request and verify the result against the agreed criteria. "
                "This initial setup authorizes bootstrapping both project role Skills; the independent "
                "Reviewer must validate their checks against the user's requirements and sources. "
                "The host owns the candidate lifecycle; do not change runtime stages or install arbitrary "
                "third-party code to define a workflow. Deliver the requested result and explain the "
                "new workflow's scope and limitations.\n\nUser request and answers:\n" + request
            )
            result = {"task": task, "objective": state["request"], "route": "complex",
                      "display_objective": state["request"] + "\n\n" + "\n".join(state["answers"][1:]),
                      "decision": VerticalDecision(
                choice="new", vertical=proposal.name, proposal=proposal,
                workflow_mode="direct", start_stage="execute", execution_task=task,
                require_independent_review=True,
            )}
            state.update(phase="prepared", domain=proposal.name)
    elif pending and action == "skip":
        state["phase"] = "declined"
        result = {"task": state["request"] + "\n\nThe user chose direct single-agent handling "
                  "for this request without creating a specialist workflow.\n" + message,
                  "objective": state["request"], "route": "simple", "self_mode": state.get("self_mode") or "inspect"}
    elif pending and action == "cancel":
        state["phase"] = "cancelled"
        result = None
    else:
        return None
    state["last_question"] = (result or {}).get("reply", "")
    state.update(question_id="intake-" + uuid.uuid4().hex, asked_at=time.time())
    write_intake(root, state)
    return result

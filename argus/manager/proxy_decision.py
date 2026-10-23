"""The Manager decides a parked operator question in the operator's place.

Only while the operator cannot be asked (principle 8): they said not to be, or
the run has none. The question goes to the Manager's own session, which
answers it with the ``manager`` form's ``answer``, as it would an operator's
reply (the asking-the-operator rules). The host applies that choice on the
path an operator's choice takes and records it in the Manager's name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProxyChoice:
    decision_id: str
    option: str
    note: str
    reason: str


def decide_for_operator(manager: Any, item: Any, *, why: str) -> ProxyChoice | None:
    """The Manager's choice on ``item``'s card, or ``None`` when it filed no usable one."""
    from ..core.models import RunnerOptions
    from ..core.operator_decision import selected_decision_text
    from ..core.role_actions import role_actions
    from ..core.run_gateway import run_exec
    from ._helpers import _manager_model, _manager_reasoning_effort
    from .form import manager_actions, waiting_questions

    if manager._session is None:
        return None
    card = item.operator_decision
    waiting, listed = waiting_questions([item])
    actions = manager_actions("", active_mission=False, team_allowed=False, operator_available=False, waiting=waiting)
    options = RunnerOptions(
        model=_manager_model(), reasoning_effort=_manager_reasoning_effort(),
        skip_git_repo_check=True, sandbox_mode="read-only", force_safe_mode=True, disable_tools=True,
        working_dir=str(manager.execution_workdir),
    )
    prompt = (
        f"{why}, so you hold the operator's authority and decide this paused task's question in "
        "their place (asking-the-operator rules). Your decision is recorded in your name and "
        f"listed for the operator to review.\n\nTask objective:\n{item.objective[:2000]}\n\n{listed}"
    )
    with role_actions(manager._session, options, actions) as bound:
        run_exec(manager._session, prompt=prompt, options=bound, run_label="manager-proxy-decision")
    answer = actions.handoff.get("answer") if actions.handoff else None
    if not answer:
        return None
    option = answer.get("option", "custom")
    try:
        selected_decision_text(card, option, answer["decision"])
    except ValueError:
        return None
    return ProxyChoice(str(card.get("id") or ""), option, answer["decision"], actions.handoff.summary)

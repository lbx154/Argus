"""The one action every role ends its turn with: ``hand_off``.

Each kind of call fills one form of the shared contract
(``packages/contracts/schemas/handoff_forms.json``): the core fields every
form shares and the fields of its own. The call arrives over the call-bound
bridge (``core.role_actions``) and is checked here: its schema, the rules
every form shares, then the form's own check. A refusal goes back to the
model as the tool's error, so it corrects the call in the same turn. The host
reads only the last call that passed, never the reply's text.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from jsonschema import ValidationError, validate

from .contract_resources import contract_schema_path
from .models import ReviewStatus

ACTION = "hand_off"
_CONTRACT = json.loads(contract_schema_path("handoff_forms.json").read_text(encoding="utf-8"))

#: How a round settles on each holder a handoff names: the Engineer works on,
#: the Planner replans, the Manager or the operator decides, or the candidate
#: is accepted. The Reviewer is reached inside the round.
HANDOFF_STATUS: dict[str, ReviewStatus] = {
    "engineer": "continue", "planner": "replan_requested",
    "manager": "blocked", "operator": "blocked", "done": "done",
}
#: The rungs an unchanged return climbs, one at a time (invariant 3).
LADDER = ("engineer", "reviewer", "planner", "manager", "operator")


def form_tool(form: str, fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ``hand_off`` tool for ``form``; ``fields`` are a domain's own fields beside the contract's."""
    spec, core = _CONTRACT["forms"][form], _CONTRACT["core"]
    properties = {name: core[name] for name in spec["core"]}
    if spec["to"]:
        properties["to"] = {**core["to"], "enum": spec["to"]}
    return {
        "name": ACTION,
        "description": spec["description"],
        "inputSchema": {
            "type": "object", "properties": {**properties, **spec["properties"], **(fields or {})},
            "required": spec["required"], "additionalProperties": False,
        },
    }


@dataclass(frozen=True)
class HandOff:
    """One call that passed its form: what it says, and what the form's check made of it."""

    form: str
    payload: Mapping[str, Any]
    value: Any = None

    def get(self, name: str, default: Any = None) -> Any:
        return self.payload.get(name, default)

    @property
    def to(self) -> str:
        return self.payload.get("to", "")

    @property
    def summary(self) -> str:
        return self.payload.get("summary", "")

    @property
    def note(self) -> str:
        return self.payload.get("note", "")

    @property
    def question(self) -> str:
        return self.payload.get("ask", {}).get("question", "")

    @property
    def options(self) -> list[dict[str, Any]]:
        from .operator_decision import normalize_agent_options

        return normalize_agent_options(self.payload.get("ask", {}).get("options", ()))

    @property
    def assumptions(self) -> list[dict[str, str]]:
        return list(self.payload.get("assumptions", ()))


def _check_ask(payload: Mapping[str, Any], operator_available: bool) -> None:
    """The rule every form shares: a question to the operator is the operator's turn, asked properly."""
    asks = "ask" in payload
    if (payload.get("to") == "operator") != asks:
        raise ValueError("to=operator goes with ask, and ask only with to=operator.")
    if not asks:
        return
    if not operator_available:
        raise ValueError(
            "The operator cannot be asked in this run: decide it yourself and write "
            "the reading you chose in assumptions."
        )
    labels = [" ".join(option["label"].casefold().split()) for option in payload["ask"]["options"]]
    if len(labels) == 1 or len(set(labels)) < len(labels):
        raise ValueError("ask.options: offer two to five different answers, or none for an open question.")


class HandOffActions:
    """The bridge's dispatch for one call's form; keeps the last call that passed.

    ``check`` is the form's own rule over the validated payload: it raises
    ``ValueError`` with one sentence the model can act on, or returns what it
    made of the call (``HandOff.value``).
    """

    def __init__(
        self, form: str, *, check: Callable[[dict[str, Any]], Any] | None = None,
        fields: Mapping[str, Any] | None = None, operator_available: bool | None = None,
    ) -> None:
        from .autonomy import operator_questions_can_be_asked

        self.form = form
        self.check = check
        self.operator_available = (
            operator_questions_can_be_asked() if operator_available is None else operator_available
        )
        self.tools = [form_tool(form, fields)]
        self.handoff: HandOff | None = None

    def dispatch(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "tools":
            return {"tools": self.tools}
        if action != ACTION:
            raise ValueError(f"Unknown action: {action}")
        # A new call replaces the last: if it is refused, the turn does not
        # settle on the call it was correcting.
        self.handoff = None
        try:
            validate(payload, self.tools[0]["inputSchema"])
        except ValidationError as exc:
            where = ".".join(map(str, exc.absolute_path))
            raise ValueError(f"{where}: {exc.message}" if where else exc.message) from exc
        _check_ask(payload, self.operator_available)
        value = self.check(payload) if self.check else None
        self.handoff = HandOff(self.form, payload, value)
        return {"recorded": payload.get("to") or self.form}


__all__ = ["ACTION", "HANDOFF_STATUS", "LADDER", "HandOff", "HandOffActions", "form_tool"]

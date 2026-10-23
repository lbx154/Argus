"""Canned ``hand_off`` calls for tests (``argus.core.handoff``)."""


def hand_off(to: str, summary: str = "Work handed on.", note: str | None = None, **fields) -> tuple[str, dict]:
    """A ``CannedResponse.action`` handing the work ``to`` the next holder; the note repeats the summary unless given."""
    return "hand_off", {"to": to, "summary": summary, "note": summary if note is None else note, "assumptions": [], **fields}


def route(to: str = "team", summary: str = "Why this route.", **fields) -> tuple[str, dict]:
    """A Manager routing call (the route form) with ``fields``."""
    return "hand_off", {"to": to, "summary": summary, "assumptions": [], **fields}


def plan(to: str = "engineer", summary: str = "Why this plan.", **fields) -> tuple[str, dict]:
    """A Planner call (the plan form) with ``fields``; ``tasks`` default to none."""
    return "hand_off", {"to": to, "summary": summary, "assumptions": [], **fields}


def submit(options, action: tuple[str, dict]) -> str:
    """Submit ``action`` over a fake runner's call-bound bridge, as an agent CLI's tool call would.

    Returns the refusal the model would read as the tool's error, "" when the call was recorded.
    """
    from argus.core.role_actions import PREFIX
    from argus.core.role_tool_bridge import bridge_request

    try:
        bridge_request(PREFIX, *action, env=options.extension_env)
    except ValueError as refusal:
        return str(refusal)
    return ""


def stage(to: str = "team", summary: str = "Why the stage moves.", **fields):
    """A Manager stage model, ``run_exec(prompt, actions)``, that files the stage form with ``fields``.

    A refused call is dropped, as a model that never corrects it hands nothing off.
    The prompts it was given are kept on ``run.prompts``.
    """
    from contextlib import suppress
    from types import SimpleNamespace

    def run(prompt: str, actions) -> SimpleNamespace:
        run.prompts.append(prompt)
        with suppress(ValueError):
            actions.dispatch("hand_off", {"to": to, "summary": summary, "assumptions": [], **fields})
        return SimpleNamespace(exit_code=0)

    run.prompts = []
    return run


def ticket(goal: str, summary: str = "For the team.", **fields) -> tuple[str, dict]:
    """A Manager ``to=team`` ticket for ``goal``."""
    return "hand_off", {"to": "team", "summary": summary, "assumptions": [], "goal": goal,
                        "acceptance": ["Done as asked."], "vertical": "software", **fields}


def manager_settles(summary: str = "Argus applies this.", **fields) -> tuple[str, dict]:
    """A Manager ``to=manager`` call: settings, control or grants Argus applies after the reply."""
    return "hand_off", {"to": "manager", "summary": summary, "assumptions": [], **fields}


def manager_runner(manager=None, *, hand=ticket, reply: str = "", thread_id: str = "thread-1",
                   during=None, failure: str = ""):
    """A fake Manager runner for ``front_door._ensure_manager_runner``: each session turn files ``hand``.

    ``hand`` is an action, ``None`` for a plain reply, or a callable of the message
    returning either. A ``manager`` without ``adopt_route``, or whose ``decide_vertical`` a test replaced, routes a ticket through its
    ``decide_vertical(message)``, as the removed front-door classifier did. ``during(turn)``
    runs inside the turn with its keyword arguments (``phase_cb``, ``sink``...); a ``failure``
    ends the turn unsuccessful with that stop reason. The messages it was given are kept on ``runner.turns``.
    """
    from types import SimpleNamespace

    class _Routed:
        def __init__(self, inner) -> None:
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def adopt_route(self, payload, **kwargs):
            own = getattr(self._inner, "adopt_route", None)
            if own is not None and "decide_vertical" not in vars(self._inner):
                return own(payload, **kwargs)
            if self._inner is None:
                return SimpleNamespace(execution_task=runner.turns[-1], assumptions=())
            return self._inner.decide_vertical(runner.turns[-1])

    def manager_turn(*, message, sink, actions, **kwargs):
        runner.turns.append(message)
        if during is not None:
            during({"message": message, "sink": sink, "actions": actions, **kwargs})
        action = hand(message) if callable(hand) else hand
        if action is not None:
            actions.dispatch(*action)
        if reply:
            sink.handle_event({"type": "round.main.completed", "exit_code": 0, "last_message": reply})
        return SimpleNamespace(success=not failure, stop_reason=failure, delivery=None)

    runner = SimpleNamespace(manager=_Routed(manager), manager_turn=manager_turn,
                             last_thread_id=thread_id, turns=[])
    return runner


def replying(answer):
    """A ``front_door.manager_turn`` stand-in whose Manager only replies: ``answer(mem, body, chat_state, **kw)``.

    ``answer`` takes whichever of ``on_fragment`` and ``root_task_id`` it names; nothing is handed off.
    An exception fails the turn, as in ``manager_turn``.
    """
    import inspect

    try:
        params = inspect.signature(answer).parameters
    except (TypeError, ValueError):
        params = {}
    takes_all = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    def manager_turn(mem, body, chat_state, *, on_fragment=None, root_task_id=None, team_allowed=True):
        offered = {"on_fragment": on_fragment, "root_task_id": root_task_id}
        kwargs = {k: v for k, v in offered.items() if takes_all or k in params}
        try:
            return answer(mem, body, chat_state, **kwargs), None
        except Exception as exc:  # noqa: BLE001 — a failed turn replies with its failure, as manager_turn does
            chat_state["_self_failure"] = {"detail": str(exc)}
            return str(exc), None

    return manager_turn

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

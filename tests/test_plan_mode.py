"""Unit tests for Manager Plan mode (``argus_skill.manager.plan_mode``).

Plan mode previews a SHORT step-by-step plan BEFORE any task is queued
(Codex / Claude-Code / Cursor parity). These tests target the pure parser
(:func:`parse_plan_text` / :func:`parse_plan_notes`) and the renderer
(:func:`render_plan`) — no live model — plus :func:`draft_plan`'s explicit
failure surfacing driven by tiny stub runners.
"""
from __future__ import annotations

import json

from argus_skill.manager.plan_mode import (
    Plan,
    PlanStep,
    build_plan_prompt,
    draft_plan,
    parse_plan_notes,
    parse_plan_text,
    render_plan,
)

# ---------------------------------------------------------------------------
# Stub runner shapes (no live model)
# ---------------------------------------------------------------------------

class _Result:
    """Minimal RunnerResult shape (last_agent_message + exit_code)."""

    def __init__(self, msg: str, exit_code: int = 0) -> None:
        self.last_agent_message = msg
        self.exit_code = exit_code


class _StubRunner:
    """A runner whose run_exec returns a fixed reply text."""

    def __init__(self, text: str, exit_code: int = 0) -> None:
        self._text = text
        self._exit = exit_code
        self.calls = 0
        self.last_prompt = ""
        self.last_options = None
        self.last_resume_thread_id = None

    def run_exec(self, *, prompt: str, options, run_label: str,  # noqa: ANN001
                 resume_thread_id=None):
        self.calls += 1
        self.last_prompt = prompt
        self.last_options = options
        self.last_resume_thread_id = resume_thread_id
        return _Result(self._text, self._exit)


class _BoomRunner:
    def run_exec(self, *, prompt: str, options, run_label: str,  # noqa: ANN001
                 resume_thread_id=None):
        raise RuntimeError("backend down")


class _BackendWrapper:
    """Mirrors the REPL runner: no top-level run_exec, only a ``.backend``."""

    def __init__(self, backend) -> None:  # noqa: ANN001
        self.backend = backend


# ---------------------------------------------------------------------------
# parse_plan_text — JSON forms
# ---------------------------------------------------------------------------

def test_parse_json_list_of_objects() -> None:
    text = json.dumps(
        [
            {"title": "Read the repo", "detail": "skim the key modules"},
            {"title": "Write tests", "detail": "cover the parser"},
            {"title": "Run pytest"},
        ]
    )
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == ["Read the repo", "Write tests", "Run pytest"]
    assert steps[0].detail == "skim the key modules"
    assert steps[2].detail == ""  # missing detail → empty, not a crash


def test_parse_json_object_with_steps_and_notes() -> None:
    payload = {
        "steps": [
            {"title": "Profile", "description": "find the bottleneck"},
            {"step": "Optimize", "why": "remove the stall"},
        ],
        "notes": ["assumes B200", "no ncu access"],
    }
    text = json.dumps(payload)
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == ["Profile", "Optimize"]
    # alternative key names map to detail
    assert steps[0].detail == "find the bottleneck"
    assert steps[1].detail == "remove the stall"
    assert parse_plan_notes(text) == ["assumes B200", "no ncu access"]


def test_plan_parser_ignores_domain_specific_step_kinds() -> None:
    text = json.dumps({
        "steps": [
            {
                "title": "Formalize the lemma",
                "detail": "compile it independently",
                "kind": "lean_formalization",
            },
            {
                "title": "Write the explanation",
                "kind": "work",
            },
        ]
    })

    steps = parse_plan_text(text)

    assert [step.title for step in steps] == [
        "Formalize the lemma",
        "Write the explanation",
    ]


def test_plan_prompt_has_no_domain_specific_orchestration_kind() -> None:
    ordinary = build_plan_prompt("ordinary task")
    math = build_plan_prompt(
        "prove the theorem",
        role_banner="MISSION TYPE: MATHEMATICS.",
    )

    assert "lean_formalization" not in ordinary
    assert '"kind"' not in ordinary
    assert "lean_formalization" not in math
    assert '"kind"' not in math


def test_parse_json_list_of_strings() -> None:
    text = json.dumps(["First do A", "Then do B: with a colon detail"])
    steps = parse_plan_text(text)
    assert steps[0].title == "First do A"
    assert steps[1].title == "Then do B"
    assert steps[1].detail == "with a colon detail"


def test_parse_json_in_code_fence() -> None:
    inner = json.dumps({"steps": [{"title": "Only step"}]})
    text = f"```json\n{inner}\n```"
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == ["Only step"]


def test_parse_json_with_prose_around_object() -> None:
    inner = json.dumps({"steps": [{"title": "Embedded"}]})
    text = f"Sure, here is the plan:\n{inner}\nHope that helps!"
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == ["Embedded"]


# ---------------------------------------------------------------------------
# parse_plan_text — numbered / bulleted list fallback
# ---------------------------------------------------------------------------

def test_parse_numbered_list() -> None:
    text = (
        "1. Set up the environment — install deps\n"
        "2) Profile the kernel: find the stall\n"
        "3. Ship it\n"
    )
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == [
        "Set up the environment",
        "Profile the kernel",
        "Ship it",
    ]
    assert steps[0].detail == "install deps"
    assert steps[1].detail == "find the stall"
    assert steps[2].detail == ""


def test_parse_bulleted_list() -> None:
    text = "- Read code\n* Write tests\n• Run them\n"
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == ["Read code", "Write tests", "Run them"]


def test_parse_numbered_list_ignores_prose_lines() -> None:
    text = (
        "Here is my approach:\n"
        "1. Inspect the API\n"
        "this line is not a step\n"
        "2. Implement it\n"
    )
    steps = parse_plan_text(text)
    assert [s.title for s in steps] == ["Inspect the API", "Implement it"]


def test_parse_strips_markdown_bold_in_title() -> None:
    steps = parse_plan_text("1. **Bold title** — detail here")
    assert steps[0].title == "Bold title"
    assert steps[0].detail == "detail here"


# ---------------------------------------------------------------------------
# parse_plan_text / parse_plan_notes — garbage → fail soft
# ---------------------------------------------------------------------------

def test_parse_garbage_returns_empty() -> None:
    assert parse_plan_text("the quick brown fox jumped over nothing") == []
    assert parse_plan_text("{not valid json at all") == []
    assert parse_plan_text("") == []
    assert parse_plan_text("   \n  \n") == []


def test_parse_json_without_steps_key_returns_empty() -> None:
    # Valid JSON, but not a plan shape → no steps (not a crash).
    assert parse_plan_text(json.dumps({"foo": "bar"})) == []


def test_parse_notes_failsoft() -> None:
    assert parse_plan_notes("not json") == []
    assert parse_plan_notes(json.dumps(["a", "b"])) == []  # list, no notes key
    assert parse_plan_notes(json.dumps({"notes": "single caveat"})) == ["single caveat"]
    assert parse_plan_notes(json.dumps({"notes": []})) == []


# ---------------------------------------------------------------------------
# render_plan
# ---------------------------------------------------------------------------

def test_render_plan_contains_titles_details_and_notes() -> None:
    plan = Plan(
        objective="optimize the kernel",
        steps=[
            PlanStep("Profile", "find the stall"),
            PlanStep("Optimize", ""),
        ],
        notes=["assumes B200"],
    )
    out = render_plan(plan)
    assert "optimize the kernel" in out
    assert "1." in out and "2." in out
    assert "Profile" in out
    assert "find the stall" in out
    assert "Optimize" in out
    assert "Notes:" in out
    assert "assumes B200" in out
    # Multi-line, scannable.
    assert out.count("\n") >= 3


def test_render_plan_no_steps_is_safe() -> None:
    out = render_plan(Plan(objective="x", steps=[], notes=[]))
    assert "Plan" in out
    assert "(no steps)" in out


def test_render_plan_surfaces_draft_error() -> None:
    out = render_plan(Plan(objective="x", steps=[], notes=[], error="backend error"))
    assert "draft failed" in out
    assert "backend error" in out


def test_render_plan_with_theme_uses_theme_methods() -> None:
    class _Theme:
        def __init__(self) -> None:
            self.used: list[str] = []

        def _mark(self, name: str, text: str) -> str:
            self.used.append(name)
            return f"<{name}>{text}</{name}>"

        def bold(self, text: str) -> str:
            return self._mark("bold", text)

        def cyan(self, text: str) -> str:
            return self._mark("cyan", text)

        def gray(self, text: str) -> str:
            return self._mark("gray", text)

        def dim(self, text: str) -> str:
            return self._mark("dim", text)

    theme = _Theme()
    plan = Plan(objective="o", steps=[PlanStep("A", "d")], notes=["n"])
    out = render_plan(plan, theme)
    assert "<bold>" in out and "<cyan>" in out and "<dim>" in out
    assert "bold" in theme.used and "cyan" in theme.used


def test_render_plan_failsoft_theme_method_raises() -> None:
    class _BadTheme:
        def bold(self, text: str) -> str:
            raise RuntimeError("nope")

    plan = Plan(objective="o", steps=[PlanStep("A", "")], notes=[])
    out = render_plan(plan, _BadTheme())  # must not raise
    assert "A" in out


# ---------------------------------------------------------------------------
# draft_plan — happy path + explicit failure surfacing (stub runners only)
# ---------------------------------------------------------------------------

def test_draft_plan_parses_stub_reply() -> None:
    payload = json.dumps(
        {
            "steps": [
                {"title": "Read", "detail": "skim"},
                {"title": "Write", "detail": "code"},
            ],
            "notes": ["caveat"],
        }
    )
    runner = _StubRunner(payload)
    plan = draft_plan(runner, "do the thing")
    assert runner.calls == 1
    assert plan.objective == "do the thing"
    assert [s.title for s in plan.steps] == ["Read", "Write"]
    assert plan.notes == ["caveat"]


def test_grounded_plan_gets_full_repository_tools() -> None:
    runner = _StubRunner(json.dumps([{"title": "Inspect sibling"}]))

    draft_plan(
        runner,
        "task\n\n## Manager project grounding\nInspect parser.py",
        working_dir="/tmp/project",
        dangerous_yolo=True,
        max_seconds=60,
        allow_repository_inspection=True,
    )

    assert "Manager project grounding" in runner.last_prompt
    assert "Inspect the repository with tools" in runner.last_prompt
    assert runner.last_options.working_dir == "/tmp/project"
    assert runner.last_options.dangerous_yolo is True
    assert runner.last_options.sandbox_mode is None
    assert callable(
        runner.last_options.external_interrupt_reason_provider
    )
    assert runner.last_resume_thread_id is None


def test_draft_plan_resolves_backend_wrapper() -> None:
    # The REPL runner exposes the backend via ``.backend`` (no top-level run_exec).
    inner = _StubRunner(json.dumps([{"title": "Step one"}]))
    plan = draft_plan(_BackendWrapper(inner), "obj")
    assert inner.calls == 1
    assert [s.title for s in plan.steps] == ["Step one"]


def test_draft_plan_trims_to_eight_steps() -> None:
    many = [{"title": f"Step {i}"} for i in range(20)]
    runner = _StubRunner(json.dumps(many))
    plan = draft_plan(runner, "obj")
    assert len(plan.steps) == 8


def test_draft_plan_runner_error_sets_explicit_error() -> None:
    plan = draft_plan(_BoomRunner(), "my objective")  # must not raise
    assert plan.steps == []
    assert "backend error" in plan.error


def test_draft_plan_missing_runner_sets_explicit_error() -> None:
    plan = draft_plan(None, "lonely objective")
    assert plan.steps == []
    assert "no runner backend" in plan.error


def test_draft_plan_nonzero_exit_sets_explicit_error() -> None:
    runner = _StubRunner(json.dumps([{"title": "Ignored"}]), exit_code=1)
    plan = draft_plan(runner, "obj")
    assert plan.steps == []
    assert "non-zero" in plan.error


def test_draft_plan_garbage_reply_sets_explicit_error() -> None:
    runner = _StubRunner("I am not going to give you JSON, sorry.")
    plan = draft_plan(runner, "obj")
    assert plan.steps == []
    assert "unparseable" in plan.error


def test_draft_plan_emits_sink_events() -> None:
    events: list[dict] = []

    class _Sink:
        def handle_event(self, event: dict) -> None:
            events.append(event)

    runner = _StubRunner(json.dumps([{"title": "A"}]))
    draft_plan(runner, "obj", sink=_Sink())
    types = [e.get("type") for e in events]
    assert "plan.draft.start" in types
    assert "plan.draft.done" in types

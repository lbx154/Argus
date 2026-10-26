"""Fixed text goes once per provider session; a heavy session is not resumed."""
from __future__ import annotations

import json
from pathlib import Path

from argus.core.models import RunnerResult
from argus.core.role_session import ROLE_SESSION_RESUME_BUDGET_TOKENS, RoleSessionCapsule
from argus.core.run_gateway import run_exec
from argus.core.session_ledger import (
    elide_sent_sections,
    record_sent,
    session_size_tokens,
    turn_size_tokens,
)

RULES = "## Stage rules\n" + "- keep the evaluation split fixed\n" * 30
SKILLS = "## Skill libraries (on-demand)\n" + "1. `/opt/argus/builtin_skills`\n" * 30


def _prompt(task: str, *, rules: str = RULES) -> str:
    return "\n\n".join([f"## This turn\n{task}", rules, SKILLS]) + "\n"


class _Backend:
    """Records each prompt; answers on ``thread`` (or the resumed one)."""

    def __init__(self, *, thread: str = "t-1", missing: bool = False) -> None:
        self.calls: list[dict] = []
        self.thread = thread
        self.missing = missing

    def run_exec(self, *, prompt, options=None, run_label="", resume_thread_id=None):
        self.calls.append({"prompt": prompt, "resume": resume_thread_id})
        if resume_thread_id and self.missing:
            self.missing = False
            return RunnerResult(exit_code=1, fatal_error="No session, task, or name matched t-old")
        item = {"type": "item.completed", "item": {"type": "command_execution",
                "command": "cat paper.txt", "aggregated_output": "x" * 90_000}}
        return RunnerResult(
            exit_code=0, thread_id=resume_thread_id or self.thread,
            stdout_lines=[json.dumps(item)],
        )


def test_resumed_turn_sends_only_sections_the_thread_lacks() -> None:
    backend = _Backend()
    run_exec(backend, prompt=_prompt("first task"), run_label="engineer")
    changed = RULES + "- also report the variance\n"
    run_exec(backend, prompt=_prompt("second task"), run_label="engineer", resume_thread_id="t-1")
    run_exec(backend, prompt=_prompt("third", rules=changed), run_label="engineer",
             resume_thread_id="t-1")

    first, second, third = (call["prompt"] for call in backend.calls)
    assert RULES in first and SKILLS in first
    assert "second task" in second and RULES not in second and SKILLS not in second
    assert "`Stage rules`; `Skill libraries (on-demand)`" in second
    # A changed rule is the change: it goes again; the unchanged block does not.
    assert "also report the variance" in third and SKILLS not in third


def test_sections_from_any_earlier_turn_count_and_notes_merge() -> None:
    record_sent("t-9", _prompt("one"), holds_prompt=True, added_tokens=0)
    planner_elided = (
        "## Continued cycle\nnew facts\n\n## Unchanged since your previous turn\n"
        "These sections are identical ...: `Plan contract`.\n\n" + SKILLS
    )
    sent = elide_sent_sections("t-9", planner_elided)
    assert SKILLS not in sent
    assert sent.count("## Unchanged since your previous turn") == 1
    assert "`Plan contract`; `Skill libraries (on-demand)`" in sent


def test_lost_or_failed_sessions_get_everything_again() -> None:
    backend = _Backend(missing=True, thread="t-new")
    record_sent("t-old", _prompt("one"), holds_prompt=True, added_tokens=0)
    run_exec(backend, prompt=_prompt("two"), run_label="engineer", resume_thread_id="t-old")
    retry = backend.calls[-1]
    assert retry["resume"] is None and RULES in retry["prompt"] and SKILLS in retry["prompt"]
    # A turn that did not complete leaves no section record for its thread.
    record_sent("t-failed", _prompt("x"), holds_prompt=False, added_tokens=5)
    assert elide_sent_sections("t-failed", _prompt("y")) == _prompt("y")
    assert session_size_tokens("t-failed") == 5


def test_turn_size_counts_prompt_and_capped_tool_output() -> None:
    item = {"type": "item.completed", "item": {"type": "command_execution",
            "command": "cat big.txt", "aggregated_output": "y" * 500_000}}
    size = turn_size_tokens("p" * 3_000, [json.dumps(item), "not json"])
    assert 13_000 < size < 15_000  # 3 KB prompt + one output capped at 40 KB


def _capsule(path: Path, workdir: Path) -> RoleSessionCapsule:
    return RoleSessionCapsule.open(
        role="engineer", policy="rolling", objective_revision="goal", workdir=workdir,
        backend="codex", model="m", checkpoint_path=None, path=path,
    )


def test_a_heavy_session_starts_fresh_instead_of_resuming(tmp_path: Path) -> None:
    capsule = _capsule(tmp_path / "capsule.json", tmp_path)
    capsule.prepare()
    capsule.complete(RunnerResult(exit_code=0, thread_id="t-light"), prompt="p")
    record_sent("t-light", "p", holds_prompt=True, added_tokens=20_000)
    assert _capsule(tmp_path / "capsule.json", tmp_path).prepare() == "t-light"

    record_sent("t-light", "p", holds_prompt=True, added_tokens=ROLE_SESSION_RESUME_BUDGET_TOKENS)
    heavy = _capsule(tmp_path / "capsule.json", tmp_path)
    assert heavy.prepare() is None
    assert heavy.rotation_reason == "context_budget"


def test_operator_context_is_not_appended_twice() -> None:
    from argus.core.operator_context import append_operator_context

    block = "## OperatorContext\nSafety first.\noperator_context_revision=0"
    once = append_operator_context("message", block)
    assert append_operator_context(once, block) == once
    assert once.count("## OperatorContext") == 1


def test_a_new_process_resuming_a_thread_charges_only_its_own_turn() -> None:
    from argus.adapters.agent_cli_backend._result import UsageAccumulator

    first, later = UsageAccumulator(), UsageAccumulator()
    assert first.usage_delta_for_thread(thread_id="t-m", raw_totals=(730, 640, 10, 1)) == (730, 640, 10, 1)
    # Another backend object (e.g. after a restart) resumes the same thread.
    assert later.usage_delta_for_thread(thread_id="t-m", raw_totals=(1460, 1280, 17, 2)) == (730, 640, 7, 1)


def test_manager_chat_resume_drops_the_skill_block_the_thread_already_holds() -> None:
    from argus.core.operator_context import append_operator_context
    from argus.roles.prompts.manager import build_manager_prompt

    block = "## Vertical for this single-agent task: software\n" + "Apply methods.\n" * 40 + (
        "\n## Skill libraries (on-demand)\n" + "1. `/opt/argus/builtin_skills`\n" * 30
    )
    context = "## OperatorContext\n" + "Safety first.\n" * 40
    # The layout _self_reply builds: the page, then the block; later turns
    # are the message, then the block.
    fresh = append_operator_context("\n\n".join([
        build_manager_prompt("first", workspace="/w", state_root="/s"), block,
    ]), context)
    record_sent("t-chat", fresh, holds_prompt=True, added_tokens=0)
    resumed = elide_sent_sections("t-chat", append_operator_context(
        "\n\n".join(["second message", block]), context))
    assert resumed.startswith("second message")
    assert "Apply methods." not in resumed and "builtin_skills" not in resumed
    moved = elide_sent_sections("t-chat", "\n\n".join(
        ["third", block.replace("/opt/argus", "/opt/argus-2")]))
    assert "/opt/argus-2/builtin_skills" in moved

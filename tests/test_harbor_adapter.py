"""Tests for the offline-testable helpers in benchmarks/harbor_adapter.py.

We can't unit-test the full ``ArgusSkillCodex.run`` without Harbor's
runtime (it needs a ``BaseEnvironment``), but we can exhaustively test
the logic that runs on host: codex JSON parsing, prompt builder,
host-prep ablation flags.
"""
from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture(scope="module")
def adapter() -> ModuleType:
    """Load benchmarks/harbor_adapter as a module without installing harbor."""
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "benchmarks" / "harbor_adapter.py"
    spec = importlib.util.spec_from_file_location(
        "argus_skill_benchmarks_harbor_adapter", src
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_agent_messages_picks_up_completed_items(adapter):
    text = (
        '{"type":"thread.started","thread_id":"thr_x"}\n'
        '{"type":"item.started","item":{"type":"agent_message"}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}\n'
        'arbitrary non-json\n'
        '{"type":"item.completed","item":{"type":"reasoning","text":"ignore me"}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"second"}}\n'
        '{"type":"turn.completed"}\n'
    )
    msgs = adapter._parse_agent_messages_from_jsonl(text)
    assert msgs == ["first", "second"]


def test_parse_agent_messages_handles_empty(adapter):
    assert adapter._parse_agent_messages_from_jsonl("") == []
    assert adapter._parse_agent_messages_from_jsonl("not json\nnot json either\n") == []


def test_extract_thread_id(adapter):
    text = (
        'foo bar\n'
        '{"type":"thread.started","thread_id":"thr_abc"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"x"}}\n'
    )
    assert adapter._extract_thread_id_from_jsonl(text) == "thr_abc"
    assert adapter._extract_thread_id_from_jsonl("nothing") is None


def test_round_prompt_with_skill_and_feedback(adapter):
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="fix the bug in foo.py",
        skill_text="## Title\nDebug regression\n",
        review_feedback="run pytest first",
        round_idx=2,
        total_rounds=3,
    )
    assert "## Skill guide" in prompt
    assert "## Reviewer hint (from round 1)" in prompt
    assert "## Task\nfix the bug in foo.py" in prompt
    # v7: Reporting-requirements and "round X of Y" reminders were dropped.
    assert "## Reporting requirements" not in prompt
    assert "round 2 of 3" not in prompt


def test_round_prompt_without_skill(adapter):
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="task",
        skill_text="",
        review_feedback=None,
        round_idx=1,
        total_rounds=1,
    )
    assert "## Skill guide" not in prompt
    assert "## Reviewer hint" not in prompt
    assert "## Previous attempt" not in prompt
    assert "## Task\ntask" in prompt
    # v7: round 1 mirrors skill-cap-phaseA's exact shape — no Reporting
    # requirements, no "round X of Y" hint.
    assert "## Reporting requirements" not in prompt
    assert "round 1 of 1" not in prompt


def test_round_prompt_round1_matches_sc_a_shape(adapter):
    """v7: round 1 with a skill must produce a prompt with exactly the same
    structural sections as skill-cap-phaseA's adapter — bare guide intro +
    `## Skill guide` + `## Task`. No Reporting-requirements, no Previous-
    attempt, no Reviewer hint, no round-X-of-Y tail."""
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="do the thing",
        skill_text="## Title\nSome guide\n",
        review_feedback=None,
        round_idx=1,
        total_rounds=2,
    )
    assert "You have been provided with a reusable skill guide" in prompt
    assert "## Skill guide" in prompt
    assert "## Task\ndo the thing" in prompt
    # The R1 prompt must NOT pre-leak any retry / reviewer scaffolding.
    assert "## Previous attempt" not in prompt
    assert "## Reviewer hint" not in prompt
    assert "## Reporting requirements" not in prompt
    assert "round 1 of 2" not in prompt


def test_round_prompt_passes_previous_failure_to_r2(adapter):
    """v7: R2 only fires on objective R1 failure. The retry prompt must
    surface the failure mode and (when available) the engineer's last
    summary, framed as retry context — not as reviewer skepticism."""
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="solve task",
        skill_text="",
        review_feedback="focus on missing cases",
        round_idx=2,
        total_rounds=2,
        previous_round_summary="I edited /app/main.py partially.",
        previous_round_failure="engineer round timed out after 900s",
    )
    assert "## Previous attempt (round 1)" in prompt
    assert "RETRY CONTEXT" in prompt
    assert "Failure mode: engineer round timed out after 900s" in prompt
    assert "I edited /app/main.py partially." in prompt
    assert "## Reviewer hint (from round 1)" in prompt
    assert "focus on missing cases" in prompt


def test_round_prompt_truncates_huge_previous_summary(adapter):
    """Bound the previous-round summary so we don't blow past the prompt
    cap when an earlier round dumped a 50 KB self-report."""
    huge = "X" * 10000
    prompt = adapter.ArgusSkillCodex._build_round_prompt(
        instruction="t",
        skill_text="",
        review_feedback=None,
        round_idx=2,
        total_rounds=2,
        previous_round_summary=huge,
        previous_round_failure="engineer produced no agent message",
    )
    assert "[... truncated ...]" in prompt
    # truncation cap is 4000 chars; total should reflect that
    assert prompt.count("X") <= 4001


def test_bool_env_handles_falsey_values(adapter, monkeypatch):
    monkeypatch.setenv("FOO", "")
    assert adapter._bool_env("FOO") is False
    monkeypatch.setenv("FOO", "0")
    assert adapter._bool_env("FOO") is False
    monkeypatch.setenv("FOO", "no")
    assert adapter._bool_env("FOO") is False
    monkeypatch.setenv("FOO", "false")
    assert adapter._bool_env("FOO") is False
    monkeypatch.delenv("FOO")
    assert adapter._bool_env("FOO", default=True) is True
    assert adapter._bool_env("FOO", default=False) is False


def test_bool_env_handles_truthy_values(adapter, monkeypatch):
    for value in ("1", "true", "yes", "on", "anything-not-explicit-false"):
        monkeypatch.setenv("FOO", value)
        assert adapter._bool_env("FOO") is True


def test_int_and_float_env_default_on_invalid(adapter, monkeypatch):
    monkeypatch.setenv("BAR", "not-a-number")
    assert adapter._int_env("BAR", 7) == 7
    assert adapter._float_env("BAR", 1.25) == 1.25
    monkeypatch.setenv("BAR", "42")
    assert adapter._int_env("BAR", 7) == 42
    assert adapter._float_env("BAR", 1.25) == 42.0


def test_compute_model_cost_usd_applies_cached_discount(adapter, monkeypatch):
    fake = ModuleType("litellm")
    fake.model_cost = {
        "gpt-5.4-mini": {
            "input_cost_per_token": 2.0,
            "output_cost_per_token": 5.0,
            "cache_read_input_token_cost": 0.5,
        }
    }
    monkeypatch.setitem(sys.modules, "litellm", fake)

    cost = adapter._compute_model_cost_usd("gpt-5.4-mini", 10, 3, 4)
    assert cost == 29.0


def test_sum_all_session_tokens_accumulates_token_counts(adapter, tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    first = sessions / "2026" / "05" / "15" / "rollout-a.jsonl"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "session-a"}}),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 11,
                                    "cached_input_tokens": 3,
                                    "output_tokens": 7,
                                }
                            },
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    second = sessions / "2026" / "05" / "15" / "rollout-b.jsonl"
    second.parent.mkdir(parents=True, exist_ok=True)
    second.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "session-b"}}),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {
                                    "input_tokens": 13,
                                    "cached_input_tokens": 5,
                                    "output_tokens": 9,
                                }
                            },
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    usage = adapter._sum_all_session_tokens(sessions)

    assert usage["total_input"] == 24
    assert usage["total_cached"] == 8
    assert usage["total_output"] == 16
    assert len(usage["sessions"]) == 2


def test_no_skill_ablation_skips_host_prep(adapter, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_NO_SKILL", "1")
    prep = adapter._do_host_prep("any task")
    assert prep.skill_used is False
    assert prep.skill_text == ""
    assert prep.fallback_reason == "no_skill_ablation"
    assert prep.scientist_tokens == 0


def test_prepare_container_writes_concrete_openai_base_url(
    adapter: ModuleType,
) -> None:
    import asyncio as _asyncio
    import logging

    calls: list[tuple[str, dict[str, str]]] = []

    class _FakeEnvironment:
        default_user = None

    async def _fake_exec_as_agent(
        environment: Any,  # noqa: ARG001
        *,
        command: str,
        env: dict[str, str],
    ) -> None:
        calls.append((command, dict(env)))

    async def _fake_exec_as_root(
        environment: Any,  # noqa: ARG001
        *,
        command: str,
    ) -> None:
        raise AssertionError(f"unexpected root command: {command}")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    inst.logger = logging.getLogger("test")
    inst.exec_as_agent = _fake_exec_as_agent
    inst.exec_as_root = _fake_exec_as_root
    inst._resolve_auth_json_path = lambda: None
    inst._get_env = lambda name: {
        "OPENAI_API_KEY": "secret",
        "OPENAI_BASE_URL": "https://example.invalid/openai/v1",
    }.get(name)
    inst._build_register_skills_command = lambda: ""
    inst._build_register_mcp_servers_command = lambda: ""

    env, setup_command = _asyncio.get_event_loop().run_until_complete(
        inst._prepare_container(_FakeEnvironment())
    )

    assert env["OPENAI_BASE_URL"] == "https://example.invalid/openai/v1/"
    assert 'model_provider = "codex"' in setup_command
    assert 'base_url = "https://example.invalid/openai/v1/"' in setup_command
    assert 'wire_api = "responses"' in setup_command
    assert "codex login --with-api-key" in setup_command
    assert calls
    assert calls[0][0].startswith('mkdir -p "$CODEX_HOME" ')
    assert calls[0][1]["CODEX_HOME"] == inst._REMOTE_CODEX_HOME.as_posix()


# --- distill-on-miss save_distilled None-branch -----------------------------
#
# Regression for tb2-ablation-2026-05-10 finding 2 (`harbor_adapter.py`
# called `.render()` on the None that ``SkillStore.save_distilled`` returns
# when its quality gate rejects). We exercise the *real* ``_do_host_prep``
# path with the matcher / distiller / store stubbed so we can drive the
# branch deterministically.


class _StubMatchEmpty:
    def find_relevant(self, _instruction):
        return [], 0


class _StubStoreGateReject(_StubMatchEmpty):
    """SkillStore that always returns None from save_distilled (gate reject)."""

    save_distilled_calls = 0

    def save_distilled(self, **_kwargs):
        type(self).save_distilled_calls += 1
        return None


class _StubStoreGateAccept(_StubMatchEmpty):
    class _Skill:
        name = "stub-skill"

        def render(self):
            return "## Stub skill body"

    def save_distilled(self, **_kwargs):
        return self._Skill()


class _StubStoreParseError(_StubMatchEmpty):
    def save_distilled(self, **_kwargs):
        raise ValueError("could not parse")


class _StubStoreCaptureSkillsDir(_StubMatchEmpty):
    seen_dirs: list[Path] = []

    class _Skill:
        name = "stub-skill"

        def render(self):
            return "## Stub skill body"

    def __init__(self, skills_dir, **_kwargs):
        type(self).seen_dirs.append(Path(skills_dir))

    def find_relevant(self, _instruction):
        return [self._Skill()], 0


class _StubDistillResult:
    last_agent_message = "raw distill output text"
    input_tokens = 100
    output_tokens = 50


class _StubDistiller:
    def __init__(self, _backend):
        pass

    def distill(self, *, task_description, config):  # noqa: ARG002
        return _StubDistillResult()


class _StubDistillerCfg:
    def __init__(self, **_kwargs):
        pass


class _StubBackend:
    def __init__(self, *_a, **_kw):
        pass


def _patch_deps(adapter, monkeypatch, store_cls):
    deps = {
        "CodexRunnerBackend": _StubBackend,
        "Reviewer": object,
        "ReviewerConfig": object,
        "Distiller": _StubDistiller,
        "DistillerConfig": _StubDistillerCfg,
        "Skill": object,
        "SkillStore": lambda *_a, **_kw: store_cls(),
    }
    monkeypatch.setattr(adapter, "_import_argus_skill", lambda: deps)
    monkeypatch.delenv("ARGUS_SKILL_HARBOR_NO_SKILL", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_HARBOR_NO_DISTILL", raising=False)


def test_save_distilled_none_falls_back_to_raw_text(adapter, monkeypatch, tmp_path):
    """Quality gate rejects → save_distilled returns None → adapter must
    NOT call .render() on None. It should instead use the raw distill
    text as the skill hint and record fallback_reason='skill_gate_rejected'."""
    _patch_deps(adapter, monkeypatch, _StubStoreGateReject)
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_SKILLS_DIR", str(tmp_path / "skills"))

    prep = adapter._do_host_prep("recover the missing commit on master")

    assert prep.skill_text == "raw distill output text"
    assert prep.skill_used is True
    assert prep.fallback_reason == "skill_gate_rejected"
    assert prep.matched is False
    assert prep.matched_skill is None
    # crucial: the misleading "save_distilled failed: 'NoneType'..." message
    # from the old code path should no longer be produced.
    assert "parse_failure" not in (prep.fallback_reason or "")


def test_save_distilled_accepted_uses_render(adapter, monkeypatch, tmp_path):
    _patch_deps(adapter, monkeypatch, _StubStoreGateAccept)
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_SKILLS_DIR", str(tmp_path / "skills"))

    prep = adapter._do_host_prep("any task")

    assert prep.skill_text == "## Stub skill body"
    assert prep.fallback_reason is None
    assert prep.matched_skill is not None


def test_save_distilled_real_exception_records_parse_failure(
    adapter, monkeypatch, tmp_path
):
    """A real exception inside save_distilled (e.g. parse error) still
    flows through the broad ``except Exception`` and yields a
    ``parse_failure:<ExceptionType>`` fallback_reason — separate from the
    legitimate gate-reject path."""
    _patch_deps(adapter, monkeypatch, _StubStoreParseError)
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_SKILLS_DIR", str(tmp_path / "skills"))

    prep = adapter._do_host_prep("any task")

    assert prep.fallback_reason == "parse_failure:ValueError"
    assert prep.skill_text == "raw distill output text"


def test_do_host_prep_default_skills_dir_stays_outside_results_tree(
    adapter, monkeypatch, tmp_path
) -> None:
    deps = {
        "CodexRunnerBackend": _StubBackend,
        "Reviewer": object,
        "ReviewerConfig": object,
        "Distiller": _StubDistiller,
        "DistillerConfig": _StubDistillerCfg,
        "Skill": object,
        "SkillStore": _StubStoreCaptureSkillsDir,
    }
    monkeypatch.setattr(adapter, "_import_argus_skill", lambda: deps)
    monkeypatch.delenv("ARGUS_SKILL_HARBOR_SKILLS_DIR", raising=False)
    monkeypatch.setattr(adapter.Path, "home", lambda: tmp_path)

    _StubStoreCaptureSkillsDir.seen_dirs.clear()

    prep = adapter._do_host_prep("any task")
    expected = tmp_path / ".cache" / "argus-skill-harbor" / "skills"

    assert _StubStoreCaptureSkillsDir.seen_dirs == [expected]
    assert prep.skill_used is True
    assert "benchmarks/results" not in str(expected)


# --- reviewer budget default ------------------------------------------------


def test_reviewer_budget_default_is_at_least_120s(adapter):
    """Regression for tb2-ablation-2026-05-10 finding 1: default 60 s was
    empirically too tight for gpt-5.4 reviewer calls (6/6 timeouts).
    Don't let it silently regress below 120 s without re-validation."""
    assert adapter._DEFAULT_REVIEWER_BUDGET >= 120.0


def test_verifier_pass_short_circuit_decision_is_reviewer_shaped(adapter):
    decision = adapter._verifier_pass_short_circuit_decision(
        {"passed": True, "exit_code": 0}
    )

    assert decision["status"] == "done"
    assert decision["confidence"] == 1.0
    assert decision["source"] == "verifier_pass_short_circuit"
    assert decision["input_tokens"] == 0
    assert adapter._verifier_pass_short_circuit_decision({"passed": False}) is None
    assert adapter._verifier_pass_short_circuit_decision(None) is None


def test_reviewer_gate_controls_continue_retry(adapter):
    decision = {"status": "continue"}

    assert adapter._should_retry_after_review(
        decision, reviewer_gate=True, round_idx=1, max_rounds=2
    )
    assert not adapter._should_retry_after_review(
        decision, reviewer_gate=False, round_idx=1, max_rounds=2
    )
    assert not adapter._should_retry_after_review(
        decision, reviewer_gate=True, round_idx=2, max_rounds=2
    )
    assert not adapter._should_retry_after_review(
        {"status": "done"}, reviewer_gate=True, round_idx=1, max_rounds=2
    )


# --- v4 priority 1: reviewer-sees-acceptance-checks plumbing ----------------
#
# RC1 (see tb2-ablation-2026-05-10-v3/RESULTS.md §3): for v3 the reviewer
# was called with ``checks=[]`` regardless of what the engineer actually
# produced in the container — making it a pure-text classifier that
# couldn't tell a passing test from a failing one. v4 introduces an
# acceptance-checks pipe; the tests below pin its contract.


def test_parse_checks_commands_handles_blanks_and_comments(adapter):
    raw = "\n".join([
        "pytest /tests/ -x",
        "  ",                # blank → drop
        "# this is a comment, drop it",
        "  git status --porcelain  ",  # leading/trailing space → strip
        "",
    ])
    assert adapter._parse_checks_commands(raw) == [
        "pytest /tests/ -x",
        "git status --porcelain",
    ]
    assert adapter._parse_checks_commands(None) == []
    assert adapter._parse_checks_commands("") == []


def test_tail_check_output_truncates_to_cap(adapter):
    big = "X" * (adapter._CHECK_OUTPUT_TAIL_CHARS + 500)
    tail = adapter._tail_check_output(big)
    # We keep the LAST max_chars characters (a tail, not a head) so the
    # reviewer sees the most recent output (e.g. pytest's failure summary).
    assert len(tail) == adapter._CHECK_OUTPUT_TAIL_CHARS
    assert tail == big[-adapter._CHECK_OUTPUT_TAIL_CHARS:].strip()
    short = "tiny output"
    assert adapter._tail_check_output(short) == "tiny output"
    assert adapter._tail_check_output(None) == ""


def test_invoke_reviewer_passes_checks_through_to_reviewer(
    adapter: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug RC1 fixes: ``_invoke_reviewer`` used to hardcode
    ``checks=[]``. Verify that a non-empty ``checks_data`` actually
    reaches ``Reviewer.evaluate`` as a list of CheckResult-shaped
    objects."""
    captured: dict[str, Any] = {}

    class _StubCheckResult:
        def __init__(
            self,
            *,
            command: str,
            exit_code: int,
            passed: bool,
            output_tail: str,
        ) -> None:
            self.command = command
            self.exit_code = exit_code
            self.passed = passed
            self.output_tail = output_tail

    class _StubReviewerCfg:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    class _StubDecision:
        status = "done"
        confidence = 0.9
        reason = "checks all passed"
        next_action = ""

    class _StubReviewer:
        def __init__(self, _backend: Any) -> None:
            pass

        def evaluate(self, **kwargs: Any) -> _StubDecision:
            captured["kwargs"] = kwargs
            return _StubDecision()

    deps = {
        "CodexRunnerBackend": lambda *a, **kw: object(),
        "Reviewer": _StubReviewer,
        "ReviewerConfig": _StubReviewerCfg,
        "CheckResult": _StubCheckResult,
        "Distiller": object,
        "DistillerConfig": object,
        "Skill": object,
        "SkillStore": object,
    }
    monkeypatch.setattr(adapter, "_import_argus_skill", lambda: deps)

    out = adapter._invoke_reviewer(
        instruction="fix the bug",
        last_msg="I claim done",
        round_idx=1,
        thread_id=None,
        main_error=None,
        checks_data=[
            {
                "command": "pytest /tests/ -x",
                "exit_code": 0,
                "passed": True,
                "output_tail": "5 passed in 0.42s",
            },
            {
                "command": "ruff check .",
                "exit_code": 1,
                "passed": False,
                "output_tail": "1 error",
            },
        ],
    )
    assert out["status"] == "done"
    forwarded = captured["kwargs"]["checks"]
    assert len(forwarded) == 2
    assert forwarded[0].command == "pytest /tests/ -x"
    assert forwarded[0].passed is True
    assert forwarded[0].output_tail == "5 passed in 0.42s"
    assert forwarded[1].passed is False
    assert forwarded[1].exit_code == 1


def test_invoke_reviewer_with_empty_checks_data_passes_empty_list(
    adapter: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backwards-compat: legacy callers (or unconfigured CHECKS_CMD) pass
    no checks → reviewer must still be invoked, with checks=[]."""
    captured: dict[str, Any] = {}

    class _Stub:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

    class _StubDecision:
        status = "continue"
        confidence = 0.0
        reason = ""
        next_action = ""

    class _StubReviewer:
        def __init__(self, _backend: Any) -> None:
            pass

        def evaluate(self, **kwargs: Any) -> _StubDecision:
            captured["kwargs"] = kwargs
            return _StubDecision()

    deps = {
        "CodexRunnerBackend": _Stub,
        "Reviewer": _StubReviewer,
        "ReviewerConfig": _Stub,
        "CheckResult": _Stub,
        "Distiller": object,
        "DistillerConfig": object,
        "Skill": object,
        "SkillStore": object,
    }
    monkeypatch.setattr(adapter, "_import_argus_skill", lambda: deps)

    adapter._invoke_reviewer(
        instruction="x",
        last_msg="y",
        round_idx=1,
        thread_id=None,
        main_error=None,
        checks_data=None,
    )
    assert captured["kwargs"]["checks"] == []


def test_collect_checks_runs_each_command_and_captures_result(
    adapter: ModuleType,
) -> None:
    """``_collect_checks`` must run each command via ``environment.exec``
    in order, never raise on non-zero exit, and produce a CheckResult-
    shaped dict per command."""
    import asyncio as _asyncio

    calls: list[dict[str, Any]] = []

    class _FakeExecResult:
        def __init__(self, return_code: int, stdout: str) -> None:
            self.return_code = return_code
            self.stdout = stdout

    class _FakeEnvironment:
        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> _FakeExecResult:
            calls.append({"command": command, "env": env, "timeout_sec": timeout_sec})
            if "pytest" in command:
                return _FakeExecResult(0, "5 passed")
            if "ruff" in command:
                return _FakeExecResult(1, "found 2 errors")
            raise RuntimeError(f"unexpected command: {command}")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    # ``logger`` is read by _collect_checks; provide a no-op stand-in.
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_checks(
            environment=_FakeEnvironment(),
            env={"FOO": "bar"},
            commands=["pytest /tests/ -x", "ruff check ."],
            timeout_sec=30,
        )
    )
    assert len(out) == 2
    assert out[0]["command"] == "pytest /tests/ -x"
    assert out[0]["exit_code"] == 0
    assert out[0]["passed"] is True
    assert "5 passed" in out[0]["output_tail"]
    assert out[1]["command"] == "ruff check ."
    assert out[1]["exit_code"] == 1
    assert out[1]["passed"] is False
    # Each command was invoked with a 2>&1 merge so the reviewer sees
    # combined stdout+stderr in the tail.
    for entry in calls:
        assert entry["command"].endswith("2>&1")
        assert entry["timeout_sec"] == 30
        assert entry["env"] == {"FOO": "bar"}


def test_collect_checks_preserves_alternate_output_fields(
    adapter: ModuleType,
) -> None:
    import asyncio as _asyncio

    class _FakeExecResult:
        def __init__(
            self,
            return_code: int,
            *,
            stdout: str = "",
            stderr: str = "",
            output: str = "",
            combined_output: str = "",
        ) -> None:
            self.return_code = return_code
            self.stdout = stdout
            self.stderr = stderr
            self.output = output
            self.combined_output = combined_output

    class _FakeEnvironment:
        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> _FakeExecResult:
            if "pytest" in command:
                return _FakeExecResult(0, output="5 passed in 0.42s")
            if "ruff" in command:
                return _FakeExecResult(1, stderr="1 error")
            raise RuntimeError(f"unexpected command: {command}")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_checks(
            environment=_FakeEnvironment(),
            env={"FOO": "bar"},
            commands=["pytest /tests/ -x", "ruff check ."],
            timeout_sec=30,
        )
    )
    assert len(out) == 2
    assert out[0]["passed"] is True
    assert out[0]["exit_code"] == 0
    assert out[0]["output_tail"] == "5 passed in 0.42s"
    assert out[1]["passed"] is False
    assert out[1]["exit_code"] == 1
    assert out[1]["output_tail"] == "1 error"


def test_collect_checks_with_no_commands_returns_empty(
    adapter: ModuleType,
) -> None:
    import asyncio as _asyncio

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_checks(
            environment=object(),  # never touched
            env={},
            commands=[],
            timeout_sec=30,
        )
    )
    assert out == []


def test_collect_checks_per_command_timeout_becomes_failed_check(
    adapter: ModuleType,
) -> None:
    """A RuntimeError from harbor's docker exec (typically a per-command
    timeout) must NOT abort the whole batch — it must be recorded as a
    failed CheckResult so the reviewer sees the timeout, not silence."""
    import asyncio as _asyncio

    class _FakeEnvironment:
        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> Any:  # noqa: ARG002
            raise RuntimeError("Command timed out after 60 seconds")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_checks(
            environment=_FakeEnvironment(),
            env={},
            commands=["sleep 9999"],
            timeout_sec=1,
        )
    )
    assert len(out) == 1
    assert out[0]["passed"] is False
    assert out[0]["exit_code"] == -1
    assert "RuntimeError" in out[0]["output_tail"]


def test_collect_v12_verifier_preserves_alternate_output_fields(
    adapter: ModuleType,
) -> None:
    import asyncio as _asyncio

    class _FakeExecResult:
        def __init__(
            self,
            return_code: int,
            *,
            stdout: str = "",
            stderr: str = "",
            output: str = "",
            combined_output: str = "",
        ) -> None:
            self.return_code = return_code
            self.stdout = stdout
            self.stderr = stderr
            self.output = output
            self.combined_output = combined_output

    class _FakeEnvironment:
        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> _FakeExecResult:
            if command.startswith("test -f /tests/test.sh"):
                return _FakeExecResult(0, output="exists")
            if command.startswith("bash /tests/test.sh"):
                return _FakeExecResult(1, combined_output="FAILED test_outputs.py::test_x")
            raise RuntimeError(f"unexpected command: {command}")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_v12_verifier(
            environment=_FakeEnvironment(),
            env={"FOO": "bar"},
            timeout_sec=30,
        )
    )
    assert out is not None
    assert out["passed"] is False
    assert out["exit_code"] == 1
    assert out["output_tail"] == "FAILED test_outputs.py::test_x"


def test_collect_v12_verifier_uses_terminal_bench_reward_file(
    adapter: ModuleType,
) -> None:
    import asyncio as _asyncio

    class _FakeExecResult:
        def __init__(self, return_code: int, *, output: str = "") -> None:
            self.return_code = return_code
            self.output = output

    class _FakeEnvironment:
        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> _FakeExecResult:
            if command.startswith("test -f /tests/test.sh"):
                return _FakeExecResult(0, output="exists")
            if command.startswith("bash /tests/test.sh"):
                return _FakeExecResult(
                    0,
                    output="pytest failed\n__ARGUS_TB_REWARD__=0\n",
                )
            raise RuntimeError(f"unexpected command: {command}")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_v12_verifier(
            environment=_FakeEnvironment(),
            env={},
            timeout_sec=30,
        )
    )

    assert out is not None
    assert out["exit_code"] == 0
    assert out["reward"] == "0"
    assert out["reward_source"] == "reward.txt"
    assert out["missing_reward"] is False
    assert out["passed"] is False


def test_collect_v12_verifier_marks_missing_reward_artifact_as_fail(
    adapter: ModuleType,
) -> None:
    import asyncio as _asyncio

    class _FakeExecResult:
        def __init__(self, return_code: int, *, output: str = "") -> None:
            self.return_code = return_code
            self.output = output

    class _FakeEnvironment:
        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> _FakeExecResult:
            if command.startswith("test -f /tests/test.sh"):
                return _FakeExecResult(0, output="exists")
            if command.startswith("bash /tests/test.sh"):
                return _FakeExecResult(0, output="pytest passed\n")
            raise RuntimeError(f"unexpected command: {command}")

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    import logging
    inst.logger = logging.getLogger("test")

    out = _asyncio.get_event_loop().run_until_complete(
        inst._collect_v12_verifier(
            environment=_FakeEnvironment(),
            env={},
            timeout_sec=30,
        )
    )

    assert out is not None
    assert out["exit_code"] == 0
    assert out["reward"] is None
    assert out["reward_source"] == "missing_reward_artifact"
    assert out["missing_reward"] is True
    assert out["passed"] is False


def test_default_checks_timeout_is_documented_constant(
    adapter: ModuleType,
) -> None:
    """Default per-check timeout is the documented constant. If tests
    elsewhere rely on the exact value, this is the canary."""
    assert adapter._DEFAULT_CHECKS_TIMEOUT == 60


# --- v12 phase-4 raw-evidence helpers --------------------------------------


def test_format_v12_evidence_full_payload(adapter: ModuleType) -> None:
    """All three sections (engineer self-report + runtime probe + official
    verifier) get rendered with v12-faithful headers + indentation."""
    out = adapter._format_v12_evidence(
        engineer_self_report="Built artifact at /app/out.csv.\nVerified 79586 rows.",
        runtime_probe="== /app contents ==\ntotal 4\n-rw-r--r-- 1 root root 0 out.csv",
        verifier_check={
            "command": "bash /tests/test.sh",
            "exit_code": 0,
            "passed": True,
            "output_tail": "3 passed in 0.42s",
        },
    )
    assert "- engineer self-report (verbatim):" in out
    assert "    Built artifact at /app/out.csv." in out
    assert "- runtime probe (independent post-round container state" in out
    assert "    == /app contents ==" in out
    assert (
        "- official verifier (PASS, exit=0, cmd: bash /tests/test.sh)" in out
    )
    assert "**ground truth**" in out
    assert "trust this and not the engineer" in out
    assert "    verifier stdout (tail):" in out
    assert "    3 passed in 0.42s" in out


def test_format_v12_evidence_missing_verifier_self_skips(
    adapter: ModuleType,
) -> None:
    """Non-TB datasets: no /tests/test.sh → verifier_check=None → only
    engineer self-report + runtime probe sections rendered."""
    out = adapter._format_v12_evidence(
        engineer_self_report="claim done",
        runtime_probe="== /app contents ==\n(empty)",
        verifier_check=None,
    )
    assert "engineer self-report" in out
    assert "runtime probe" in out
    assert "official verifier" not in out
    assert "ground truth" not in out


def test_format_v12_evidence_failure_framing(adapter: ModuleType) -> None:
    """FAIL verifier renders with exit_code and FAIL label, preserving the
    "trust this not the engineer" admonition so the reviewer recognises
    engineer self-report disagreement as the failure signal it is."""
    out = adapter._format_v12_evidence(
        engineer_self_report="all tests pass!",
        runtime_probe=None,
        verifier_check={
            "command": "bash /tests/test.sh",
            "exit_code": 1,
            "passed": False,
            "output_tail": "FAILED test_outputs.py::test_x",
        },
    )
    assert "- official verifier (FAIL, exit=1" in out
    assert "ground truth" in out
    assert "trust this and not the engineer" in out
    assert "FAILED test_outputs.py::test_x" in out
    # engineer prose contradicting verifier → reviewer can see both
    assert "all tests pass" in out


def test_format_v12_evidence_empty_inputs_yield_empty_string(
    adapter: ModuleType,
) -> None:
    """Nothing to surface → empty string (caller can skip the entire
    'Raw verification evidence' section in the prompt)."""
    out = adapter._format_v12_evidence(
        engineer_self_report="",
        runtime_probe=None,
        verifier_check=None,
    )
    assert out == ""


def test_format_v12_evidence_caps_huge_engineer_message(
    adapter: ModuleType,
) -> None:
    """Engineer messages exceeding ``_V12_ENGINEER_SELF_REPORT_MAX_CHARS``
    get tail-truncated so a chatty engineer can't blow up the reviewer
    prompt."""
    huge = "lorem ipsum " * 1000  # ~12 000 chars
    out = adapter._format_v12_evidence(
        engineer_self_report=huge,
        runtime_probe=None,
        verifier_check=None,
    )
    assert "<...truncated...>" in out
    # The "engineer self-report" body itself (lines starting with "    ")
    # should fit in the truncation budget (plus some indent overhead).
    body_lines = [ln for ln in out.splitlines() if ln.startswith("    ")]
    body = "\n".join(body_lines)
    cap = adapter._V12_ENGINEER_SELF_REPORT_MAX_CHARS
    assert len(body) <= cap * 2  # generous: 4-space indent per line


def test_format_v12_evidence_truncates_huge_runtime_probe(adapter):
    """Runtime probe lines beyond ``_V12_RUNTIME_PROBE_MAX_LINES`` are
    truncated by ``_collect_runtime_probe`` (which is what produces the
    string in production), but ``_format_v12_evidence`` itself does not
    re-truncate — verify the formatter passes through indented lines."""
    probe = "\n".join(f"line {i}" for i in range(20))
    out = adapter._format_v12_evidence(
        engineer_self_report="",
        runtime_probe=probe,
        verifier_check=None,
    )
    assert "    line 0" in out
    assert "    line 19" in out


def test_v12_constants_match_v12_baseline(adapter):
    """Canary: the hardcoded v12 commands/timeouts match the v12
    fullbench (2026-05-06) trace shape. Changing these silently would
    invalidate the "we reproduce v12" claim — bump this test
    deliberately if you also bump the baseline."""
    assert adapter._V12_VERIFIER_CMD == "bash /tests/test.sh"
    assert adapter._V12_VERIFIER_TIMEOUT_SEC == 600
    assert "ls -la /app" in adapter._V12_RUNTIME_PROBE_CMD
    assert "ss -tlnp" in adapter._V12_RUNTIME_PROBE_CMD
    assert "ps -ef" in adapter._V12_RUNTIME_PROBE_CMD
    assert "/app/output.*" in adapter._V12_RUNTIME_PROBE_CMD


def _build_v12_round_runner_test(
    adapter: ModuleType,
    *,
    verifier_return_code: int,
    verifier_output: str,
    reviewer_status: str,
    expected_verifier_passed: bool,
    expected_reviewer_status: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio as _asyncio
    import logging

    decision_log = tmp_path / "decisions.jsonl"
    agent_dir = tmp_path / "agent"
    monkeypatch.setattr(
        adapter,
        "EnvironmentPaths",
        SimpleNamespace(agent_dir=agent_dir),
    )
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_V12_VERIFIER", "1")
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_REVIEWER_GATE", "1")
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_VERIFIER_PASS_SHORT_CIRCUIT", "0")
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_RUNTIME_PROBE", "0")
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_MAX_ROUNDS", "1")
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_DECISIONS_LOG", str(decision_log))
    monkeypatch.delenv("ARGUS_SKILL_HARBOR_NO_REVIEWER", raising=False)

    captured: dict[str, Any] = {}

    class _FakeExecResult:
        def __init__(
            self,
            return_code: int,
            *,
            output: str = "",
            stdout: str = "",
            stderr: str = "",
        ) -> None:
            self.return_code = return_code
            self.output = output
            self.stdout = stdout
            self.stderr = stderr

    class _FakeEnvironment:
        default_user = None

        async def exec(
            self,
            *,
            command: str,
            env: dict[str, str],
            timeout_sec: int,
        ) -> _FakeExecResult:
            if command.startswith("test -f /tests/test.sh"):
                return _FakeExecResult(0, output="exists")
            if command.startswith("bash /tests/test.sh"):
                return _FakeExecResult(verifier_return_code, output=verifier_output)
            raise AssertionError(f"unexpected command: {command}")

    async def _fake_exec_as_agent(
        _environment: Any,
        *,
        command: str,
        env: dict[str, str],  # noqa: ARG001
    ) -> None:
        if "printf '%s\\n'" in command and ">>" in command:
            tokens = shlex.split(command)
            body = tokens[2]
            target = Path(tokens[-1].strip("'"))
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(existing + body + "\n", encoding="utf-8")
            return
        if command.startswith("mkdir -p "):
            return
        raise AssertionError(f"unexpected agent command: {command}")

    async def _fake_prepare_container(_environment: Any) -> tuple[dict[str, str], str]:
        return {"CODEX_HOME": "/tmp/codex"}, ""

    async def _fake_run_codex_in_container(
        *,
        environment: Any,  # noqa: ARG001
        env: dict[str, str],  # noqa: ARG001
        cli_flags_arg: str,  # noqa: ARG001
        model: str,  # noqa: ARG001
        prompt: str,  # noqa: ARG001
        round_idx: int,  # noqa: ARG001
        round_timeout: int,  # noqa: ARG001
        resume_session_id: str | None,  # noqa: ARG002
    ) -> tuple[str, int]:
        round_log = agent_dir / f"argus-skill-round-{round_idx}.txt"
        round_log.parent.mkdir(parents=True, exist_ok=True)
        round_log.write_text(
            "\n".join(
                [
                    '{"type":"thread.started","thread_id":"thr_v12"}',
                    '{"type":"item.completed","item":{"type":"agent_message","text":"engineer says done"}}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        codex_log = agent_dir / "codex.txt"
        codex_log.write_text(round_log.read_text(encoding="utf-8"), encoding="utf-8")
        return (
            round_log.read_text(encoding="utf-8"),
            0,
        )

    class _StubReviewerConfig:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    class _StubReviewer:
        def __init__(self, _backend: Any) -> None:
            pass

        def evaluate(self, **kwargs: Any) -> Any:
            captured["reviewer_kwargs"] = kwargs
            return SimpleNamespace(
                status=reviewer_status,
                confidence=0.5,
                reason="verifier gate test",
                next_action=(
                    "retry"
                    if reviewer_status == "continue"
                    else ""
                ),
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                source="test",
            )

    deps = {
        "CodexRunnerBackend": lambda *a, **kw: object(),
        "Reviewer": _StubReviewer,
        "ReviewerConfig": _StubReviewerConfig,
        "CheckResult": object,
        "Distiller": object,
        "DistillerConfig": object,
        "Skill": object,
        "SkillStore": object,
    }
    monkeypatch.setattr(adapter, "_import_argus_skill", lambda: deps)

    inst = adapter.ArgusSkillCodex.__new__(adapter.ArgusSkillCodex)
    inst.logger = logging.getLogger("test")
    inst.model_name = "openai/gpt-5.4"
    inst.build_cli_flags = lambda: ""
    inst.exec_as_agent = _fake_exec_as_agent
    inst._prepare_container = _fake_prepare_container
    inst._run_codex_in_container = _fake_run_codex_in_container

    context = SimpleNamespace(
        cost_usd=None,
        n_input_tokens=0,
        n_output_tokens=0,
        n_cache_tokens=0,
    )

    _asyncio.get_event_loop().run_until_complete(
        inst.run(
            instruction="verify the official verifier path",
            environment=_FakeEnvironment(),
            context=context,
        )
    )

    round_kwargs = captured["reviewer_kwargs"]
    assert "- official verifier" in round_kwargs["raw_evidence"]
    assert round_kwargs["raw_evidence"].count("bash /tests/test.sh") == 1

    assert decision_log.exists()
    lines = [json.loads(line) for line in decision_log.read_text().splitlines()]
    assert len(lines) == 1
    round_record = lines[0]["rounds"][0]
    assert round_record["v12_evidence"]["verifier_present"] is True
    assert round_record["v12_evidence"]["verifier_passed"] is expected_verifier_passed
    assert "- official verifier" in round_record["raw_evidence"]
    assert round_record["review_status"] == expected_reviewer_status

    round_log = agent_dir / "argus-skill-round-1.txt"
    assert round_log.exists()
    transcript = round_log.read_text(encoding="utf-8")
    assert "- official verifier" in transcript
    assert "bash /tests/test.sh" in transcript


def test_run_records_v12_verifier_pass_and_raw_evidence(
    adapter: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _build_v12_round_runner_test(
        adapter,
        verifier_return_code=0,
        verifier_output="pytest passed\n__ARGUS_TB_REWARD__=1\n",
        reviewer_status="done",
        expected_verifier_passed=True,
        expected_reviewer_status="done",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


def test_run_records_v12_verifier_fail_and_continue_reviewer(
    adapter: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _build_v12_round_runner_test(
        adapter,
        verifier_return_code=0,
        verifier_output="pytest failed\n__ARGUS_TB_REWARD__=0\n",
        reviewer_status="continue",
        expected_verifier_passed=False,
        expected_reviewer_status="continue",
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

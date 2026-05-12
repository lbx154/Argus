"""Tests for the offline-testable helpers in benchmarks/harbor_adapter.py.

We can't unit-test the full ``ArgusSkillCodex.run`` without Harbor's
runtime (it needs a ``BaseEnvironment``), but we can exhaustively test
the logic that runs on host: codex JSON parsing, prompt builder,
host-prep ablation flags.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def adapter():
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


def test_no_skill_ablation_skips_host_prep(adapter, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_HARBOR_NO_SKILL", "1")
    prep = adapter._do_host_prep("any task")
    assert prep.skill_used is False
    assert prep.skill_text == ""
    assert prep.fallback_reason == "no_skill_ablation"
    assert prep.scientist_tokens == 0


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


# --- reviewer budget default ------------------------------------------------


def test_reviewer_budget_default_is_at_least_120s(adapter):
    """Regression for tb2-ablation-2026-05-10 finding 1: default 60 s was
    empirically too tight for gpt-5.4 @ medium effort (6/6 timeouts).
    Don't let it silently regress below 120 s without re-validation."""
    assert adapter._DEFAULT_REVIEWER_BUDGET >= 120.0


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


def test_invoke_reviewer_passes_checks_through_to_reviewer(adapter, monkeypatch):
    """The bug RC1 fixes: ``_invoke_reviewer`` used to hardcode
    ``checks=[]``. Verify that a non-empty ``checks_data`` actually
    reaches ``Reviewer.evaluate`` as a list of CheckResult-shaped
    objects."""
    captured: dict = {}

    class _StubCheckResult:
        def __init__(self, *, command, exit_code, passed, output_tail):
            self.command = command
            self.exit_code = exit_code
            self.passed = passed
            self.output_tail = output_tail

    class _StubReviewerCfg:
        def __init__(self, **_kwargs):
            pass

    class _StubDecision:
        status = "done"
        confidence = 0.9
        reason = "checks all passed"
        next_action = ""

    class _StubReviewer:
        def __init__(self, _backend):
            pass

        def evaluate(self, **kwargs):
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


def test_invoke_reviewer_with_empty_checks_data_passes_empty_list(adapter, monkeypatch):
    """Backwards-compat: legacy callers (or unconfigured CHECKS_CMD) pass
    no checks → reviewer must still be invoked, with checks=[]."""
    captured: dict = {}

    class _Stub:
        def __init__(self, *a, **kw):
            pass

    class _StubDecision:
        status = "continue"
        confidence = 0.0
        reason = ""
        next_action = ""

    class _StubReviewer:
        def __init__(self, _backend):
            pass

        def evaluate(self, **kwargs):
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


def test_collect_checks_runs_each_command_and_captures_result(adapter):
    """``_collect_checks`` must run each command via ``environment.exec``
    in order, never raise on non-zero exit, and produce a CheckResult-
    shaped dict per command."""
    import asyncio as _asyncio

    calls: list[dict] = []

    class _FakeExecResult:
        def __init__(self, return_code, stdout):
            self.return_code = return_code
            self.stdout = stdout

    class _FakeEnvironment:
        async def exec(self, *, command, env, timeout_sec):
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


def test_collect_checks_preserves_alternate_output_fields(adapter):
    import asyncio as _asyncio

    class _FakeExecResult:
        def __init__(self, return_code, *, stdout="", stderr="", output="", combined_output=""):
            self.return_code = return_code
            self.stdout = stdout
            self.stderr = stderr
            self.output = output
            self.combined_output = combined_output

    class _FakeEnvironment:
        async def exec(self, *, command, env, timeout_sec):
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


def test_collect_checks_with_no_commands_returns_empty(adapter):
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


def test_collect_checks_per_command_timeout_becomes_failed_check(adapter):
    """A RuntimeError from harbor's docker exec (typically a per-command
    timeout) must NOT abort the whole batch — it must be recorded as a
    failed CheckResult so the reviewer sees the timeout, not silence."""
    import asyncio as _asyncio

    class _FakeEnvironment:
        async def exec(self, *, command, env, timeout_sec):  # noqa: ARG002
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


def test_collect_v12_verifier_preserves_alternate_output_fields(adapter):
    import asyncio as _asyncio

    class _FakeExecResult:
        def __init__(self, return_code, *, stdout="", stderr="", output="", combined_output=""):
            self.return_code = return_code
            self.stdout = stdout
            self.stderr = stderr
            self.output = output
            self.combined_output = combined_output

    class _FakeEnvironment:
        async def exec(self, *, command, env, timeout_sec):
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


def test_default_checks_timeout_is_documented_constant(adapter):
    """Default per-check timeout is the documented constant. If tests
    elsewhere rely on the exact value, this is the canary."""
    assert adapter._DEFAULT_CHECKS_TIMEOUT == 60


# --- v12 phase-4 raw-evidence helpers --------------------------------------


def test_format_v12_evidence_full_payload(adapter):
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


def test_format_v12_evidence_missing_verifier_self_skips(adapter):
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


def test_format_v12_evidence_failure_framing(adapter):
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


def test_format_v12_evidence_empty_inputs_yield_empty_string(adapter):
    """Nothing to surface → empty string (caller can skip the entire
    'Raw verification evidence' section in the prompt)."""
    out = adapter._format_v12_evidence(
        engineer_self_report="",
        runtime_probe=None,
        verifier_check=None,
    )
    assert out == ""


def test_format_v12_evidence_caps_huge_engineer_message(adapter):
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

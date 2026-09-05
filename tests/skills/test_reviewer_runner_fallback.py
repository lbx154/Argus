from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus_skill.core.models import RunnerResult
from argus_skill.tools.image_api import ImageToolError
from argus_skill.verticals.research import (
    _reviewer_runner_fallback as fallback,
)
from argus_skill.verticals.research import academic_language_review as language
from argus_skill.verticals.research import paper_infrastructure_review as infrastructure
from argus_skill.verticals.research._reviewer_runner_fallback import (
    ReviewerRunnerError,
)
from tests.skills.researched_venues import EIGHT_PAGE_CONFERENCE


def _explicit_env() -> dict[str, str]:
    return {
        "ARGUS_SKILL_REVIEWER_BACKEND": "claude",
        "ARGUS_SKILL_RUNNER_BACKEND": "copilot",
        "ARGUS_SKILL_REVIEWER_RUNNER_BIN": "/opt/reviewer",
        "ARGUS_SKILL_RUNNER_BIN": "/opt/shared",
        "ARGUS_SKILL_REVIEWER_MODEL": "claude-reviewer",
        "ARGUS_SKILL_REVIEWER_REASONING_EFFORT": "medium",
        "ARGUS_SKILL_RUNNER_EXTRA_ARGS": "--trace",
    }


def test_fallback_uses_canonical_reviewer_config_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Backend:
        def __init__(self, **kwargs: object) -> None:
            captured["backend_kwargs"] = kwargs

    def _run(backend, *, prompt, options, run_label):  # noqa: ANN001
        captured.update(
            backend=backend,
            prompt=prompt,
            options=options,
            run_label=run_label,
        )
        return RunnerResult(exit_code=0, agent_messages=['{"accepted":true}'])

    from argus_skill.adapters import agent_cli_backend
    from argus_skill.core import run_gateway

    monkeypatch.setattr(agent_cli_backend, "AgentCliBackend", _Backend)
    monkeypatch.setattr(run_gateway, "run_exec", _run)
    ticks = iter([100.0, 106.0])
    monkeypatch.setattr(fallback.time, "monotonic", lambda: next(ticks))

    raw, label = fallback.run_reviewer_prompt_via_runner(
        "review this",
        run_label="research.test_review",
        working_dir="/workspace",
        env=_explicit_env(),
        timeout=5.0,
    )

    assert raw == '{"accepted":true}'
    assert label == "runner:claude:claude-reviewer"
    assert "answer in clear prose" in captured["prompt"]
    assert "ONLY the single JSON" not in captured["prompt"]
    assert captured["backend_kwargs"] == {
        "backend": "claude",
        "runner_bin": "/opt/reviewer",
        "default_extra_args": ["--trace"],
    }
    options = captured["options"]
    assert options.model == "claude-reviewer"
    assert options.reasoning_effort == "medium"
    assert options.working_dir == "/workspace"
    assert options.full_auto is True
    assert options.watchdog_hard_idle_seconds == 5
    assert options.external_interrupt_reason_provider() == (
        "reviewer timeout after 5.0s"
    )


def test_explicit_shared_runner_bin_beats_persisted_role_bin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.adapters import agent_cli_backend
    from argus_skill.core import knob_store, run_gateway

    captured = {}
    monkeypatch.setattr(
        knob_store,
        "read_persisted_knobs",
        lambda: {"ARGUS_SKILL_REVIEWER_RUNNER_BIN": "/persisted/reviewer"},
    )
    monkeypatch.setattr(
        agent_cli_backend,
        "AgentCliBackend",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        run_gateway,
        "run_exec",
        lambda *args, **kwargs: RunnerResult(
            exit_code=0,
            agent_messages=['{"accepted":true}'],
        ),
    )
    env = _explicit_env()
    env.pop("ARGUS_SKILL_REVIEWER_RUNNER_BIN")
    env["ARGUS_SKILL_RUNNER_BIN"] = "/env/shared"

    fallback.run_reviewer_prompt_via_runner(
        "review this",
        run_label="research.test_review",
        env=env,
        timeout=5.0,
    )

    assert captured["runner_bin"] == "/env/shared"


def test_reviewer_backend_override_ignores_persisted_shared_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from argus_skill.adapters import agent_cli_backend
    from argus_skill.core import knob_store, run_gateway

    captured = {}
    monkeypatch.setattr(
        knob_store,
        "read_persisted_knobs",
        lambda: {
            "ARGUS_SKILL_RUNNER_BACKEND": "dsh",
            "ARGUS_SKILL_RUNNER_BIN": "/persisted/dsh",
        },
    )
    monkeypatch.setattr(
        agent_cli_backend,
        "AgentCliBackend",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        run_gateway,
        "run_exec",
        lambda *args, **kwargs: RunnerResult(
            exit_code=0,
            agent_messages=['{"accepted":true}'],
        ),
    )

    fallback.run_reviewer_prompt_via_runner(
        "review this",
        run_label="research.test_review",
        env={"ARGUS_SKILL_REVIEWER_BACKEND": "copilot"},
        timeout=5.0,
    )

    assert captured["backend"] == "copilot"
    assert captured["runner_bin"] is None


def test_malformed_runner_extra_args_becomes_handled_error() -> None:
    env = _explicit_env()
    env["ARGUS_SKILL_RUNNER_EXTRA_ARGS"] = "'unterminated"

    with pytest.raises(ReviewerRunnerError, match="invalid reviewer runner configuration"):
        fallback.run_reviewer_prompt_via_runner(
            "review this",
            run_label="research.test_review",
            env=env,
            timeout=5.0,
        )


@pytest.mark.parametrize(
    "result",
    [
        RunnerResult(exit_code=1, agent_messages=['{"looks":"valid"}']),
        RunnerResult(
            exit_code=0,
            agent_messages=['{"looks":"valid"}'],
            fatal_error="quota denied",
        ),
        RunnerResult(exit_code=0),
    ],
)
def test_fallback_rejects_failed_or_empty_runner_results(
    monkeypatch: pytest.MonkeyPatch,
    result: RunnerResult,
) -> None:
    from argus_skill.adapters import agent_cli_backend
    from argus_skill.core import run_gateway

    monkeypatch.setattr(
        agent_cli_backend,
        "AgentCliBackend",
        lambda **kwargs: SimpleNamespace(kwargs=kwargs),
    )
    monkeypatch.setattr(run_gateway, "run_exec", lambda *args, **kwargs: result)

    with pytest.raises(ReviewerRunnerError):
        fallback.run_reviewer_prompt_via_runner(
            "review this",
            run_label="research.test_review",
            env=_explicit_env(),
            timeout=5.0,
        )


@pytest.mark.parametrize(
    ("module", "error_type", "kwargs"),
    [
        (
            language,
            language.AcademicLanguageReviewError,
            {
                "source_text_by_path": {"paper/main.tex": "Hello."},
                "deterministic": {},
            },
        ),
        (
            infrastructure,
            infrastructure.PaperInfrastructureReviewError,
            {"source_text_by_path": {"paper/main.tex": "Hello."}},
        ),
    ],
)
def test_gate_converts_runner_failure_to_handled_review_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    module,
    error_type,
    kwargs,
) -> None:
    monkeypatch.setattr(
        module,
        "_require_route",
        lambda *args, **values: (_ for _ in ()).throw(ImageToolError("missing")),
    )
    monkeypatch.setattr(module, "runner_fallback_enabled", lambda env: True)
    monkeypatch.setattr(
        module,
        "run_reviewer_prompt_via_runner",
        lambda *args, **values: (_ for _ in ()).throw(
            ReviewerRunnerError("runner failed")
        ),
    )

    with pytest.raises(error_type, match="runner failed"):
        module._run_model_review(
            root=tmp_path,
            env={},
            timeout=7.0,
            venue=EIGHT_PAGE_CONFERENCE,
            **kwargs,
        )

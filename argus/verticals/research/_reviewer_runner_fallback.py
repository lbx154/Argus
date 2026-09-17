"""In-fleet agent-runner fallback for the text reviewer gates.

The ``paper_infrastructure_review`` and ``academic_language_review`` tools ask a
reviewer *model* to inspect manuscript PROSE (never figures) and return an
advisory review. Historically they required an OpenAI-compatible ``reviewer`` model
API route (api_key + base_url + model). On fleets that drive their agents through
an agent-CLI runner (e.g. copilot) instead of a raw model-API vault, that route
is often unconfigured, which hard-blocked the paper at ``model_review_unavailable``.

Because the review is a pure text judgement, it does not need the model-API
transport at all: any capable text LLM will do. When the ``reviewer`` route is
unavailable we therefore fall back to the SAME agent-CLI runner that already
executes the rest of Argus, mirroring the fail-soft philosophy of the vision
``paper_layout_review`` gate (which degrades gracefully when its model is
missing rather than hard-blocking).

Set ``ARGUS_SKILL_REVIEWER_DISABLE_RUNNER_FALLBACK=1`` to restore the historic
hard-block behaviour (require the model-API route). The fallback uses the same
canonical Reviewer role configuration as the resident fleet:

* ``ARGUS_SKILL_REVIEWER_BACKEND`` — runner backend to drive the review
  (``codex`` / ``claude`` / ``copilot`` / ``cursor`` / ``opencode`` / ``pi`` / ``grok``),
  with the normal shared/persisted
  fallback chain.
* ``ARGUS_SKILL_REVIEWER_RUNNER_BIN`` — role-specific runner binary, falling
  back to ``ARGUS_SKILL_RUNNER_BIN``.
* ``ARGUS_SKILL_REVIEWER_MODEL`` — model id to request (default: the backend's
  configured default).
* ``ARGUS_SKILL_REVIEWER_REASONING_EFFORT`` — normal persisted role effort,
  default ``high`` for this gate.
"""
from __future__ import annotations

import os
import shlex
import time
from typing import Mapping

_DISABLE_ENV = "ARGUS_SKILL_REVIEWER_DISABLE_RUNNER_FALLBACK"
_MODEL_ENV = "ARGUS_SKILL_REVIEWER_MODEL"
_EFFORT_ENV = "ARGUS_SKILL_REVIEWER_REASONING_EFFORT"

_TRUE_TOKENS = {"1", "true", "yes", "on"}

_RUNNER_PREAMBLE = (
    "You are running as an independent academic paper reviewer. Follow the "
    "review instructions below and answer in clear prose. Put material findings "
    "in severity order, with a location, evidence, and suggested fix for each. "
    "Finish with any short named lines the review instructions request. Do not "
    "call tools or run commands; base the review solely on the supplied manuscript.\n\n"
)


class ReviewerRunnerError(RuntimeError):
    """Raised when the configured fleet Reviewer cannot return a valid turn."""


def runner_fallback_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether the agent-runner fallback may close the reviewer gate.

    Enabled by default; disabled only when ``ARGUS_SKILL_REVIEWER_DISABLE_RUNNER_FALLBACK``
    is set to a truthy token, which forces the historic model-API hard-block.
    """
    source = env if env is not None else os.environ
    return str(source.get(_DISABLE_ENV, "")).strip().lower() not in _TRUE_TOKENS


def run_reviewer_prompt_via_runner(
    prompt: str,
    *,
    run_label: str,
    working_dir: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None,
) -> tuple[str, str]:
    """Run the reviewer PROMPT through the fleet agent-CLI runner.

    Returns ``(raw_text, model_label)`` where ``raw_text`` is the model's reply
    and ``model_label`` records which
    runner/model produced it. Raises on any failure so the caller can fall back
    to the historic ``model_review_unavailable`` block.
    """
    source = env if env is not None else os.environ

    # Imported lazily so the review modules stay importable in environments that
    # never exercise the runner fallback (e.g. unit tests, docs builds).
    from ...adapters.agent_cli_backend import AgentCliBackend, _strip_legacy_codex_profile_args
    from ...agent_cli.runner_backend import normalize_runner_backend
    from ...core.knobs import (
        resolve_knob,
        resolve_role_backend,
        resolve_role_model,
        resolve_role_reasoning_effort,
        resolve_runner_bin_setting,
    )
    from ...core.models import RunnerOptions
    from ...core.run_gateway import run_exec as gateway_run_exec

    try:
        backend_name = normalize_runner_backend(
            resolve_role_backend("reviewer", env=source)
        )
        model = (
            resolve_role_model(
                "reviewer",
                role_env=_MODEL_ENV,
                env=source,
            ).strip()
            or None
        )
        effort = resolve_role_reasoning_effort(
            _EFFORT_ENV,
            env=source,
            default="high",
        )
        runner_bin = resolve_runner_bin_setting(
            "reviewer",
            backend=backend_name,
            env=source,
        ) or None
        raw_extra = resolve_knob(
            "ARGUS_SKILL_RUNNER_EXTRA_ARGS",
            "",
            env=source,
        ).value
        extra_args = _strip_legacy_codex_profile_args(
            shlex.split(raw_extra) if raw_extra else None
        )
    except Exception as exc:
        raise ReviewerRunnerError(
            f"invalid reviewer runner configuration: {type(exc).__name__}: {exc}"
        ) from exc
    deadline = time.monotonic() + timeout if timeout is not None else None

    def _timeout_reason() -> str | None:
        if deadline is not None and time.monotonic() >= deadline:
            return f"reviewer timeout after {timeout:.1f}s"
        return None

    try:
        backend = AgentCliBackend(
            backend=backend_name,
            runner_bin=runner_bin,
            default_extra_args=extra_args,
        )
        result = gateway_run_exec(
            backend,
            prompt=_RUNNER_PREAMBLE + prompt,
            options=RunnerOptions(
                model=model,
                reasoning_effort=effort,
                skip_git_repo_check=True,
                full_auto=True,
                working_dir=working_dir,
                external_interrupt_reason_provider=(
                    _timeout_reason if timeout is not None else None
                ),
                watchdog_hard_idle_seconds=(
                    max(1, int(timeout)) if timeout is not None else 0
                ),
            ),
            run_label=run_label,
        )
    except Exception as exc:
        raise ReviewerRunnerError(
            f"reviewer runner could not start: {type(exc).__name__}: {exc}"
        ) from exc

    exit_code = int(getattr(result, "exit_code", -1))
    fatal_error = str(getattr(result, "fatal_error", "") or "").strip()
    if exit_code != 0 or fatal_error:
        detail = fatal_error or f"runner exited with code {exit_code}"
        raise ReviewerRunnerError(f"reviewer runner failed: {detail}")
    raw_text = (getattr(result, "last_agent_message", "") or "").strip()
    if not raw_text:
        raise ReviewerRunnerError("reviewer runner returned no text")
    model_label = f"runner:{backend_name}:{model or 'default'}"
    return raw_text, model_label


__all__ = [
    "ReviewerRunnerError",
    "runner_fallback_enabled",
    "run_reviewer_prompt_via_runner",
]

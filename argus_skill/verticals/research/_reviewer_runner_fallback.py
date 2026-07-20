"""In-fleet agent-runner fallback for the text reviewer gates.

The ``paper_infrastructure_review`` and ``academic_language_review`` gates ask a
strict reviewer *model* to inspect manuscript PROSE (never figures) and return a
JSON verdict. Historically they required an OpenAI-compatible ``reviewer`` model
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
hard-block behaviour (require the model-API route). Overrides:

* ``ARGUS_SKILL_REVIEWER_RUNNER_BACKEND`` — runner backend to drive the review
  (``codex`` / ``claude`` / ``copilot``); falls back to
  ``ARGUS_SKILL_RUNNER_BACKEND`` and finally ``copilot``.
* ``ARGUS_SKILL_REVIEWER_MODEL`` — model id to request (default: the backend's
  configured default).
* ``ARGUS_SKILL_REVIEWER_REASONING_EFFORT`` — default ``high``.
"""
from __future__ import annotations

import os
from typing import Mapping

_DISABLE_ENV = "ARGUS_SKILL_REVIEWER_DISABLE_RUNNER_FALLBACK"
_BACKEND_ENV = "ARGUS_SKILL_REVIEWER_RUNNER_BACKEND"
_MODEL_ENV = "ARGUS_SKILL_REVIEWER_MODEL"
_EFFORT_ENV = "ARGUS_SKILL_REVIEWER_REASONING_EFFORT"

_TRUE_TOKENS = {"1", "true", "yes", "on"}

_RUNNER_PREAMBLE = (
    "You are running as a strict, independent academic paper reviewer. Follow "
    "the review instructions below EXACTLY. Reply with ONLY the single JSON "
    "object the instructions request — no prose before or after, no Markdown "
    "code fence, and do NOT call any tools or run any commands. Base your "
    "verdict solely on the manuscript text supplied in the instructions.\n\n"
)


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
) -> tuple[str, str]:
    """Run the reviewer PROMPT through the fleet agent-CLI runner.

    Returns ``(raw_text, model_label)`` where ``raw_text`` is the model's reply
    (expected to be the reviewer JSON object) and ``model_label`` records which
    runner/model produced it. Raises on any failure so the caller can fall back
    to the historic ``model_review_unavailable`` block.
    """
    source = env if env is not None else os.environ

    # Imported lazily so the review modules stay importable in environments that
    # never exercise the runner fallback (e.g. unit tests, docs builds).
    from ...adapters.agent_cli_backend import AgentCliBackend
    from ...core.models import RunnerOptions
    from ...core.run_gateway import run_exec as gateway_run_exec

    backend_name = (
        str(source.get(_BACKEND_ENV, "")).strip()
        or str(source.get("ARGUS_SKILL_RUNNER_BACKEND", "")).strip()
        or "copilot"
    )
    model = str(source.get(_MODEL_ENV, "")).strip() or None
    effort = str(source.get(_EFFORT_ENV, "")).strip() or "high"

    backend = AgentCliBackend(backend=backend_name)
    result = gateway_run_exec(
        backend,
        prompt=_RUNNER_PREAMBLE + prompt,
        options=RunnerOptions(
            model=model,
            reasoning_effort=effort,
            skip_git_repo_check=True,
            full_auto=True,
            working_dir=working_dir,
        ),
        run_label=run_label,
    )
    raw_text = (getattr(result, "last_agent_message", "") or "").strip()
    if not raw_text:
        raise RuntimeError("reviewer runner returned no text")
    model_label = f"runner:{backend_name}:{model or 'default'}"
    return raw_text, model_label


__all__ = ["runner_fallback_enabled", "run_reviewer_prompt_via_runner"]

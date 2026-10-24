"""Argus must run on a Copilot token that only allows automatic model selection.

Such a token rejects every explicit ``--model``, and Copilot's automatic
choice rejects ``--reasoning-effort``. Three things used to break there:

* an empty/``auto`` model still sent ``--reasoning-effort`` (or ``--model auto``);
* the light model (front door, reflection, ``/plan`` preview, prompt
  rewrite) named a fixed small model on Copilot even when the operator asked
  for ``auto``;
* a first call that failed before Copilot created its session still reported
  the pre-bound session id, so every later call tried to resume a session
  that never existed ("No session ... matched").

Normal deployments (a named model, or no model setting at all) keep their
behaviour; those are pinned here too.
"""

from __future__ import annotations

import json
import os

import pytest

from argus.agent_cli import _run_exec as runner_exec
from argus.agent_cli.agent_cli_runner import AgentCliRunner, RunnerOptions
from argus.agent_cli.runner_backend import BACKEND_COPILOT
from argus.core.knobs import LIGHT_MODEL_DEFAULT, resolve_light_model

BOUND = "0cb916db-26aa-40f2-86b5-1ba81b225fd2"


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("ARGUS_SKILL_CODEX_CONFIG", raising=False)
    for name in (
        "ARGUS_SKILL_MODEL",
        "ARGUS_SKILL_MANAGER_MODEL",
        "ARGUS_SKILL_PLAN_MODEL",
        "ARGUS_SKILL_LIGHT_MODEL",
        "ARGUS_SKILL_TRIAL",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# (1) command construction                                                    #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# (2) the light model                                                         #
# --------------------------------------------------------------------------- #


def _env(**extra: str) -> dict[str, str]:
    return {
        "ARGUS_SKILL_LIFE_BACKEND": "copilot",
        "CODEX_HOME": os.environ["CODEX_HOME"],
        **extra,
    }


@pytest.mark.parametrize("name", ["ARGUS_SKILL_MODEL", "ARGUS_SKILL_MANAGER_MODEL"])
def test_main_auto_model_makes_the_light_model_automatic(name) -> None:
    assert resolve_light_model(env=_env(**{name: "auto"})) == ""


@pytest.mark.parametrize("extra", [{}, {"ARGUS_SKILL_MODEL": "named-model"}])
def test_normal_copilot_deployment_keeps_the_light_default(extra) -> None:
    assert resolve_light_model(env=_env(**extra)) == LIGHT_MODEL_DEFAULT


def test_explicit_light_model_still_wins_over_auto() -> None:
    env = _env(ARGUS_SKILL_MODEL="auto", ARGUS_SKILL_LIGHT_MODEL="pinned-model")
    assert resolve_light_model(env=env) == "pinned-model"


# --------------------------------------------------------------------------- #
# (3) no resume of a session that was never created                           #
# --------------------------------------------------------------------------- #


class _FakeStdin:
    def write(self, _s):
        return None

    def close(self):
        return None


class _ExitedProc:
    def __init__(self, stdout: list[str], stderr: list[str], returncode: int) -> None:
        self.stdout = iter(stdout)
        self.stderr = iter(stderr)
        self.stdin = _FakeStdin()
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):  # noqa: ARG002
        return self.returncode


_STARTUP_ONLY = [
    json.dumps({
        "type": "session.mcp_server_status_changed",
        "data": {"serverName": "builtin", "status": "connected"},
        "ephemeral": True,
    }),
]
_REJECTED = ['Error: Model "named-model" from --model flag is not available.']


@pytest.mark.parametrize(("label", "models"), [
    ("manager-frontdoor-classify", ["named-model", None]),
    ("engineer-r1", ["named-model"]),
])
def test_a_manager_call_runs_on_copilots_model_when_the_named_one_is_gone(
    monkeypatch, label, models,
) -> None:
    answered = [
        json.dumps({"type": "assistant.message", "data": {"content": "OK", "model": "copilot-pick"}}),
        json.dumps({"type": "result", "sessionId": BOUND, "exitCode": 0}),
    ]
    processes = iter([_ExitedProc(_STARTUP_ONLY, _REJECTED, returncode=1), _ExitedProc(answered, [], 0)])
    asked: list[str | None] = []
    monkeypatch.setattr(runner_exec, "spawn_owned_process", lambda *a, **k: next(processes))
    monkeypatch.setattr(AgentCliRunner, "_resolve_executable", staticmethod(lambda v: v))
    monkeypatch.setattr(
        AgentCliRunner, "_build_command",
        lambda self, *, options, **_kw: asked.append(options.model) or ["copilot"],
    )
    result = AgentCliRunner(agent_bin="copilot", backend=BACKEND_COPILOT).run_exec(
        prompt="classify", resume_thread_id=None,
        options=RunnerOptions(model="named-model", provider_session_id=BOUND), run_label=label,
    )

    assert asked == models
    if len(models) == 2:
        assert result.turn_completed and result.usage_model == "copilot-pick"
    else:
        assert result.turn_failed and "not available" in result.fatal_error

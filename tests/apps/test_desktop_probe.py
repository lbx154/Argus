"""The desktop readiness command obeys normal run/cancellation boundaries."""
import json
import os
import re
import sys
from contextlib import contextmanager

import pytest

from argus.agent_cli import agent_cli_runner
from argus.apps import desktop_probe
from argus.core import role_actions as actions_module
from argus.core.models import RunnerResult
from argus.core.run_gateway import run_interrupt_scope


@pytest.fixture
def scripted_probe(monkeypatch):
    state = {"calls": [], "confirm": True, "reply": True, "result": None}

    @contextmanager
    def bind(runner, options, actions):
        state["actions"] = actions
        assert options.disable_tools and options.sandbox_mode == "read-only"
        yield options

    class Runner:
        def __init__(self, *, agent_bin, backend):
            self.backend = backend
            self.agent_bin = agent_bin

        def run_exec(self, **kwargs):
            state["calls"].append(kwargs)
            challenge = re.search(r"ARGUS_READY_[0-9a-f]{12}", kwargs["prompt"])[0]
            if state["confirm"]:
                state["actions"].dispatch("hand_off", {"decision": {
                    "to": "manager", "summary": challenge, "assumptions": [],
                }})
            result = RunnerResult(exit_code=0, agent_messages=[challenge if state["reply"] else "wrong reply"])
            state["result"] = result
            return result

    monkeypatch.setattr(agent_cli_runner, "AgentCliRunner", Runner)
    monkeypatch.setattr(actions_module, "role_actions", bind)
    return state


def test_readiness_uses_gateway_and_requires_real_action_receipt(scripted_probe):
    before = dict(os.environ)
    result = desktop_probe.probe("pi", "fixture-cli")
    assert result["ok"] and result["tool_confirmed"] and result["reply_confirmed"]
    assert len(scripted_probe["calls"]) == 1
    assert scripted_probe["calls"][0]["run_label"] == "desktop-readiness"
    assert scripted_probe["result"].call_id.startswith("gateway-")
    assert dict(os.environ) == before


@pytest.mark.parametrize("missing", ["confirm", "reply"])
def test_readiness_never_accepts_only_half_of_the_challenge(scripted_probe, missing):
    scripted_probe[missing] = False
    result = desktop_probe.probe("pi", "fixture-cli")
    assert not result["ok"] and result["error"]


def test_gateway_cancellation_prevents_the_readiness_provider_call(scripted_probe):
    before = dict(os.environ)
    with run_interrupt_scope(lambda: "operator cancelled"):
        result = desktop_probe.probe("pi", "fixture-cli")
    assert not result["ok"] and "operator cancelled" in result["error"]
    assert scripted_probe["calls"] == []
    assert dict(os.environ) == before


def test_unsupported_backend_is_not_executed(scripted_probe):
    result = desktop_probe.probe("grok", "fixture-cli")
    assert not result["ok"]
    assert scripted_probe["calls"] == []


def test_expected_configuration_failure_returns_failed_receipt(monkeypatch, capsys):
    def fail(*_args):
        raise ValueError("invalid QA selection")

    monkeypatch.setattr(desktop_probe, "probe", fail)
    monkeypatch.setattr(sys, "argv", ["desktop_probe", "--backend", "pi", "--executable", "fixture-cli"])
    assert desktop_probe.main() == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_programming_errors_are_not_silently_converted_to_readiness(monkeypatch):
    def fail(*_args):
        raise TypeError("broken QA implementation")

    monkeypatch.setattr(desktop_probe, "probe", fail)
    monkeypatch.setattr(sys, "argv", ["desktop_probe", "--backend", "pi", "--executable", "fixture-cli"])
    with pytest.raises(TypeError, match="broken QA implementation"):
        desktop_probe.main()

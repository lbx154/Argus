"""Hosted control routes resolve the provider selector before execution/accounting."""
from __future__ import annotations

import os

import pytest

from argus_skill import trial
from argus_skill.core import knobs


@pytest.fixture(autouse=True)
def isolated_trial(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith("ARGUS_SKILL_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_TRIAL", "1")
    # A distinct selector exposes rewrite's accidental match with a built-in
    # default too. No model, provider profile or network is used by these tests.
    monkeypatch.setattr(trial, "CLIENT_MODEL", "hosted-test-selector")


@pytest.mark.parametrize("sentinel", ["", "auto", "inherit", "default"])
@pytest.mark.parametrize("route", ["classify", "dag", "plan", "rewrite"])
def test_all_automatic_control_routes_resolve_the_hosted_selector(monkeypatch, route, sentinel):
    from argus_skill.manager.dispatch import _bounded_dag_model
    from argus_skill.webapi.manager_bridge import _plan_preview_model, _rewrite_model_and_effort

    routes = {
        "classify": ("ARGUS_SKILL_FRONTDOOR_MODEL", knobs.resolve_manager_classify_model),
        "dag": ("ARGUS_SKILL_BOUNDED_DAG_MODEL", _bounded_dag_model),
        "plan": ("ARGUS_SKILL_PLAN_PREVIEW_MODEL", _plan_preview_model),
        "rewrite": ("ARGUS_SKILL_REWRITE_MODEL", lambda: _rewrite_model_and_effort()[0]),
    }
    name, resolve = routes[route]
    monkeypatch.setenv(name, sentinel)
    assert resolve() == trial.CLIENT_MODEL


def test_hosted_copilot_does_not_advertise_the_personal_catalog():
    assert not knobs.backend_uses_openai_catalog("copilot")


def test_personal_copilot_keeps_existing_cheap_route_defaults(monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_COPILOT_TRIAL", "0")
    assert knobs.backend_uses_openai_catalog("copilot")
    assert knobs.resolve_manager_classify_model() == "gpt-5.4-mini"


def test_persisted_trial_mode_is_honored_without_mutating_settings(monkeypatch):
    from argus_skill.core import knob_store

    saved = {"ARGUS_SKILL_COPILOT_TRIAL": "1"}
    monkeypatch.delenv("ARGUS_SKILL_COPILOT_TRIAL")
    monkeypatch.setattr(knob_store, "read_persisted_knobs", lambda: saved)
    assert knobs.resolve_manager_classify_model(backend="copilot") == trial.CLIENT_MODEL
    assert saved == {"ARGUS_SKILL_COPILOT_TRIAL": "1"}

"""Packaged acceptance: a desktop inheritance sentinel is not a CLI model id."""
import os

import pytest

from argus.core.desktop_selection import prepare_selection
from argus.core.knobs import resolve_light_model, resolve_role_model
from argus.life.observer import _model


@pytest.mark.parametrize("automatic", ["auto", "inherit", "default", "INHERIT"])
def test_observer_accepts_the_canonical_automatic_model_aliases(monkeypatch, automatic):
    monkeypatch.setenv("ARGUS_SKILL_OBSERVER_MODEL", automatic)
    assert _model("reviewer-provider/actual-model") == "reviewer-provider/actual-model"


@pytest.mark.parametrize("backend", ["pi", "dsh"])
def test_native_default_reaches_observer_and_light_calls_without_a_sentinel(monkeypatch, backend):
    monkeypatch.setenv("ARGUS_DESKTOP_LAUNCH_NONCE", "fixture")
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", backend)
    prepare_selection(os.environ, {"ARGUS_SKILL_RUNNER_BACKEND": "copilot", "ARGUS_SKILL_MODEL": "foreign-model"})
    reviewer = resolve_role_model("reviewer", role_env="ARGUS_SKILL_REVIEWER_MODEL", backend=backend)
    assert reviewer == ""
    assert _model(reviewer) == ""
    assert resolve_light_model(backend=backend) == ""
    monkeypatch.setenv("ARGUS_SKILL_OBSERVER_MODEL", "own-provider/observer-model")
    assert _model(reviewer) == "own-provider/observer-model"

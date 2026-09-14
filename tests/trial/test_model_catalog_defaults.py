"""A private client alias must never become the gateway's actual model ID."""
from types import MappingProxyType

import pytest

from argus_skill.trial import CLIENT_MODEL, DEFAULT_UPSTREAM_MODEL, MODEL
from argus_skill.trial.client import trial_enabled, trial_model_options
from argus_skill.trial.gateway import Settings
from argus_skill.trial.model_catalog import configured_model_ids, select_model


def test_server_default_is_distinct_from_the_opaque_client_alias(tmp_path, monkeypatch):
    monkeypatch.delenv("ARGUS_TRIAL_MODELS", raising=False)
    assert CLIENT_MODEL == MODEL
    assert Settings(tmp_path, tmp_path / "unused").model == DEFAULT_UPSTREAM_MODEL != MODEL
    assert configured_model_ids() == (DEFAULT_UPSTREAM_MODEL,)
    assert select_model(MODEL, DEFAULT_UPSTREAM_MODEL, configured_model_ids()) == DEFAULT_UPSTREAM_MODEL


def test_operator_model_catalog_does_not_accept_the_wire_alias_as_upstream():
    with pytest.raises(ValueError, match="invalid trial model ID"):
        configured_model_ids(MODEL, ())
    catalog = configured_model_ids("operator-chosen", ("another-model",))
    assert select_model(MODEL, "operator-chosen", catalog) == "operator-chosen"
    assert select_model("another-model", "operator-chosen", catalog) == "another-model"


@pytest.mark.parametrize("effort", [None, "low", "high"])
def test_client_alias_preserves_reasoning_and_readonly_environment(effort):
    env = MappingProxyType({"ARGUS_SKILL_COPILOT_TRIAL": "1"})
    assert trial_enabled(env)
    assert trial_model_options("auto", effort, env=env) == (MODEL, effort)
    with pytest.raises(ValueError, match="argus-trial"):
        trial_model_options("incompatible-explicit-model", effort, env=env)

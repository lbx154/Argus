"""A private client alias must never become the gateway's actual model ID."""
from types import MappingProxyType

import pytest

from argus.trial import MODEL
from argus.trial.client import trial_enabled, trial_model_options
from argus.trial.model_catalog import configured_model_ids, select_model


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

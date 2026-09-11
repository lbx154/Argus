import pytest

from argus_skill.trial.gateway import prepare
from argus_skill.trial.store import TrialError


def test_pi_can_use_the_actual_selected_model_name():
    data = {"model": "gpt-5.5", "messages": [{"role": "user", "content": "hello"}]}
    payload, _ = prepare(data, "gpt-5.5")
    assert payload["model"] == "gpt-5.5"
    assert data["model"] == "gpt-5.5"


def test_pi_cannot_select_a_different_upstream_model():
    with pytest.raises(TrialError):
        prepare({"model": "other-model", "messages": [{"role": "user", "content": "hi"}]}, "gpt-5.5")

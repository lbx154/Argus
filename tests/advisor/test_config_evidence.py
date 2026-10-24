
import pytest

from argus.advisor.config import AdvisorConfigError, load_advisor_config, save_advisor_config


@pytest.mark.parametrize("values", [
    {"enabled": True}, {"enabled": "true"}, {"backend": "unknown"}, {"model": "auto"},
    {"timeout_seconds": 0}, {"max_calls_per_turn": True}, {"max_evidence_bytes": 10},
    {"api_key": "never-stored"},
])
def test_invalid_config_cannot_replace_valid_settings(tmp_path, values):
    saved = save_advisor_config(tmp_path, {"effort": "low"})
    with pytest.raises(AdvisorConfigError):
        save_advisor_config(tmp_path, values)
    assert load_advisor_config(tmp_path, env={}) == saved

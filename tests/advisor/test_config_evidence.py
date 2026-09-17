import json

import pytest

from argus.advisor.config import AdvisorConfigError, load_advisor_config, save_advisor_config
from argus.advisor.evidence import collect_evidence


def test_config_is_explicit_and_project_scoped(tmp_path):
    root = tmp_path / "state"
    assert not load_advisor_config(root, env={"ARGUS_SKILL_MODEL": "main-model"}).enabled
    save_advisor_config(root, {"enabled": True, "backend": "pi", "model": "independent/expert"})
    saved = save_advisor_config(root, {"effort": "high"})
    assert saved.model == "independent/expert"
    effective = load_advisor_config(root, env={
        "ARGUS_SKILL_MODEL": "main-model", "ARGUS_SKILL_ADVISOR_MODEL": "independent/new-expert",
    })
    assert effective.model == "independent/new-expert" and effective.effort == "high"
    assert load_advisor_config(root, env={}).model == saved.model
    assert json.loads((root / "advisor/config.json").read_text()) == saved.to_dict()


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


def test_evidence_is_bounded_redacted_and_has_real_source_refs(tmp_path):
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    (workspace / "result.txt").write_text("test-secret " + "x" * 2000)
    rows = collect_evidence(["result.txt"], workspace=workspace, project_root=state,
                            byte_limit=1024, redact=lambda text: text.replace("test-secret", "[redacted]"))
    assert rows[0]["ref"] == "workspace:result.txt"
    assert rows[0]["truncated"] and rows[0]["bytes_read"] == 1024
    assert "test-secret" not in rows[0]["text"]
    assert len(rows[0]["sha256"]) == 64


@pytest.mark.parametrize("ref", ["../outside.txt", "/outside.txt", "state:../outside.txt", ".env", "escape.txt"])
def test_evidence_rejects_outside_and_credentials(tmp_path, ref):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("outside")
    (workspace / ".env").write_text("not evidence")
    (workspace / "escape.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError):
        collect_evidence([ref], workspace=workspace, project_root=tmp_path,
                         byte_limit=1024, redact=lambda text: text)

import json

import pytest

from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.research_controls import ResearchControls


@pytest.fixture
def controls(tmp_path):
    tenants = {}
    for tenant, sid in (("trial-01", "s-one"), ("trial-02", "s-two")):
        data = tmp_path / tenant
        project = data / "home/.argus-skill/projects" / sid
        project.mkdir(parents=True)
        (project / "session.json").write_text(json.dumps({"id": sid, "display_name": sid}))
        tenants[tenant] = {"data_dir": data, "internal_test": True}
    analytics = Analytics(
        tmp_path / "research", tenants, tmp_path / "usage.sqlite3",
        tmp_path / "compute.sqlite3", clock=lambda: 10000000,
    )
    analytics.record_consent("trial-01", analytics.notice_version)
    return ResearchControls(analytics)


def test_annotations_are_explicit_and_not_inferred_from_feedback(controls):
    assert controls.annotation("trial-01", "s-one")["satisfaction"] == "unknown"
    result = controls.feedback("trial-01", "s-one", {"verdict": "met_need", "note": "Useful"})
    assert result["inferred_success"] is False
    assert controls.annotation("trial-01", "s-one")["satisfaction"] == "unknown"
    saved = controls.annotate("trial-01", "s-one", {
        "user_goal": "Understand a real result", "first_deviation_event_id": "event:123",
        "satisfaction": "no", "note": "Missed the requested comparison",
    })
    assert saved["satisfaction"] == "no"
    assert saved["first_deviation_event_id"] == "event:123"


def test_cross_tenant_and_unconsented_records_are_denied(controls):
    with pytest.raises(AnalyticsError):
        controls.annotation("trial-01", "s-two")
    with pytest.raises(AnalyticsError) as error:
        controls.feedback("trial-02", "s-two", {"verdict": "met_need"})
    assert error.value.status == 403


def test_deletion_preserves_runtime_data_and_content_free_audit(controls):
    controls.annotate("trial-01", "s-one", {"user_goal": "Private user goal"})
    controls.feedback("trial-01", "s-one", {"verdict": "needs_changes"})
    result = controls.delete_project("trial-01", "s-one")
    assert result["deleted"]["research_annotations"] == 1
    assert result["deleted"]["research_feedback"] == 1
    assert result["runtime_files_deleted"] is False
    assert controls.annotation("trial-01", "s-one")["user_goal"] == ""
    audit = controls.audit_log()["events"]
    assert audit[0]["action"] == "research.delete"
    assert "Private user goal" not in json.dumps(audit)


def test_controls_obey_research_retention(controls):
    controls.annotate("trial-01", "s-one", {"user_goal": "A goal"})
    controls.feedback("trial-01", "s-one", {"verdict": "not_evaluated"})
    controls.audit("replay.view", tenant_id="trial-01", sid="s-one")
    controls.analytics.clock = lambda: 10000000 + 31 * 86400
    assert controls.prune() == {
        "research_annotations": 1, "research_feedback": 1, "research_audit": 1,
    }

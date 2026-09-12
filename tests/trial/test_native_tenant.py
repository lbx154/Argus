"""Synthetic native workspace mapping; never move or inspect live user files."""
import json

import pytest
from test_training_data import training as training

from argus_skill.trial.analytics import Analytics, AnalyticsError
from argus_skill.trial.journey_journal import Journal


def test_eleventh_native_workspace_uses_explicit_root_and_keeps_tenant_scope(tmp_path):
    now = [2_000_000_000.0]
    tenants = {f"trial-{index:02d}": {"data_dir": tmp_path / f"tenant-{index}", "internal_test": False}
               for index in range(1, 12)}
    native = tmp_path / "original-workspace/state"
    tenants["trial-11"]["global_root"] = native
    tenants["trial-11"]["runtime_mode"] = "host"
    project = native / "projects/s-native"
    project.mkdir(parents=True)
    (project / "session.json").write_text(json.dumps({"id": "s-native", "display_name": "Original native project"}))
    (project / "events.jsonl").touch()
    decoy = tenants["trial-11"]["data_dir"] / "home/.argus-skill/projects/s-decoy"
    decoy.mkdir(parents=True)
    (decoy / "session.json").write_text(json.dumps({"id": "s-decoy"}))
    analytics = Analytics(tmp_path / "index", tenants, tmp_path / "trial.sqlite3", tmp_path / "compute.sqlite3",
                          notice_version="operator-analytics-v2", clock=lambda: now[0])
    assert analytics.tenants["trial-01"]["global_root"] == tenants["trial-01"]["data_dir"] / "home/.argus-skill"
    assert analytics.tenants["trial-01"]["runtime_mode"] == "container"
    assert analytics.tenants["trial-11"]["runtime_mode"] == "host"
    with pytest.raises(AnalyticsError, match="consent"):
        analytics.projects("trial-11")
    analytics.record_consent("trial-11", analytics.notice_version)
    assert [row["id"] for row in analytics.projects("trial-11")["projects"]] == ["s-native"]
    journal = Journal(analytics)
    journal.poll("trial-11")
    now[0] += 1
    (project / "events.jsonl").write_text(json.dumps({"type": "life.mission.started", "item_id": "native-task",
                                                   "title": "Original task", "ts": now[0]}) + "\n")
    journal.poll("trial-11")
    result = journal.replay("trial-11", "s-native")
    assert any(event["task_id"] == "native-task" for event in result["events"])
    assert project.is_dir() and not project.is_symlink()
    analytics.record_consent("trial-01", analytics.notice_version)
    with pytest.raises(AnalyticsError):
        journal.replay("trial-01", "s-native")


def test_explicit_global_root_preserves_path_and_operator_index_boundaries(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    config = {"trial-11": {"data_dir": tmp_path / "legacy-data", "global_root": root, "internal_test": False}}
    with pytest.raises(ValueError, match="separate"):
        Analytics(root / "operator-index", config, tmp_path / "trial", tmp_path / "compute")
    config["trial-11"]["global_root"] = "relative/state"
    with pytest.raises(ValueError, match="global_root"):
        Analytics(tmp_path / "index", config, tmp_path / "trial", tmp_path / "compute")
    config["trial-11"]["global_root"] = root
    config["trial-11"]["runtime_mode"] = "automatic"
    with pytest.raises(ValueError, match="runtime_mode"):
        Analytics(tmp_path / "index", config, tmp_path / "trial", tmp_path / "compute")
    config["trial-11"].pop("runtime_mode")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    config["trial-11"]["global_root"] = alias
    analytics = Analytics(tmp_path / "index", config, tmp_path / "trial", tmp_path / "compute")
    analytics.record_consent("trial-11", analytics.notice_version)
    with pytest.raises(AnalyticsError, match="unsafe_path"):
        analytics.projects("trial-11")


def test_session_recovery_uses_explicit_native_root_without_rearranging_files(training, tmp_path):
    from test_training_recovery import SESSION, iso, legacy, native

    from argus_skill.trial.training_recovery import recover_episode

    data, _, now = training
    episode = legacy(training)
    decoy = native(training, [{"role": "user", "content": "Default-layout decoy."}])
    root = tmp_path / "original-native/state"
    directory = root / "pi-sessions"
    directory.mkdir(parents=True)
    source = directory / f"actual_{SESSION}.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session", "id": SESSION, "timestamp": iso(now[0])},
        {"type": "message", "id": "native-real", "timestamp": iso(now[0] + 1),
         "message": {"role": "user", "content": "Original native input."}},
    ]) + "\n")
    data.analytics.tenants["tenant-one"]["global_root"] = root
    now[0] += 10
    assert recover_episode(data, episode)["status"] == "recovered"
    with data.analytics._db() as db:
        events = data.capture.events(db, episode)
    assert events[0]["payload"]["messages"][0]["content"] == "Original native input."
    assert decoy.is_file() and source.is_file() and not source.is_symlink()

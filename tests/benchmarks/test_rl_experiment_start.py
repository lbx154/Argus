import json

from tools.rl_experiment_benchmark import start


def test_resume_preserves_current_objective_and_other_arguments(tmp_path):
    (tmp_path / "continuous.json").write_text(json.dumps({"objective": "Current Manager direction"}))
    command = ["python", "-m", "argus", "--daemon", "--continuous", "--objective-file", "old.md", "--resume", "s-case"]
    assert start.resume_command(command, tmp_path) == [
        "python", "-m", "argus", "--daemon", "--resume", "s-case", "--resume-continuous",
    ]
    (tmp_path / "continuous.json").unlink()
    assert start.resume_command(command, tmp_path) == command


def test_existing_processes_do_not_start_a_second_collector_or_daemon(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    session = tmp_path / "state/projects/s-case"
    workspace.mkdir()
    session.mkdir(parents=True)
    (session / "daemon.pid").write_text("123")
    (tmp_path / "collector-process.json").write_text(json.dumps({"pid": 456}))
    (tmp_path / "case.json").write_text(json.dumps({
        "case_id": "case", "workspace": str(workspace), "session_root": str(session),
        "launch": {"command": ["python", "-m", "argus", "--daemon", "--resume", "s-case"]},
    }))
    markers = []
    def matches(pid, marker):
        markers.append((pid, marker))
        return True
    monkeypatch.setattr(start, "process_matches", matches)
    def unexpected(*args, **kwargs):
        raise AssertionError("Already-running case must not create processes")
    monkeypatch.setattr(start.subprocess, "Popen", unexpected)
    monkeypatch.setattr(start.subprocess, "run", unexpected)
    first = start.start(tmp_path)
    second = start.start(tmp_path)
    assert first == second == {"collector_pid": 456, "daemon_pid": 123, "case_id": "case"}
    assert (456, "--root\0" + str(tmp_path)) in markers
    assert json.loads((tmp_path / "case.json").read_text())["started_at"]

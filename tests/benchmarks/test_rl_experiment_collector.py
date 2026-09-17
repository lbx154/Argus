import json

from tools.rl_experiment_benchmark.collect import collect


def setup(tmp_path):
    work = tmp_path / "workspace"
    session = tmp_path / "session"
    work.mkdir()
    session.mkdir()
    (tmp_path / "case.json").write_text(json.dumps({
        "case_id": "test", "started_at": "2026-09-16T04:38:32+00:00",
        "workspace": str(work), "session_root": str(session),
    }))
    return work, session


def test_collection_deduplicates_and_keeps_partial_append_for_next_pass(tmp_path):
    work, session = setup(tmp_path)
    event = {"type": "agent.io.start", "ts": 2000000000, "call_id": "a", "run_label": "engineer-r1"}
    path = session / "events.jsonl"
    path.write_text(json.dumps(event))
    assert not collect(tmp_path)["role_calls"]
    with path.open("a") as f:
        f.write("\n")
    assert collect(tmp_path)["role_calls"] == {"engineer": 1}
    assert collect(tmp_path)["role_calls"] == {"engineer": 1}
    assert len((tmp_path / "observations.jsonl").read_text().splitlines()) == 1


def test_old_metrics_never_count_as_new_learning_and_conversations_are_excluded(tmp_path):
    work, session = setup(tmp_path)
    run = work / "runs/agentic-learning-20260915/test"
    run.mkdir(parents=True)
    metrics = [{"time": 1, "grad_norm": 1, "executable_reward_std": 1},
               {"time": 2000000000, "grad_norm": 0.1, "executable_reward_std": 0}]
    (run / "performance.jsonl").write_text("".join(json.dumps(x) + "\n" for x in metrics))
    (run / "rollout-boundaries.jsonl").write_text(json.dumps({"time": 2000000000, "samples": [
        {"stop_reason": "completion_token_limit", "messages": [{"content": "private conversation"}]}]}) + "\n")
    auth = work / "configs/pi-agent"
    auth.mkdir(parents=True)
    (auth / "auth.json").write_text('{"credential": "private credential"}')
    status = collect(tmp_path)
    assert status["new_reward_contrast_optimizer_records"] == 0
    output = (tmp_path / "observations.jsonl").read_text()
    assert "private conversation" not in output and "private credential" not in output
    assert '"period": "historical"' in output
    assert "completion_token_limit" in output


def test_source_changes_and_job_transitions_survive_collector_restart(tmp_path):
    work, session = setup(tmp_path)
    source = work / "src/agentic_grpo/training.py"
    source.parent.mkdir(parents=True)
    source.write_text("first\n")
    jobs = work / ".argus_subagents"
    jobs.mkdir()
    job = jobs / "train.json"
    job.write_text(json.dumps({"task_id": "train", "run_id": "one", "state": "waiting_resource", "heartbeat_at": 1}))
    assert collect(tmp_path)["active_jobs"][0]["state"] == "waiting_resource"
    job.write_text(json.dumps({"task_id": "train", "run_id": "one", "state": "waiting_resource", "heartbeat_at": 2}))
    collect(tmp_path)
    records = [json.loads(line) for line in (tmp_path / "observations.jsonl").read_text().splitlines()]
    assert sum(r["kind"] == "job" for r in records) == 1
    source.write_text("second\n")
    job.write_text(json.dumps({"task_id": "train", "run_id": "one", "state": "running"}))
    assert collect(tmp_path)["active_jobs"][0]["state"] == "running"
    assert len(list((tmp_path / "source-snapshots").glob("*.py"))) == 2


def test_evaluation_candidates_and_edit_attribution_are_preserved(tmp_path):
    work, session = setup(tmp_path)
    run = work / "runs/agentic-learning-20260915/eval"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps([
        {"instance_id": "same-task", "resolved": True},
        {"instance_id": "same-task", "resolved": True},
    ]))
    (session / "events.jsonl").write_text(json.dumps({
        "type": "engineer.progress", "ts": 2000000000, "actor": "engineer-r1",
        "tool_name": "edit", "text": 'edit: {"path": "src/train.py", "content": "not collected"}',
    }) + "\n")
    collect(tmp_path)
    records = [json.loads(line) for line in (tmp_path / "observations.jsonl").read_text().splitlines()]
    candidates = [r for r in records if r["kind"] == "metric"]
    assert len(candidates) == 2
    assert {r["data"]["source_row_index"] for r in candidates} == {0, 1}
    event = next(r["data"] for r in records if r["kind"] == "role_event")
    assert event["actor"] == "engineer-r1" and event["target_paths"] == ["src/train.py"]
    assert "not collected" not in json.dumps(records)

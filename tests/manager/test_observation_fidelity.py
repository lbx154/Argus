"""Canonical evidence must survive bounded Manager presentation and reuse checks."""
from __future__ import annotations

import json
import multiprocessing
import os
import socket

import pytest

from argus_skill.core.event_catalog import EventType
from argus_skill.core.models import RunnerResult
from argus_skill.daemon.state import write_continuous_config
from argus_skill.life.context_packet import mission_context_dir
from argus_skill.life.memory import Backlog, BacklogItem
from argus_skill.manager import Manager
from argus_skill.manager import observation as observation_module
from argus_skill.manager.directive import load_active_manager_directive
from argus_skill.manager.observation import control_identity, observe_project
from argus_skill.manager.supervision import supervise

LONG_REQUIREMENT_PREFIX = "Validate each recorded acceptance rule without weakening it. " * 30
LONG_EXCERPT_PREFIX = "Recorded diagnostic detail from the earlier attempt. " * 40


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Observation fidelity tests must not call any provider")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


class Backend:
    def __init__(self, before_return=None):
        self.prompts = []
        self.before_return = before_return

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label == "manager-supervision" and options.disable_tools
        self.prompts.append(prompt)
        if self.before_return:
            self.before_return()
        return RunnerResult(exit_code=0, call_id=f"offline-fidelity-{len(self.prompts)}",
                            thread_id=resume_thread_id or "offline-fidelity-session", agent_messages=[json.dumps({
                                "action": "steer", "reason": "Repair the observed grouping error while preserving the acceptance standard.",
                                "directive": "Compute each group separately and preserve missing-value checks.",
                                "evidence_refs": ["backlog.jsonl"],
                            })])


def project(root, acceptance="Compute each group separately; exclude missing values"):
    write_continuous_config(root, enabled=True, objective="Produce a validated grouped summary")
    item = BacklogItem.new(item_id="fidelity-task", title="Check grouped means", objective="Preserve the explicit acceptance standard")
    item.acceptance_check = acceptance
    backlog = Backlog(root / "backlog.jsonl")
    backlog.add(item)
    return backlog, item, {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": item.id}


def task_row(observation, task_id):
    return next(row for row in observation.facts["tasks"] if row["id"] == task_id)


def controls(root):
    return control_identity(root), (root / "backlog.jsonl").read_bytes()


def write_excerpt(root, backlog, item, field, value):
    if field == "last_error":
        backlog.update(item.id, last_error=value)
        return
    packet = mission_context_dir(root, item.id) / "mission.json"
    packet.parent.mkdir(parents=True, exist_ok=True)
    packet.write_text(json.dumps({"id": item.id, "acceptance_check": item.acceptance_check}))
    (packet.parent / "latest.json").write_text(json.dumps({"kind": "review_handoff", "engineer_summary": value}))


def displayed_excerpt(observation, task_id, field):
    row = task_row(observation, task_id)
    return row["last_error"] if field == "last_error" else row["evidence"]["engineer_summary"]


def bounded_source_reads(monkeypatch, source, after_read=None):
    """Measure the actual bounded source read without substituting its contents."""
    original_open = observation_module.open_regular_file
    reads = []

    class Reader:
        def __init__(self, handle):
            self.handle = handle
            self.total = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def read(self, size=-1):
            assert 0 < size <= observation_module.MAX_SOURCE_BYTES + 1
            data = self.handle.read(size)
            self.total += len(data)
            assert self.total <= observation_module.MAX_SOURCE_BYTES + 1
            reads.append(len(data))
            if after_read:
                after_read()
            return data

    def opened(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return Reader(handle) if path == source else handle

    monkeypatch.setattr(observation_module, "open_regular_file", opened)
    return reads


def assert_source_unobserved(observation, source):
    assert str(source) in observation.incomplete_requirements
    assert any(str(source) in note and "observed" in note for note in observation.facts["limitations"])
    assert len(observation.render().encode("utf-8")) <= observation_module.MAX_OBSERVATION_BYTES


def assert_unobserved_source_blocks_supervision(root, event, source):
    before = controls(root)
    backend = Backend()
    record = supervise(Manager(root, runner=backend, memory_maintenance_enabled=False), root, event)
    assert record["status"] == "failed" and record["failure_stage"] == "decision"
    assert record["error_code"] == "observation_incomplete"
    assert str(source) in record["incomplete_requirements"]
    assert any(str(source) in note for note in record["observation_limitations"])
    assert not backend.prompts and not record.get("decision") and not record.get("effects")
    assert controls(root) == before and load_active_manager_directive(root) is None


def test_acceptance_tail_change_has_a_new_revision_and_cannot_reuse_an_applied_receipt(tmp_path):
    first_requirement = LONG_REQUIREMENT_PREFIX + "FINAL CHECK: retain all negative observations."
    second_requirement = LONG_REQUIREMENT_PREFIX + "FINAL CHECK: independently verify every negative observation."
    assert first_requirement[:1600] == second_requirement[:1600]
    backlog, item, event = project(tmp_path, first_requirement)
    backend = Backend()
    manager = Manager(tmp_path, runner=backend, memory_maintenance_enabled=False)
    before = observe_project(tmp_path, event=event)
    first = supervise(manager, tmp_path, event)
    assert first["status"] == "applied" and len(backend.prompts) == 1

    backlog.update(item.id, acceptance_check=second_requirement)
    after = observe_project(tmp_path, event=event)
    assert after.evidence_revision != before.evidence_revision
    second = supervise(manager, tmp_path, event)
    assert second["status"] == "applied" and second["id"] != first["id"]
    assert len(backend.prompts) == 2 and second_requirement in backend.prompts[-1]
    assert supervise(manager, tmp_path, event)["id"] == second["id"]
    assert len(backend.prompts) == 2


def test_acceptance_that_fits_the_observation_budget_reaches_the_actual_model_prompt_intact(tmp_path):
    requirement = LONG_REQUIREMENT_PREFIX + "FINAL CHECK: report the independently recomputed values before claiming success."
    _, item, event = project(tmp_path, requirement)
    observation = observe_project(tmp_path, event=event)
    assert task_row(observation, item.id)["acceptance_check"] == requirement
    backend = Backend()
    record = supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "applied"
    assert len(backend.prompts) == 1 and requirement in backend.prompts[0]


def test_required_acceptance_survives_secondary_evidence_removal_within_utf8_budget(tmp_path):
    requirement = LONG_REQUIREMENT_PREFIX * 3 + "FINAL ACCEPTANCE: independently recompute the original group values."
    backlog, item, event = project(tmp_path, requirement)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", LONG_EXCERPT_PREFIX)
    backlog.update(item.id, last_error=LONG_EXCERPT_PREFIX)
    packet_dir = mission_context_dir(tmp_path, item.id)
    (packet_dir / "frontier.json").write_text(json.dumps({
        "current_hypothesis": LONG_EXCERPT_PREFIX,
        "remaining_work": LONG_EXCERPT_PREFIX,
        "active_regression": LONG_EXCERPT_PREFIX,
        "evidence": [{"summary": LONG_EXCERPT_PREFIX, "reason": LONG_EXCERPT_PREFIX} for _ in range(8)],
    }))
    observation = observe_project(tmp_path, event=event)
    assert task_row(observation, item.id)["acceptance_check"] == requirement
    assert observation.incomplete_requirements == ()
    assert observation.facts["limitations"]
    assert len(observation.render().encode("utf-8")) <= observation_module.MAX_OBSERVATION_BYTES
    backend = Backend()
    record = supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "applied" and requirement in backend.prompts[0]


@pytest.mark.parametrize("field", ["acceptance_check", "objective", "pending_question"])
def test_overbudget_critical_field_is_named_as_unobserved_and_stops_before_model_call(tmp_path, field):
    backlog, item, event = project(tmp_path)
    # UTF-8 size, rather than character count, determines whether the requirement fits.
    unit = "必须独立核验原始结果；"
    value = unit * (observation_module.MAX_OBSERVATION_BYTES // len(unit.encode("utf-8")) + 1)
    value += "REQUIRED_TAIL_MUST_NOT_BE_SILENTLY_LOST"
    backlog.update(item.id, **{field: value})
    before = controls(tmp_path)
    observation = observe_project(tmp_path, event=event)
    path = f"tasks[{item.id}].{field}"
    notes = observation.facts["limitations"]
    assert path in observation.incomplete_requirements
    assert any(path in note and any(word in note.lower() for word in ("budget", "trunc", "预算", "截断")) for note in notes)
    assert len(observation.render().encode("utf-8")) <= observation_module.MAX_OBSERVATION_BYTES
    assert "REQUIRED_TAIL_MUST_NOT_BE_SILENTLY_LOST" not in observation.render()
    backend = Backend()
    record = supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    assert record["status"] == "failed" and record["failure_stage"] == "decision"
    assert path in record["incomplete_requirements"]
    assert record["observation_limitations"] == notes
    assert record["error_code"] == "observation_incomplete"
    assert not backend.prompts and not record.get("decision") and not record.get("effects")
    assert controls(tmp_path) == before and load_active_manager_directive(tmp_path) is None


@pytest.mark.parametrize("field", ["last_error", "engineer_summary"])
def test_excerpt_tail_edits_change_canonical_revision_even_when_presentation_is_identical(tmp_path, field):
    backlog, item, event = project(tmp_path)
    original = LONG_EXCERPT_PREFIX + "Hidden tail: the check passed."
    changed = LONG_EXCERPT_PREFIX + "Hidden tail: the check failed."
    assert original[:1600] == changed[:1600]
    write_excerpt(tmp_path, backlog, item, field, original)
    before = observe_project(tmp_path, event=event)
    write_excerpt(tmp_path, backlog, item, field, changed)
    after = observe_project(tmp_path, event=event)
    assert displayed_excerpt(before, item.id, field) == displayed_excerpt(after, item.id, field)
    assert before.evidence_revision != after.evidence_revision


@pytest.mark.parametrize("field", ["last_error", "engineer_summary"])
def test_unseen_excerpt_tail_edit_fences_a_running_manager_result(tmp_path, field):
    backlog, item, event = project(tmp_path)
    original = LONG_EXCERPT_PREFIX + "Hidden tail: the check passed."
    changed = LONG_EXCERPT_PREFIX + "Hidden tail: the check failed."
    write_excerpt(tmp_path, backlog, item, field, original)
    before = observe_project(tmp_path, event=event)
    expected_control = []

    def edit_while_model_runs():
        write_excerpt(tmp_path, backlog, item, field, changed)
        expected_control.append(controls(tmp_path))

    backend = Backend(before_return=edit_while_model_runs)
    record = supervise(Manager(tmp_path, runner=backend, memory_maintenance_enabled=False), tmp_path, event)
    after = observe_project(tmp_path, event=event)
    assert displayed_excerpt(before, item.id, field) == displayed_excerpt(after, item.id, field)
    assert before.evidence_revision != after.evidence_revision
    assert record["status"] == "superseded" and not record.get("effects")
    assert controls(tmp_path) == expected_control[0]
    assert load_active_manager_directive(tmp_path) is None and not (tmp_path / "inbox.jsonl").exists()
    assert len(backend.prompts) == 1


@pytest.mark.parametrize("filename", ["frontier.json", "latest.json"])
@pytest.mark.parametrize("rewrite", ["same-inode", "new-inode"])
def test_oversize_sources_with_equal_prefix_size_and_mtime_keep_distinct_unobserved_identity(tmp_path, monkeypatch, filename, rewrite):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / filename
    prefix = '{"padding":"' + "p" * (observation_module.MAX_SOURCE_BYTES + 64)
    original = (prefix + '","tail":"UNREAD_TAIL_A"}').encode()
    changed = (prefix + '","tail":"UNREAD_TAIL_B"}').encode()
    assert len(original) == len(changed)
    assert original[:observation_module.MAX_SOURCE_BYTES + 1] == changed[:observation_module.MAX_SOURCE_BYTES + 1]
    source.write_bytes(original)
    stamp = source.stat()
    reads = bounded_source_reads(monkeypatch, source)
    before = observe_project(tmp_path, event=event)
    assert_source_unobserved(before, source)
    assert_unobserved_source_blocks_supervision(tmp_path, event, source)

    if rewrite == "same-inode":
        source.write_bytes(changed)
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    else:
        replacement = source.with_suffix(".replacement")
        replacement.write_bytes(changed)
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        os.replace(replacement, source)
    replaced = source.stat()
    assert replaced.st_size == stamp.st_size and replaced.st_mtime_ns == stamp.st_mtime_ns
    if rewrite == "same-inode":
        assert replaced.st_ino == stamp.st_ino and replaced.st_ctime_ns != stamp.st_ctime_ns
    else:
        assert replaced.st_ino != stamp.st_ino
    after = observe_project(tmp_path, event=event)
    assert_source_unobserved(after, source)
    assert after.evidence_revision != before.evidence_revision
    assert_unobserved_source_blocks_supervision(tmp_path, event, source)
    assert reads and max(reads) <= observation_module.MAX_SOURCE_BYTES + 1


@pytest.mark.parametrize("contents", ['{"unfinished":', '[{"claim":"not an object"}]'])
def test_invalid_source_objects_are_explicitly_unobserved_and_cannot_authorize_a_call(tmp_path, contents):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / "frontier.json"
    source.write_text(contents)
    observation = observe_project(tmp_path, event=event)
    assert_source_unobserved(observation, source)
    assert_unobserved_source_blocks_supervision(tmp_path, event, source)


def test_source_changed_during_read_is_not_treated_as_a_complete_snapshot(tmp_path, monkeypatch):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / "frontier.json"
    source.write_text(json.dumps({"remaining_work": "Original observation"}))
    writes = []

    def change_after_read():
        writes.append(len(writes))
        source.write_text(json.dumps({"remaining_work": "Changed while reading " + str(len(writes))}))

    reads = bounded_source_reads(monkeypatch, source, change_after_read)
    observation = observe_project(tmp_path, event=event)
    assert_source_unobserved(observation, source)
    assert any(str(source) in note and "changed" in note for note in observation.facts["limitations"])
    assert_unobserved_source_blocks_supervision(tmp_path, event, source)
    assert reads and writes


@pytest.mark.parametrize("shape", ["ninth-list-item", "deep-field"])
def test_unpresented_frontier_tail_has_a_canonical_identity_and_specific_limitation(tmp_path, shape):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / "frontier.json"

    def content(tail):
        if shape == "ninth-list-item":
            return {"evidence": [{"check": f"visible-{index}"} for index in range(8)] + [{"check": tail}]}
        return {"evidence": {"layer1": {"layer2": {"layer3": {"check": tail}}}}}

    source.write_text(json.dumps(content("UNPRESENTED_TAIL_A")))
    before = observe_project(tmp_path, event=event)
    source.write_text(json.dumps(content("UNPRESENTED_TAIL_B")))
    after = observe_project(tmp_path, event=event)
    assert task_row(before, item.id)["frontier"] == task_row(after, item.id)["frontier"]
    assert before.evidence_revision != after.evidence_revision
    assert "UNPRESENTED_TAIL_A" not in before.render() and "UNPRESENTED_TAIL_B" not in after.render()
    path = f"tasks[{item.id}].frontier.evidence"
    detail = "entries" if shape == "ninth-list-item" else "deeper"
    assert any(path in note and detail in note for note in after.facts["limitations"])
    assert before.incomplete_requirements == after.incomplete_requirements == ()


@pytest.mark.skipif(not hasattr(os, "mkfifo") or "fork" not in multiprocessing.get_all_start_methods(),
                    reason="Requires a POSIX FIFO and a bounded fork worker")
def test_fifo_source_without_a_writer_is_rejected_without_blocking_supervision(tmp_path):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / "frontier.json"
    os.mkfifo(source)
    context = multiprocessing.get_context("fork")
    reader, writer = context.Pipe(duplex=False)

    def check_source():
        try:
            observation = observe_project(tmp_path, event=event)
            assert_source_unobserved(observation, source)
            assert_unobserved_source_blocks_supervision(tmp_path, event, source)
            writer.send({"passed": True})
        except BaseException as exc:
            writer.send({"passed": False, "error": type(exc).__name__, "detail": str(exc)})
        finally:
            writer.close()

    worker = context.Process(target=check_source)
    worker.start()
    writer.close()
    try:
        worker.join(timeout=3)
        assert not worker.is_alive(), "FIFO read blocked without a writer"
        assert worker.exitcode == 0 and reader.poll(1)
        result = reader.recv()
        assert result["passed"], result
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=2)
        reader.close()
        worker.close()


@pytest.mark.parametrize("link", ["leaf", "parent"])
def test_symlink_sources_are_named_as_unobserved_without_reading_the_target(tmp_path, link):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    packet_dir = mission_context_dir(tmp_path, item.id)
    source = packet_dir / "frontier.json"
    marker = "SYMLINK_TARGET_CONTENT_MUST_NOT_BE_OBSERVED"
    if link == "leaf":
        target = tmp_path / "untrusted-frontier.json"
        target.write_text(json.dumps({"remaining_work": marker}))
        source.symlink_to(target)
    else:
        target = tmp_path / "untrusted-packet"
        packet_dir.rename(target)
        (target / "frontier.json").write_text(json.dumps({"remaining_work": marker}))
        packet_dir.symlink_to(target, target_is_directory=True)
    observation = observe_project(tmp_path, event=event)
    assert_source_unobserved(observation, source)
    assert marker not in observation.render()
    assert_unobserved_source_blocks_supervision(tmp_path, event, source)

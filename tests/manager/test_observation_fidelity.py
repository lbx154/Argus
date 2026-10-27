"""What the Manager is shown is bounded, every bound is named, and none stops it being asked."""
from __future__ import annotations

import json
import socket

import pytest

from argus.core.event_catalog import EventType
from argus.core.models import RunnerResult
from argus.core.role_actions import PREFIX
from argus.core.role_tool_bridge import bridge_request
from argus.daemon.state import write_continuous_config
from argus.life.context_packet import mission_context_dir
from argus.life.memory import Backlog, BacklogItem
from argus.manager import Manager
from argus.manager import observation as observation_module
from argus.manager.directive import load_active_manager_directive
from argus.manager.observation import observe_project
from argus.manager.supervision import supervise

LONG_EXCERPT_PREFIX = "Recorded diagnostic detail from the earlier attempt. " * 40


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Only the call's own loopback action bridge may be reached."""
    connect = socket.socket.connect

    def loopback_only(self, address):
        if address[0] != "127.0.0.1":
            raise AssertionError("Observation fidelity tests must not call any provider")
        return connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)


class Backend:
    def __init__(self, before_return=None):
        self.prompts = []
        self.before_return = before_return

    def fork(self):
        return self

    def run_exec(self, *, prompt, options, run_label, resume_thread_id=None):
        assert run_label == "manager-supervision" and not options.disable_tools and options.sandbox_mode == "read-only"
        self.prompts.append(prompt)
        bridge_request(PREFIX, "hand_off", {
            "to": "team", "summary": "Repair the observed grouping error while preserving the acceptance standard.",
            "note": "Compute each group separately and preserve missing-value checks.",
            "evidence_refs": ["backlog.jsonl"], "assumptions": [],
        }, env=options.extension_env)
        if self.before_return:
            self.before_return()
        return RunnerResult(exit_code=0, call_id=f"offline-fidelity-{len(self.prompts)}",
                            thread_id=resume_thread_id or "offline-fidelity-session", agent_messages=["Steered."])


def project(root, acceptance="Compute each group separately; exclude missing values"):
    write_continuous_config(root, enabled=True, objective="Produce a validated grouped summary")
    item = BacklogItem.new(item_id="fidelity-task", title="Check grouped means", objective="Preserve the explicit acceptance standard")
    item.acceptance_check = acceptance
    backlog = Backlog(root / "backlog.jsonl")
    backlog.add(item)
    return backlog, item, {"type": EventType.LIFE_MISSION_COMPLETED, "item_id": item.id}


def task_row(observation, task_id):
    return next(row for row in observation.facts["tasks"] if row["id"] == task_id)


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
    assert any(str(source) in note and "observed" in note for note in observation.facts["limitations"])
    assert len(observation.render().encode("utf-8")) <= observation_module.MAX_OBSERVATION_BYTES


def assert_unobserved_source_still_asks_the_manager(root, event, source):
    """A source it could not read is named to the Manager, which still decides (s-efe9deac)."""
    backend = Backend()
    record = supervise(Manager(root, runner=backend), root, event)
    assert len(backend.prompts) == 1 and str(source) in backend.prompts[0]
    assert any(str(source) in note for note in record["observation_limitations"])
    assert record["status"] == "applied"
    assert load_active_manager_directive(root).text == record["decision"]["directive"]


def test_budget_omissions_share_one_note_so_notes_do_not_evict_objectives(tmp_path):
    # s-a43d1018: six parallel arms with long Chinese briefs. A sentence per
    # omitted field (title, plan_id, deps, ...) cost more than the field and
    # evicted whole objectives; one note leaves room for one more objective.
    write_continuous_config(tmp_path, enabled=True, objective="在高维小样本分类中寻找通用方法")
    backlog = Backlog(tmp_path / "backlog.jsonl")
    for arm in ("cpc", "general", "dense", "sparse", "count", "centered"):
        item = BacklogItem.new(item_id=f"arm-{arm}", title=f"完成{arm}臂的稀疏先验训练与同输入几何比较",
                               objective=f"【{arm}】" + "复用共享实现，保持原生设置并只改变配对几何；" * 34)
        item.acceptance_check = "分析文件存在且逐对象导出可由独立消费者复算。"
        item.goal_contribution = "判断配对几何是否改善相同支持与查询上的预测。"
        item.last_error = "上一轮在训练终点分析时报错，训练产物保持完整。"
        item.plan_id, item.plan_version = "plan-7", 3
        backlog.add(item)

    observation = observe_project(tmp_path)

    budget_notes = [note for note in observation.facts["limitations"] if "byte budget" in note]
    assert len(budget_notes) == 1
    assert "tasks[arm-cpc].title" in budget_notes[0]
    assert all(row["objective"] == item.objective
               for row, item in zip(observation.facts["tasks"], backlog.active(), strict=True))


@pytest.mark.parametrize("field", ["last_error", "engineer_summary"])
def test_unseen_excerpt_tail_edit_while_the_manager_decides_is_recorded_not_voided(tmp_path, field):
    """Work that moved on while the Manager decided leaves its decision standing.

    Voiding it lost the decision, and nothing asked the Manager again
    (2026-10-20: a steer for a stuck campaign dropped, the project went silent).
    """
    backlog, item, event = project(tmp_path)
    original = LONG_EXCERPT_PREFIX + "Hidden tail: the check passed."
    changed = LONG_EXCERPT_PREFIX + "Hidden tail: the check failed."
    write_excerpt(tmp_path, backlog, item, field, original)
    before = observe_project(tmp_path, event=event)

    backend = Backend(before_return=lambda: write_excerpt(tmp_path, backlog, item, field, changed))
    record = supervise(Manager(tmp_path, runner=backend), tmp_path, event)
    after = observe_project(tmp_path, event=event)
    assert displayed_excerpt(before, item.id, field) == displayed_excerpt(after, item.id, field)
    assert record["status"] == "applied"
    assert load_active_manager_directive(tmp_path).text == record["decision"]["directive"]
    assert len(backend.prompts) == 1


@pytest.mark.parametrize("filename", ["mission.json", "latest.json"])
def test_an_oversize_source_is_read_only_to_its_bound_and_named(tmp_path, monkeypatch, filename):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / filename
    source.write_bytes(('{"padding":"' + "p" * (observation_module.MAX_SOURCE_BYTES + 64) + '"}').encode())
    reads = bounded_source_reads(monkeypatch, source)
    assert_source_unobserved(observe_project(tmp_path, event=event), source)
    assert_unobserved_source_still_asks_the_manager(tmp_path, event, source)
    assert reads and max(reads) <= observation_module.MAX_SOURCE_BYTES + 1


def test_source_changed_during_read_is_not_treated_as_a_complete_snapshot(tmp_path, monkeypatch):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    source = mission_context_dir(tmp_path, item.id) / "mission.json"
    source.write_text(json.dumps({"remaining_work": "Original observation"}))
    writes = []

    def change_after_read():
        writes.append(len(writes))
        source.write_text(json.dumps({"remaining_work": "Changed while reading " + str(len(writes))}))

    reads = bounded_source_reads(monkeypatch, source, change_after_read)
    observation = observe_project(tmp_path, event=event)
    assert_source_unobserved(observation, source)
    assert any(str(source) in note and "changed" in note for note in observation.facts["limitations"])
    assert_unobserved_source_still_asks_the_manager(tmp_path, event, source)
    assert reads and writes


@pytest.mark.parametrize("shape", ["ninth-list-item", "deep-field"])
def test_an_unpresented_handoff_tail_is_named_in_a_specific_limitation(tmp_path, shape):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    if shape == "ninth-list-item":
        review = [{"check": f"visible-{index}"} for index in range(8)] + [{"check": "UNPRESENTED_TAIL"}]
    else:
        review = {"layer1": {"layer2": {"layer3": {"check": "UNPRESENTED_TAIL"}}}}
    (mission_context_dir(tmp_path, item.id) / "latest.json").write_text(json.dumps(
        {"kind": "review_handoff", "engineer_summary": "A readable initial handoff", "review": review}))
    observation = observe_project(tmp_path, event=event)
    assert "UNPRESENTED_TAIL" not in observation.render()
    path = f"tasks[{item.id}].evidence.review"
    detail = "entries" if shape == "ninth-list-item" else "deeper"
    assert any(path in note and detail in note for note in observation.facts["limitations"])


@pytest.mark.parametrize("link", ["leaf", "parent"])
def test_symlink_sources_are_named_as_unobserved_without_reading_the_target(tmp_path, link):
    backlog, item, event = project(tmp_path)
    write_excerpt(tmp_path, backlog, item, "engineer_summary", "A readable initial handoff")
    packet_dir = mission_context_dir(tmp_path, item.id)
    source = packet_dir / "mission.json"
    marker = "SYMLINK_TARGET_CONTENT_MUST_NOT_BE_OBSERVED"
    if link == "leaf":
        target = tmp_path / "untrusted-mission.json"
        target.write_text(json.dumps({"remaining_work": marker}))
        source.unlink()
        source.symlink_to(target)
    else:
        target = tmp_path / "untrusted-packet"
        packet_dir.rename(target)
        (target / "mission.json").write_text(json.dumps({"remaining_work": marker}))
        packet_dir.symlink_to(target, target_is_directory=True)
    observation = observe_project(tmp_path, event=event)
    assert_source_unobserved(observation, source)
    assert marker not in observation.render()
    assert_unobserved_source_still_asks_the_manager(tmp_path, event, source)

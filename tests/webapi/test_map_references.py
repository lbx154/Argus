"""Server-side expansion of Atlas card references (``[[Argus引用 {...}]]``).

The Atlas composer serializes a quoted card/step as one marker line
(``frontend/web/src/map/presentation.ts`` ``referenceText``); before this
module existed the backend handed the raw JSON line to the Manager verbatim.
These tests pin:

- the parsing contract (a strict mirror of the frontend ``splitDraft``
  validation — a malformed line is left untouched, never guessed at),
- the readable inline replacement in the persisted operator text,
- the bounded context block (backlog task data + event-journal excerpts,
  secret-redacted, capped at four references and ~4KB),
- the dependency filter used when the message becomes a task (F3): only
  referenced tasks that exist and are not terminal-without-done may be
  attached as deps, because ``memory._cascade_blocked`` skips dependents of
  failed/skipped/superseded tasks.
"""

from __future__ import annotations

import json
from pathlib import Path

from argus_skill.life.memory import BacklogItem, LifeMemory
from argus_skill.webapi.map_references import (
    CONTEXT_BLOCK_MAX_CHARS,
    expand_operator_references,
    parse_card_reference,
)
from argus_skill.webapi.map_view import digest


def _marker(task_id: str = "task-a", **overrides) -> str:
    ref = {
        "source": "live:s-ref",
        "task_id": task_id,
        "task_title": "Coverage study",
        "event_ids": [],
    }
    ref.update(overrides)
    return "[[Argus引用 " + json.dumps(ref, ensure_ascii=False) + "]]"


_DIGEST_EVENT = {
    "type": "agent.message",
    "item_id": "task-a",
    "text": "第二条证据：区间宽度保持稳定。",
    "ts": 4.0,
}


def _life(tmp_path: Path) -> Path:
    life = tmp_path / "projects" / "s-ref"
    memory = LifeMemory.open(life)
    memory.backlog.add(
        BacklogItem(
            id="task-a",
            ts=1,
            title="Compare coverage",
            objective="Run 100 seeds. " + "详细说明。" * 200,
            status="done",
        )
    )
    memory.backlog.add(
        BacklogItem(
            id="task-b", ts=2, title="Broken step", objective="obj b", status="failed"
        )
    )
    memory.backlog.add(
        BacklogItem(
            id="task-c", ts=3, title="Live step", objective="obj c", status="pending"
        )
    )
    rows = [
        {
            "type": "round.main.completed",
            "item_id": "task-a",
            "event_id": "ev-1",
            "summary": "覆盖率 91%，凭据 AKIAABCDEFGHIJKLMNOP 已写入配置。",
            "ts": 3.0,
        },
        _DIGEST_EVENT,
    ]
    (life / "events.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return life


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_accepts_the_frontend_shape() -> None:
    ref = parse_card_reference(
        _marker(step_id="task-a:2", step_title="步骤一", part=2, event_ids=["ev-1"])
    )
    assert ref is not None
    assert ref.task_id == "task-a"
    assert ref.task_title == "Coverage study"
    assert ref.step_title == "步骤一"
    assert ref.part == 2
    assert list(ref.event_ids) == ["ev-1"]


def test_parse_rejects_malformed_lines() -> None:
    bad_lines = [
        "[[Argus引用 not-json]]",
        "[[Argus引用 [1, 2]]]",
        _marker(task_id=5),  # task_id must be a string
        _marker(part=0),  # part must be a positive integer
        _marker(part=True),  # bool is not an accepted integer
        _marker(event_ids=[1]),  # event ids must be strings
        _marker(step_title=7),
        "plain text with [[Argus引用 inside",
    ]
    for line in bad_lines:
        assert parse_card_reference(line) is None, line


def test_malformed_reference_line_is_left_untouched(tmp_path: Path) -> None:
    life = _life(tmp_path)
    text = "请看这两行\n[[Argus引用 not-json]]\n" + _marker(task_id=5)
    expansion = expand_operator_references(text, life)
    assert expansion.matched is False
    assert expansion.text == text
    assert expansion.context_block == ""


# ---------------------------------------------------------------------------
# expansion
# ---------------------------------------------------------------------------


def test_valid_reference_replaces_marker_and_builds_block(tmp_path: Path) -> None:
    life = _life(tmp_path)
    digest_id = digest(_DIGEST_EVENT)
    text = (
        "请基于这个继续\n"
        + _marker(step_title="步骤一", event_ids=["ev-1", digest_id])
        + "\n收尾说明"
    )
    expansion = expand_operator_references(text, life)

    assert expansion.matched is True
    assert "[[Argus引用" not in expansion.text
    assert "（引用：《Compare coverage》· 步骤一）" in expansion.text
    assert expansion.text.startswith("请基于这个继续\n")
    assert expansion.text.endswith("\n收尾说明")
    # The block is separate from the visible text.
    assert "## 操作员引用的地图节点" not in expansion.text

    block = expansion.context_block
    assert block.startswith("## 操作员引用的地图节点")
    assert "task-a" in block
    assert "《Compare coverage》" in block
    assert "状态 done" in block
    assert "Run 100 seeds." in block
    assert "引用环节: 步骤一" in block
    # Both events resolve: the explicit event_id and the digest-derived id.
    assert "覆盖率 91%" in block
    assert "区间宽度保持稳定" in block
    # Secret redaction uses the same helper the map projection uses.
    assert "AKIAABCDEFGHIJKLMNOP" not in block
    assert "<REDACTED:aws-key>" in block
    # The objective is clipped, not dumped whole.
    objective_line = next(line for line in block.splitlines() if "目标" in line)
    assert len(objective_line) < 400


def test_missing_task_keeps_inline_line_without_block(tmp_path: Path) -> None:
    life = _life(tmp_path)
    expansion = expand_operator_references(_marker(task_id="task-zz"), life)
    assert expansion.matched is True
    assert "[[Argus引用" not in expansion.text
    # Falls back to the title carried by the reference itself.
    assert "（引用：《Coverage study》）" in expansion.text
    assert expansion.context_block == ""
    assert expansion.dep_task_ids == []


def test_missing_event_ids_do_not_break_the_entry(tmp_path: Path) -> None:
    life = _life(tmp_path)
    expansion = expand_operator_references(
        _marker(event_ids=["does-not-exist"]), life
    )
    assert "《Compare coverage》" in expansion.context_block
    assert "相关记录" not in expansion.context_block


def test_reference_cap_first_four(tmp_path: Path) -> None:
    life = tmp_path / "projects" / "s-cap"
    memory = LifeMemory.open(life)
    for index in range(6):
        memory.backlog.add(
            BacklogItem(
                id=f"task-{index}",
                ts=index + 1,
                title=f"Step {index}",
                objective=f"objective {index}",
                status="pending",
            )
        )
    text = "\n".join(
        _marker(task_id=f"task-{index}", task_title=f"Step {index}")
        for index in range(6)
    )
    expansion = expand_operator_references(text, life)
    assert "[[Argus引用" not in expansion.text
    assert expansion.text.count("（引用：") == 6
    assert expansion.context_block.count("- 任务 ") == 4
    assert len(expansion.dep_task_ids) == 4


def test_block_stays_bounded(tmp_path: Path) -> None:
    life = tmp_path / "projects" / "s-bound"
    memory = LifeMemory.open(life)
    rows = []
    for index in range(4):
        memory.backlog.add(
            BacklogItem(
                id=f"task-{index}",
                ts=index + 1,
                title="很长的标题" * 60,
                objective="长目标。" * 500,
                status="pending",
            )
        )
        for event_index in range(3):
            rows.append({
                "type": "agent.message",
                "item_id": f"task-{index}",
                "event_id": f"ev-{index}-{event_index}",
                "text": "长记录。" * 400,
                "ts": float(index * 3 + event_index),
            })
    (life / "events.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    text = "\n".join(
        _marker(
            task_id=f"task-{index}",
            event_ids=[f"ev-{index}-{j}" for j in range(3)],
        )
        for index in range(4)
    )
    expansion = expand_operator_references(text, life)
    assert expansion.context_block
    assert len(expansion.context_block) <= CONTEXT_BLOCK_MAX_CHARS


# ---------------------------------------------------------------------------
# dependency filter (F3)
# ---------------------------------------------------------------------------


def test_dep_filter_attaches_only_satisfiable_tasks(tmp_path: Path) -> None:
    life = _life(tmp_path)
    text = "\n".join([
        _marker(task_id="task-a"),
        _marker(task_id="task-b"),
        _marker(task_id="task-c"),
        _marker(task_id="task-zz"),
    ])
    expansion = expand_operator_references(text, life)
    # done and pending are attachable; failed and missing are not.
    assert expansion.dep_task_ids == ["task-a", "task-c"]
    assert expansion.skipped_dep_task_ids == ["task-b"]
    # The failed reference is explained in the block rather than silently
    # attached (a dep on a failed task would cascade-skip the new work).
    task_b_entry = expansion.context_block.split("- 任务 task-b", 1)[1]
    assert "不会等待" in task_b_entry.split("- 任务 ", 1)[0]


def test_duplicate_references_are_deduplicated(tmp_path: Path) -> None:
    life = _life(tmp_path)
    text = _marker(task_id="task-a") + "\n" + _marker(task_id="task-a")
    expansion = expand_operator_references(text, life)
    assert expansion.dep_task_ids == ["task-a"]
    assert expansion.context_block.count("- 任务 task-a") == 1

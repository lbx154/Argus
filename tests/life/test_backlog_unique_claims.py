"""One campaign item has at most one active claim.

A continuation row that keeps the origin's ``node_key`` (or names the
origin's id as its own node key) is the same piece of campaign work. While
the origin is running or parked on external work, a second worker must not
claim the continuation; once the origin settles the continuation is
claimable again. Based on the same-origin claim exclusion in #133.
"""

from __future__ import annotations

from pathlib import Path

from argus.life.memory import Backlog, BacklogItem


def _backlog(tmp_path: Path) -> Backlog:
    return Backlog(tmp_path / "backlog.jsonl")


def test_second_worker_cannot_claim_continuation_of_running_node(tmp_path: Path) -> None:
    backlog = _backlog(tmp_path)
    origin = backlog.add(BacklogItem.new(title="a", objective="o", node_key="train"))
    backlog.add(BacklogItem.new(title="a again", objective="o", node_key="train"))
    first = backlog.claim_next(owner="primary")
    assert first is not None and first.id == origin.id

    assert backlog.claim_next(owner="worker-2") is None
    assert backlog.next_pending() is None


def test_continuation_keyed_by_origin_id_is_excluded(tmp_path: Path) -> None:
    backlog = _backlog(tmp_path)
    origin = backlog.add(BacklogItem.new(title="a", objective="o"))
    backlog.claim_next(owner="primary")
    backlog.add(BacklogItem.new(title="b", objective="o", node_key=origin.id))

    assert backlog.claim_next(owner="worker-2") is None


def test_paused_external_origin_still_holds_the_claim(tmp_path: Path) -> None:
    backlog = _backlog(tmp_path)
    origin = backlog.add(BacklogItem.new(title="a", objective="o", node_key="eval"))
    backlog.claim_next(owner="primary")
    backlog.update(origin.id, status="paused_external_work")
    backlog.add(BacklogItem.new(title="a", objective="o", node_key="eval"))

    assert backlog.claim_next(owner="worker-2") is None


def test_unrelated_and_settled_items_remain_claimable(tmp_path: Path) -> None:
    backlog = _backlog(tmp_path)
    origin = backlog.add(BacklogItem.new(title="a", objective="o", node_key="train"))
    other = backlog.add(BacklogItem.new(title="b", objective="o", node_key="plot"))
    follow = backlog.add(BacklogItem.new(title="c", objective="o", node_key="train"))
    assert backlog.claim_next(owner="primary").id == origin.id

    second = backlog.claim_next(owner="primary")
    assert second is not None and second.id == other.id

    backlog.mark_done(origin.id)
    third = backlog.claim_next(owner="primary")
    assert third is not None and third.id == follow.id

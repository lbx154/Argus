"""Tests for the Signal-B recurring-failure advisory hook
(``LifeSupervisor._maybe_journal_recurring_failure_advisory`` →
``argus_skill.life.recurring_failure_advisor.RecurringFailureAdvisor``).

Behaviour contract:
- Per mission, each detected infra-failure signature is recorded once as a
  ``self_evolve.failure_observation`` journal row (idempotent per mission).
- Only when a signature recurs across ``min_recurrence`` DISTINCT missions
  is a single ``self_evolve.recurring_failure_advisory`` surfaced.
- The advisory is deduped (not re-emitted every tick) and NEVER auto-enqueues
  a mint-skill BacklogItem (judgment stays with reviewer/planner).
- mint-skill missions are skipped (anti-recursion).
"""
from __future__ import annotations

from pathlib import Path


def _make_stub_supervisor(tmp_path: Path):
    from argus_skill.life.memory import LifeMemory
    from argus_skill.life.supervisor import LifeSupervisor

    mem = LifeMemory.open(tmp_path)
    mem.init()

    sup = LifeSupervisor.__new__(LifeSupervisor)
    sup.memory = mem
    sup._inject_cumulative_cost = lambda entry: None
    sup._emit_status = lambda *a, **kw: None
    return sup, mem


def _item(title: str, tags: list[str] | None = None):
    from argus_skill.life.memory import BacklogItem
    return BacklogItem.new(title=title, objective="train", tags=list(tags or []))


_OOM = {"agent_messages": ["torch.OutOfMemoryError: CUDA out of memory."]}
_IMAGE_UNKNOWN_MODEL = {
    "agent_messages": [
        "argus-skill image-tool: API request failed (400) at "
        "/images/generations: {\"error\":{\"code\":\"unknown_model\","
        "\"message\":\"Unknown model: gpt-image-2\"}}"
    ]
}


def _obs(mem) -> list:
    return [e for e in mem.journal.all()
            if e.kind == "self_evolve.failure_observation"]


def _adv(mem) -> list:
    return [e for e in mem.journal.all()
            if e.kind == "self_evolve.recurring_failure_advisory"]


# ---------------------------------------------------------------------------
# Below threshold: observations recorded, NO advisory yet
# ---------------------------------------------------------------------------


def test_below_threshold_records_observation_no_advisory(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)

    s1 = sup._maybe_journal_recurring_failure_advisory(_item("m1"), _OOM)
    s2 = sup._maybe_journal_recurring_failure_advisory(_item("m2"), _OOM)

    assert s1 == [] and s2 == []
    assert len(_obs(mem)) == 2          # one observation per mission
    assert _adv(mem) == []              # threshold (3) not reached


# ---------------------------------------------------------------------------
# At threshold (3 distinct missions): advisory surfaced exactly once
# ---------------------------------------------------------------------------


def test_advisory_surfaced_at_threshold(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)

    sup._maybe_journal_recurring_failure_advisory(_item("m1"), _OOM)
    sup._maybe_journal_recurring_failure_advisory(_item("m2"), _OOM)
    surfaced = sup._maybe_journal_recurring_failure_advisory(_item("m3"), _OOM)

    assert "cuda_oom" in surfaced
    advisories = _adv(mem)
    assert len(advisories) == 1
    assert "cuda_oom" in advisories[0].title
    assert "sig:cuda_oom" in advisories[0].tags
    assert "×3" in advisories[0].title

    # CRITICAL: never auto-enqueue a mint-skill mission.
    assert not any("mint-skill" in (i.tags or []) for i in mem.backlog.all())


# ---------------------------------------------------------------------------
# Dedup: once advised, further recurrences don't re-surface
# ---------------------------------------------------------------------------


def test_advisory_deduped_after_first(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    for i in range(1, 4):
        sup._maybe_journal_recurring_failure_advisory(_item(f"m{i}"), _OOM)
    # 4th and 5th distinct missions with same failure → no new advisory
    s4 = sup._maybe_journal_recurring_failure_advisory(_item("m4"), _OOM)
    s5 = sup._maybe_journal_recurring_failure_advisory(_item("m5"), _OOM)
    assert s4 == [] and s5 == []
    assert len(_adv(mem)) == 1


# ---------------------------------------------------------------------------
# Idempotency: same mission re-ticked does not double-count
# ---------------------------------------------------------------------------


def test_same_mission_not_double_counted(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    m1 = _item("m1")
    # Re-tick the SAME mission 3 times — must NOT reach threshold (only 1
    # distinct mission), and must write only one observation.
    for _ in range(3):
        surfaced = sup._maybe_journal_recurring_failure_advisory(m1, _OOM)
        assert surfaced == []
    assert len(_obs(mem)) == 1
    assert _adv(mem) == []


# ---------------------------------------------------------------------------
# Distinct failure classes are counted independently
# ---------------------------------------------------------------------------


def test_distinct_signatures_counted_separately(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    assert_text = {"agent_messages": ["CUDA error: device-side assert triggered"]}
    # 3 OOM missions → OOM advisory; only 2 assert missions → no assert advisory
    for i in range(3):
        sup._maybe_journal_recurring_failure_advisory(_item(f"oom{i}"), _OOM)
    for i in range(2):
        sup._maybe_journal_recurring_failure_advisory(_item(f"as{i}"), assert_text)

    sigs = {t.split(":", 1)[1]
            for a in _adv(mem) for t in a.tags if t.startswith("sig:")}
    assert sigs == {"cuda_oom"}


def test_image_route_recurrence_surfaces_external_capability_advisory(
    tmp_path: Path,
) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)

    for i in range(2):
        sup._maybe_journal_recurring_failure_advisory(
            _item(f"image-route-{i}"), _IMAGE_UNKNOWN_MODEL
        )
    surfaced = sup._maybe_journal_recurring_failure_advisory(
        _item("image-route-2"), _IMAGE_UNKNOWN_MODEL
    )

    assert surfaced == ["image_generation_model_unavailable"]
    advisories = _adv(mem)
    assert len(advisories) == 1
    assert "external capability blocker" in advisories[0].title
    assert "operator/provider dependency" in advisories[0].summary
    assert "blind-retrying" in advisories[0].summary
    assert "external-capability" in advisories[0].tags
    assert "sig:image_generation_model_unavailable" in advisories[0].tags


# ---------------------------------------------------------------------------
# Anti-recursion: mint-skill missions are skipped
# ---------------------------------------------------------------------------


def test_mint_skill_mission_skipped(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    item = _item("mint-skill: cuda-oom-fix", tags=["mint-skill", "self-evolve"])
    surfaced = sup._maybe_journal_recurring_failure_advisory(item, _OOM)
    assert surfaced == []
    assert _obs(mem) == [] and _adv(mem) == []


# ---------------------------------------------------------------------------
# No infra failure → nothing happens
# ---------------------------------------------------------------------------


def test_noop_on_clean_mission(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    surfaced = sup._maybe_journal_recurring_failure_advisory(
        _item("m1"), {"agent_messages": ["All tests passed; reward=0.5"]}
    )
    assert surfaced == []
    assert _obs(mem) == [] and _adv(mem) == []


def test_noop_on_empty_result(tmp_path: Path) -> None:
    sup, mem = _make_stub_supervisor(tmp_path)
    assert sup._maybe_journal_recurring_failure_advisory(_item("m1"), None) == []

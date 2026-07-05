"""Self-modification safety: the `protected` skill field, the learning vertical,
and the SkillRouter guards that stop a self-modifying mission from removing or
blindly overwriting the skills governing it.

Covers operator requirements:
  1. seed/governing skills are protected (by `protected: true` OR a governing
     category — anti-cheat / guardrail / role-identity);
  2. a protected skill can never be archived/deleted, and a `create` cannot
     shadow one by reusing its name; ordinary skills a mission merely used stay
     retirable (retiring a wrong/harmful skill is the flywheel working);
  3. updating a protected skill is always refused at runtime, never gated
     through a diff/approval mechanism — strengthening one requires an
     explicit, out-of-band source-code change (no Manager approval gate
     exists for skill content either way — the Reviewer is sole authority);
  (4/5 — deferred effect and rollback — rely on existing mission-close ordering
   and the `.prev.md` snapshot, exercised elsewhere.)
"""
from __future__ import annotations

from argus_skill.skills.skill_router import SkillRouter
from argus_skill.skills.store import Skill, SkillStore
from argus_skill.skills.vertical_select import VERTICALS
from argus_skill.verticals._base import load_vertical

_VALID = """## Title
{name}
## Description
{desc}
## When to use
When teaching yourself from operator-supplied material.
## How to solve
Read the material, inventory the current library, then propose evidence-anchored
create/update/archive changes with a source span for every claim.
"""


def _content(name: str = "Gov Skill", desc: str = "A governing playbook for learning.") -> str:
    return _VALID.format(name=name, desc=desc)


def _store(tmp_path) -> SkillStore:
    d = tmp_path / "skills"
    d.mkdir()
    return SkillStore(d)


# --------------------------------------------------------------------------- #
# `protected` field round-trips
# --------------------------------------------------------------------------- #
def test_protected_field_roundtrips():
    sk = Skill(name="X", description="d", category="learning",
               content="## Title\nX\n## When to use\na\n## How to solve\nb",
               protected=True)
    assert Skill.parse(sk.render()).protected is True


def test_absent_protected_defaults_false():
    text = ("---\nname: X\ndescription: d\ncategory: c\nversion: 1\n"
            "created_at: t\n---\n\nbody")
    assert Skill.parse(text).protected is False


# --------------------------------------------------------------------------- #
# learning vertical is registered and loadable
# --------------------------------------------------------------------------- #
def test_learning_vertical_registered_and_loads():
    assert "learning" in VERTICALS
    mod = load_vertical("learning")
    assert mod.STAGE_ORDER == ["ingest", "study", "curate", "review"]
    assert mod.completion_gate == "none"
    assert set(mod.REVIEWER_CHECKLISTS) == set(mod.STAGE_ORDER)
    for role in ("planner", "engineer", "reviewer"):
        assert "LEARNING" in mod.role_banner(role).upper()


# --------------------------------------------------------------------------- #
# #1 protected skills cannot be archived/deleted
# --------------------------------------------------------------------------- #
def test_protected_skill_cannot_be_archived(tmp_path):
    store = _store(tmp_path)
    store.save(Skill(name="Gov Skill", description="d", category="learning",
                     content=_content(), protected=True))
    events: list[dict] = []
    router = SkillRouter(skill_store=store)
    counts = router.apply_ops([{"op": "archive", "name": "Gov Skill", "why": "x"}],
                              task="t", on_event=events.append)
    assert counts["archived"] == 0 and counts["rejected"] == 1
    assert any(s["name"] == "Gov Skill" for s in store.list_summaries())
    assert any(e.get("type") == "skill.op.refused" for e in events)


# --------------------------------------------------------------------------- #
# ordinary skills a mission used stay retirable (protected floor is the boundary)
# --------------------------------------------------------------------------- #
def test_ordinary_archive_still_works(tmp_path):
    """A plain, non-protected skill still archives — retiring a skill you used and
    found wrong/harmful is the flywheel working, not a self-governance breach."""
    store = _store(tmp_path)
    store.save(Skill(name="Plain Skill", description="d", category="c",
                     content=_content(name="Plain Skill")))
    router = SkillRouter(skill_store=store)
    counts = router.apply_ops([{"op": "archive", "name": "Plain Skill"}], task="t")
    assert counts["archived"] == 1
    assert not any(s["name"] == "Plain Skill" for s in store.list_summaries())


# --------------------------------------------------------------------------- #
# #3 protected skills are not updated by runtime skill candidates
# --------------------------------------------------------------------------- #
def test_protected_update_is_rejected(tmp_path):
    store = _store(tmp_path)
    store.save(Skill(name="Gov Skill", description="d", category="learning",
                     content=_content(), protected=True))
    router = SkillRouter(skill_store=store)
    counts = router.apply_ops(
        [{"op": "update", "name": "Gov Skill", "content": _content(desc="Revised, improved.")}],
        task="t")
    assert counts["updated"] == 0 and counts["rejected"] == 1


def test_ordinary_update_not_gated_by_diff_aware(tmp_path):
    """A plain, non-protected update is accepted as a provisional candidate."""
    store = _store(tmp_path)
    store.save(Skill(name="Plain Skill", description="d", category="c",
                     content=_content(name="Plain Skill")))
    router = SkillRouter(skill_store=store)
    counts = router.apply_ops(
        [{"op": "update", "name": "Plain Skill",
          "content": _content(name="Plain Skill", desc="Revised.")}],
        task="t")
    assert counts["updated"] == 1


# --------------------------------------------------------------------------- #
# category-level protected floor (governing skills need no explicit flag)
# --------------------------------------------------------------------------- #
def test_role_identity_category_is_protected_without_flag(tmp_path):
    store = _store(tmp_path)
    # a governing skill by CATEGORY, no explicit protected: true
    store.save(Skill(name="Role Skill", description="d", category="role-identity",
                     content=_content(name="Role Skill")))
    router = SkillRouter(skill_store=store)
    counts = router.apply_ops([{"op": "archive", "name": "Role Skill"}], task="t")
    assert counts["archived"] == 0 and counts["rejected"] == 1
    assert any(s["name"] == "Role Skill" for s in store.list_summaries())


def test_anti_cheat_category_update_is_refused(tmp_path):
    store = _store(tmp_path)
    store.save(Skill(name="Guard Skill", description="d", category="anti-cheat",
                     content=_content(name="Guard Skill")))
    counts = SkillRouter(skill_store=store).apply_ops(
        [{"op": "update", "name": "Guard Skill",
          "content": _content(name="Guard Skill", desc="edit")}], task="t")
    assert counts["updated"] == 0 and counts["rejected"] == 1


# --------------------------------------------------------------------------- #
# a create must not SHADOW a protected skill by reusing its name
# --------------------------------------------------------------------------- #
def test_create_colliding_with_protected_name_is_refused(tmp_path):
    store = _store(tmp_path)
    store.save(Skill(name="Gov Skill", description="d", category="learning",
                     content=_content(), protected=True))
    router = SkillRouter(skill_store=store)
    counts = router.apply_ops(
        [{"op": "create", "content": _content(name="Gov Skill", desc="a shadow playbook")}],
        task="t")
    assert counts["created"] == 0 and counts["rejected"] == 1
    # the protected original is untouched and still the only "Gov Skill"
    govs = [s for s in store.list_summaries() if s["name"] == "Gov Skill"]
    assert len(govs) == 1

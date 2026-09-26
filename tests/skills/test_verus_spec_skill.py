import re
from pathlib import Path

import pytest
import yaml

from argus.apps.cli import main
from argus.skills.builtins import (
    iter_builtin_skill_texts,
    iter_context_skill_assets,
    iter_vertical_skill_texts,
    seed_context_skills,
    vertical_skill_source_path,
)
from argus.skills.layered import LayeredSkillStore, shared_skill_scope_dir
from argus.skills.role_library import role_skill_libraries
from argus.skills.vertical_select import persist_vertical

SHARED_SKILL = "verus-spec-generation-and-repair.md"
ROLE_SKILLS = {
    role: f"{role}/formal-verification/{SHARED_SKILL}"
    for role in ("manager", "planner", "engineer", "reviewer")
}
CHINESE_REVIEWS = {
    SHARED_SKILL: "references/verus-spec-generation-and-repair.zh-CN.md",
    **{
        skill: f"{role}/formal-verification/references/verus-spec-generation-and-repair.zh-CN.md"
        for role, skill in ROLE_SKILLS.items()
    },
}
BASE_ROLE_GUIDES = {
    "manager": "manager/software-project-grounding.md",
    "planner": "planner/software-project-grounding.md",
    "engineer": "engineer/software-change-implementation.md",
    "reviewer": "reviewer/software-change-review.md",
}


@pytest.mark.parametrize("skill", CHINESE_REVIEWS)
def test_verus_skills_are_bundled_with_minimal_frontmatter(skill: str) -> None:
    texts = dict(iter_vertical_skill_texts("software"))
    text = texts[skill]
    front = yaml.safe_load(text.split("---", 2)[1])
    review = CHINESE_REVIEWS[skill]

    assert set(front) == {"name", "description"}
    assert front["name"]
    assert front["description"]
    assert text == (vertical_skill_source_path("software") / skill).read_text(
        encoding="utf-8"
    )
    assert review not in texts
    assert dict(iter_context_skill_assets("software"))[review] == (
        vertical_skill_source_path("software") / review
    ).read_text(encoding="utf-8")


def test_verus_shared_contract_keeps_both_proof_tracks_and_deliverables() -> None:
    texts = dict(iter_vertical_skill_texts("software"))
    body = " ".join(texts[SHARED_SKILL].split())
    for requirement in (
        "Analyze the module before generating API specifications",
        "define it before writing dependent API contracts",
        "bottom-up",
        "Use both source code and docstrings",
        "source implementation proof, verified by Verus",
        "spec-determin-tool",
        "`specs/`",
        "`specs/<submodule>/`",
        "`proofs/correctness/<submodule>/<api>.rs`",
        "`proofs/completeness/<submodule>/<api>.rs`",
        "`issues/`",
        "`issues/INDEX.md`",
        "Accept an API only after both dimensions pass",
        "all four artifact collections",
    ):
        assert requirement in body
    names = {
        yaml.safe_load(texts[skill].split("---", 2)[1])["name"]
        for skill in CHINESE_REVIEWS
    }
    assert len(names) == len(CHINESE_REVIEWS)


def test_shared_workflow_requires_reviewed_issues_without_false_bug_claims() -> None:
    text = dict(iter_vertical_skill_texts("software"))[SHARED_SKILL]
    body = " ".join(text.split())
    for requirement in (
        "Raise an issue before making an implementation change",
        "strict independent review",
        "executable rewrites or desugaring in source-derived proof copies",
        "`verus-limitation`",
        "`stdlib-implementation`",
        "`stdlib-docstring`",
        "Proof failure or `UNKNOWN` alone is not evidence",
        "`needs-review`",
        "explicit operator authorization",
        "`awaiting-filing`",
        "never describe a local draft as an already-filed upstream issue",
    ):
        assert requirement in body


@pytest.mark.parametrize("role", ROLE_SKILLS)
def test_each_role_carries_issue_responsibilities_in_both_languages(role: str) -> None:
    skill = ROLE_SKILLS[role]
    text = dict(iter_vertical_skill_texts("software"))[skill]
    review = dict(iter_context_skill_assets("software"))[CHINESE_REVIEWS[skill]]

    for body in (text, review):
        assert "`issues/`" in body
        assert "implementation" in body.lower()
        assert "review" in body.lower()
        assert "Verus" in body
        assert "docstring" in body


@pytest.mark.parametrize("vertical", ["research", "math", "learning"])
def test_verus_skills_are_software_scoped(tmp_path: Path, vertical: str) -> None:
    skills = set(CHINESE_REVIEWS)
    assert skills.isdisjoint(dict(iter_builtin_skill_texts()))
    assert skills.isdisjoint(dict(iter_vertical_skill_texts(vertical)))

    written = seed_context_skills(tmp_path, vertical)

    assert skills.isdisjoint(written)
    assert not any((tmp_path / skill).exists() for skill in skills)
    assert not any((tmp_path / review).exists() for review in CHINESE_REVIEWS.values())


@pytest.mark.parametrize("role", [*ROLE_SKILLS, "self"])
def test_each_role_gets_own_verus_skill_and_shared_contract(
    tmp_path: Path, role: str
) -> None:
    global_dir = tmp_path / "global"
    vertical_dir = shared_skill_scope_dir(global_dir, "software")
    assert vertical_dir is not None
    written = seed_context_skills(vertical_dir, "software")
    store = LayeredSkillStore(
        project_dir=tmp_path / "project",
        vertical_dir=vertical_dir,
        global_dir=global_dir,
    )

    libraries = role_skill_libraries(store, role=role)
    owner_role = "manager" if role == "self" else role
    role_path = (vertical_dir / owner_role).resolve()

    assert vertical_dir.resolve() in libraries.library_roots
    assert str(vertical_dir.resolve()) in libraries.block
    assert vertical_dir.resolve() in libraries.native_paths
    assert vertical_dir.resolve() in libraries.own_paths
    assert role_path in libraries.native_paths
    assert role_path in libraries.own_paths
    for skill in (SHARED_SKILL, ROLE_SKILLS[owner_role]):
        path = (vertical_dir / skill).resolve()
        body = path.read_text(encoding="utf-8")
        review = CHINESE_REVIEWS[skill]
        assert written[skill] is True
        assert store.list_paths().count(str(path)) == 1
        assert body == dict(iter_vertical_skill_texts("software"))[skill]
        assert body not in libraries.block
        assert written[review] is True
        assert (vertical_dir / review).read_text(encoding="utf-8") == dict(
            iter_context_skill_assets("software")
        )[review]


def test_seeded_role_routing_and_bilingual_navigation_links_resolve(tmp_path: Path) -> None:
    seed_context_skills(tmp_path, "software")
    for role, relative in BASE_ROLE_GUIDES.items():
        guide = tmp_path / relative
        assert "(formal-verification/verus-spec-generation-and-repair.md)" in (
            guide.read_text(encoding="utf-8")
        )
        assert (tmp_path / ROLE_SKILLS[role]).is_file()
    for relative in (*CHINESE_REVIEWS, *CHINESE_REVIEWS.values()):
        path = tmp_path / relative
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            resolved = (path.parent / target).resolve()
            assert resolved.is_relative_to(tmp_path.resolve())
            assert resolved.is_file(), (relative, target)


@pytest.mark.parametrize("skill", CHINESE_REVIEWS)
def test_verus_runtime_seeding_preserves_existing_local_edits(
    tmp_path: Path, skill: str
) -> None:
    seed_context_skills(tmp_path, "software")
    path = tmp_path / skill
    edited = path.read_text(encoding="utf-8") + "\nLocal project guidance.\n"
    path.write_text(edited, encoding="utf-8")

    written = seed_context_skills(tmp_path, "software")

    assert written[skill] is False
    assert path.read_text(encoding="utf-8") == edited


def test_cli_exports_all_verus_role_skills_only_for_decided_software_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "project" / "argus_builtin_skills"

    assert main(["--export-builtin-skills", str(target)]) == 0
    assert not any((target / skill).exists() for skill in CHINESE_REVIEWS)
    capsys.readouterr()

    persist_vertical(target.parent, "software")

    assert main(["--export-builtin-skills", str(target)]) == 0
    assert "vertical: software" in capsys.readouterr().out
    for skill, review in CHINESE_REVIEWS.items():
        assert (target / skill).read_text(encoding="utf-8") == dict(
            iter_vertical_skill_texts("software")
        )[skill]
        assert (target / review).read_text(encoding="utf-8") == dict(
            iter_context_skill_assets("software")
        )[review]

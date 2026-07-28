"""Keep current design docs aligned with the live orchestration contract.

This is intentionally narrow. Historical plans and the technical report may
describe older releases; the files below advertise current behavior and must
not regress to retired control paths.
"""

import re
from pathlib import Path

from argus_skill.core.knobs import KNOBS

ROOT = Path(__file__).resolve().parents[1]
CURRENT_DOCS = (
    "AGENTS.md",
    "README.md",
    "README.zh-CN.md",
    "DESIGN.md",
    "PRODUCT.md",
    "docs/DESIGN_AUTHORITY.md",
    "docs/ARCHITECTURE.md",
    "docs/STATE_MACHINE_AND_DEADLOCKS.md",
    "docs/ARGUS_RUNTIME_SETTINGS.md",
    "docs/protocols.md",
    "docs/daemon-command-protocol.md",
    "docs/event-catalog.md",
    "docs/run-exec-gateway.md",
    "docs/release-identity.md",
    "docs/orchestration-modules.md",
    "docs/observability.md",
    "docs/cost-control.md",
    "docs/IDEA_WIKI_DESIGN.md",
    "docs/LEARNING_VERTICAL_DESIGN.md",
    "docs/LIVE_EXPERIMENT_PROTOCOL.md",
    "docs/VALUE_VS_HONESTY.md",
    "docs/edit-principle/README.md",
    "docs/edit-principle/skills/04-harness-vs-agent-boundary.md",
    "docs/edit-principle/skills/05-read-design-history-first.md",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_current_docs_share_one_authority_map() -> None:
    for path in (
        "AGENTS.md",
        "README.md",
        "README.zh-CN.md",
        "DESIGN.md",
        "docs/ARCHITECTURE.md",
        "docs/STATE_MACHINE_AND_DEADLOCKS.md",
        "docs/ARGUS_RUNTIME_SETTINGS.md",
        "docs/event-catalog.md",
    ):
        assert "DESIGN_AUTHORITY.md" in _read(path), path


def test_current_docs_have_no_broken_local_markdown_links() -> None:
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for relative in CURRENT_DOCS:
        document = ROOT / relative
        for raw_target in link_re.findall(document.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (document.parent / target).resolve().exists(), (
                relative,
                target,
            )


def test_current_role_docs_do_not_advertise_retired_self_review() -> None:
    for path in (
        "README.md",
        "README.zh-CN.md",
        "AGENTS.md",
        "docs/ARCHITECTURE.md",
        "argus_skill/builtin_skills/engineer/argus-engineer-role.md",
        "argus_skill/roles/prompts/engineer.py",
    ):
        text = _read(path)
        assert "Allowed low-risk bounded work may instead" not in text, path
        assert "your explicit `review=skip` self-verification can complete" not in text, path
        assert "产出 artifact 并选择 `review=skip|required`" not in text, path
        assert "skipping independent review" not in text, path

    readme = _read("README.md")
    readme_zh = _read("README.zh-CN.md")
    assert "checkpoint.json" not in readme
    assert "checkpoint.json" not in readme_zh
    assert "mission-scoped control file" not in _read("AGENTS.md")
    assert "control file 写入 `wait_for=subagent`" not in _read("AGENTS.md")


def test_current_paper_gate_uses_venue_neutral_name() -> None:
    agents = _read("AGENTS.md")
    architecture = _read("docs/ARCHITECTURE.md")
    assert "full_emnlp_gate" not in agents
    assert 'completion_gate == "full_emnlp"' not in agents
    assert "full_paper_gate" in agents
    assert "full_paper" in architecture


def test_retired_dynamic_plan_knobs_are_not_operator_knobs() -> None:
    names = {knob.name for knob in KNOBS}
    assert "ARGUS_SKILL_DYNAMIC_PLAN_MODE" not in names
    assert "ARGUS_SKILL_DYNAMIC_PLAN_CONFIRM_ROUNDS" not in names

    settings = _read("docs/ARGUS_RUNTIME_SETTINGS.md")
    event_doc = _read("docs/event-catalog.md")
    assert "已退役配置" in settings
    assert "current code has no producer" in event_doc
    assert "There is no\n`off|shadow|active` Dynamic Plan mode" in event_doc


def test_current_architecture_paths_exist() -> None:
    for path in (
        "argus_skill/apps/cli/_parser.py",
        "argus_skill/apps/cli/_core.py",
        "argus_skill/webapi/manager_bridge.py",
        "argus_skill/daemon/life_worker.py",
        "argus_skill/apps/_runtime.py",
        "argus_skill/life/supervisor/_core.py",
        "argus_skill/apps/_runtime_execute.py",
        "argus_skill/loop.py",
        "argus_skill/engineer/runner.py",
        "argus_skill/reviewer/_core.py",
        "argus_skill/core/project_contract.py",
        "argus_skill/core/project_api.py",
        "argus_skill/core/run_gateway.py",
        "argus_skill/adapters/agent_cli_backend",
    ):
        assert (ROOT / path).exists(), path


def test_historical_north_star_review_is_not_presented_as_current() -> None:
    text = _read("argus_skill/Argus-North-Star架构审查与改进方案.md")
    assert "历史设计评审，不是当前运行规范" in text
    assert "docs/DESIGN_AUTHORITY.md" in text


def test_financing_materials_are_kept_out_of_the_main_worktree() -> None:
    forbidden = (
        "vc_materials",
        "docs/bp_figures",
        "docs/Argus_VC_Deck.pdf",
        "docs/Argus_商业计划书.md",
        "docs/Argus_商业计划书.pdf",
        "docs/Argus_项目介绍.md",
        "docs/Argus_一页纸概览.md",
        "docs/Argus_微信文案.md",
        "docs/argus_skill_auto_research_pitch.md",
        "docs/argus_skill_auto_research_pitch.pptx",
    )
    assert not [path for path in forbidden if (ROOT / path).exists()]

    ignore = _read(".gitignore")
    assert "/vc_materials/" in ignore
    assert "/docs/bp_figures/" in ignore
    assert "/docs/Argus_商业计划书.md" in ignore


def test_historical_snapshots_are_kept_out_of_main() -> None:
    forbidden = (
        "ARGUS_BUG_scope_stage_livelock.md",
        "LANES.md",
        "UX_BEFORE_AFTER.md",
        "docs/analysis",
        "docs/experiment",
        "docs/goals",
        "docs/incidents",
        "docs/reviews",
        "docs/showcase",
        "docs/superpowers/plans",
        "docs/test-health",
    )
    assert not [path for path in forbidden if (ROOT / path).exists()]

    specs = ROOT / "docs/superpowers/specs"
    assert [path.name for path in specs.glob("*")] == [
        "2026-07-15-ai-redraw-structural-report-figures-design.md"
    ]

    ignore = _read(".gitignore")
    assert "/docs/goals/" in ignore
    assert "/docs/reviews/" in ignore
    assert "/docs/superpowers/*" in ignore

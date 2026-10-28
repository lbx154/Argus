"""The method card, the pinned reference and the executable spec are policy.

One project shipped a method the Engineer had defined in passing, implemented
in a simplified form, covered with outcome-shaped tests, and had certified by a
read-only Reviewer from the Engineer's own account. These tests pin the
prompt fragments, the skill documents and the templates that make the method
statement, the reference clone and the spec suite the first work of
Experiment, and the Reviewer's first reading. The card is written once; its
volatile half (tests per component, reused code, hyperparameters, history) is
derived by the host, so no fragment may ask for hand-maintained tables.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import argus
from argus.verticals.research.library_preparation import STAGE_PLAYBOOK_PATHS
from argus.verticals.research.prompt_policy import render_role_prompt_fragment
from argus.verticals.research.stages import STAGE_CHECKLISTS, role_banner

SKILLS = Path(argus.__file__).parent / "verticals" / "research" / "skills"
ENGINEER = SKILLS / "engineer"


def _fragment(role: str, stage: str, operation: str = "execute", scope: str = "") -> str:
    return render_role_prompt_fragment(
        role=role, operation=operation, stage=stage, scope=scope, project_root=None,
    )


def test_reviewer_context_carries_the_derived_card_when_a_project_root_is_given(tmp_path: Path) -> None:
    from argus.verticals.research.prompt_policy import render_role_prompt_context

    def context(stage: str, root: Path | None) -> str:
        return render_role_prompt_context(
            role="reviewer", operation="evaluate", stage=stage, scope="", project_root=root,
        )

    # No card: nothing derived, nothing appended, no exception.
    assert "## Method card, derived by the host" not in context("experiment", tmp_path)
    assert "## Method card, derived by the host" not in context("experiment", None)

    (tmp_path / "METHOD.md").write_text(
        "# Card\n\nStatement.\n\n## Components\n\n| Component | The idea prescribes | Notes |\n"
        "|---|---|---|\n| gate | \"quoted\" | |\n",
        encoding="utf-8",
    )
    for stage in ("experiment", "review"):
        text = context(stage, tmp_path)
        assert "## Method card, derived by the host" in text, stage
        assert "no test carries its marker: gate" in text, stage
        assert "not a gate" in text, stage
    assert "## Method card, derived by the host" not in context("paper", tmp_path)
    # The static fragment stays static: the derived text lives in the per-round context only.
    assert "## Method card, derived by the host" not in _fragment("reviewer", "experiment", operation="evaluate")


def test_paper_fragment_binds_method_section_and_claims_to_the_card() -> None:
    text = _fragment("engineer", "paper")

    assert "Method section and claims follow METHOD.md" in text
    assert "every deviation is named there first" in text
    assert "reproduces the reused code and hyperparameters the host derives" in text


def test_stage_checklist_and_banners_name_the_card_as_the_named_exception() -> None:
    experiment = " ".join(item.statement for item in STAGE_CHECKLISTS["experiment"])

    assert "METHOD.md" in experiment
    assert "third_party/" in experiment
    assert "tests/spec" in experiment
    assert "knockout" in experiment
    assert "METHOD.md" in role_banner("planner")
    assert "work product, not a report" in role_banner("engineer")


def test_skill_documents_and_templates_exist() -> None:
    for name in ("method-card.md",):
        assert (ENGINEER / name).is_file(), name
    for absorbed in ("method_card_template.md", "executable-spec.md", "delta-on-reference.md"):
        assert not (ENGINEER / absorbed).exists(), absorbed
    template = (ENGINEER / "method-card.md").read_text(encoding="utf-8")
    for heading in ("## Components", "## Protocol", "## What would falsify the claim"):
        assert heading in template
    # Hand-written only: the volatile sections are derived, not templated.
    for heading in (
        "## Reference implementations",
        "## Reused code",
        "## Key hyperparameters",
        "## Change log",
    ):
        assert heading not in template, heading
    assert "| Component | The idea prescribes | Notes |" in template
    assert "Implemented in" not in template and "Proven by" not in template
    assert "derived by the host" in template
    skill = (ENGINEER / "method-card.md").read_text(encoding="utf-8")
    assert "Write it once" in skill
    assert "@pytest.mark.component" in skill
    assert "# why: ..." in skill
    assert "only when the method itself changes" in skill
    spec = skill
    assert "## Component markers" in spec
    assert ".argus/spec_components.json" in spec
    assert "Proven by" not in spec
    templates = ENGINEER / "spec_test_templates"
    for name in (
        "conftest.py",
        "test_differential.py",
        "test_knockouts.py",
        "test_claim_shape.py",
        "test_parity_reference.py",
    ):
        assert (templates / name).is_file(), name
    conftest = (templates / "conftest.py").read_text(encoding="utf-8")
    assert "def knockout(" in conftest
    assert "def scaling_slope(" in conftest
    assert "def pytest_configure(" in conftest and "addinivalue_line" in conftest
    assert "def pytest_collection_modifyitems(" in conftest
    for name in ("test_differential.py", "test_knockouts.py", "test_claim_shape.py", "test_parity_reference.py"):
        source = (templates / name).read_text(encoding="utf-8")
        assert source.count("@pytest.mark.component(") == source.count("\ndef test_"), name


def test_spec_templates_are_collectable_with_todo_tests_skipped(tmp_path: Path) -> None:
    target = tmp_path / "spec"
    shutil.copytree(ENGINEER / "spec_test_templates", target)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(target)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = proc.stdout.strip().splitlines()[-1]
    assert "failed" not in summary and "error" not in summary, summary
    assert "skipped" in summary or "passed" in summary, summary
    assert "PytestUnknownMarkWarning" not in proc.stdout + proc.stderr
    # The conftest hook recorded every marker under pytest's rootdir for the
    # host to join with the card.
    written = sorted(tmp_path.rglob("spec_components.json"))
    assert len(written) == 1 and written[0].parent.name == ".argus", written
    recorded = json.loads(written[0].read_text(encoding="utf-8"))
    items = recorded["items"]
    assert items, "no component markers recorded"
    assert all(set(entry) == {"component", "kind"} for entry in items.values())
    kinds = {nodeid.split("::")[0].rsplit("/", 1)[-1]: entry["kind"] for nodeid, entry in items.items()}
    assert kinds == {
        "test_differential.py": "differential",
        "test_knockouts.py": "knockout",
        "test_claim_shape.py": "claim",
        "test_parity_reference.py": "parity",
    }


def test_experiment_playbook_points_to_the_landscape_survey_for_infrastructure() -> None:
    text = (SKILLS / STAGE_PLAYBOOK_PATHS["experiment"]).read_text(encoding="utf-8")

    assert "`engineer/training-infrastructure.md`" in text
    assert "## Method card" in text
    assert "written once" in text and "touched again only when the method itself changes" in text
    assert "derives them from the code" in text
    assert "## Executable spec" in text
    assert "`engineer/method-card.md`" in text
    assert "executable-spec.md" not in text and "delta-on-reference.md" not in text
    assert "pinned revision into `third_party/`" in text
    paper = (SKILLS / STAGE_PLAYBOOK_PATHS["paper"]).read_text(encoding="utf-8")
    assert "follow the project-root `METHOD.md`" in paper


def test_contract_and_trace_skills_point_to_the_card() -> None:
    contract = (ENGINEER / "method-card.md").read_text(encoding="utf-8")
    trace = (SKILLS / "reviewer" / "claim-to-code-trace.md").read_text(encoding="utf-8")

    assert "METHOD.md" in contract
    assert "do not\ncreate a separate file" not in contract
    assert "Reading order" in trace
    assert "Raw verification evidence" in trace


def test_a_value_set_before_its_measurement_is_marked_provisional() -> None:
    """s-efe9deac (10-27): asked only to report a ratio, the team wrote
    `max_abs_logprob_error: 0.1 # why: BF16 mantissa spacing is 1/128` before any
    measurement; the first run measured 0.1546 while the operator's own measure
    was clean, and four reviews held the work to the invented 0.1 for 3 h ($46)
    until the operator revoked it. A value's reason names its source, and one
    set before its measurement says it is provisional."""
    from argus.verticals.research.prompt_policy import _method_card_engineer_block

    skill = (ENGINEER / "method-card.md").read_text(encoding="utf-8")
    assert "the official recipe, a measurement, or\n`provisional` until one is taken" in skill
    assert "written before its\nquantity was measured is provisional" in skill
    assert "or provisional until measured" in _method_card_engineer_block("experiment", "")

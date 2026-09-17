"""The method card, the pinned reference and the executable spec are policy.

One project shipped a method the Engineer had defined in passing, implemented
in a simplified form, covered with outcome-shaped tests, and had certified by a
read-only Reviewer from the Engineer's own account. These tests pin the
prompt fragments, the skill documents and the templates that make the method
statement, the reference clone and the host-run spec suite the first work of
Experiment, and the Reviewer's first reading. The card is written once; its
volatile half (status, reused code, hyperparameters, history) is derived by
the host, so no fragment may ask for hand-maintained tables.
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


def test_engineer_experiment_fragment_orders_card_reference_and_spec_first() -> None:
    text = _fragment("engineer", "experiment")

    assert "## Method card and executable spec" in text
    assert "METHOD.md" in text
    assert "Evidence considered" in text
    assert "third_party/" in text and "pinned revision" in text
    assert "tests/spec" in text
    assert "one function per equation" in text
    assert "one knockout per component" in text
    assert "claim-shaped tests" in text
    assert "host runs tests/spec after every round" in text
    assert "write METHOD.md once" in text
    assert "@pytest.mark.component" in text
    assert "'# why: ...'" in text
    assert "maintain no tables by hand" in text
    assert "edit METHOD.md only when the method changes" in text
    for stale in ("Reused code table", "Key hyperparameters table", "Proven-by", "Change log"):
        assert stale not in text, stale
    # The experiment-stage handoff is the card, the clone and the suite; the
    # "need not finish the whole paper" wording belongs to the paper stages.
    assert "Finish the method card, the reference clone and the spec suite" in text
    assert "you need not finish the whole paper" not in text
    assert "you need not finish the whole paper" in _fragment("engineer", "paper")


def test_engineer_block_reaches_scientific_revision_but_not_paper_or_idea() -> None:
    assert "## Method card and executable spec" in _fragment("engineer", "review")
    assert "## Method card and executable spec" not in _fragment(
        "engineer", "review", operation="narrative_edit"
    )
    for stage in ("idea", "paper"):
        assert "## Method card and executable spec" not in _fragment("engineer", stage)


def test_reviewer_experiment_fragment_reads_the_card_before_the_account() -> None:
    text = _fragment("reviewer", "experiment", operation="evaluate")

    assert "## Method card first" in text
    order = [
        text.index("METHOD.md"),
        text.index("Raw verification evidence"),
        text.index("tests/spec"),
        text.index("the Engineer's account last"),
    ]
    assert order == sorted(order)
    for verdict in ("MATCHES", "CONTRADICTS", "NOT_IMPLEMENTED", "INSUFFICIENT_EVIDENCE"):
        assert verdict in text
    assert "file:line" in text
    assert "unexplained-skip" in text
    assert "collected last round but missing now" in text
    assert "Do not ask for tools or re-run anything yourself" in text
    assert "derived method-card status" in text
    for status in ("proven", "contradicted", "partial", "untested", "unchecked"):
        assert status in text
    assert "hyperparameter change without a '# why'" in text
    assert "hyperparameter table" not in text
    assert "## Method card first" not in _fragment("reviewer", "paper", operation="evaluate")
    assert "## Method card first" not in _fragment("reviewer", "review", operation="cold_read")


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
        assert "- gate: untested" in text, stage
        assert "not a gate" in text, stage
    assert "## Method card, derived by the host" not in context("paper", tmp_path)
    # The static fragment stays static: the derived text lives in the per-round context only.
    assert "## Method card, derived by the host" not in _fragment("reviewer", "experiment", operation="evaluate")


def test_planner_experiment_fragment_keeps_definition_with_the_implementer() -> None:
    text = _fragment("planner", "experiment", operation="plan")

    assert "## Method card, reference and spec first" in text
    assert "same Engineer who implements" in text
    assert "never a separate 'define the method' task" in text
    assert "verbatim into acceptance" in text
    assert "re-issued after an infrastructure failure" in text
    assert "## Method card, reference and spec first" not in _fragment(
        "planner", "paper", operation="plan"
    )


def test_paper_fragment_binds_method_section_and_claims_to_the_card() -> None:
    text = _fragment("engineer", "paper")

    assert "Method section and claims follow METHOD.md" in text
    assert "every deviation is named there first" in text
    assert "reproduces the reused code and hyperparameters the host derives" in text


def test_prompt_blocks_stay_short() -> None:
    def words(role: str, header: str, operation: str) -> int:
        text = _fragment(role, "experiment", operation=operation)
        body = text.split(header, 1)[1].split("\n\n## ", 1)[0]
        return len(body.split())

    # Raised from 130/130/90 for the write-for-review anchors, the review
    # packet reading order and the implementation brief, then to 180/190/145
    # for the stand-in rule after trimming each block, then the reviewer to 220
    # for the dated results and random-input facts after trimming its reading
    # order, then to 260/340/200 for the claim-attainment statement, the
    # weakest-link reading and the stage rule on unmet clauses; trim before raising.
    assert words("engineer", "## Method card and executable spec", "execute") <= 260
    assert words("reviewer", "## Method card first", "evaluate") <= 340
    assert words("planner", "## Method card, reference and spec first", "plan") <= 200


def test_stage_checklist_and_banners_name_the_card_as_the_named_exception() -> None:
    experiment = " ".join(item.statement for item in STAGE_CHECKLISTS["experiment"])

    assert "METHOD.md" in experiment
    assert "third_party/" in experiment
    assert "tests/spec" in experiment
    assert "knockout" in experiment
    assert "METHOD.md" in role_banner("planner")
    assert "work product, not a report" in role_banner("engineer")


def test_team_worker_reads_the_card_and_leaves_editing_to_the_lead(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_TEAM_TASK_ID", "worker-1")
    banner = role_banner("engineer")

    assert "METHOD.md" in banner
    assert "only the lead edits it" in banner


def test_skill_documents_and_templates_exist() -> None:
    for name in (
        "method-card.md",
        "method_card_template.md",
        "executable-spec.md",
        "delta-on-reference.md",
    ):
        assert (ENGINEER / name).is_file(), name
    template = (ENGINEER / "method_card_template.md").read_text(encoding="utf-8")
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
    spec = (ENGINEER / "executable-spec.md").read_text(encoding="utf-8")
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
    # host to join with its run.
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

    assert "`engineer/infrastructure-landscape-survey.md`" in text
    assert "## Method card" in text
    assert "written once" in text and "touched again only when the method itself changes" in text
    assert "derives them from the code" in text
    assert "## Executable spec" in text
    assert "`engineer/method-card.md`" in text
    assert "`engineer/executable-spec.md`" in text
    assert "`engineer/delta-on-reference.md`" in text
    assert "pinned revision into `third_party/`" in text
    paper = (SKILLS / STAGE_PLAYBOOK_PATHS["paper"]).read_text(encoding="utf-8")
    assert "follow the project-root `METHOD.md`" in paper


def test_contract_and_trace_skills_point_to_the_card() -> None:
    contract = (ENGINEER / "hypothesis-implementation-contract.md").read_text(encoding="utf-8")
    trace = (SKILLS / "reviewer" / "claim-to-code-trace.md").read_text(encoding="utf-8")

    assert "METHOD.md" in contract
    assert "do not\ncreate a separate file" not in contract
    assert "Reading order" in trace
    assert "Raw verification evidence" in trace


def test_stand_ins_are_named_for_engineer_reviewer_planner_and_paper() -> None:
    from argus.verticals.research.prompt_policy import _paper_narrative_packaging_block

    engineer = _fragment("engineer", "experiment", operation="execute")
    reviewer = _fragment("reviewer", "experiment", operation="evaluate")
    planner = _fragment("planner", "experiment", operation="plan")

    assert "Stand-ins (mock model, fake environment" in engineer
    assert "never report a simulation as the benchmark" in engineer
    assert "listed under Run reality is NOT_IMPLEMENTED whatever the tests say" in reviewer
    assert "write .argus/claim_attainment.json" in engineer
    assert "A clause you cannot meet is a negative result to state, never to reword" in engineer
    assert "Start from Claim attainment" in reviewer
    assert "choose the one link most likely not to hold the claim and read only there" in reviewer
    assert "ask at most two questions, each answerable by an artifact" in reviewer
    assert "Read Claim attainment before deciding the stage" in planner
    assert "advancing to Paper on the clauses that happened to pass is claim drift" in planner
    assert "a one-minute run or random keys is not the protocol's evaluation" in reviewer
    assert "a benchmark run through a stand-in is not a result" in planner
    assert "a simulation is never described as a benchmark" in _paper_narrative_packaging_block()


def test_method_figure_goes_through_ppt_master_for_every_role() -> None:
    engineer = _fragment("engineer", "paper", operation="execute")
    reviewer = _fragment("reviewer", "paper", operation="evaluate")
    planner = _fragment("planner", "paper", operation="plan")

    assert "composed only through PPT Master (Method D; Method B fallback)" in engineer
    assert "Matplotlib patches, boxes and arrows are not a route for this figure" in engineer
    assert "figure_spec_scripts/pptx_export.py --pptx paper/figures/<name>.pptx" in engineer
    assert "open `paper/figures/<name>.png`, the exporter's render at manuscript width" in reviewer
    assert "written from it by `figure_spec_scripts/pptx_export.py" in planner
    assert "without a native PPT source of the same stem" in reviewer
    assert "## Method figure through PPT Master" in planner
    assert "A matplotlib or TeX-compiled diagram does not satisfy it" in planner
    assert "## Method figure through PPT Master" not in _fragment("planner", "experiment", operation="plan")

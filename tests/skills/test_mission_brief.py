"""The task brief the research vertical prepends to every Experiment mission.

The claim is fixed and quoted verbatim, each component points at its code,
the environment is described as it actually is, the task's own acceptance
check is restated, and the brief is derived at zero model cost. It never
raises and says nothing when there is nothing to say.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.verticals import research
from argus.verticals.research import mission_brief
from argus.verticals.research.mission_brief import (
    DEFINITION_OF_DONE,
    FIXED_CLAIM_SENTENCE,
    MAX_BRIEF_LINES,
    prepare_mission,
)

STATEMENT = (
    "The method adds a learned gate to the residual branch of attention and "
    "claims the same accuracy at half the attention FLOPs."
)

METHOD_MD = f"""# Gated residual attention

{STATEMENT}

## Components

| Component | The idea prescribes | Notes |
|---|---|---|
| residual gate | "the residual is scaled by a per-token sigmoid gate" | |
| sparse attention | "attention runs on the top-k keys per query" | |
| curriculum warmup | "k grows linearly over the first 10% of steps" | |

## Protocol

- Configs: `configs/base.yaml`

## What would falsify the claim

Accuracy at k=64 falls more than one point below dense attention.
"""

MODEL_PY = """from __future__ import annotations

import numpy as np
from rpc import attention


# @component residual gate
def gate(x):
    return 1.0 / (1.0 + np.exp(-x))


# @simplified sparse attention: fixed k instead of learned k
# @component sparse attention
def sparse(x, k=64):
    return attention(x)[:k]
"""

NOTES_MD = """# Research notes

## Idea

- Selected route: route-02, gated residual attention at half the FLOPs.
"""

LATEST_CHECKS = {
    "round_index": 2,
    "ran_at": 2.0,
    "exit_code": 1,
    "counts": {"PASSED": 1, "FAILED": 1},
    "outcomes": {
        "tests/spec/test_knockouts.py::test_gate": "PASSED",
        "tests/spec/test_knockouts.py::test_sparse": "FAILED",
    },
}

SPEC_COMPONENTS = {
    "generated_at": 1.0,
    "items": {
        "tests/spec/test_knockouts.py::test_gate": {"component": "residual gate", "kind": "knockout"},
        "tests/spec/test_knockouts.py::test_sparse": {"component": "sparse attention", "kind": "knockout"},
    },
}


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    )


def _init_repo(path: Path, message: str) -> None:
    _git(["init", "-q"], path)
    _git(["add", "-A"], path)
    _git(["commit", "-q", "-m", message], path)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "model.py").write_text(MODEL_PY, encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "base.yaml").write_text("model:\n  rank: 64  # why: route section 3\n", encoding="utf-8")
    (root / "METHOD.md").write_text(METHOD_MD, encoding="utf-8")
    (root / "RESEARCH_NOTES.md").write_text(NOTES_MD, encoding="utf-8")
    (root / "tests" / "spec").mkdir(parents=True)
    (root / "tests" / "spec" / "test_knockouts.py").write_text("import numpy\n", encoding="utf-8")
    (root / "data" / "wikitext").mkdir(parents=True)
    (root / "data" / "wikitext" / "train.txt").write_bytes(b"x" * 2048)
    clone = root / "third_party" / "x"
    (clone / "rpc").mkdir(parents=True)
    (clone / "rpc" / "__init__.py").write_text("def attention(x):\n    return x\n", encoding="utf-8")
    _init_repo(clone, "reference clone")
    argus_dir = root / ".argus" / "round-checks"
    argus_dir.mkdir(parents=True)
    (root / ".argus" / "spec_components.json").write_text(json.dumps(SPEC_COMPONENTS), encoding="utf-8")
    (argus_dir / "latest.json").write_text(json.dumps(LATEST_CHECKS), encoding="utf-8")
    _init_repo(root, "method card, spec and clone")
    (root / "src" / "scratch.py").write_text("x = 1\n", encoding="utf-8")  # untracked since
    return root


def _mission(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": "m-1",
        "title": "Implement sparse attention as the route states it",
        "objective": "Make the top-k attention path match the oracle.",
        "tags": ["experiment"],
        "acceptance_check": "tests/spec knockout and differential tests for sparse attention pass",
        "decision_rule": "if the differential test still fails after two rounds, report the gap, not a narrower claim",
        "non_goals": ["tuning the learning rate", "changing the datasets"],
        "manager_decision": {},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _sections(brief: str) -> list[str]:
    return [line for line in brief.splitlines() if line.startswith("### ")]


def test_brief_has_the_five_sections_and_quotes_the_claim(project: Path, tmp_path: Path) -> None:
    brief = prepare_mission(
        stage="experiment", project_root=project, state_root=tmp_path / "state", mission=_mission()
    )
    lines = brief.splitlines()

    assert lines[0] == "## Task brief"
    assert 0 < len(lines) <= MAX_BRIEF_LINES
    assert _sections(brief) == [
        "### Claim (fixed) — Gated residual attention",
        "### Components now",
        "### Environment now",
        "### This task",
        "### Since last time",
    ]
    assert any(STATEMENT in line for line in lines), "the METHOD.md statement is quoted verbatim"
    assert FIXED_CLAIM_SENTENCE in lines


def test_components_point_at_their_code_anchors(project: Path, tmp_path: Path) -> None:
    brief = prepare_mission(
        stage="experiment", project_root=project, state_root=tmp_path, mission=_mission()
    )
    lines = brief.splitlines()

    gate = next(line for line in lines if line.startswith("- residual gate:"))
    assert "proven" in gate and "src/model.py:7 (gate)" in gate
    sparse = next(line for line in lines if line.startswith("- sparse attention:"))
    assert "contradicted (simplified)" in sparse
    assert "src/model.py:13 (sparse)" in sparse
    assert "failing: tests/spec/test_knockouts.py::test_sparse" in sparse
    warmup = next(line for line in lines if line.startswith("- curriculum warmup:"))
    assert "untested" in warmup and "no `# @component` anchor" in warmup


def test_environment_describes_the_tree_as_it_is(project: Path, tmp_path: Path) -> None:
    brief = prepare_mission(
        stage="experiment", project_root=project, state_root=tmp_path, mission=_mission()
    )
    lines = brief.splitlines()

    assert any(line.startswith("- Interpreter: no .venv/bin/python") for line in lines)
    clones = next(line for line in lines if line.startswith("- third_party clones:"))
    assert "x @ " in clones and "no git revision" not in clones
    data = next(line for line in lines if line.startswith("- data/:"))
    assert "2.0 KB" in data and "wikitext/" in data
    checks = next(line for line in lines if line.startswith("- Last host-run tests/spec:"))
    assert "round 2, exit 1" in checks and "1 failed, 1 passed" in checks
    assert "contradicted: sparse attention" in checks
    packages = next(line for line in lines if line.startswith("- Packages:"))
    assert "numpy" in packages


def test_environment_reports_the_project_interpreter_when_present(project: Path, tmp_path: Path) -> None:
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text("#!/bin/sh\necho 'Python 3.99.1'\n", encoding="utf-8")
    python.chmod(0o755)
    site = project / ".venv" / "lib" / "python3.99" / "site-packages"
    (site / "numpy-2.0.0.dist-info").mkdir(parents=True)
    (site / "pyyaml-6.0.dist-info").mkdir()

    brief = prepare_mission(
        stage="experiment", project_root=project, state_root=tmp_path, mission=_mission()
    )
    lines = brief.splitlines()

    interpreter = next(line for line in lines if line.startswith("- Interpreter:"))
    assert interpreter.startswith("- Interpreter: .venv/bin/python (Python 3.99.1)")
    assert "run tests as `.venv/bin/python -m pytest tests/spec`" in interpreter
    packages = next(line for line in lines if line.startswith("- Packages:"))
    assert "2 distributions installed in .venv" in packages
    assert "imported packages present: numpy" in packages


def test_this_task_restates_the_mission_contract_verbatim(project: Path, tmp_path: Path) -> None:
    mission = _mission()
    brief = prepare_mission(
        stage="experiment", project_root=project, state_root=tmp_path, mission=mission
    )
    lines = brief.splitlines()

    assert f"- Acceptance check: {mission.acceptance_check}" in lines
    assert f"- Decision rule: {mission.decision_rule}" in lines
    assert "- Non-goals: tuning the learning rate; changing the datasets" in lines
    assert DEFINITION_OF_DONE in lines


def test_this_task_falls_back_to_the_mission_context_file(project: Path, tmp_path: Path) -> None:
    state = tmp_path / "state"
    contract_dir = state / "handoffs" / "m-1"
    contract_dir.mkdir(parents=True)
    (contract_dir / "mission.json").write_text(
        json.dumps({"acceptance_check": "the knockout changes the output", "non_goals": ["retuning"]}),
        encoding="utf-8",
    )
    mission = _mission(acceptance_check="", decision_rule="", non_goals=[])

    brief = prepare_mission(stage="experiment", project_root=project, state_root=state, mission=mission)
    lines = brief.splitlines()

    assert "- Acceptance check: the knockout changes the output" in lines
    assert "- Non-goals: retuning" in lines
    assert not any(line.startswith("- Decision rule:") for line in lines)


def test_since_last_time_counts_uncommitted_work_and_names_the_last_change(
    project: Path, tmp_path: Path
) -> None:
    brief = prepare_mission(
        stage="experiment", project_root=project, state_root=tmp_path, mission=_mission()
    )
    lines = brief.splitlines()

    uncommitted = next(line for line in lines if line.startswith("- Uncommitted:"))
    assert "untracked" in uncommitted and "src/scratch.py" in uncommitted
    last = next(line for line in lines if line.startswith("- Last change:"))
    assert "method card, spec and clone" in last and "METHOD.md" in last


def test_review_stage_gets_a_brief_only_for_a_repair_mission(project: Path, tmp_path: Path) -> None:
    repair = _mission(tags=["review", "repair"])
    prose = _mission(tags=["review", "manuscript"])

    assert prepare_mission(stage="review", project_root=project, state_root=tmp_path, mission=repair)
    assert prepare_mission(stage="review", project_root=project, state_root=tmp_path, mission=prose) == ""


def test_paper_stage_gets_no_brief(project: Path, tmp_path: Path) -> None:
    for stage in ("paper", "idea", "", None):
        assert prepare_mission(stage=stage, project_root=project, state_root=tmp_path, mission=_mission()) == ""


def test_project_without_a_card_falls_back_to_the_selected_idea(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    (root / ".argus").mkdir(parents=True)
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({"selected_idea": {"route_id": "route-02", "rationale": "Strongest direct case."}}),
        encoding="utf-8",
    )

    brief = prepare_mission(stage="experiment", project_root=root, state_root=tmp_path, mission=_mission())
    lines = brief.splitlines()

    assert lines[0] == "## Task brief"
    assert "### Claim (fixed)" in lines
    assert any("route-02" in line and "Strongest direct case." in line for line in lines)
    assert FIXED_CLAIM_SENTENCE in lines
    assert "### Components now" not in lines
    assert "### Since last time" not in lines


def test_project_with_nothing_yields_nothing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert prepare_mission(stage="experiment", project_root=empty, state_root=tmp_path, mission=_mission()) == ""
    assert prepare_mission(stage="experiment", project_root=tmp_path / "missing", state_root=tmp_path, mission=None) == ""


def test_broken_project_never_raises(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / ".argus" / "round-checks" / "latest.json").write_text("{not json", encoding="utf-8")
    (project / "src" / "bad.py").write_bytes(b"\xff\xfe# @component \xff\n")
    unreadable = project / "METHOD.md"
    unreadable.chmod(0o000)
    try:
        brief = prepare_mission(
            stage="experiment", project_root=project, state_root=tmp_path, mission=_mission()
        )
    finally:
        unreadable.chmod(0o644)
    assert isinstance(brief, str)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("derivation exploded")

    monkeypatch.setattr(mission_brief.method_card, "derive_method_card", explode)
    monkeypatch.setattr(mission_brief.method_card, "changed_files", explode)
    monkeypatch.setattr(mission_brief, "read_research_notes", explode)
    assert prepare_mission(stage="experiment", project_root=project, state_root=tmp_path, mission=object()) == ""


def test_brief_is_capped_and_recomputed_each_call(project: Path, tmp_path: Path) -> None:
    rows = "\n".join(f"| component {index} | \"rule {index}\" | |" for index in range(40))
    (project / "METHOD.md").write_text(
        METHOD_MD.replace('| curriculum warmup | "k grows linearly over the first 10% of steps" | |', rows),
        encoding="utf-8",
    )
    first = prepare_mission(stage="experiment", project_root=project, state_root=tmp_path, mission=_mission())
    assert len(first.splitlines()) <= MAX_BRIEF_LINES
    assert "### This task" in first, "the task section survives the cap"

    (project / "METHOD.md").write_text(METHOD_MD.replace(STATEMENT, "A different fixed claim."), encoding="utf-8")
    second = prepare_mission(stage="experiment", project_root=project, state_root=tmp_path, mission=_mission())
    assert "A different fixed claim." in second and STATEMENT not in second


def test_the_vertical_package_exports_the_hook_by_keyword() -> None:
    assert research.prepare_mission is prepare_mission
    assert "prepare_mission" in research.__all__


def test_project_without_a_card_quotes_the_selected_route_itself(tmp_path: Path) -> None:
    # Before METHOD.md exists the fixed text is the route the selector chose,
    # not the selector's account of why it won.
    root = tmp_path / "bare"
    route = root / ".argus" / "teams" / "t1" / "artifacts" / "routes" / "route-01.md"
    route.parent.mkdir(parents=True)
    route.write_text(
        "# Ideation Route 01: Gated residual attention\n\n"
        "## Executive Summary\n\n"
        "Random features approximate the kernel by Monte Carlo averaging.\n"
        "Gating the residual branch keeps the accuracy at half the attention FLOPs.\n\n"
        "## Prior work\n\nNobody gated it before.\n",
        encoding="utf-8",
    )
    (root / ".argus" / "PIPELINE_STATE.json").write_text(
        json.dumps({
            "selected_idea": {
                "route_id": "route-01",
                "rationale": "Every other route was rejected by its reviewer.",
                "route_artifact": ".argus/teams/t1/artifacts/routes/route-01.md",
            }
        }),
        encoding="utf-8",
    )

    brief = prepare_mission(stage="experiment", project_root=root, state_root=tmp_path, mission=_mission())
    lines = brief.splitlines()

    assert "### Claim (fixed) — Ideation Route 01: Gated residual attention" in lines
    claim = lines[lines.index("### Claim (fixed) — Ideation Route 01: Gated residual attention") + 1]
    assert claim.startswith("Random features approximate the kernel by Monte Carlo averaging. Gating")
    assert "route-01.md; the selected route, verbatim" in claim
    assert "Every other route was rejected" not in brief
    assert FIXED_CLAIM_SENTENCE in lines


def test_run_reality_names_stand_ins_and_the_results_footprint(project: Path, tmp_path: Path) -> None:
    (project / "src" / "eval.py").write_text(
        "def mock_worker_llm(p, t):\n    return '{}'\n\ndef run():\n    return mock_worker_llm('a', 'b')\n",
        encoding="utf-8",
    )
    (project / "results").mkdir()
    (project / "results" / "summary.json").write_text("{}", encoding="utf-8")

    brief = prepare_mission(stage="experiment", project_root=project, state_root=tmp_path, mission=_mission())
    lines = brief.splitlines()

    assert "### Run reality" in lines
    assert any(line.startswith("- src/eval.py:1 mock_worker_llm — used from src/eval.py:5") for line in lines)
    assert any(line.startswith("Results footprint: results/ 1 files") for line in lines)
    assert lines.index("### Run reality") < lines.index("### This task")


def test_environment_names_the_local_model_caches(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Engineers searched the whole disk for weights (one `find /` ran 66 minutes)
    # or mocked the model; the brief names the hub caches and what they hold.
    hub = tmp_path / "hub"
    for name in ("models--org--small-model", "models--org--other-model", "datasets--org--corpus"):
        (hub / name).mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))

    brief = prepare_mission(stage="experiment", project_root=project, state_root=tmp_path, mission=_mission())
    line = next(row for row in brief.splitlines() if row.startswith("- Model caches"))

    assert "2 models [org/other-model, org/small-model], 1 datasets" in line
    assert str(hub.resolve()) in line
    assert "use them before downloading or substituting" in line


def test_brief_carries_the_last_claim_attainment_statement(project: Path, tmp_path: Path) -> None:
    import json

    (project / "results").mkdir()
    (project / "results" / "r.json").write_text(json.dumps({"ours": {"acc": 0.35}}), encoding="utf-8")
    (project / ".argus").mkdir(exist_ok=True)
    (project / ".argus" / "claim_attainment.json").write_text(
        json.dumps({"clauses": [{"clause": "keep 96% of BF16", "obtained": "35%", "met": "no", "source": {"path": "results/r.json", "field": "ours.acc"}}]}),
        encoding="utf-8",
    )

    brief = prepare_mission(stage="experiment", project_root=project, state_root=tmp_path, mission=_mission())
    lines = brief.splitlines()

    assert "### Claim attainment (last statement)" in lines
    assert any(line.startswith('- [not met] keep 96% of BF16 — Engineer: "35%"; host reads results/r.json ours.acc = 0.35') for line in lines)
    assert lines.index("### Claim attainment (last statement)") < lines.index("### Environment now")


def test_the_brief_says_where_torch_is_instead_of_letting_the_engineer_search_the_disk(
    tmp_path: Path, monkeypatch
) -> None:
    from argus.verticals.research import mission_brief as mb

    monkeypatch.setattr(mb, "_TORCH_PROBE_CACHE", {})
    monkeypatch.setattr(
        mb,
        "_host_interpreters",
        lambda root: [("host runtime (read-only; never install into it)", Path("/opt/rt/bin/python")), ("python3 on PATH", Path("/usr/bin/python3"))],
    )

    def fake_run(cmd, **kwargs):
        has = cmd[0] == "/opt/rt/bin/python"
        return SimpleNamespace(returncode=0 if has else 1, stdout="2.9.0 True\n" if has else "", stderr="" if has else "ModuleNotFoundError")

    monkeypatch.setattr(mb.subprocess, "run", fake_run)
    line = mb._torch_line(tmp_path)
    assert line.startswith("- Torch on this host: /opt/rt/bin/python [host runtime (read-only; never install into it)]: torch 2.9.0 (CUDA yes); /usr/bin/python3 [python3 on PATH]: no torch.")
    assert "do not scan the disk for packages (`find /`)" in line
    # cached: a second call runs no probe
    monkeypatch.setattr(mb.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("probe ran twice")))
    assert mb._torch_line(tmp_path) == line

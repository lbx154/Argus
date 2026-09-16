"""The claims ledger: what the Experiment stage must prove before Paper.

A single-seed win, a comparison against a weak baseline, a difference inside
run-to-run noise, and a headline that only holds on synthetic data all read as
results in prose. The ledger makes each of them a validator failure.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from argus.verticals.research import experiment_claims as mod
from argus.verticals.research.stages import stage_completion_issues
from tests.research_evidence import arm as _arm
from tests.research_evidence import valid_ledger, write_ledger


def _mutate(root: Path, **claim_overrides) -> list[str]:
    record = valid_ledger(root)
    record["claims"][0].update(claim_overrides)
    return mod.validate_claims(record, project_root=root)


def test_a_repeated_separated_real_headline_validates(tmp_path: Path) -> None:
    assert mod.validate_claims(valid_ledger(tmp_path), project_root=tmp_path) == []


def test_missing_ledger_is_named_as_the_experiment_obligation(tmp_path: Path) -> None:
    issues = mod.experiment_claims_issues(tmp_path)
    assert len(issues) == 1
    assert "experiments/claims.json is missing" in issues[0]
    assert mod.experiment_claims_issues(tmp_path, require=False) == ()


def test_a_single_seed_comparison_is_not_evidence(tmp_path: Path) -> None:
    issues = _mutate(
        tmp_path,
        ours=_arm("ours", 0.50, n=1),
        strongest_baseline=_arm("nystrom", 0.60, n=1),
    )
    assert any("below 3 independent seeds" in issue for issue in issues)


def test_a_deterministic_comparison_must_say_why_one_run_settles_it(
    tmp_path: Path,
) -> None:
    issues = _mutate(tmp_path, variation="none")
    assert any("deterministic_reason" in issue for issue in issues)
    issues = _mutate(
        tmp_path,
        variation="none",
        deterministic_reason="closed-form solve; the split is fixed by the benchmark",
        ours={"name": "ours", "mean": 0.5, "std": None, "n": 1},
        strongest_baseline={"name": "nystrom", "mean": 0.6, "std": None, "n": 1},
    )
    assert issues == []


def test_ours_losing_cannot_be_called_supported(tmp_path: Path) -> None:
    issues = _mutate(tmp_path, ours=_arm("ours", 0.65))
    assert any("is not lower than strongest_baseline" in issue for issue in issues)
    assert any("no headline claim is supported" in issue for issue in issues)


def test_a_difference_inside_run_to_run_spread_is_inconclusive(tmp_path: Path) -> None:
    issues = _mutate(
        tmp_path,
        ours=_arm("ours", 0.595, std=0.02),
        strongest_baseline=_arm("nystrom", 0.60, std=0.02),
    )
    assert any("within run-to-run uncertainty" in issue for issue in issues)
    # Recording the same numbers honestly passes the claim itself, but the
    # ledger still lacks a supported headline.
    issues = _mutate(
        tmp_path,
        ours=_arm("ours", 0.595, std=0.02),
        strongest_baseline=_arm("nystrom", 0.60, std=0.02),
        status="inconclusive",
    )
    assert issues == [
        "no headline claim is supported: the evidence does not yet establish the "
        "thesis; improve the method or experiment, or re-derive the thesis from what "
        "the evidence does establish, before Paper"
    ]


def test_the_strongest_baseline_must_be_the_best_one_measured(tmp_path: Path) -> None:
    issues = _mutate(tmp_path, other_baselines=[_arm("greedy", 0.55)])
    assert any("is not the strongest measured baseline" in issue for issue in issues)
    assert any("`greedy` scores better" in issue for issue in issues)


def test_a_synthetic_only_headline_needs_a_justification(tmp_path: Path) -> None:
    issues = _mutate(tmp_path, synthetic=True)
    assert any("synthetic" in issue for issue in issues)
    record = valid_ledger(tmp_path)
    record["claims"][0]["synthetic"] = True
    record["headline_synthetic_justification"] = (
        "the mechanism claim is about a controlled factorial design with known ground truth"
    )
    assert mod.validate_claims(record, project_root=tmp_path) == []


def test_parity_claims_need_a_tolerance_and_must_stay_inside_it(tmp_path: Path) -> None:
    issues = _mutate(tmp_path, direction="parity")
    assert any("positive `tolerance`" in issue for issue in issues)
    issues = _mutate(tmp_path, direction="parity", tolerance=0.05)
    assert any("exceeds the parity tolerance" in issue for issue in issues)
    issues = _mutate(tmp_path, direction="parity", tolerance=0.2)
    assert issues == []


def test_evidence_files_must_exist_inside_the_project(tmp_path: Path) -> None:
    issues = _mutate(tmp_path, evidence=["experiments/missing.json"])
    assert any("does not exist" in issue for issue in issues)
    issues = _mutate(tmp_path, evidence=["../outside.json"])
    assert any("must stay inside the project" in issue for issue in issues)
    issues = _mutate(tmp_path, evidence=[])
    assert any("at least one project-relative raw result file" in issue for issue in issues)


def test_refuted_cannot_hide_a_win_and_ours_must_differ_from_baseline(
    tmp_path: Path,
) -> None:
    issues = _mutate(tmp_path, status="refuted")
    assert any("status is `refuted` but ours beats" in issue for issue in issues)
    issues = _mutate(tmp_path, strongest_baseline=_arm("ours", 0.6))
    assert any("different method from ours" in issue for issue in issues)


def test_placeholders_and_wrong_vocabulary_are_rejected(tmp_path: Path) -> None:
    template = mod.template()
    issues = mod.validate_claims(template, project_root=tmp_path)
    assert any("statement is empty or templated" in issue for issue in issues)
    assert any("metric is empty or templated" in issue for issue in issues)
    issues = _mutate(tmp_path, role="main", direction="better", status="won", variation="runs")
    assert any(".role must be one of" in issue for issue in issues)
    assert any(".direction must be one of" in issue for issue in issues)
    assert any(".status must be one of" in issue for issue in issues)
    assert any(".variation must be one of" in issue for issue in issues)
    assert mod.validate_claims([], project_root=tmp_path) == [
        "experiments/claims.json must be one JSON object"
    ]
    assert mod.validate_claims({"schema_version": 1, "claims": []}, project_root=tmp_path)


def test_duplicate_claim_ids_are_reported_once(tmp_path: Path) -> None:
    record = valid_ledger(tmp_path)
    record["claims"].append(copy.deepcopy(record["claims"][0]))
    issues = mod.validate_claims(record, project_root=tmp_path)
    assert issues == ["claim `headline-rmse` appears more than once"]


def test_experiment_stage_holds_until_ledger_and_notes_are_written(tmp_path: Path) -> None:
    issues = stage_completion_issues("experiment", tmp_path)
    assert any("experiments/claims.json is missing" in issue for issue in issues)
    assert any("RESEARCH_NOTES.md does not begin with" in issue for issue in issues)

    write_ledger(tmp_path, valid_ledger(tmp_path))
    (tmp_path / "RESEARCH_NOTES.md").write_text(
        "# Research notes — Idea stage\n\nselected route", encoding="utf-8"
    )
    issues = stage_completion_issues("experiment", tmp_path)
    assert len(issues) == 1 and "Experiment stage" in issues[0]

    (tmp_path / "RESEARCH_NOTES.md").write_text(
        "# Research notes — Experiment stage\n\nthesis: headline-rmse", encoding="utf-8"
    )
    assert stage_completion_issues("experiment", tmp_path) == ()


def test_a_broken_ledger_also_holds_paper_but_a_missing_one_does_not(tmp_path: Path) -> None:
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text("\\documentclass{article}", encoding="utf-8")
    (paper / "main.html").write_text(
        "<html><body>" + "x" * 300 + "</body></html>", encoding="utf-8"
    )
    assert stage_completion_issues("paper", tmp_path) == ()
    record = valid_ledger(tmp_path)
    record["claims"][0]["ours"] = _arm("ours", 0.9)
    write_ledger(tmp_path, record)
    issues = stage_completion_issues("paper", tmp_path)
    assert any("experiments/claims.json: claim `headline-rmse`" in issue for issue in issues)


def test_cli_reports_defects_and_prints_a_template(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert mod.main(["validate", "--project-root", str(tmp_path)]) == 1
    assert "experiments/claims.json is missing" in capsys.readouterr().err
    write_ledger(tmp_path, valid_ledger(tmp_path))
    assert mod.main(["validate", "--project-root", str(tmp_path)]) == 0
    assert "validates" in capsys.readouterr().out
    assert mod.main(["template"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["schema_version"] == mod.SCHEMA_VERSION


def _mutate_record(root: Path, **overrides) -> list[str]:
    record = valid_ledger(root)
    record.update(overrides)
    return mod.validate_claims(record, project_root=root)


def test_the_mechanism_map_must_point_at_code_that_exists(tmp_path: Path) -> None:
    issues = _mutate_record(tmp_path, mechanism=[])
    assert any("mechanism must list the selected idea's load-bearing components" in i for i in issues)
    issues = _mutate_record(
        tmp_path,
        mechanism=[{
            "component": "pivot selection",
            "idea_says": "ridge-regularized diagonal",
            "implemented_in": "src/missing.py:Nope.fit",
            "status": "faithful",
        }],
    )
    assert any("`src/missing.py` does not exist in the project" in i for i in issues)
    issues = _mutate_record(
        tmp_path,
        mechanism=[{
            "component": "pivot selection",
            "idea_says": "ridge-regularized diagonal",
            "implemented_in": "src/method.py:OtherThing.run",
            "status": "faithful",
        }],
    )
    assert any("does not define or mention it" in i for i in issues)


def test_simplified_components_need_a_note_and_a_named_variant(tmp_path: Path) -> None:
    simplified = {
        "component": "streaming GPU engine",
        "idea_says": "blocked streaming with tensor-core GEMMs",
        "implemented_in": "src/method.py:OurMethod.fit",
        "status": "simplified",
    }
    issues = _mutate_record(tmp_path, mechanism=[simplified])
    assert any(".note must say exactly how the implementation departs" in i for i in issues)
    assert any("variant must name the implemented variant" in i for i in issues)
    issues = _mutate_record(
        tmp_path,
        mechanism=[{**simplified, "note": "dense in-memory fit; no streaming"}],
        variant="Reg-RPCholesky (dense, non-streaming)",
    )
    assert issues == []


def test_a_missing_component_cannot_carry_a_supported_headline(tmp_path: Path) -> None:
    issues = _mutate_record(
        tmp_path,
        mechanism=[{
            "component": "adaptive lambda schedule",
            "idea_says": "lambda tracks the residual trace",
            "status": "missing",
        }],
    )
    assert issues == [
        "a headline claim is supported while a mechanism component is still `missing`: "
        "implement it, or mark it simplified and scope the claims to the implemented variant"
    ]


def test_reference_implementations_are_recorded_or_explicitly_waived(tmp_path: Path) -> None:
    issues = _mutate_record(tmp_path, reference_implementations=[])
    assert any("reference_implementations is empty" in i for i in issues)
    issues = _mutate_record(
        tmp_path,
        reference_implementations=[],
        no_reference_implementation_reason="the kernel is new; no public implementation exists",
    )
    assert issues == []
    issues = _mutate_record(
        tmp_path,
        reference_implementations=[{"name": "verl", "url": "https://example.org/verl", "used_for": "PPO trainer"}],
    )
    assert any("revision must pin the commit or tag" in i for i in issues)
    issues = _mutate_record(
        tmp_path,
        reference_implementations=[{"name": "verl", "local_path": "third_party/verl", "used_for": "PPO trainer"}],
    )
    assert any("`third_party/verl` does not exist in the project" in i for i in issues)
    (tmp_path / "third_party" / "verl").mkdir(parents=True)
    issues = _mutate_record(
        tmp_path,
        reference_implementations=[{"name": "verl", "local_path": "third_party/verl", "used_for": "PPO trainer"}],
    )
    assert issues == []

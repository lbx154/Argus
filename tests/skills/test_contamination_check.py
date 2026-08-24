from __future__ import annotations

import json
from pathlib import Path

from argus_skill.verticals.research.contamination_check import contamination_issues
from argus_skill.verticals.research.publication_scale import ASSESSMENT_PATH
from argus_skill.verticals.research.stages import stage_completion_issues


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _declare(root: Path, training: list[str], evaluation: str) -> None:
    path = root / ASSESSMENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "claim_bearing_evidence": [
                    {
                        "role": "primary",
                        "training_artifacts": training,
                        "evaluation_artifact": evaluation,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_disjoint_training_and_evaluation_identifiers_pass(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "data/train.jsonl", [{"prompt_id": f"train-{i}"} for i in range(3)]
    )
    (tmp_path / "data/eval.json").write_text(
        json.dumps([{"problem_id": f"eval-{i}"} for i in range(5)]),
        encoding="utf-8",
    )
    _declare(tmp_path, ["data/train.jsonl"], "data/eval.json")

    assert contamination_issues(tmp_path) == ()


def test_run03_shape_blocks_130_of_130_training_overlap(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "training/prompts.jsonl", [{"prompt_id": i} for i in range(130)]
    )
    _write_jsonl(
        tmp_path / "evaluation/math500.jsonl", [{"prompt_id": i} for i in range(500)]
    )
    _declare(
        tmp_path,
        ["training/prompts.jsonl"],
        "evaluation/math500.jsonl",
    )

    issues = contamination_issues(tmp_path)

    assert any("130 identifier(s) overlap" in issue for issue in issues)
    assert any("100.0% of training" in issue for issue in issues)
    assert any("26.0% of evaluation" in issue for issue in issues)
    assert any("training/prompts.jsonl" in issue for issue in issues)
    assert any("evaluation/math500.jsonl" in issue for issue in issues)
    assert any(
        issue.startswith("[contamination]")
        for issue in stage_completion_issues("analysis", tmp_path)
    )


def test_unreadable_declared_artifact_fails_closed(tmp_path: Path) -> None:
    training = tmp_path / "training/prompts.jsonl"
    training.parent.mkdir(parents=True)
    training.write_text("not-json\n", encoding="utf-8")
    _write_jsonl(tmp_path / "evaluation/prompts.jsonl", [{"id": 1}])
    _declare(
        tmp_path,
        ["training/prompts.jsonl"],
        "evaluation/prompts.jsonl",
    )

    issues = contamination_issues(tmp_path)

    assert any("unreadable declared training artifact" in issue for issue in issues)
    assert any("invalid JSONL" in issue for issue in issues)


def test_neither_declaration_without_training_signal_passes(tmp_path: Path) -> None:
    path = tmp_path / ASSESSMENT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "claim_bearing_evidence": [
                    {
                        "role": "primary",
                        "source_type": "measurement over frozen models",
                        "claim": "Token-frontier survival was measured.",
                        "artifacts": ["results/survival.json"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert contamination_issues(tmp_path) == ()


def test_exactly_one_declaration_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / ASSESSMENT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "claim_bearing_evidence": [
                    {"training_artifact": "data/train.jsonl"},
                    {"evaluation_artifact": "data/eval.jsonl"},
                ]
            }
        ),
        encoding="utf-8",
    )

    issues = contamination_issues(tmp_path)

    assert any("[0].evaluation_artifact" in issue for issue in issues)
    assert any("[1].training_artifacts" in issue for issue in issues)
    assert all("partial training/evaluation artifact pair" in issue for issue in issues)


def test_neither_declaration_with_training_signal_quotes_trigger(tmp_path: Path) -> None:
    path = tmp_path / ASSESSMENT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "claim_bearing_evidence": [
                    {
                        "artifacts": ["models/final.safetensors"],
                        "claim": "The adapted model improved accuracy.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    issues = contamination_issues(tmp_path)

    assert len(issues) == 1
    assert "because claim_bearing_evidence[0].artifacts[0] indicates training" in issues[0]
    assert '"models/final.safetensors"' in issues[0]


def test_arm_config_training_signal_quotes_trigger(tmp_path: Path) -> None:
    path = tmp_path / ASSESSMENT_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "claim_bearing_evidence": [
                    {"arm_configs": {"method": "configs/train_lora.toml"}}
                ]
            }
        ),
        encoding="utf-8",
    )

    issues = contamination_issues(tmp_path)

    assert len(issues) == 1
    assert "claim_bearing_evidence[0].arm_configs.method" in issues[0]
    assert '"configs/train_lora.toml"' in issues[0]

"""The volatile half of the method card is derived, not hand-maintained.

The Engineer writes METHOD.md once (statement, components with the route's
words, protocol, falsifier). Component status, reused code, hyperparameters
and history come from the code, the markers on tests/spec, the config files
and git, at zero model cost, and never raise.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from argus.verticals.research import method_card
from argus.verticals.research.method_card import derive_method_card, render_for_reviewer

METHOD_MD = """# Gated residual attention

The method adds a learned gate to the residual branch of attention so that
"the residual is scaled by a per-token sigmoid gate" (route section 2) and
claims the same accuracy at half the attention FLOPs.

## Components

| Component | The idea prescribes | Notes |
|---|---|---|
| residual gate | "the residual is scaled by a per-token sigmoid gate" | |
| sparse attention | "attention runs on the top-k keys per query" | simplified: fixed k instead of learned k |
| curriculum warmup | "k grows linearly over the first 10% of steps" | |

## Protocol

- Datasets: WikiText-103 (train/validation)
- Baselines: dense attention from the reference clone
- Configs: `configs/base.yaml`
- Deviations: none

## What would falsify the claim

Accuracy at k=64 falls more than one point below dense attention on the
validation split at the 125M scale.
"""

SPEC_COMPONENTS = {
    "generated_at": 1.0,
    "items": {
        "tests/spec/test_knockouts.py::test_knockout_gate": {
            "component": "residual gate",
            "kind": "knockout",
        },
        "tests/spec/test_differential.py::test_gate_matches_oracle": {
            "component": "Residual gate",
            "kind": "differential",
        },
        "tests/spec/test_knockouts.py::test_knockout_sparse": {
            "component": "sparse attention",
            "kind": "knockout",
        },
        "tests/spec/test_differential.py::test_sparse_matches_oracle": {
            "component": "sparse attention",
            "kind": "differential",
        },
        "tests/spec/test_claim_shape.py::test_extra_component": {
            "component": "positional bias",
            "kind": "claim",
        },
    },
}

LATEST_CHECKS = {
    "round_index": 3,
    "ran_at": 2.0,
    "exit_code": 1,
    "timed_out": False,
    "duration_s": 4.2,
    "counts": {"PASSED": 3, "FAILED": 1, "SKIPPED": 1},
    "outcomes": {
        "tests/spec/test_knockouts.py::test_knockout_gate": "PASSED",
        "tests/spec/test_differential.py::test_gate_matches_oracle": "PASSED",
        "tests/spec/test_knockouts.py::test_knockout_sparse": "PASSED",
        "tests/spec/test_differential.py::test_sparse_matches_oracle": "FAILED",
        "tests/spec/test_claim_shape.py::test_extra_component": "SKIPPED",
    },
    "skipped_reasons": {"tests/spec/test_claim_shape.py::test_extra_component": "TODO"},
    "removed_test_ids": [],
}

BASE_YAML = """model:
  # why: the route's section 3 fixes rank 64 for the 125M scale
  rank: 64
  dropout: 0.1  # why: reference recipe default
training:
  lr: 3.0e-4
  seeds: [1, 2, 3]
  schedule:
    warmup_steps: 1000
"""

MODEL_PY = """from __future__ import annotations

import json
import os

import numpy as np
from rpc import attention
import third_party.rpc.utils


def forward(x):
    return attention(np.asarray(x))
"""


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


def _init_repo(path: Path, remote: str | None = None) -> None:
    _git(["init", "-q"], path)
    if remote:
        _git(["remote", "add", "origin", remote], path)
    _git(["add", "-A"], path)
    _git(["commit", "-q", "-m", "initial method, config and clone"], path)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "model.py").write_text(MODEL_PY, encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "base.yaml").write_text(BASE_YAML, encoding="utf-8")
    (root / "METHOD.md").write_text(METHOD_MD, encoding="utf-8")
    (root / "tests" / "spec").mkdir(parents=True)
    (root / "tests" / "spec" / "test_knockouts.py").write_text(
        "import numpy\n", encoding="utf-8"
    )
    clone = root / "third_party" / "rpc"
    (clone / "rpc").mkdir(parents=True)
    (clone / "rpc" / "__init__.py").write_text("def attention(x):\n    return x\n", encoding="utf-8")
    (clone / "rpc" / "utils.py").write_text("", encoding="utf-8")
    _init_repo(clone, remote="https://example.invalid/rpc.git")
    argus_dir = root / ".argus" / "round-checks"
    argus_dir.mkdir(parents=True)
    (root / ".argus" / "spec_components.json").write_text(
        json.dumps(SPEC_COMPONENTS), encoding="utf-8"
    )
    (argus_dir / "latest.json").write_text(json.dumps(LATEST_CHECKS), encoding="utf-8")
    _init_repo(root)
    return root


def test_card_text_is_parsed_from_the_hand_written_sections(project: Path) -> None:
    card = derive_method_card(project)

    assert card["exists"] is True
    assert card["path"] == "METHOD.md"
    assert card["title"] == "Gated residual attention"
    assert card["statement"].startswith("The method adds a learned gate")
    assert card["statement"].endswith("half the attention FLOPs.")
    assert card["truncated"] is False
    assert card["markdown"] == METHOD_MD
    assert "WikiText-103" in card["protocol"] and "## " not in card["protocol"]
    assert card["falsifiers"].startswith("Accuracy at k=64")
    assert card["updated_at"]
    json.dumps(card)  # JSON-serialisable end to end


def test_component_status_joins_markers_with_the_host_run(project: Path) -> None:
    card = derive_method_card(project)
    by_name = {entry["component"]: entry for entry in card["components"]}

    assert list(by_name) == ["residual gate", "sparse attention", "curriculum warmup"]
    gate = by_name["residual gate"]
    assert gate["status"] == "proven"
    assert gate["prescribes"] == '"the residual is scaled by a per-token sigmoid gate"'
    assert {test["kind"] for test in gate["tests"]} == {"knockout", "differential"}
    assert all(test["outcome"] == "PASSED" for test in gate["tests"])
    sparse = by_name["sparse attention"]
    assert sparse["status"] == "contradicted"
    assert sparse["notes"].startswith("simplified:")
    assert by_name["curriculum warmup"]["status"] == "untested"
    assert by_name["curriculum warmup"]["tests"] == []
    assert [entry["component"] for entry in card["unlisted_components"]] == ["positional bias"]
    assert card["checks"] == {
        "round_index": 3,
        "ran_at": 2.0,
        "exit_code": 1,
        "counts": {"PASSED": 3, "FAILED": 1, "SKIPPED": 1},
    }


def test_markers_without_a_host_run_are_unchecked(project: Path) -> None:
    (project / ".argus" / "round-checks" / "latest.json").unlink()
    card = derive_method_card(project)
    by_name = {entry["component"]: entry for entry in card["components"]}

    assert by_name["residual gate"]["status"] == "unchecked"
    assert by_name["curriculum warmup"]["status"] == "untested"
    assert card["checks"] is None


def test_host_join_in_latest_json_is_preferred_when_present(project: Path) -> None:
    latest = dict(LATEST_CHECKS)
    latest["components"] = {
        "residual gate": {
            "tests": [{"id": "tests/spec/test_knockouts.py::test_knockout_gate", "kind": "knockout", "outcome": "PASSED"}],
            "status": "partial",
        }
    }
    (project / ".argus" / "round-checks" / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
    card = derive_method_card(project)
    by_name = {entry["component"]: entry for entry in card["components"]}

    assert by_name["residual gate"]["status"] == "partial"
    # Markers the host has not run yet stay visible without an outcome.
    ids = {test["id"] for test in by_name["residual gate"]["tests"]}
    assert "tests/spec/test_differential.py::test_gate_matches_oracle" in ids


def test_old_five_column_component_table_still_parses(project: Path) -> None:
    (project / "METHOD.md").write_text(
        "# Old shape\n\nStatement.\n\n## Components\n\n"
        "| Component | The idea prescribes | Implemented in | Status | Proven by |\n"
        "|---|---|---|---|---|\n"
        "| residual gate | \"gate\" | `src/model.py:forward` | as specified | none yet |\n",
        encoding="utf-8",
    )
    card = derive_method_card(project)

    assert [entry["component"] for entry in card["components"]] == ["residual gate"]
    assert card["components"][0]["notes"] == ""
    assert card["components"][0]["status"] == "proven"


def test_reused_code_maps_imports_to_clones_and_packages(project: Path) -> None:
    card = derive_method_card(project)
    by_name = {(entry["kind"], entry["name"]): entry for entry in card["reused_code"]}

    clone = by_name[("third_party", "rpc")]
    assert clone["revision_or_version"], "the clone's short revision is read from git"
    assert clone["remote"] == "https://example.invalid/rpc.git"
    assert clone["modules"] == ["rpc", "third_party.rpc.utils"]
    assert clone["imported_from"] == ["src/model.py"]
    numpy_entry = by_name[("package", "numpy")]
    assert numpy_entry["revision_or_version"]  # installed in the test environment
    assert numpy_entry["modules"] == ["numpy"]
    # Standard-library imports and the project's own modules are not "reused code".
    assert ("package", "json") not in by_name and ("package", "os") not in by_name
    assert card["reused_code"][0]["kind"] == "third_party"


def test_hyperparameters_carry_why_and_diff_against_the_previous_snapshot(project: Path) -> None:
    first = derive_method_card(project)["hyperparameters"]
    by_key = {entry["key"]: entry for entry in first}

    assert by_key["model.rank"]["value"] == "64"
    assert by_key["model.rank"]["file"] == "configs/base.yaml"
    assert by_key["model.rank"]["why"] == "the route's section 3 fixes rank 64 for the 125M scale"
    assert by_key["model.dropout"]["why"] == "reference recipe default"
    assert by_key["training.lr"]["why"] == ""
    assert by_key["training.seeds"]["value"] == "[1, 2, 3]"
    assert by_key["training.schedule.warmup_steps"]["value"] == "1000"
    assert all(entry["changed"] is False and entry["previous"] is None for entry in first)
    snapshot = project / ".argus" / "method-card" / "hyperparameters.json"
    assert snapshot.is_file()

    (project / "configs" / "base.yaml").write_text(
        BASE_YAML.replace("rank: 64", "rank: 128"), encoding="utf-8"
    )
    second = {entry["key"]: entry for entry in derive_method_card(project)["hyperparameters"]}
    assert second["model.rank"]["changed"] is True
    assert second["model.rank"]["previous"] == "64"
    assert second["model.rank"]["value"] == "128"
    assert second["training.lr"]["changed"] is False

    # A third derivation without a config edit keeps reporting the same diff.
    third = {entry["key"]: entry for entry in derive_method_card(project)["hyperparameters"]}
    assert third["model.rank"]["changed"] is True and third["model.rank"]["previous"] == "64"


def test_change_log_comes_from_git(project: Path) -> None:
    log = derive_method_card(project)["change_log"]

    assert log, "the fixture repository has one commit touching METHOD.md"
    assert log[0]["summary"] == "initial method, config and clone"
    assert len(log[0]["when"]) == 10
    assert "METHOD.md" in log[0]["files"]
    assert "configs/base.yaml" in log[0]["files"]


def test_reviewer_rendering_is_short_and_names_the_repairs(project: Path) -> None:
    (project / "configs" / "base.yaml").write_text(
        BASE_YAML.replace("rank: 64", "rank: 128"), encoding="utf-8"
    )
    derive_method_card(project)
    (project / "configs" / "base.yaml").write_text(
        BASE_YAML.replace("rank: 64", "rank: 256"), encoding="utf-8"
    )
    text = render_for_reviewer(project)
    lines = text.splitlines()

    assert 0 < len(lines) <= 40
    assert lines[0].startswith("## Method card")
    assert "not a gate" in lines[0]
    assert any(line.startswith("- residual gate: proven") for line in lines)
    assert any(line.startswith("- sparse attention: contradicted") for line in lines)
    assert any("no test carries its marker: curriculum warmup" in line for line in lines)
    assert any("Markers without a card row: positional bias" in line for line in lines)
    assert any(line.startswith("- third_party/rpc @ ") and "example.invalid" in line for line in lines)
    assert any(line.startswith("- numpy @ ") for line in lines)
    assert any("configs/base.yaml:model.rank 128 -> 256" in line for line in lines)
    assert any("round 3, exit 1" in line for line in lines)


def test_project_without_a_card_is_empty_and_silent(tmp_path: Path) -> None:
    card = derive_method_card(tmp_path)

    assert card["exists"] is False
    assert card["components"] == [] and card["reused_code"] == []
    assert card["hyperparameters"] == [] and card["change_log"] == []
    assert card["checks"] is None
    assert render_for_reviewer(tmp_path) == ""
    assert render_for_reviewer(tmp_path / "missing") == ""


def test_derivation_survives_broken_inputs(project: Path) -> None:
    (project / ".argus" / "spec_components.json").write_text("{not json", encoding="utf-8")
    (project / ".argus" / "round-checks" / "latest.json").write_text("[]", encoding="utf-8")
    (project / "configs" / "broken.yaml").write_text("a: [unclosed", encoding="utf-8")
    (project / "src" / "bad.py").write_text("def (:\n", encoding="utf-8")

    card = derive_method_card(project)

    assert card["exists"] is True
    assert all(entry["status"] == "untested" for entry in card["components"])
    assert card["checks"] is None
    assert {entry["file"] for entry in card["hyperparameters"]} == {"configs/base.yaml"}
    assert any(entry["name"] == "rpc" for entry in card["reused_code"])


def test_yaml_why_comments_follow_nesting() -> None:
    whys = method_card.yaml_why_comments(
        "a:\n  b:\n    # why: above\n    c: 1\n  d: 2 # WHY: inline\n"
        "# why: orphan comment then blank line\n\ne: 3\nf: 4\n"
    )

    assert whys == {"a.b.c": "above", "a.d": "inline"}

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

    assert 0 < len(lines) <= method_card.REVIEWER_MAX_LINES
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


ANCHORED_MODEL_PY = """from __future__ import annotations

import numpy as np
from rpc import attention


# @component residual gate
def gate(x):
    return 1.0 / (1.0 + np.exp(-x))


# @simplified sparse attention: fixed k instead of learned k
# @component Sparse Attention
def sparse(x, k=64):
    # @reuses rpc.attention the dense baseline path
    return attention(x)[:k]


# @component rotary cache
class RotaryCache:
    pass
"""


@pytest.fixture
def anchored(project: Path) -> Path:
    (project / "src" / "model.py").write_text(ANCHORED_MODEL_PY, encoding="utf-8")
    return project


def test_code_anchors_attach_to_the_card_rows(anchored: Path) -> None:
    card = derive_method_card(anchored)
    by_name = {entry["component"]: entry for entry in card["components"]}

    gate = by_name["residual gate"]
    assert gate["status"] == "proven"
    assert gate["simplified"] == []
    assert len(gate["anchors"]) == 1
    anchor = gate["anchors"][0]
    assert (anchor["file"], anchor["line"], anchor["symbol"]) == ("src/model.py", 7, "gate")
    assert anchor["excerpt"].startswith("# @component residual gate\ndef gate(x):")
    assert len(anchor["excerpt"].splitlines()) <= method_card.ANCHOR_EXCERPT_LINES
    assert method_card.anchor_location(gate) == "src/model.py:7 (gate)"

    # The join is case- and whitespace-insensitive, like the marker join.
    sparse = by_name["sparse attention"]
    assert sparse["status"] == "contradicted (simplified)"
    assert sparse["anchors"][0]["symbol"] == "sparse"
    assert sparse["simplified"] == [
        {"reason": "fixed k instead of learned k", "file": "src/model.py", "line": 12}
    ]
    assert by_name["curriculum warmup"]["anchors"] == []
    assert by_name["curriculum warmup"]["status"] == "untested"

    assert card["reuse_anchors"] == [
        {"file": "src/model.py", "line": 15, "what": "rpc.attention", "note": "the dense baseline path"}
    ]
    json.dumps(card)


def test_anchors_for_components_the_card_does_not_name_are_listed(anchored: Path) -> None:
    card = derive_method_card(anchored)
    unlisted = {entry["component"]: entry for entry in card["unlisted_components"]}

    assert set(unlisted) == {"positional bias", "rotary cache"}
    rotary = unlisted["rotary cache"]
    assert rotary["status"] == "untested" and rotary["tests"] == []
    assert rotary["anchors"][0]["symbol"] == "RotaryCache"
    assert unlisted["positional bias"]["anchors"] == []


def test_review_packet_shows_anchor_excerpt_changes_and_gaps(anchored: Path) -> None:
    (anchored / "src" / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    text = render_for_reviewer(anchored)
    lines = text.splitlines()

    assert 0 < len(lines) <= method_card.REVIEWER_MAX_LINES
    gate_index = next(i for i, line in enumerate(lines) if line.startswith("- residual gate: proven"))
    assert "src/model.py:7 (gate)" in lines[gate_index]
    # The excerpt sits under its own status line, indented, at most 20 lines.
    excerpt = []
    for line in lines[gate_index + 1 :]:
        if not line.startswith("    "):
            break
        excerpt.append(line)
    assert excerpt and excerpt[0].strip() == "# @component residual gate"
    assert excerpt[1].strip() == "def gate(x):"
    assert len(excerpt) <= method_card.REVIEWER_EXCERPT_LINES + 1
    sparse = next(line for line in lines if line.startswith("- sparse attention: contradicted (simplified)"))
    assert "src/model.py:13 (sparse)" in sparse
    assert any(line.strip().startswith("simplified: fixed k instead of learned k") for line in lines)
    assert any(line.strip().startswith("failing: tests/spec/test_differential.py::test_sparse_matches_oracle") for line in lines)
    assert any("no `# @component` anchor in the code: curriculum warmup" in line for line in lines)
    assert any("Anchors without a card row: rotary cache (src/model.py:19 (RotaryCache))" in line for line in lines)
    assert any("Markers without a card row: positional bias" in line for line in lines)
    changed = next(i for i, line in enumerate(lines) if line.startswith("Files changed this round"))
    assert any("src/new_module.py" in line for line in lines[changed : changed + method_card.REVIEWER_CHANGED_FILES + 1])
    assert any("src/model.py" in line for line in lines[changed : changed + method_card.REVIEWER_CHANGED_FILES + 1])


def test_review_packet_holds_its_cap_under_many_anchored_components(project: Path) -> None:
    rows = "\n".join(f'| component {i} | "rule {i}" | |' for i in range(60))
    (project / "METHOD.md").write_text(
        METHOD_MD.replace('| curriculum warmup | "k grows linearly over the first 10% of steps" | |', rows),
        encoding="utf-8",
    )
    body = "\n".join(
        f"# @component component {i}\ndef f{i}(x):\n" + "\n".join(f"    y{j} = x + {j}" for j in range(40)) + "\n    return x\n\n"
        for i in range(60)
    )
    (project / "src" / "wide.py").write_text(body, encoding="utf-8")
    for index in range(40):
        (project / "src" / f"extra_{index}.py").write_text(f"v{index} = {index}\n", encoding="utf-8")

    lines = render_for_reviewer(project).splitlines()

    assert 0 < len(lines) <= method_card.REVIEWER_MAX_LINES
    assert lines[0].startswith("## Method card")
    assert any(line.startswith("Files changed this round") for line in lines)
    assert sum(1 for line in lines if line.startswith("- ") and "src/" in line and " " in line[2:4]) <= method_card.REVIEWER_CHANGED_FILES + 1


def test_stand_ins_in_project_code_are_listed_with_call_sites_and_footprint(tmp_path: Path) -> None:
    # One project evaluated "350 benchmark tasks" through `mock_worker_llm` in
    # four minutes. The card lists such names outside tests/ with their call
    # sites and how long results/ took to write; the Reviewer judges.
    root = tmp_path / "proj"
    (root / "src" / "pbis").mkdir(parents=True)
    (root / "METHOD.md").write_text("# PBIS\n\nPBIS isolates untrusted content.\n\n## Components\n\n| Component | The idea prescribes | Notes |\n|---|---|---|\n| runtime | \"dual privilege\" | |\n", encoding="utf-8")
    (root / "src" / "pbis" / "evaluator.py").write_text(
        "def mock_worker_llm(prompt, text):\n    return '{}'\n\n"
        "class SyntheticDOMGenerator:\n    pass\n\n"
        "def run():\n    rt = Runtime(worker_llm_fn=mock_worker_llm)\n    gen = SyntheticDOMGenerator()\n"
        "    # fallback mock parser for spec tests\n    return rt, gen\n",
        encoding="utf-8",
    )
    (root / "tests" / "spec").mkdir(parents=True)
    (root / "tests" / "spec" / "test_x.py").write_text("def mock_model():\n    pass\n", encoding="utf-8")
    (root / "results").mkdir()
    (root / "results" / "summary.json").write_text("{}", encoding="utf-8")

    card = derive_method_card(root)

    names = {e["name"] for e in card["stand_ins"] if e["kind"] == "definition"}
    assert names == {"mock_worker_llm", "SyntheticDOMGenerator"}
    mock = next(e for e in card["stand_ins"] if e["name"] == "mock_worker_llm")
    assert mock["file"] == "src/pbis/evaluator.py" and mock["line"] == 1
    assert "src/pbis/evaluator.py:8" in mock["used_from"]
    assert any(e["kind"] == "note" and "fallback mock parser" in e["name"] for e in card["stand_ins"])
    assert card["results_footprint"][0]["dir"] == "results" and card["results_footprint"][0]["files"] == 1

    packet = render_for_reviewer(root)
    assert "Run reality (derived from the tree, not from any account):" in packet
    assert "src/pbis/evaluator.py:1 mock_worker_llm — used from src/pbis/evaluator.py:8" in packet
    assert "Results footprint: results/ 1 files" in packet


def test_projects_without_stand_ins_show_no_run_reality_lines(project: Path) -> None:
    card = derive_method_card(project)
    assert [e for e in card["stand_ins"] if e["kind"] == "definition"] == []
    assert "Possible stand-ins" not in render_for_reviewer(project)


def test_random_input_measurements_and_fast_results_are_dated(tmp_path: Path) -> None:
    # One "real model benchmark" loaded two 7B checkpoints for perplexity and
    # then computed "retrieval recall at 32k" on torch.randn keys; the summary
    # listed both models above the numbers and was written 57 s after the
    # script's last edit. Two facts from the tree say so without a gate.
    import os

    root = tmp_path / "proj"
    (root / "src" / "eval").mkdir(parents=True)
    (root / "METHOD.md").write_text("# RotKV\n\nRotKV keeps retrieval under INT2.\n", encoding="utf-8")
    script = root / "src" / "eval" / "run_model_eval.py"
    script.write_text(
        "import torch\n\n"
        "def evaluate_retrieval_at_scale(\n"
        "    seq_lens=(8192,),\n"
        "    num_trials=10,\n"
        "):\n"
        '    """Evaluates multi-depth retrieval recall across contexts."""\n'
        "    K = torch.randn(1, 8, 8192, 128)\n"
        "    idx = torch.randint(0, 8192, (5,))  # needle positions\n"
        "    return K, idx\n\n"
        "def run_model_eval():\n"
        '    """Runs perplexity on real models and synthetic long-context retrieval."""\n'
        "    return evaluate_retrieval_at_scale()\n\n"
        "def init_weights(module):\n"
        '    """Xavier initialisation for the projection."""\n'
        "    module.weight.data = torch.randn(4, 4)\n\n"
        "def load_wikitext(path):\n"
        '    """Reads the test split from the arrow file."""\n'
        "    return open(path, 'rb').read()\n",
        encoding="utf-8",
    )
    (root / "results").mkdir()
    summary = root / "results" / "model_eval_summary.json"
    summary.write_text("{}", encoding="utf-8")
    code_time = 1_800_000_000.0
    os.utime(script, (code_time, code_time))
    os.utime(summary, (code_time + 57, code_time + 57))

    card = derive_method_card(root)

    by_name = {e["name"]: e for e in card["stand_ins"] if e["kind"] == "definition"}
    assert set(by_name) == {"evaluate_retrieval_at_scale", "run_model_eval"}
    assert by_name["evaluate_retrieval_at_scale"]["note"] == (
        "builds inputs with torch.randn, torch.randint; docstring: 'Evaluates multi-depth retrieval recall across contexts.'"
    )
    assert "src/eval/run_model_eval.py:14" in by_name["evaluate_retrieval_at_scale"]["used_from"]
    assert by_name["run_model_eval"]["note"].startswith("docstring: 'Runs perplexity on real models and synthetic")
    footprint = card["results_footprint"][0]
    assert footprint["newest"] == [
        {"file": "results/model_eval_summary.json", "seconds_after_code": 57, "code_file": "src/eval/run_model_eval.py"}
    ]

    packet = render_for_reviewer(root)
    assert "src/eval/run_model_eval.py:3 evaluate_retrieval_at_scale — used from src/eval/run_model_eval.py:14; builds inputs with torch.randn" in packet
    assert "- results/model_eval_summary.json was written 57 s after the last edit to src/eval/run_model_eval.py" in packet
    assert "init_weights" not in packet and "load_wikitext" not in packet


def test_claim_attainment_shows_the_engineers_words_beside_the_hosts_values(tmp_path: Path) -> None:
    # One project reported "+22 pp over KIVI" for a claim that asked for 96% of
    # BF16 and got 35%. Per clause, the Engineer says met or not and points at
    # the number; the host reads the pointed field and prints it beside the words.
    import json

    root = tmp_path / "proj"
    (root / "results").mkdir(parents=True)
    (root / ".argus").mkdir()
    (root / "METHOD.md").write_text("# RotKV\n\nRotKV keeps 96% of BF16 retrieval.\n", encoding="utf-8")
    (root / "results" / "ruler.json").write_text(
        json.dumps({"bf16": {"acc": 1.0}, "rotkv": {"acc": 0.3458}, "runs": [{"acc": 0.9}]}), encoding="utf-8"
    )
    (root / ".argus" / "claim_attainment.json").write_text(
        json.dumps(
            {
                "clauses": [
                    {"clause": "preserve >96% of BF16 retrieval", "obtained": "34.6% of BF16", "met": "no",
                     "source": {"path": "results/ruler.json", "field": "rotkv.acc"}},
                    {"clause": "first run above 0.8", "obtained": "0.9", "met": "yes",
                     "source": {"path": "results/ruler.json", "field": "runs.0.acc"}},
                    {"clause": "beat KIVI by 25 pp", "obtained": "+22 pp", "met": "partial",
                     "source": {"path": "results/ruler.json", "field": "comparison.gain"}},
                    {"clause": "1.8x throughput", "obtained": "not measured", "met": "untested",
                     "source": {"path": "results/profile.json", "field": "speedup"}},
                    {"clause": "reads elsewhere", "obtained": "x", "met": "yes",
                     "source": {"path": "/etc/hostname", "field": ""}},
                ]
            }
        ),
        encoding="utf-8",
    )

    card = derive_method_card(root)
    entries = card["claim_attainment"]

    assert [e["met"] for e in entries] == ["not met", "met", "partial", "untested", "met"]
    assert [e["pointer"] for e in entries] == ["ok", "ok", "no field", "no file", "outside workspace"]
    assert entries[0]["value"] == "0.3458" and entries[1]["value"] == "0.9"

    packet = render_for_reviewer(root)
    assert "Claim attainment (the Engineer's statement per clause of the claim" in packet
    assert '- [not met] preserve >96% of BF16 retrieval — Engineer: "34.6% of BF16"; host reads results/ruler.json rotkv.acc = 0.3458' in packet
    assert "- [partial] beat KIVI by 25 pp — Engineer: \"+22 pp\"; pointer no field: results/ruler.json comparison.gain" in packet
    assert "pointer outside workspace: /etc/hostname" in packet
    assert packet.index("Claim attainment") < packet.index("Run reality") if "Run reality" in packet else True


def test_results_without_an_attainment_statement_are_named_as_such(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "results").mkdir(parents=True)
    (root / "METHOD.md").write_text("# M\n\nM does X.\n", encoding="utf-8")
    (root / "results" / "summary.json").write_text("{}", encoding="utf-8")

    packet = render_for_reviewer(root)

    assert "No claim attainment statement (.argus/claim_attainment.json): results exist but the Engineer has not said which clauses of the claim they meet." in packet


def test_result_tables_read_per_method_numbers_and_name_metrics_that_separate_nothing(tmp_path: Path) -> None:
    from argus.verticals.research import method_card as mc

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    results = tmp_path / "results"
    results.mkdir()
    (results / "pilot.json").write_text(json.dumps({
        "metadata": {"context_length": 8192, "seeds": [1, 2, 3], "budget_ratio": 0.2},
        "full_cache": {"ppl": 2.4505, "niah_accuracy": 1.0, "eval_time_seconds": 34.1},
        "snapkv": {"ppl": 2.4634, "niah_accuracy": 1.0, "eval_time_seconds": 37.0},
        "ours": {"ppl": 2.5088, "niah_accuracy": 1.0, "eval_time_seconds": 37.5},
    }), encoding="utf-8")
    tables = mc.result_tables(tmp_path)
    assert len(tables) == 1 and tables[0]["methods"] == ["full_cache", "snapkv", "ours"]
    assert tables[0]["metrics"]["ppl"] == {"full_cache": 2.4505, "snapkv": 2.4634, "ours": 2.5088}
    assert tables[0]["no_separation"] == ["niah_accuracy"]
    lines = mc.render_result_tables({"result_tables": tables})
    assert lines[0] == "Numbers in the newest result files, per method:"
    assert "ppl: full_cache 2.4505, snapkv 2.4634, ours 2.5088" in lines[1]
    assert "niah_accuracy: all 3 methods 1 (separates nothing)" in lines[1]
    (results / "notes.json").write_text(json.dumps({"seed": 1, "note": "x"}), encoding="utf-8")
    assert all(t["file"] != "results/notes.json" for t in mc.result_tables(tmp_path))

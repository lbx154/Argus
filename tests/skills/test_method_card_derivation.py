"""The volatile half of the method card is derived, not hand-maintained.

The Engineer writes METHOD.md once (statement, components with the route's
words, protocol, falsifier). The tests behind each component, reused code,
hyperparameters and history come from the code, the markers on tests/spec, the config files
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
    (root / ".argus").mkdir()
    (root / ".argus" / "spec_components.json").write_text(
        json.dumps(SPEC_COMPONENTS), encoding="utf-8"
    )
    _init_repo(root)
    return root


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
    assert {test["kind"] for test in card["components"][0]["tests"]} == {"knockout", "differential"}


def test_reviewer_rendering_is_short_and_names_the_repairs(project: Path) -> None:
    text = render_for_reviewer(project)
    lines = text.splitlines()

    assert 0 < len(lines) <= method_card.REVIEWER_MAX_LINES
    assert lines[0].startswith("## Method card")
    assert "not a gate" in lines[0]
    assert any(line.startswith("- residual gate (1 differential, 1 knockout)") for line in lines)
    assert any(line.startswith("- sparse attention (1 differential, 1 knockout)") for line in lines)
    assert any("no test carries its marker: curriculum warmup" in line for line in lines)
    assert any("Markers without a card row: positional bias" in line for line in lines)
    assert any(line.startswith("- third_party/rpc @ ") and "example.invalid" in line for line in lines)
    assert any(line.startswith("- numpy @ ") for line in lines)


def test_derivation_survives_broken_inputs(project: Path) -> None:
    (project / ".argus" / "spec_components.json").write_text("{not json", encoding="utf-8")
    (project / "configs" / "broken.yaml").write_text("a: [unclosed", encoding="utf-8")
    (project / "src" / "bad.py").write_text("def (:\n", encoding="utf-8")

    card = derive_method_card(project)

    assert card["exists"] is True
    assert all(entry["tests"] == [] for entry in card["components"])
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


def test_review_packet_names_tests_and_changes_and_leaves_the_code_to_the_card(anchored: Path) -> None:
    # The card's rows name each entry point; comments in the code add nothing
    # to the packet, so a component the code leaves unmarked is no gap.
    (anchored / "src" / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    text = render_for_reviewer(anchored)
    lines = text.splitlines()

    assert 0 < len(lines) <= method_card.REVIEWER_MAX_LINES
    assert "- residual gate (1 differential, 1 knockout)" in lines
    assert "- sparse attention (1 differential, 1 knockout)" in lines
    assert "- curriculum warmup" in lines
    assert "Markers without a card row: positional bias" in lines
    assert "anchor" not in text.lower() and "@component" not in text and "def gate" not in text
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


def test_statement_clauses_are_held_against_the_cards_falsification_conditions(tmp_path: Path) -> None:
    from argus.verticals.research import method_card as mc

    card = dict(mc._empty_card()) if hasattr(mc, "_empty_card") else {}
    card["falsifiers"] = (
        "The thesis is falsified if:\n"
        "1. Under a 20% KV cache budget on the 8B model at 32K context, the method fails to outperform "
        "VATP and RoCo by at least 3.0 points on average LongBench score, or\n"
        "2. NIAH retrieval accuracy drops below 90% in the middle context depths (30%--70%), or\n"
        "3. The runtime overhead reduces decoding throughput by more than 5% compared to attention-only eviction.\n"
    )
    assert len(mc.falsifier_items(card)) == 3
    assert mc.falsifier_items(card)[0].startswith("Under a 20% KV cache budget") and not mc.falsifier_items(card)[0].endswith("or")
    card["claim_attainment"] = [
        {"clause": "reducing KV cache memory by 75%--80%", "obtained": "80% reduction at rho 0.2", "met": "met",
         "source": "results/bench.json", "field": "runs.ours_budget_20.budget_ratio", "pointer": "ok", "value": 0.2, "age_minutes": 3},
        {"clause": "NIAH retrieval accuracy stays above 90% in the middle depths (30%--70%)", "obtained": "100%", "met": "met",
         "source": "results/bench.json", "field": "runs.ours_budget_20.niah_mid_depth_accuracy", "pointer": "ok", "value": 1.0, "age_minutes": 3},
    ]
    against = mc.attainment_against_falsifiers(card)
    assert against["restated"] == [0]
    assert len(against["unaddressed"]) == 2 and against["unaddressed"][0].startswith("Under a 20% KV cache budget")
    lines = mc.render_claim_attainment(card)
    assert "a configuration value, not a measurement" in lines[1]
    assert "[restated: this clause is not among METHOD.md's falsification conditions" in lines[1]
    assert "[restated" not in lines[2]
    assert any(line.startswith("Falsification conditions in METHOD.md the statement does not address") for line in lines)
    assert sum(1 for line in lines if line.startswith("- Under a 20%") or line.startswith("- The runtime overhead")) == 2


def test_without_a_falsification_list_the_statement_is_shown_as_is(tmp_path: Path) -> None:
    from argus.verticals.research import method_card as mc

    card = {"falsifiers": "", "claim_attainment": [
        {"clause": "x", "obtained": "", "met": "met", "source": "r.json", "field": "acc", "pointer": "ok", "value": 1, "age_minutes": None}
    ]}
    lines = mc.render_claim_attainment(card)
    assert len(lines) == 2 and "[restated" not in lines[1]

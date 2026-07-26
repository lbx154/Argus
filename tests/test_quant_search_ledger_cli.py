"""The search ledger's tamper-evidence, reachable from the command line.

The ledger is the raw record of every backtest trial — survivors and discards
alike — and it is hash-chained precisely so that "I logged every trial" can be
audited. `verify_chain` has existed and been tested since the ledger landed, but
nothing outside the test suite could reach it: no CLI, no production caller. The
reviewer was pointed at the JSONL file and left to eyeball hashes, which means
the tamper-evidence was never actually checked.

These tests exercise the CLI as a subprocess — the way an agent invokes it — and
pin the three cases the audit exists for: a forged ledger, an edited row, and a
deleted row. The last test pins what the tool must NOT do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from argus_skill.verticals.quant.search_ledger import SearchLedger

_MODULE = "argus_skill.verticals.quant.search_ledger"


def _verify(path: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-m", _MODULE, "verify", "--path", str(path), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.stderr == "", proc.stderr
    return proc.returncode, json.loads(proc.stdout)


def _real_ledger(tmp_path: Path, trials: int = 3) -> Path:
    path = tmp_path / "SEARCH_LEDGER.jsonl"
    ledger = SearchLedger(path)
    for i in range(trials):
        ledger.append({"factor": f"f{i}", "ic": round(0.01 * i, 4)})
    return path


def test_authentic_ledger_verifies(tmp_path: Path) -> None:
    code, report = _verify(_real_ledger(tmp_path))

    assert code == 0
    assert report["chain_valid"] is True
    assert report["rows"] == 3


def test_fabricated_ledger_is_reported_not_raised(tmp_path: Path) -> None:
    # `echo {} > run/SEARCH_LEDGER.jsonl` is the cheapest possible forgery and
    # the exact thing this audit exists to catch, so it must produce an
    # actionable verdict rather than a traceback the reviewer cannot use.
    path = tmp_path / "SEARCH_LEDGER.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    code, report = _verify(path)

    assert code == 1
    assert report["chain_valid"] is False
    assert report["rows"] == 0


def test_edited_row_breaks_the_chain(tmp_path: Path) -> None:
    # Rewriting a losing trial into a winning one is the interesting forgery:
    # the file still parses, the row count is unchanged, only the hash betrays it.
    path = _real_ledger(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert '"ic":0.01' in lines[1]
    lines[1] = lines[1].replace('"ic":0.01', '"ic":0.90')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    code, report = _verify(path)

    assert code == 1
    assert report["chain_valid"] is False
    assert report["rows"] == 3  # still parses; only the chain reveals it


def test_deleted_row_breaks_the_chain(tmp_path: Path) -> None:
    # Silently dropping trials is how a search of 3,000 hypotheses is made to
    # look like a search of 3.
    path = _real_ledger(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")

    code, report = _verify(path)

    assert code == 1
    assert report["chain_valid"] is False
    assert report["rows"] == 2


def test_missing_ledger_is_not_evidence(tmp_path: Path) -> None:
    code, report = _verify(tmp_path / "nope.jsonl")

    assert code == 1
    assert report == {
        "path": str(tmp_path / "nope.jsonl"),
        "exists": False,
        "chain_valid": False,
        "rows": 0,
    }


def test_empty_but_authentic_ledger_verifies(tmp_path: Path) -> None:
    # Zero trials is a valid chain and a research problem, not a forgery. The
    # tool must not conflate the two — "you searched too little" is the
    # reviewer's judgment to make from `rows`, not this exit code's.
    path = tmp_path / "SEARCH_LEDGER.jsonl"
    path.write_text("", encoding="utf-8")

    code, report = _verify(path)

    assert code == 0
    assert report["chain_valid"] is True
    assert report["rows"] == 0


def test_the_audit_does_not_judge_research_quality() -> None:
    # Reverse assertion. It would be easy, and wrong, to make this tool fail on
    # "too few trials" or "suspicious IC" — that is the L2 reviewer's call, and
    # the ledger's own trust model says it is evidence, not a gate. If a
    # threshold ever appears here, the harness has started grading science.
    import ast
    import inspect

    from argus_skill.verticals.quant import search_ledger as mod

    tree = ast.parse(inspect.getsource(mod._cli))
    ast.get_docstring(tree.body[0])  # assert it parses as a function with a docstring
    tree.body[0].body = tree.body[0].body[1:]  # drop the docstring; prose may discuss it
    code = ast.unparse(tree)

    for grading_word in ("min_trials", "too few", "threshold", "suspicious", "cherry"):
        assert grading_word not in code, grading_word


@pytest.mark.parametrize("stage", ["run", "analysis"])
def test_reviewer_is_told_the_audit_exists(stage: str) -> None:
    # A tool no role knows about is the state this change is fixing; the quant
    # checklist must name the command, not just the file.
    from argus_skill.verticals.quant.stages import CHECKLIST_ITEMS

    hints = " ".join(
        item.evidence_hint or "" for item in CHECKLIST_ITEMS.get(stage, ())
    )
    if "SEARCH_LEDGER" not in hints:
        pytest.skip(f"{stage} does not cite the ledger")
    assert _MODULE in hints

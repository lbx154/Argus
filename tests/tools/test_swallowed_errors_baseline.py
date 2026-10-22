"""Broad ``except`` handlers may only become fewer, package by package (CO-3).

The baseline is ``swallowed_errors_baseline.json`` next to this file, written by
``python -m argus.release_tools.swallowed_errors --write-baseline``. Like the
mypy gate (``argus/release_tools/typecheck_gate.py``), existing debt is allowed
and only new debt fails; unlike it, the reference is a committed file, so the
check needs no Git history and no second scan.
"""

from __future__ import annotations

import ast
import json
import warnings

from argus.release_tools import swallowed_errors

_REGENERATE = "python -m argus.release_tools.swallowed_errors --write-baseline --write-doc"


def test_no_package_gains_a_broad_except():
    baseline = json.loads(swallowed_errors.BASELINE_PATH.read_text(encoding="utf-8"))
    current = swallowed_errors.broad_counts(swallowed_errors.scan())
    grown = {package: (baseline.get(package, 0), count)
             for package, count in current.items() if count > baseline.get(package, 0)}
    assert not grown, (
        "New broad `except Exception` / `except BaseException` / bare `except:` handlers "
        f"(package: baseline -> now): {grown}. Catch the specific exceptions the boundary raises, "
        "or let the error propagate; see 'A mechanical rule' in docs/simplification-plan.md."
    )
    shrunk = {package: (count, current.get(package, 0))
              for package, count in baseline.items() if current.get(package, 0) < count}
    if shrunk:
        warnings.warn(
            f"Broad except handlers went down (package: baseline -> now): {shrunk}. "
            f"Lower the baseline so they cannot come back: {_REGENERATE}",
            stacklevel=1,
        )


def _handlers(source: str) -> list[swallowed_errors.Handler]:
    return list(swallowed_errors._handlers(ast.parse(source), "life/supervisor/_core.py"))


def test_classifies_breadth_and_outcome():
    found = _handlers(
        "def f():\n"
        "    try: pass\n"
        "    except Exception: return []\n"
        "    try: pass\n"
        "    except (OSError, BaseException) as exc: raise RuntimeError() from exc\n"
        "    try: pass\n"
        "    except: pass\n"
        "    try: pass\n"
        "    except OSError: return None\n"
        "    try: pass\n"
        "    except builtins.Exception:\n"
        "        log()\n"
        "        return 'fallback'\n"
        "    try: pass\n"
        "    except ValueError:\n"
        "        def later():\n"
        "            raise\n"
        "        return dict()\n"
    )
    assert [(h.broad, h.outcome) for h in found] == [
        (True, "returns default"),
        (True, "raises"),
        (True, "pass/continue"),
        (False, "returns default"),
        (True, "other"),
        (False, "returns default"),  # a raise in a nested function is not the handler's
    ]
    assert {h.function for h in found} == {"f"}
    assert all(h.main_path == "mission loop" for h in found)


def test_narrow_handlers_that_do_something_are_not_listed(tmp_path):
    (tmp_path / "mod.py").write_text(
        "try: pass\nexcept OSError: return_code = 1\n"
        "try: pass\nexcept KeyError: raise\n",
        encoding="utf-8",
    )
    assert swallowed_errors.scan(tmp_path) == []


def test_packages_and_main_path():
    assert swallowed_errors.Handler("loop.py", 1, "f", "Exception", True, "other").package == "<root>"
    assert swallowed_errors.Handler("loop.py", 1, "f", "Exception", True, "other").main_path == "round loop"
    settlement = swallowed_errors.Handler("engineer/round_settlement.py", 1, "f", "Exception", True, "other")
    assert (settlement.package, settlement.main_path) == ("engineer", "settlement")
    assert swallowed_errors.Handler("webapi/server.py", 1, "f", "Exception", True, "other").main_path is None

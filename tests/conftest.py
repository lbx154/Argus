"""Test-suite isolation guard.

Some tests drive real entry points (``apps.cli.main``, the TUI/web launch path).
Those resolve their state root from ``ARGUS_SKILL_HOME`` at call time, so a test
that does not set it writes into the DEVELOPER'S REAL ``~/.argus-skill``.

That is not a cosmetic leak. Observed on this checkout: running
``tests/apps/test_cli_parser.py`` created real sessions named after the test's
own objective string, spawned real daemons against the real home, and those
daemons ran the real Manager — including its self-maintenance loop, whose
Engineer then EDITED THE SOURCE CHECKOUT while the suite was running. The
spawned daemon also killed the pytest process partway through the file, so the
run ended with no summary and every later failure was invisible.

This fixture pins every state root at a per-test temporary directory. A test
that deliberately wants its own value still wins: ``monkeypatch.setenv`` inside
the test body runs after this fixture.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def _isolate_argus_state_roots(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point every argus state root at a throwaway directory for this test."""
    root = Path(tmp_path_factory.mktemp("argus-home"))

    # Ambient ARGUS_SKILL_* vars steer backend/model/budget resolution, so a
    # developer shell that exports e.g. ARGUS_SKILL_RUNNER_BACKEND silently
    # changes what the suite exercises: that one var outranks the
    # ARGUS_SKILL_LIFE_BACKEND a test sets, so a guard the test expects to trip
    # never fires and the CLI launches the real cockpit instead. COPILOT_HOME is
    # just as stateful: if the developer runs pytest from inside a Copilot-backed
    # Argus session, child-env tests inherit the real worker home and stop
    # exercising the "no explicit home was chosen" path. Start from a clean slate;
    # a test that needs a value sets it itself.
    for name in [k for k in os.environ if k.startswith("ARGUS_SKILL_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))

    special = root / "special_prompts"
    special.mkdir(parents=True, exist_ok=True)
    # Seed one trusted directive so the lifetime entry gate passes and tests
    # exercise what they actually target. 0644 is required: the trust check
    # rejects group/world-writable files (the default umask yields 0664). A
    # test that specifically exercises the missing-prompt gate points
    # ARGUS_SKILL_SPECIAL_PROMPTS_DIR somewhere empty itself.
    house_rules = special / "10-house-rules.md"
    house_rules.write_text("Operational house rules for this box.\n", encoding="utf-8")
    house_rules.chmod(0o644)
    monkeypatch.setenv("ARGUS_SKILL_SPECIAL_PROMPTS_DIR", str(special))

    # A test must never be able to hand a real daemon the developer's checkout
    # as its self-maintenance source tree.
    source = root / "source"
    source.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARGUS_SKILL_SOURCE_ROOT", str(source))



def pytest_configure(config: pytest.Config) -> None:  # noqa: ARG001
    """Keep collection-time imports in safe mode.

    The per-test fixture above re-points every state root, but module import
    happens before it runs; safe mode keeps any import-time side effect from
    reaching a real sandbox escape.
    """
    os.environ.setdefault("ARGUS_SKILL_SAFE_MODE", "1")


@pytest.fixture(autouse=True)
def _isolate_working_directory(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Give every test its own working directory instead of the checkout.

    Much of the runtime resolves "which project am I?" from the process cwd —
    ``resolve_project_root``, ``resolve_vertical``, ``PIPELINE_STATE.json``
    lookups, the daemon's own workdir. Under pytest that cwd was the source
    checkout, so a test would silently adopt the repository as its project. The
    visible symptom was log lines like ``no Manager vertical resolved for
    .../argus-skill; using research only as a compatibility fallback`` during
    unrelated tests; the invisible one is any test that writes project state
    into the tree it is testing.

    A test that genuinely needs a specific directory still calls
    ``monkeypatch.chdir`` itself and wins, since fixtures run before the test
    body.
    """
    workdir = tmp_path_factory.mktemp("workdir")
    monkeypatch.chdir(workdir)
    return workdir


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _forbid_project_state_in_the_checkout() -> Iterator[None]:
    """Fail a test that writes project state into the source tree.

    The cwd fixture above removes the usual way this happens, but a test can
    still pass an explicit path. Rather than trust that, this checks afterwards:
    the files the runtime creates to mark a project are named, so their
    appearance in the checkout is unambiguous and worth failing on immediately —
    the alternative is finding them days later in `git status` and not knowing
    which test left them.
    """
    root = _repo_root()
    markers = ("research/PIPELINE_STATE.json", "research/CHECKLISTS.json", ".autors")
    before = {name for name in markers if (root / name).exists()}
    yield
    leaked = sorted(
        name for name in markers if (root / name).exists() and name not in before
    )
    assert not leaked, (
        f"test wrote project state into the source checkout: {leaked}. "
        "Give the daemon/supervisor an explicit workdir under tmp_path."
    )

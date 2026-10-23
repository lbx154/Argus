"""Test-suite isolation guard.

Some tests drive real entry points (``apps.cli.main``, the TUI/web launch path).
Those resolve their state root from ``ARGUS_SKILL_HOME`` at call time, so a test
that does not set it writes into the DEVELOPER'S REAL ``~/.argus-skill``.

That is not a cosmetic leak. Observed on this checkout: running
``tests/apps/test_cli_parser.py`` created real sessions named after the test's
own objective string, spawned real daemons against the real home, and those
daemons ran the real Manager and Engineer against the source checkout. The
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


@pytest.fixture
def platform_process_env() -> dict[str, str]:
    """Minimal public OS bootstrap values, never an ambient credential copy.

    Node/OpenSSL on Windows fails its CSPRNG initialization without SystemRoot.
    Explicitly isolated child-env tests must retain this OS prerequisite and
    the QA runner's temporary directories while still discarding user config.
    """
    if os.name != "nt":
        return {}
    return {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP")
            if name in os.environ}


@pytest.fixture
def require_symlink_support(tmp_path: Path) -> None:
    """Skip only when this host cannot create the symlinks a test requires.

    Windows supports symlinks when Developer Mode or the corresponding account
    privilege is enabled.  Treat that as a runtime capability instead of
    skipping every Windows host: capable Windows CI still exercises the real
    security boundary, while restricted developer machines do not report a
    fixture-permission error as a product regression.
    """
    probe = tmp_path / "symlink-capability"
    probe.mkdir()
    file_target = probe / "file-target"
    file_target.write_text("probe\n", encoding="utf-8")
    directory_target = probe / "directory-target"
    directory_target.mkdir()
    try:
        (probe / "file-link").symlink_to(file_target)
        (probe / "directory-link").symlink_to(
            directory_target,
            target_is_directory=True,
        )
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"host cannot create test symlinks: {exc}")


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
    for name in [k for k in os.environ if k.startswith(("ARGUS_SKILL_", "ARGUS_WORKBENCH_", "ARGUS_PLUGIN_", "ARGUS_DESKTOP_", "ARGUS_TRIAL_"))]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    # The life-worker boot path setdefault()s the workbench host root into
    # os.environ in-process; a test that booted a worker would otherwise hand
    # its throwaway root to every later test that resolves trial_home().
    monkeypatch.delenv("ARGUS_WORKBENCH_HOST_ROOT", raising=False)

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(root))
    # Model resolution inspects Codex's provider config to decide whether an
    # OpenAI model id is valid. Never let a developer's ~/.codex/config.toml
    # change default-model assertions. CODEX_HOME remains overridable by tests
    # that deliberately exercise a custom provider.
    monkeypatch.setenv("CODEX_HOME", str(root / "codex-home"))

    # ARGUS_SKILL_SOURCE_ROOT deliberately stays UNSET (the loop above already
    # dropped any developer-shell value). Setting it now ARMS the source-root
    # startup preflight, so an ambient throwaway path would make every real
    # daemon/WebAPI boot in the suite refuse startup; the self-maintenance
    # machine that once read it as a maintenance source tree is gone. A test
    # that exercises the preflight sets its own root.



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
    .../argus; using research only as a compatibility fallback`` during
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
    markers = (".argus/PIPELINE_STATE.json", "research/CHECKLISTS.json", ".autors")
    before = {name for name in markers if (root / name).exists()}
    yield
    leaked = sorted(
        name for name in markers if (root / name).exists() and name not in before
    )
    assert not leaked, (
        f"test wrote project state into the source checkout: {leaked}. "
        "Give the daemon/supervisor an explicit workdir under tmp_path."
    )


@pytest.fixture(autouse=True)
def _no_real_copilot_help_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never run the installed Copilot CLI to feature-detect ``--session-id``.

    The adapter probes the executable's help text once per process before it
    binds a new session identity. A developer machine with Copilot on PATH
    would otherwise make the suite spawn a real Node binary and change which
    argv/identity path every Copilot-backed test exercises. Default to "no
    such flag"; a test that exercises the binding stubs the probe itself.
    """
    from argus.agent_cli import copilot_session as _copilot_session

    _copilot_session.reset_copilot_session_id_support_cache()
    monkeypatch.setattr(_copilot_session, "_copilot_help_text", lambda executable: "")
    yield
    _copilot_session.reset_copilot_session_id_support_cache()


@pytest.fixture(autouse=True)
def _no_host_sandbox(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent CLIs in tests are fakes that write where the test reads, so they
    run without the host's sandbox; a test of the sandbox asks for
    ``host_sandbox``."""
    if "host_sandbox" not in request.fixturenames:
        monkeypatch.setattr("argus.core.sandbox.sandbox_tool", lambda: None)


@pytest.fixture
def host_sandbox() -> str:
    from argus.core.sandbox import sandbox_tool

    tool = sandbox_tool()
    if tool is None or Path(tool).name != "bwrap":
        pytest.skip("needs bubblewrap")
    return tool


@pytest.fixture(autouse=True)
def _no_stop_leaks_between_tests():
    """The process-wide stop flag outlives a test by design — it exists so a
    wait deep inside a mission can see a signal. One test setting it once made
    an unrelated external-work test read `stop_requested` instead of the
    outcome that had actually arrived."""
    from argus.core import process_stop

    process_stop.clear_stop()
    yield
    process_stop.clear_stop()


@pytest.fixture
def redact_secrets_on(monkeypatch):
    """Opt into Argus's credential masking, which is off by default."""
    monkeypatch.setenv("ARGUS_SKILL_REDACT_SECRETS", "1")


def _install_vertical(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: object) -> Iterator[str]:
    """Advertise one in-memory plugin vertical as the only entry point."""
    from types import ModuleType, SimpleNamespace

    from argus.verticals import _registry

    module = ModuleType(f"{name}.stages")
    module.ARGUS_VERTICAL_API_VERSION = 1
    module.VERTICAL_PURPOSE = f"{name} test vertical"
    for key, value in attrs.items():
        setattr(module, key, value)
    entry = SimpleNamespace(name=name, value=module.__name__, load=lambda: module)
    monkeypatch.setattr(
        _registry, "entry_points",
        lambda group: [entry] if group == _registry.ENTRY_POINT_GROUP else [],
    )
    _registry.refresh_vertical_plugins()
    yield name
    _registry.refresh_vertical_plugins()


@pytest.fixture
def optimize_vertical(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """``optimize_lab``: a metric vertical (setup -> optimize -> measure -> report).

    No optimization vertical is built in any more (``math_synth`` and the rest
    live in argus-verticals), so generic stage, completion and mission tests
    install this plugin instead.
    """
    from argus.verticals.optimization_base import speedrun_base_contract

    base = speedrun_base_contract()
    yield from _install_vertical(
        monkeypatch, "optimize_lab",
        CHECKLIST_STAGE_ORDER=base.stage_order,
        CHECKLIST_ITEMS=base.checklist_items,
        completion_gate="metric",
        role_banner=lambda role: f"optimize_lab {role} banner",
    )


@pytest.fixture
def targeted_vertical(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """``targeted_lab``: research targets without a certified gate, the shape ``math`` has."""
    from argus.skills.stage_machine import ChecklistItem

    stages = ("scope", "solve", "review")
    yield from _install_vertical(
        monkeypatch, "targeted_lab",
        CHECKLIST_STAGE_ORDER=stages,
        CHECKLIST_ITEMS={stage: (ChecklistItem(f"{stage}.done", f"{stage} is done", stage),) for stage in stages},
        completion_gate="none",
        COMPLETION_CONTRACT_VERSION=1,
        RESEARCH_TARGET_LEVELS=("exploratory", "publishable", "doctoral"),
    )

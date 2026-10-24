"""Backend resolution must state its answer and its source, or refuse.

The incident these tests pin down: seven campaign daemons were launched with
``--backend copilot``. ``--backend`` is a CLI ARGUMENT — it lands in
``LifeWorkerConfig.backend`` and was never exported — so
``core.knobs.resolve_role_backend("reviewer")`` walked
``ARGUS_SKILL_REVIEWER_BACKEND`` -> ``ARGUS_SKILL_RUNNER_BACKEND`` ->
``ARGUS_SKILL_LIFE_BACKEND`` -> the persisted knob store, found nothing
(``/proc/<pid>/environ`` of every live daemon carried ``ARGUS_SKILL_HOME`` and
no ``ARGUS_SKILL_*_BACKEND`` at all), and fell through to a bare
``return "codex"``. The reviewer role then drove ``codex`` at a local relay
that has no token in the daemon environment: ``401 Missing bearer`` on every
review, and each paper hard-gated at ``model_review_unavailable``. The only
workaround was hand-writing ``{"ARGUS_SKILL_REVIEWER_BACKEND": "copilot"}``
into five separate ``state/config.json`` files.

Nothing about that was loud. The daemon logged a healthy boot, the roles panel
showed a backend, and "the operator chose codex" and "we could not find what
the operator chose" were the same value.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.agent_cli.runner_backend import (
    normalize_runner_backend,
)
from argus.core.knobs import (
    BackendResolutionError,
    resolve_role_backend,
)

# The autouse fixture in tests/conftest.py clears every ambient ARGUS_SKILL_*
# var and repoints ARGUS_SKILL_HOME at a throwaway directory, so each test here
# starts from a genuinely unconfigured host — which is the state the incident
# happened in.


@pytest.fixture(autouse=True)
def _restore_backend_env():
    """Undo writes the daemon boot export makes directly to ``os.environ``.

    ``_rf_export_configured_backend`` deliberately mutates the real process
    environment (that IS the fix), so a test that calls it would otherwise leak
    its backend into whatever runs next.
    """
    saved = {
        name: os.environ.get(name)
        for name in (
            "ARGUS_SKILL_RUNNER_BACKEND",
            "ARGUS_SKILL_LIFE_BACKEND",
            "ARGUS_SKILL_REVIEWER_BACKEND",
        )
    }
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# ---------------------------------------------------------------------------
# 1. An unconfigured resolve refuses instead of guessing
# ---------------------------------------------------------------------------


def test_shared_resolve_with_no_role_also_raises() -> None:
    with pytest.raises(BackendResolutionError):
        resolve_role_backend("", env={})


# ---------------------------------------------------------------------------
# 2. An explicit default is honored — and is visible in the source tag
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. Provenance for each chain position; a retired per-role knob is ignored
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. A typo'd backend is rejected, an unset one still has a default
# ---------------------------------------------------------------------------


def test_normalize_still_defaults_an_unset_value() -> None:
    """Empty means "not configured", which is a real state with a real default.

    This is the half that must NOT become an error: several callers rely on
    ``normalize_runner_backend("")`` naming the codex default.
    """
    assert normalize_runner_backend("") == "codex"
    assert normalize_runner_backend(None) == "codex"
    assert normalize_runner_backend("   ") == "codex"


# ---------------------------------------------------------------------------
# 5. A corrupt knob store is not "nothing persisted"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. THE INCIDENT: --backend must reach the reviewer
# ---------------------------------------------------------------------------


def _worker(tmp_path: Path, backend: str):
    from argus.daemon.life_worker import LifeWorker, LifeWorkerConfig

    return LifeWorker(
        LifeWorkerConfig(
            life_dir=tmp_path / "life",
            project_workdir=tmp_path / "proj",
            backend=backend,
            poll_interval=0.1,
        )
    )


def test_daemon_launched_with_backend_copilot_gives_the_reviewer_copilot(
    tmp_path: Path,
) -> None:
    """This test IS the incident.

    Seven daemons ran ``--backend copilot``. Nothing exported it, so the
    reviewer resolved to codex, hit ``401 Missing bearer`` against a tokenless
    relay, and every paper stopped at ``model_review_unavailable``.

    The environment below is the one read out of ``/proc/<pid>/environ`` on
    those live daemons: ``ARGUS_SKILL_HOME`` and no backend knob at all. The
    ONLY place the operator's choice exists is ``cfg.backend``.
    """
    worker = _worker(tmp_path, "copilot")
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ
    assert "ARGUS_SKILL_REVIEWER_BACKEND" not in os.environ

    # Pre-fix this line did not exist; the reviewer resolved to codex below.
    worker._rf_export_configured_backend()

    assert resolve_role_backend("reviewer") == "copilot"
    # and the whole fleet, not just the role that happened to fail first
    for role in ("manager", "planner", "engineer", "reviewer"):
        assert resolve_role_backend(role) == "copilot"


def test_an_explicit_env_override_still_outranks_the_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``setdefault``, not assignment.

    An operator who exported the var before launching has made the more
    specific statement; the flag must not overwrite it.
    """
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    _worker(tmp_path, "copilot")._rf_export_configured_backend()
    assert os.environ["ARGUS_SKILL_RUNNER_BACKEND"] == "claude"
    assert resolve_role_backend("reviewer") == "claude"


def test_the_export_runs_before_anything_resolves_a_role(tmp_path: Path) -> None:
    """Ordering is the whole point: a late export would fix nothing.

    ``_rf_bootstrap_environment`` is ``run_forever``'s first call and the export
    happens inside it, ahead of the vault preflight (which itself resolves the
    reviewer's backend to decide whether to probe the codex vault).
    """
    import inspect

    from argus.daemon._life_worker_boot import LifeWorkerBootMixin

    boot = inspect.getsource(LifeWorkerBootMixin.run_forever)
    assert boot.index("_rf_bootstrap_environment") < boot.index("_rf_vault_preflight")

    bootstrap = inspect.getsource(LifeWorkerBootMixin._rf_bootstrap_environment)
    assert "_rf_export_configured_backend" in bootstrap


# ---------------------------------------------------------------------------
# 7. The resolution is recorded, so it can be seen without a debugger
# ---------------------------------------------------------------------------


def test_a_recorded_backend_distinguishes_configured_from_defaulted(
    tmp_path: Path,
) -> None:
    """The distinction the incident had no way to express.

    Without the export, ``cfg.backend`` is the only source of the answer and the
    event says so — ``source == "default"`` is the shape of "nobody told us,
    we fell back", which used to be indistinguishable from a real choice.
    """
    worker = _worker(tmp_path, "copilot")  # deliberately NOT exported

    recorded: list[dict] = []
    worker._rf_record_role_backends(
        SimpleNamespace(cfg=worker.config, sink=SimpleNamespace(append=recorded.append))
    )
    reviewer = next(e for e in recorded if e["role"] == "reviewer")
    assert reviewer["backend"] == "copilot"
    assert reviewer["source"] == "default"


# ---------------------------------------------------------------------------
# A corrupt knob file must be survivable: loud at boot, legible in the cockpit.
# ---------------------------------------------------------------------------

def _corrupt_knob_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text("{ not json", encoding="utf-8")


def test_the_daemon_refuses_to_boot_on_a_corrupt_knob_file(tmp_path, monkeypatch) -> None:
    """Refuse at the preflight, where the operator can read the reason.

    Every resolver reads this file, so without an explicit check the first role
    resolution raises somewhere deep in construction and the traceback's top
    frame has nothing to do with the fault. Booting anyway is worse still: it
    silently reverts every persisted switch at once.
    """
    from types import SimpleNamespace

    from argus.daemon._life_worker_run import LifeWorkerRunMixin

    _corrupt_knob_store(tmp_path, monkeypatch)
    mixin = LifeWorkerRunMixin.__new__(LifeWorkerRunMixin)
    state = SimpleNamespace(cfg=SimpleNamespace(backend="copilot"))

    assert LifeWorkerRunMixin._rf_vault_preflight(mixin, state) == 2


def test_the_config_page_shows_the_corruption_instead_of_dying_of_it(
    tmp_path,
    monkeypatch,
) -> None:
    """The snapshot exists to show what Argus is configured to use.

    Raising here would take down the one page that could explain the fault
    (``webapi.mission_items`` builds it), and returning an empty map would
    render every persisted switch as "unset" — the silent revert this whole
    change removes. Report it, and render the defaults it is actually showing.
    """
    from argus.core.config_snapshot import (
        build_config_snapshot,
        format_config_snapshot_markdown,
    )

    _corrupt_knob_store(tmp_path, monkeypatch)
    snapshot = build_config_snapshot(env={})

    assert str(tmp_path) in snapshot["persisted_knob_error"]
    assert "Repair or delete" in snapshot["persisted_knob_error"]
    # Still renders, and says plainly that these are defaults, not the file.
    assert snapshot["operator_knobs"]
    assert "PERSISTED KNOBS UNREADABLE" in format_config_snapshot_markdown(snapshot)

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

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus_skill.agent_cli.runner_backend import (
    SUPPORTED_BACKENDS,
    normalize_runner_backend,
)
from argus_skill.core import knob_store
from argus_skill.core.knobs import (
    BackendResolutionError,
    resolve_role_backend,
    resolve_role_backend_with_source,
)
from argus_skill.core.paths import config_path

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


def _persist(**knobs: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(knobs), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. An unconfigured resolve refuses instead of guessing
# ---------------------------------------------------------------------------


def test_unconfigured_resolve_raises_instead_of_returning_codex() -> None:
    """The terminal ``return "codex"`` is gone.

    Pre-fix this call returned ``"codex"`` — the exact silent substitution that
    sent the reviewer to a backend nobody selected.
    """
    with pytest.raises(BackendResolutionError):
        resolve_role_backend("reviewer", env={})


def test_the_refusal_is_actionable_without_reading_source() -> None:
    """An operator hitting this must be able to fix it from the message alone."""
    with pytest.raises(BackendResolutionError) as excinfo:
        resolve_role_backend("reviewer", env={})
    message = str(excinfo.value)

    # the role that failed
    assert "reviewer" in message
    # every knob that was checked, so they know where a value may go
    assert "ARGUS_SKILL_REVIEWER_BACKEND" in message
    assert "ARGUS_SKILL_RUNNER_BACKEND" in message
    assert "ARGUS_SKILL_LIFE_BACKEND" in message
    # and the file the persisted layer lives in
    assert str(config_path()) in message


def test_shared_resolve_with_no_role_also_raises() -> None:
    with pytest.raises(BackendResolutionError):
        resolve_role_backend("", env={})


# ---------------------------------------------------------------------------
# 2. An explicit default is honored — and is visible in the source tag
# ---------------------------------------------------------------------------


def test_explicit_default_is_honored() -> None:
    assert resolve_role_backend("reviewer", env={}, default="copilot") == "copilot"


def test_explicit_default_reports_itself_as_the_default_source() -> None:
    backend, source = resolve_role_backend_with_source(
        "reviewer", env={}, default="copilot"
    )
    assert (backend, source) == ("copilot", "default")


def test_an_empty_default_is_not_a_default() -> None:
    """``default=""`` must not read as "configured as empty string"."""
    for empty in ("", "   ", None):
        with pytest.raises(BackendResolutionError):
            resolve_role_backend("reviewer", env={}, default=empty)


def test_configured_value_outranks_the_default() -> None:
    backend, source = resolve_role_backend_with_source(
        "reviewer",
        env={"ARGUS_SKILL_REVIEWER_BACKEND": "claude"},
        default="copilot",
    )
    assert (backend, source) == ("claude", "env:ARGUS_SKILL_REVIEWER_BACKEND")


# ---------------------------------------------------------------------------
# 3. Provenance for each of the four chain positions
# ---------------------------------------------------------------------------


def test_source_tag_role_env() -> None:
    assert resolve_role_backend_with_source(
        "reviewer",
        env={
            "ARGUS_SKILL_REVIEWER_BACKEND": "copilot",
            "ARGUS_SKILL_RUNNER_BACKEND": "claude",
            "ARGUS_SKILL_LIFE_BACKEND": "pi",
        },
    ) == ("copilot", "env:ARGUS_SKILL_REVIEWER_BACKEND")


def test_source_tag_shared_runner_env() -> None:
    assert resolve_role_backend_with_source(
        "reviewer",
        env={"ARGUS_SKILL_RUNNER_BACKEND": "claude", "ARGUS_SKILL_LIFE_BACKEND": "pi"},
    ) == ("claude", "env:ARGUS_SKILL_RUNNER_BACKEND")


def test_source_tag_life_env() -> None:
    assert resolve_role_backend_with_source(
        "reviewer", env={"ARGUS_SKILL_LIFE_BACKEND": "pi"}
    ) == ("pi", "env:ARGUS_SKILL_LIFE_BACKEND")


def test_source_tag_persisted_knob() -> None:
    _persist(ARGUS_SKILL_REVIEWER_BACKEND="copilot")
    assert resolve_role_backend_with_source("reviewer", env={}) == (
        "copilot",
        "persisted:ARGUS_SKILL_REVIEWER_BACKEND",
    )


def test_env_outranks_the_persisted_knob() -> None:
    """A deliberate one-off export must not be shadowed by last week's switch."""
    _persist(ARGUS_SKILL_REVIEWER_BACKEND="copilot")
    assert resolve_role_backend_with_source(
        "reviewer", env={"ARGUS_SKILL_REVIEWER_BACKEND": "claude"}
    ) == ("claude", "env:ARGUS_SKILL_REVIEWER_BACKEND")


def test_the_source_vocabulary_matches_resolve_backend_profile() -> None:
    """One dialect for provenance, not two.

    ``core.backend_readiness.resolve_backend_profile`` already tags its answer
    ``env:<VAR>`` / ``persisted:<VAR>`` / ``default``. The cockpit should not
    have to learn a second spelling to render the same fact.
    """
    from argus_skill.core.backend_readiness import resolve_backend_profile

    profile = resolve_backend_profile(env={"ARGUS_SKILL_RUNNER_BACKEND": "copilot"})
    _backend, source = resolve_role_backend_with_source(
        "", env={"ARGUS_SKILL_RUNNER_BACKEND": "copilot"}
    )
    assert profile.backend_source == source == "env:ARGUS_SKILL_RUNNER_BACKEND"


def test_resolve_role_backend_is_a_thin_wrapper() -> None:
    env = {"ARGUS_SKILL_ENGINEER_BACKEND": "grok"}
    assert resolve_role_backend("engineer", env=env) == (
        resolve_role_backend_with_source("engineer", env=env)[0]
    )


# ---------------------------------------------------------------------------
# 4. A typo'd backend is rejected, an unset one still has a default
# ---------------------------------------------------------------------------


def test_normalize_rejects_a_typo() -> None:
    """``copilto`` used to silently become ``codex``."""
    with pytest.raises(ValueError) as excinfo:
        normalize_runner_backend("copilto")
    message = str(excinfo.value)
    assert "copilto" in message
    for backend in SUPPORTED_BACKENDS:
        assert backend in message


def test_normalize_still_defaults_an_unset_value() -> None:
    """Empty means "not configured", which is a real state with a real default.

    This is the half that must NOT become an error: several callers rely on
    ``normalize_runner_backend("")`` naming the codex default.
    """
    assert normalize_runner_backend("") == "codex"
    assert normalize_runner_backend(None) == "codex"
    assert normalize_runner_backend("   ") == "codex"


def test_normalize_still_accepts_every_supported_backend_and_the_legacy_alias() -> None:
    for backend in SUPPORTED_BACKENDS:
        assert normalize_runner_backend(backend) == backend
        assert normalize_runner_backend(backend.upper()) == backend
    assert normalize_runner_backend("opencod") == "opencode"


def test_a_typo_in_a_knob_reaches_the_operator() -> None:
    """The end-to-end shape of the typo case: knob -> resolver -> normalizer."""
    requested = resolve_role_backend(
        "reviewer", env={"ARGUS_SKILL_REVIEWER_BACKEND": "copilto"}
    )
    with pytest.raises(ValueError, match="copilto"):
        normalize_runner_backend(requested)


def test_display_paths_that_guard_the_normalizer_still_show_the_raw_value() -> None:
    """``backend_readiness`` deliberately preserves an unknown value for display.

    Its guard must keep working after the normalizer got strict — otherwise the
    cockpit would crash instead of echoing back what the operator typed.
    """
    from argus_skill.core.backend_readiness import resolve_backend_profile

    profile = resolve_backend_profile(env={"ARGUS_SKILL_RUNNER_BACKEND": "copilto"})
    assert profile.backend == "copilto"
    assert profile.backend_source == "env:ARGUS_SKILL_RUNNER_BACKEND"


# ---------------------------------------------------------------------------
# 5. A corrupt knob store is not "nothing persisted"
# ---------------------------------------------------------------------------


def test_corrupt_knob_store_raises_rather_than_reverting_every_switch() -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ARGUS_SKILL_REVIEWER_BACKEND": "copi', encoding="utf-8")
    with pytest.raises(knob_store.KnobStoreCorruptError) as excinfo:
        knob_store.read_persisted_knobs()
    assert str(path) in str(excinfo.value)


def test_corrupt_knob_store_stops_role_resolution_too() -> None:
    """The consequence that matters: a truncated file must not read as codex.

    Pre-fix, a half-written config.json logged one warning and returned ``{}``,
    so every role fell to its default backend with nothing in any artifact to
    say the operator's persisted choices had been discarded.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(knob_store.KnobStoreCorruptError):
        resolve_role_backend("reviewer", env={}, default="codex")


def test_absent_knob_store_is_still_normal() -> None:
    """A missing file is not corruption — it is a host with no persisted switch."""
    assert not config_path().exists()
    assert knob_store.read_persisted_knobs() == {}
    assert resolve_role_backend("reviewer", env={}, default="codex") == "codex"


# ---------------------------------------------------------------------------
# 6. THE INCIDENT: --backend must reach the reviewer
# ---------------------------------------------------------------------------


def _worker(tmp_path: Path, backend: str):
    from argus_skill.daemon.life_worker import LifeWorker, LifeWorkerConfig

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
    for role in ("manager", "planner", "engineer", "reviewer", "curator"):
        assert resolve_role_backend(role) == "copilot"


def test_the_export_removes_the_need_for_the_hand_written_config_workaround(
    tmp_path: Path,
) -> None:
    """The five hand-written ``state/config.json`` files become unnecessary.

    The operator's workaround was to persist
    ``{"ARGUS_SKILL_REVIEWER_BACKEND": "copilot"}`` per project. After the
    export, an untouched knob store resolves to the same answer.
    """
    assert not config_path().exists()
    _worker(tmp_path, "copilot")._rf_export_configured_backend()
    backend, source = resolve_role_backend_with_source("reviewer")
    assert backend == "copilot"
    assert source == "env:ARGUS_SKILL_RUNNER_BACKEND"


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


def test_a_role_specific_knob_still_outranks_the_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "claude")
    _worker(tmp_path, "copilot")._rf_export_configured_backend()
    assert resolve_role_backend("reviewer") == "claude"
    assert resolve_role_backend("engineer") == "copilot"


def test_the_memory_backend_is_never_exported(tmp_path: Path) -> None:
    """``memory`` is the in-process test backend, not an agent-CLI backend.

    It is absent from ``SUPPORTED_BACKENDS``, so exporting it would make the
    strict normalizer reject the whole chain.
    """
    _worker(tmp_path, "memory")._rf_export_configured_backend()
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ


def test_an_empty_configured_backend_is_not_exported(tmp_path: Path) -> None:
    _worker(tmp_path, "")._rf_export_configured_backend()
    assert "ARGUS_SKILL_RUNNER_BACKEND" not in os.environ


def test_the_export_runs_before_anything_resolves_a_role(tmp_path: Path) -> None:
    """Ordering is the whole point: a late export would fix nothing.

    ``_rf_bootstrap_environment`` is ``run_forever``'s first call and the export
    happens inside it, ahead of the vault preflight (which itself resolves the
    reviewer's backend to decide whether to probe the codex vault).
    """
    import inspect

    from argus_skill.daemon._life_worker_boot import LifeWorkerBootMixin

    boot = inspect.getsource(LifeWorkerBootMixin.run_forever)
    assert boot.index("_rf_bootstrap_environment") < boot.index("_rf_vault_preflight")

    bootstrap = inspect.getsource(LifeWorkerBootMixin._rf_bootstrap_environment)
    assert "_rf_export_configured_backend" in bootstrap


# ---------------------------------------------------------------------------
# 7. The resolution is recorded, so it can be seen without a debugger
# ---------------------------------------------------------------------------


def test_daemon_boot_records_each_role_backend_with_its_source(
    tmp_path: Path,
) -> None:
    from argus_skill.core.event_catalog import validate_event_envelope

    worker = _worker(tmp_path, "copilot")
    worker._rf_export_configured_backend()

    recorded: list[dict] = []
    rf_state = SimpleNamespace(
        cfg=worker.config,
        sink=SimpleNamespace(append=recorded.append),
    )
    worker._rf_record_role_backends(rf_state)

    by_role = {event["role"]: event for event in recorded}
    assert set(by_role) == {"manager", "planner", "engineer", "reviewer", "curator"}
    for role, event in by_role.items():
        assert event["type"] == f"life.{role}.backend_resolved"
        assert event["backend"] == "copilot"
        assert event["source"] == "env:ARGUS_SKILL_RUNNER_BACKEND"
        # a declared type with a declared payload, not a free-form dict
        validation = validate_event_envelope(event, require_known=True)
        assert validation.valid, validation.errors


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


def test_a_typed_backend_flag_outranks_a_stale_ambient_env(monkeypatch) -> None:
    """``--backend`` is typed on this invocation; env and knobs may be stale.

    ``--backend`` parses with ``default=None``, so a value is unambiguous
    operator intent. Letting ``ARGUS_SKILL_RUNNER_BACKEND`` outrank it would
    silently substitute a backend nobody asked for — which is the fault this
    whole change exists to remove, reintroduced one layer up.
    """
    import argparse

    from argus_skill.core.knobs import resolve_role_backend

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "codex")
    args = argparse.Namespace(backend="copilot")

    # The resolution shape used at all three apps/cli/_core.py sites.
    resolved = getattr(args, "backend", None) or resolve_role_backend(
        "", default="codex"
    )
    assert resolved == "copilot"

    # And with no flag typed, the chain is still what decides.
    bare = argparse.Namespace(backend=None)
    assert (
        getattr(bare, "backend", None)
        or resolve_role_backend("", default="codex")
    ) == "codex"


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

    from argus_skill.daemon._life_worker_run import LifeWorkerRunMixin

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
    from argus_skill.core.config_snapshot import (
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


def test_a_healthy_knob_file_carries_no_error_banner(tmp_path, monkeypatch) -> None:
    """The negative control: the banner must mean something when it appears."""
    import json

    from argus_skill.core.config_snapshot import (
        build_config_snapshot,
        format_config_snapshot_markdown,
    )

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({}), encoding="utf-8")
    snapshot = build_config_snapshot(env={})

    assert snapshot["persisted_knob_error"] == ""
    assert snapshot["roles"], "roles should resolve normally"
    assert "PERSISTED KNOBS UNREADABLE" not in format_config_snapshot_markdown(snapshot)


def test_an_unresolvable_backend_reads_as_unavailable_supervision_not_as_health(
    tmp_path,
    monkeypatch,
) -> None:
    """The two halves of this branch have to compose.

    ``resolve_role_backend`` now refuses to guess codex, and the subagent guard
    chain must turn that refusal into a visible ``supervisor_unavailable`` —
    not a crash, and above all not the fabricated "continue / healthy" that let
    a GPU run burn to completion with no supervision at all.
    """
    from argus_skill.tools.subagent import _supervised_run as sr

    for name in [k for k in os.environ if k.startswith("ARGUS_SKILL")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path))

    result = sr._supervisor_check_with_usage(
        "t1", "python train.py", "desc",
        tmp_path / "o.log", tmp_path / "e.log",
        1.0, 1, "gpt-5.5", str(tmp_path),
    )

    assert result.health == "supervisor_unavailable"
    assert result.decision == "continue"  # a dead supervisor is not a verdict on the run
    assert "BackendResolutionError" in (result.error or "")

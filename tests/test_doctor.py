"""Tests for the Web/TUI ``/doctor`` diagnostics backend.

The diagnostics are fully fail-soft and network-free by default: every check
either returns a :class:`Check` or is converted into a failed Check, and the
model-API check only touches the network when an explicit ``probe`` is
injected. These tests run on a tmp project dir with no daemon and never make a
real network call.
"""
from __future__ import annotations

import os

from argus_skill.webapi.diagnostics import Check, render_report, run_diagnostics

# ---------------------------------------------------------------------------
# render_report formatting
# ---------------------------------------------------------------------------

def test_render_report_lists_each_check_and_fix_lines():
    checks = [
        Check("daemon", False, "no daemon is running", "run: argus-skill --daemon"),
        Check("lock sanity", True, "no stale lock files", ""),
    ]
    report = render_report(checks)  # theme=None -> plain text
    assert "argus-skill doctor" in report
    assert "✗ daemon" in report
    assert "✓ lock sanity" in report
    # The failing check's fix shows on its own indented line.
    assert "↳ fix: run: argus-skill --daemon" in report
    # A passing check shows no fix line.
    assert "no stale lock files" in report
    assert "1 issue(s) found" in report


def test_render_report_recommends_root_cause_over_symptom():
    # daemon-down is the symptom; an unconfigured/unreachable model API is the
    # root cause. The recommendation (last line) must surface the cause.
    checks = [
        Check("daemon", False, "no daemon", "run: argus-skill --daemon"),
        Check(
            "model API capability",
            False,
            "unreachable",
            "gpt-5.5 backend rate-limited (429) — wait and retry, or switch "
            "backend with /backend memory",
        ),
        Check("lock sanity", True, "ok", ""),
    ]
    report = render_report(checks)
    last = report.splitlines()[-1]
    assert last.startswith("→ recommended:")
    assert "429" in last
    assert "switch backend" in last


def test_render_report_all_green_has_no_recommendation():
    report = render_report([Check("daemon", True, "running (pid 5)", "")])
    assert "all checks passed" in report
    assert "→ recommended:" not in report


def test_render_report_with_theme_is_failsoft():
    # A theme-shaped object whose methods raise must not break rendering.
    class BrokenTheme:
        def bold(self, _):  # noqa: ANN001
            raise RuntimeError("boom")

        def __getattr__(self, _name):  # noqa: ANN001
            def _raise(_):  # noqa: ANN001
                raise RuntimeError("boom")

            return _raise

    report = render_report(
        [Check("daemon", False, "down", "run: argus-skill --daemon")],
        theme=BrokenTheme(),
    )
    assert "argus-skill doctor" in report
    assert "run: argus-skill --daemon" in report


# ---------------------------------------------------------------------------
# run_diagnostics on a tmp project with no daemon
# ---------------------------------------------------------------------------

def _by_name(checks):
    return {c.name: c for c in checks}


def test_no_daemon_flagged_with_daemon_fix(tmp_path):
    checks = run_diagnostics(tmp_path)
    by_name = _by_name(checks)
    daemon = by_name["daemon"]
    assert daemon.ok is False
    assert "argus-skill --daemon" in daemon.fix
    assert "NOT execute" in daemon.detail


def test_clean_project_has_sane_locks_and_empty_session(tmp_path):
    checks = run_diagnostics(tmp_path)
    by_name = _by_name(checks)
    # No lock files at all -> lock sanity passes.
    assert by_name["lock sanity"].ok is True
    # No backlog / events / objective and no daemon -> flagged as empty.
    sess = by_name["empty session"]
    assert sess.ok is False
    assert "--gc" in sess.fix


def test_stale_daemon_lock_is_flagged(tmp_path):
    # A pid that cannot be running (way out of range) is a stale lock.
    (tmp_path / "daemon.pid").write_text("2000000000\n", encoding="utf-8")
    checks = run_diagnostics(tmp_path)
    lock = _by_name(checks)["lock sanity"]
    assert lock.ok is False
    assert "daemon.pid" in lock.detail
    assert "rm " in lock.fix


def test_live_daemon_lock_is_not_flagged(tmp_path):
    (tmp_path / "daemon.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    checks = run_diagnostics(tmp_path)
    assert _by_name(checks)["lock sanity"].ok is True


def test_project_with_backlog_is_not_empty(tmp_path):
    (tmp_path / "backlog.jsonl").write_text(
        '{"id": "b-1", "title": "do thing"}\n', encoding="utf-8"
    )
    checks = run_diagnostics(tmp_path)
    assert _by_name(checks)["empty session"].ok is True


def test_run_diagnostics_returns_all_five_checks_and_never_raises(tmp_path):
    checks = run_diagnostics(tmp_path)
    names = {c.name for c in checks}
    assert names == {
        "daemon",
        "lock sanity",
        "model API capability",
        "backend preflight",
        "empty session",
    }
    # Every check is a Check with a bool ok and (on failure) a non-empty fix.
    for c in checks:
        assert isinstance(c, Check)
        assert isinstance(c.ok, bool)
        if not c.ok:
            assert c.fix, f"failing check {c.name!r} must carry a fix"


# ---------------------------------------------------------------------------
# backend preflight — must check the CONFIGURED backend, not always "codex"
# ---------------------------------------------------------------------------

def test_backend_preflight_checks_configured_backend_not_always_codex(monkeypatch):
    """Regression: this used to hardcode ``shutil.which("codex")`` regardless
    of ``ARGUS_SKILL_RUNNER_BACKEND``, so an operator running entirely on
    copilot/claude (no ``codex`` npm package installed, by design) got a
    false "codex binary not found" warning on every banner / /doctor run."""
    from argus_skill.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/copilot" if name == "copilot" else None
    )

    check = _check_backend_preflight()
    assert check.ok is True
    assert "copilot" in check.detail
    assert "codex" not in check.detail


def test_backend_preflight_missing_binary_names_the_configured_backend(monkeypatch):
    from argus_skill.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    check = _check_backend_preflight()
    assert check.ok is False
    assert "claude" in check.detail
    assert "claude" in check.fix
    assert "codex" not in check.detail


def test_backend_preflight_defaults_to_codex_with_original_install_hint(
    tmp_path, monkeypatch
):
    """The default (unset) backend keeps the exact original codex message so
    existing operators see no change."""
    from argus_skill.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    check = _check_backend_preflight()
    assert check.ok is False
    assert "codex" in check.detail
    assert "npm install -g @openai/codex" in check.fix


def test_backend_preflight_uses_persisted_copilot_selection(
    tmp_path, monkeypatch
):
    from argus_skill.core.knob_store import write_persisted_knob
    from argus_skill.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    assert write_persisted_knob("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/local/bin/copilot" if name == "copilot" else None,
    )

    check = _check_backend_preflight()

    assert check.ok is True
    assert "copilot backend runnable" in check.detail
    assert "codex" not in check.detail


# ---------------------------------------------------------------------------
# model-API reachability via an injected probe (no real network)
# ---------------------------------------------------------------------------

def test_injected_probe_429_surfaces_switch_backend_fix(tmp_path, monkeypatch):
    # Force a configured route so the offline gate passes, then inject a probe
    # that returns a 429 — the check must recommend switching backend.
    from argus_skill.webapi import diagnostics as doctor_mod

    class _Route:
        usable = True
        model = "gpt-5.5"
        base_url = "https://example.invalid/v1"
        api_key = "sk-test"
        wire_api = "responses"
        name = "engineer"

    # Patch the loader used by both the offline gate and vault_preflight.
    monkeypatch.setattr(
        "argus_skill.tools.capability_vault.load_model_api_route",
        lambda name, env=None: _Route(),
    )

    def fake_probe(base_url, api_key, model, wire_api, *, timeout_s=10.0):
        return (False, 429, "HTTP 429: rate limited")

    checks = run_diagnostics(tmp_path, probe=fake_probe)
    api = _by_name(checks)["model API capability"]
    assert api.ok is False
    assert "429" in api.detail
    assert "switch backend" in api.fix
    # And the rendered recommendation surfaces it (root-cause priority).
    assert "429" in render_report(checks).splitlines()[-1]
    # Sanity: doctor_mod is the module under test.
    assert hasattr(doctor_mod, "run_diagnostics")

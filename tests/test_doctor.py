"""Tests for the Web ``/doctor`` diagnostics backend.

The diagnostics are fully fail-soft and network-free by default: every check
either returns a :class:`Check` or is converted into a failed Check, and the
model-API check only touches the network when an explicit ``probe`` is
injected. These tests run on a tmp project dir with no daemon and never make a
real network call.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from argus.webapi.diagnostics import Check, render_report, run_diagnostics

# ---------------------------------------------------------------------------
# render_report formatting
# ---------------------------------------------------------------------------


def test_render_report_all_green_has_no_recommendation():
    report = render_report([Check("daemon", True, "running (pid 5)", "")])
    assert "all checks passed" in report
    assert "→ recommended:" not in report


def test_doctor_json_uses_stable_codes() -> None:
    from argus.apps.cli._core import _doctor_payload

    payload = _doctor_payload([Check("lock sanity", False, "stale", "repair")])

    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["checks"][0]["code"] == "ARGUS-STATE-001"


def test_safe_repair_removes_only_a_verified_stale_daemon_pid(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from argus.apps.cli import _core
    from argus.maintenance.doctor import DoctorContext

    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text("2000000000\n", encoding="ascii")
    context = DoctorContext(
        global_root=tmp_path / "state",
        project_root=tmp_path,
        checkout=Path(__file__).resolve().parents[1],
        python_executable=Path(sys.executable),
    )
    monkeypatch.setattr(_core, "_maintenance_context", lambda _args: context)

    rc = _core._cmd_repair(
        SimpleNamespace(safe=True, plan=False, json=True)
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc in {0, 3}  # consent/manual findings may remain after SAFE actions
    assert not pid_path.exists()
    stale = next(
        item for item in payload["actions"]
        if item["id"] == "remove_verified_stale_daemon_pid"
    )
    assert stale["status"] == "applied"


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
        [Check("daemon", False, "down", "run: argus --daemon")],
        theme=BrokenTheme(),
    )
    assert "argus doctor" in report
    assert "run: argus --daemon" in report


# ---------------------------------------------------------------------------
# run_diagnostics on a tmp project with no daemon
# ---------------------------------------------------------------------------

def _by_name(checks):
    return {c.name: c for c in checks}


def test_live_daemon_lock_is_not_flagged(tmp_path):
    (tmp_path / "daemon.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    checks = run_diagnostics(tmp_path)
    assert _by_name(checks)["lock sanity"].ok is True


# ---------------------------------------------------------------------------
# backend preflight — must check the CONFIGURED backend, not always "codex"
# ---------------------------------------------------------------------------

def _mock_backend_commands(monkeypatch, version: str) -> None:
    monkeypatch.setattr(
        "argus.core.backend_readiness._run_text",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=version,
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "argus.core.backend_readiness._probe_cli_auth",
        lambda *_args, **_kwargs: (True, ""),
    )


def test_backend_preflight_checks_configured_backend_not_always_codex(monkeypatch):
    """Regression: this used to hardcode ``shutil.which("codex")`` regardless
    of ``ARGUS_SKILL_RUNNER_BACKEND``, so an operator running entirely on
    copilot/claude (no ``codex`` npm package installed, by design) got a
    false "codex binary not found" warning on every banner / /doctor run."""
    from argus.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "copilot")
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/copilot" if name == "copilot" else None
    )
    _mock_backend_commands(monkeypatch, "GitHub Copilot CLI 1.0.74")

    check = _check_backend_preflight()
    assert check.ok is True
    assert "copilot" in check.detail
    assert "codex" not in check.detail


def test_backend_preflight_defaults_to_codex_with_original_install_hint(
    tmp_path, monkeypatch
):
    """The default (unset) backend keeps the exact original codex message so
    existing operators see no change."""
    from argus.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_HOME", str(tmp_path / "argus-home"))
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_LIFE_BACKEND", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr("shutil.which", lambda name: None)

    check = _check_backend_preflight()
    assert check.ok is False
    assert "codex" in check.detail
    assert "@openai/codex" in check.fix


# ---------------------------------------------------------------------------
# model-API reachability via an injected probe (no real network)
# ---------------------------------------------------------------------------

def test_injected_probe_429_surfaces_switch_backend_fix(tmp_path, monkeypatch):
    # Keep backend readiness green independently of the developer machine so
    # the injected 429 remains the root cause under test.
    real_which = shutil.which
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/codex" if name == "codex" else real_which(name),
    )
    _mock_backend_commands(monkeypatch, "codex-cli 0.144.5")
    # Force a configured route so the offline gate passes, then inject a probe
    # that returns a 429 — the check must recommend switching backend.
    from argus.webapi import diagnostics as doctor_mod

    class _Route:
        usable = True
        model = "gpt-5.5"
        base_url = "https://example.invalid/v1"
        api_key = "sk-test"
        wire_api = "responses"
        name = "engineer"

    # Patch the loader used by both the offline gate and vault_preflight.
    monkeypatch.setattr(
        "argus.tools.capability_vault.load_model_api_route",
        lambda name, env=None: _Route(),
    )

    def fake_probe(base_url, api_key, model, wire_api, *, timeout_s=10.0):
        return (False, 429, "HTTP 429: rate limited")

    checks = run_diagnostics(
        tmp_path,
        probe=fake_probe,
        backend="codex",
        auth_mode="model_api",
        probe_auth=False,
        allow_prerelease=True,
    )
    api = _by_name(checks)["model API capability"]
    assert api.ok is False
    assert "429" in api.detail
    assert "switch backend" in api.fix
    # And the rendered recommendation surfaces it (root-cause priority).
    assert "429" in render_report(checks).splitlines()[-1]
    # Sanity: doctor_mod is the module under test.
    assert hasattr(doctor_mod, "run_diagnostics")

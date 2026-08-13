"""Tests for the Web/TUI ``/doctor`` diagnostics backend.

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
from types import SimpleNamespace

from argus_skill.webapi.diagnostics import Check, render_report, run_diagnostics

# ---------------------------------------------------------------------------
# render_report formatting
# ---------------------------------------------------------------------------

def test_render_report_lists_each_check_and_fix_lines():
    checks = [
        Check("daemon", False, "no daemon is running", "run: argus --daemon"),
        Check("lock sanity", True, "no stale lock files", ""),
    ]
    report = render_report(checks)  # theme=None -> plain text
    assert "argus doctor" in report
    assert "✗ daemon" in report
    assert "✓ lock sanity" in report
    # The failing check's fix shows on its own indented line.
    assert "↳ fix: run: argus --daemon" in report
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


def test_doctor_json_uses_stable_codes() -> None:
    from argus_skill.apps.cli._core import _doctor_payload

    payload = _doctor_payload([Check("lock sanity", False, "stale", "repair")])

    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["checks"][0]["code"] == "ARGUS-STATE-001"


def test_safe_repair_removes_only_a_verified_stale_daemon_pid(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from argus_skill.apps.cli import _core

    pid_path = tmp_path / "daemon.pid"
    pid_path.write_text("2000000000\n", encoding="ascii")
    bundle = SimpleNamespace(project=SimpleNamespace(root=tmp_path))
    checks = [Check("lock sanity", False, "stale", "remove")]
    monkeypatch.setattr(_core, "_doctor_checks", lambda _args: (bundle, checks))

    rc = _core._cmd_repair(
        SimpleNamespace(safe=True, plan=False, json=True)
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 3  # mocked verification still reports the pre-repair failure
    assert not pid_path.exists()
    assert payload["actions"] == [{
        "id": "remove_verified_stale_daemon_pid",
        "risk": "safe",
        "target": str(pid_path),
        "status": "applied",
    }]


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
    assert "argus doctor" in report
    assert "run: argus-skill --daemon" in report


# ---------------------------------------------------------------------------
# run_diagnostics on a tmp project with no daemon
# ---------------------------------------------------------------------------

def _by_name(checks):
    return {c.name: c for c in checks}


def test_fresh_idle_session_does_not_require_a_daemon(tmp_path):
    checks = run_diagnostics(tmp_path)
    daemon = _by_name(checks)["daemon"]

    assert daemon.ok is True
    assert "starts lazily" in daemon.detail
    assert daemon.fix == ""


def test_clean_project_has_sane_locks_and_empty_session(tmp_path):
    checks = run_diagnostics(tmp_path)
    by_name = _by_name(checks)
    # No lock files at all -> lock sanity passes.
    assert by_name["lock sanity"].ok is True
    # A brand-new idle session is a valid first-use state, not a failure.
    sess = by_name["empty session"]
    assert sess.ok is True
    assert "ready for the first message" in sess.detail
    assert sess.fix == ""


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


def test_project_with_backlog_requires_an_executor(tmp_path):
    from argus_skill.life.memory import Backlog, BacklogItem

    Backlog(tmp_path / "backlog.jsonl").add(
        BacklogItem.new(item_id="b-1", title="do thing", objective="execute it")
    )
    checks = run_diagnostics(tmp_path)
    by_name = _by_name(checks)

    assert by_name["empty session"].ok is True
    assert by_name["daemon"].ok is False
    assert "argus --daemon" in by_name["daemon"].fix


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

def _mock_backend_commands(monkeypatch, version: str) -> None:
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness._run_text",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=version,
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness._probe_cli_auth",
        lambda *_args, **_kwargs: (True, ""),
    )


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
    _mock_backend_commands(monkeypatch, "GitHub Copilot CLI 1.0.74")

    check = _check_backend_preflight()
    assert check.ok is True
    assert "copilot" in check.detail
    assert "codex" not in check.detail


def test_opencode_preflight_does_not_claim_live_authentication(monkeypatch):
    from argus_skill.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/opencode" if name == "opencode" else None,
    )
    _mock_backend_commands(monkeypatch, "1.2.10")

    check = _check_backend_preflight(backend="opencode")

    assert check.ok is True
    assert "credentials listed; live token not checked" in check.detail
    assert "authentication checked" not in check.detail


def test_backend_preflight_missing_binary_names_the_configured_backend(monkeypatch):
    from argus_skill.webapi.diagnostics import _check_backend_preflight

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "claude")
    monkeypatch.delenv("ARGUS_SKILL_RUNNER_BIN", raising=False)
    monkeypatch.setattr(
        "argus_skill.core.backend_readiness.resolve_runner_bin",
        lambda *_args, **_kwargs: None,
    )

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
    _mock_backend_commands(monkeypatch, "GitHub Copilot CLI 1.0.74")

    check = _check_backend_preflight()

    assert check.ok is True
    assert "copilot 1.0.74 runnable" in check.detail
    assert "codex" not in check.detail


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

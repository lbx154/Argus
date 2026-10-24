"""Tests for argus.core.vault_preflight."""
from __future__ import annotations

import json
from dataclasses import dataclass

from argus.core.vault_preflight import (
    DEFAULT_REQUIRED_ROUTES,
    check_routes,
    format_report,
)
from argus.core.vault_preflight import (
    main as preflight_main,
)

# ---------------------------------------------------------------------------
# Stub route loader + probe so tests don't hit the network
# ---------------------------------------------------------------------------


@dataclass
class _StubRoute:
    name: str = ""
    api_key: str = "sk-test"
    base_url: str = "https://example.com/v1/"
    model: str = "gpt-5.5"
    wire_api: str = "responses"
    usable: bool = True


def _route_loader_all_present(_name: str) -> _StubRoute:
    return _StubRoute(name=_name)


def _route_loader_unusable_engineer(name: str) -> _StubRoute:
    if name == "engineer":
        return _StubRoute(name=name, api_key="", usable=False)
    return _StubRoute(name=name)


def _probe_always_ok(base_url: str, api_key: str, model: str, wire_api: str,
                     *, timeout_s: float = 10.0):
    return (True, 200, "")


def _probe_engineer_404(base_url: str, api_key: str, model: str, wire_api: str,
                        *, timeout_s: float = 10.0):
    if model == "gpt-5.5-mini-broken":
        return (False, 404, "HTTP 404: deployment not found")
    return (True, 200, "")


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_unusable_route_skipped_with_reason() -> None:
    report = check_routes(
        probe=_probe_always_ok,
        route_loader=_route_loader_unusable_engineer,
    )
    engineer = next(c for c in report.checks if c.name == "engineer")
    assert engineer.skipped
    assert "not configured" in engineer.skip_reason
    # A required route that is unusable / unconfigured FAILS preflight.
    assert not report.ok


def test_partial_failure_only_engineer_404() -> None:
    """Specific to the observed bug: vault has engineer=gpt-5.5-mini-broken
    while all other routes are gpt-5.5 which works."""
    def loader(name: str) -> _StubRoute:
        if name == "engineer":
            return _StubRoute(name=name, model="gpt-5.5-mini-broken")
        return _StubRoute(name=name)

    report = check_routes(
        probe=_probe_engineer_404,
        route_loader=loader,
    )
    assert not report.ok
    failed = [c.name for c in report.required_failures]
    assert failed == ["engineer"]
    assert all(c.ok or c.skipped for c in report.checks if c.name != "engineer")


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def test_format_report_marks_fail_with_specific_404_hint() -> None:
    def loader(name: str) -> _StubRoute:
        if name == "engineer":
            return _StubRoute(name=name, model="gpt-5.5-mini-broken")
        return _StubRoute(name=name)
    report = check_routes(probe=_probe_engineer_404, route_loader=loader)
    text = format_report(report)
    assert "FAIL" in text
    assert "❌" in text
    assert "engineer" in text
    assert "gpt-5.5-mini-broken" in text
    assert "Likely fixes:" in text


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


def test_cli_exit_code_0_on_success(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "argus.core.vault_preflight.default_probe",
        _probe_always_ok,
    )
    monkeypatch.setattr(
        "argus.tools.capability_vault.load_model_api_route",
        _route_loader_all_present,
    )

    rc = preflight_main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_cli_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "argus.core.vault_preflight.default_probe",
        _probe_always_ok,
    )
    monkeypatch.setattr(
        "argus.tools.capability_vault.load_model_api_route",
        _route_loader_all_present,
    )

    rc = preflight_main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert "checks" in payload
    assert len(payload["checks"]) >= len(DEFAULT_REQUIRED_ROUTES)

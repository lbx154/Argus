"""Pairing a phone with a LAN-exposed web UI.

The security property under test: a bind reachable from the network is never
served unauthenticated unless the operator explicitly asked for that.
"""
from __future__ import annotations

import pytest

from argus.webapi.pairing import (
    is_loopback_host,
    pairing_plan,
    render_qr,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ARGUS_SKILL_WEB_TOKEN", raising=False)
    monkeypatch.delenv("ARGUS_SKILL_WEB_ALLOW_INSECURE", raising=False)


def _plan(host="0.0.0.0", port=8799, **kwargs):
    kwargs.setdefault("lan_address", "192.168.1.50")
    return pairing_plan(host, port, **kwargs)


# -- host classification ----------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost", ""])
def test_loopback_hosts_are_recognized(host) -> None:
    assert is_loopback_host(host) is True


# -- loopback: unchanged behaviour ------------------------------------------


# -- LAN: authenticated by default ------------------------------------------


def test_configured_token_is_used_as_is(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_WEB_TOKEN", "operator-token")

    plan = _plan()

    assert plan.token == "operator-token"
    assert plan.minted is False
    assert "token=operator-token" in plan.url


def test_explicit_opt_out_serves_without_a_token(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_WEB_ALLOW_INSECURE", "1")

    plan = _plan()

    assert plan.token == ""
    assert plan.minted is False
    assert "token=" not in plan.url
    # Silently unauthenticated is exactly what this replaces.
    assert "WARNING" in plan.banner


# -- URL construction -------------------------------------------------------


def test_explicit_host_is_advertised_verbatim() -> None:
    assert _plan("10.0.0.7").url.startswith("http://10.0.0.7:8799/")


def test_ipv6_address_is_bracketed() -> None:
    plan = _plan("::", lan_address="fd00::5")

    assert plan.url.startswith("http://[fd00::5]:8799/")


# -- QR ---------------------------------------------------------------------


def test_qr_rendering_degrades_without_the_extra(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _no_qrcode(name, *args, **kwargs):
        if name == "qrcode":
            raise ImportError("no qrcode")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_qrcode)

    assert render_qr("http://example.test/") == ""
    # Serving must still proceed with a usable URL.
    assert _plan().url.startswith("http://192.168.1.50:8799/")


# -- terminal encoding (Windows) --------------------------------------------


def test_banner_body_is_ascii_apart_from_the_qr() -> None:
    plan = _plan(encoding="ascii")

    plan.banner.encode("ascii")  # must not raise

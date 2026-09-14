"""Pairing a phone with a LAN-exposed web UI.

The security property under test: a bind reachable from the network is never
served unauthenticated unless the operator explicitly asked for that.
"""
from __future__ import annotations

from types import SimpleNamespace

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


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.50", "10.0.0.2"])
def test_reachable_hosts_are_not_loopback(host) -> None:
    assert is_loopback_host(host) is False


# -- loopback: unchanged behaviour ------------------------------------------

def test_loopback_bind_needs_no_token() -> None:
    plan = pairing_plan("127.0.0.1", 8799)

    assert plan.token == ""
    assert plan.minted is False
    assert plan.url == "http://127.0.0.1:8799/"
    assert plan.qr == ""


def test_alternate_loopback_url_preserves_host() -> None:
    plan = pairing_plan("127.0.0.2", 8799)

    assert plan.token == ""
    assert plan.url == "http://127.0.0.2:8799/"


def test_loopback_bind_still_honours_a_configured_token(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_WEB_TOKEN", "configured")

    assert pairing_plan("127.0.0.1", 8799).token == "configured"


# -- LAN: authenticated by default ------------------------------------------

def test_lan_bind_without_a_token_mints_one() -> None:
    plan = _plan()

    # The control surface for an agent with shell access must not be open.
    assert plan.minted is True
    assert len(plan.token) >= 24
    assert f"token={plan.token}" in plan.url


def test_minted_tokens_differ_between_runs() -> None:
    assert _plan().token != _plan().token


def test_configured_token_is_used_as_is(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_WEB_TOKEN", "operator-token")

    plan = _plan()

    assert plan.token == "operator-token"
    assert plan.minted is False
    assert "token=operator-token" in plan.url


def test_minting_is_announced() -> None:
    banner = _plan().banner

    assert "generated for this run" in banner
    assert "ARGUS_SKILL_WEB_TOKEN" in banner


def test_explicit_opt_out_serves_without_a_token(monkeypatch) -> None:
    monkeypatch.setenv("ARGUS_SKILL_WEB_ALLOW_INSECURE", "1")

    plan = _plan()

    assert plan.token == ""
    assert plan.minted is False
    assert "token=" not in plan.url
    # Silently unauthenticated is exactly what this replaces.
    assert "WARNING" in plan.banner


# -- URL construction -------------------------------------------------------

def test_wildcard_bind_advertises_a_reachable_address() -> None:
    # A phone cannot reach "0.0.0.0"; the URL must name a real interface.
    assert _plan("0.0.0.0").url.startswith("http://192.168.1.50:8799/")


def test_explicit_host_is_advertised_verbatim() -> None:
    assert _plan("10.0.0.7").url.startswith("http://10.0.0.7:8799/")


def test_ipv6_address_is_bracketed() -> None:
    plan = _plan("::", lan_address="fd00::5")

    assert plan.url.startswith("http://[fd00::5]:8799/")


def test_wildcard_bind_falls_back_when_no_address_is_found() -> None:
    plan = _plan("0.0.0.0", lan_address="")

    assert plan.url.startswith("http://127.0.0.1:8799/")


def test_port_is_carried_through() -> None:
    assert ":9001/" in _plan(port=9001).url


# -- QR ---------------------------------------------------------------------

def test_qr_is_offered_or_its_absence_explained() -> None:
    banner = _plan().banner

    assert "Scan to open on your phone" in banner or "argus[qr]" in banner


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


# -- cockpit bridge ---------------------------------------------------------

def test_pair_plan_bridge_emits_what_the_cockpit_needs(capsys) -> None:
    """The Ink cockpit spawns the backend with stdio discarded, so it reads the
    plan from here instead of the child's banner."""
    import json

    from argus.apps.cli._core import main

    assert main(["--pair-plan", "--web-host", "0.0.0.0", "--web-port", "8801"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"token", "url", "banner", "pairing"}
    assert payload["pairing"] is True
    # The cockpit passes this token to the child it spawns; without it the
    # backend would 401 its own UI.
    assert payload["token"]
    assert f"token={payload['token']}" in payload["url"]
    assert ":8801/" in payload["url"]


def test_pair_plan_marks_a_loopback_bind_as_needing_no_pairing(capsys) -> None:
    import json

    from argus.apps.cli._core import main

    assert main(["--pair-plan", "--web-host", "127.0.0.1"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["pairing"] is False
    assert payload["token"] == ""


# -- terminal encoding (Windows) --------------------------------------------

WINDOWS_ENCODINGS = ["cp1252", "cp936", "cp932", "ascii"]


@pytest.mark.parametrize("encoding", WINDOWS_ENCODINGS + ["utf-8"])
def test_banner_survives_the_terminal_encoding(encoding) -> None:
    """A Windows console redirected to a file uses the ANSI code page.

    The banner used to carry a U+2192 arrow, and the QR uses U+00A0 plus block
    glyphs; writing either to a cp1252/cp936 stream raises UnicodeEncodeError
    and aborts `--web` entirely.
    """
    import io

    plan = _plan(encoding=encoding)
    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")

    stream.write(plan.banner)   # must not raise
    stream.flush()


@pytest.mark.parametrize("encoding", WINDOWS_ENCODINGS)
def test_qr_is_dropped_when_the_terminal_cannot_render_it(encoding) -> None:
    plan = _plan(encoding=encoding)

    # A QR with '?' substituted for every white module does not scan; no QR
    # beats a corrupted one, and the URL is still right there.
    assert plan.qr == ""
    assert "cannot show a QR code" in plan.banner
    assert plan.url in plan.banner


def test_qr_is_kept_on_a_utf8_terminal() -> None:
    plan = _plan(encoding="utf-8")

    assert plan.qr
    assert "Scan to open on your phone" in plan.banner


def test_banner_body_is_ascii_apart_from_the_qr() -> None:
    plan = _plan(encoding="ascii")

    plan.banner.encode("ascii")  # must not raise


def test_stream_encoding_falls_back_when_unknown() -> None:
    from argus.webapi.pairing import stream_encoding

    assert stream_encoding(SimpleNamespace(encoding=None)) == "utf-8"
    assert stream_encoding(SimpleNamespace(encoding="cp936")) == "cp936"


def test_encodable_rejects_unknown_codecs() -> None:
    from argus.webapi.pairing import encodable

    assert encodable("plain", "utf-8") is True
    assert encodable("→", "cp1252") is False
    assert encodable("x", "not-a-real-codec") is False

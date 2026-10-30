"""Entry-point absence and load failures are distinct boundary outcomes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from argus.core import hosting


@pytest.fixture(autouse=True)
def clear_providers():
    hosting.hosting.cache_clear()
    hosting.hosted_runtime.cache_clear()
    yield
    hosting.hosting.cache_clear()
    hosting.hosted_runtime.cache_clear()


@pytest.mark.parametrize("lookup,group", [
    (hosting.hosting, "argus.hosting.client"),
    (hosting.hosted_runtime, "argus.hosting"),
])
def test_missing_provider_uses_local_behavior(monkeypatch, lookup, group):
    seen = []
    monkeypatch.setattr(hosting, "entry_points", lambda *, group: seen.append(group) or [])
    assert lookup() is hosting.NO_HOSTING
    assert seen == [group]
    assert hosting.work_pause(".") is None


@pytest.mark.parametrize("lookup,group", [
    (hosting.hosting, "argus.hosting.client"),
    (hosting.hosted_runtime, "argus.hosting"),
])
def test_provider_is_discovered_and_loaded_once(monkeypatch, lookup, group):
    provider = object()
    loaded = []
    entry = SimpleNamespace(load=lambda: loaded.append(True) or provider)
    monkeypatch.setattr(hosting, "entry_points", lambda **kwargs: [entry] if kwargs["group"] == group else [])
    assert lookup() is provider
    assert lookup() is provider
    assert loaded == [True]


@pytest.mark.parametrize("lookup", [hosting.hosting, hosting.hosted_runtime])
@pytest.mark.parametrize("error", [ModuleNotFoundError("provider dependency missing"), RuntimeError("provider failed")])
def test_broken_provider_is_not_treated_as_uninstalled(monkeypatch, lookup, error):
    def broken():
        raise error

    monkeypatch.setattr(hosting, "entry_points", lambda **kwargs: [SimpleNamespace(load=broken)])
    with pytest.raises(type(error), match=str(error)):
        lookup()


@pytest.mark.parametrize("option", ["--trial", "--trial-url"])
def test_removed_setup_options_are_rejected(option):
    from argus.apps.cli import build_parser

    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--setup", option])
    assert exc.value.code == 2

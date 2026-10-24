"""An explicit conversation reset drops the Manager's persistent session pointer."""
from __future__ import annotations


def test_reset_exported_from_manager_package():
    # The web server's conversation reset imports it from the package.
    import argus.manager as m

    assert hasattr(m, "reset_manager_session")
    assert "reset_manager_session" in m.__all__

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop() -> Generator[None, None, None]:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# Skip the vault pre-flight by default in tests — the preflight makes
# real Azure network calls, which is wrong for unit tests. Tests that
# exercise the preflight wire themselves do so via the
# ``check_routes`` DI hooks in argus_skill.core.vault_preflight.
os.environ.setdefault("ARGUS_SKILL_SKIP_VAULT_PREFLIGHT", "1")

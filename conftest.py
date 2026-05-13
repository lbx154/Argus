from __future__ import annotations

import asyncio
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

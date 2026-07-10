"""Optional web/TUI backend API (the ``[web]`` extra).

Thin FastAPI layer over the file-based daemon pub/sub that the Ink terminal
frontend and the React web frontend both consume. See :mod:`.server`.
"""

from __future__ import annotations

__all__ = ["create_app", "serve", "build_snapshot", "project_life_dir"]


def __getattr__(name: str):  # lazy re-export so importing the package never needs fastapi
    if name in __all__:
        from . import server
        return getattr(server, name)
    raise AttributeError(name)

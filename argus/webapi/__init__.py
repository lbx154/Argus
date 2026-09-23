"""Web/TUI backend API: the FastAPI layer the Ink and React frontends consume.

Layer: delivery

Thin FastAPI layer over the file-based daemon pub/sub that the Ink terminal
frontend and the React web frontend both consume. See :mod:`.server`.
``fastapi`` and ``uvicorn`` are hard dependencies (``[project.dependencies]``
in pyproject.toml); there is no ``[web]`` extra. The package-level
``__getattr__`` below still defers importing :mod:`.server` so that importing
``argus.webapi`` itself stays cheap.

Also hosted here today: the ``manager_*`` (bridge, dispatch, pending
question, session intent, state), ``map_*`` (feed, history, model, narrative,
notes, references, team, view) and ``daemon_*`` (lifecycle, liveness,
upgrade) service modules. They are runtime services that happen to be
reached through HTTP; a later service-layer proposal decides where they
live (see docs/LAYOUT.md).
"""

from __future__ import annotations

__all__ = ["create_app", "serve", "build_snapshot", "project_life_dir"]


def __getattr__(name: str):  # lazy re-export so importing the package never needs fastapi
    if name in {"build_snapshot", "project_life_dir"}:
        from . import project_state
        return getattr(project_state, name)
    if name in {"create_app", "serve"}:
        from . import server
        return getattr(server, name)
    raise AttributeError(name)

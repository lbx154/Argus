"""Per-domain FastAPI route registrars used by :func:`argus_skill.webapi.server.create_app`.

Each sibling module exposes a single ``register_*_routes(app, ctx, server_mod)``
function that attaches one API domain's endpoints to the app. This package is
only ever imported lazily from inside ``create_app`` (after FastAPI has
already been imported there), so its modules are free to import ``fastapi`` /
``pydantic`` at module scope without breaking the optional ``[web]`` extra
contract described in :mod:`argus_skill.webapi.server` — importing this
package itself (with no submodule touched) stays free of that requirement.
"""

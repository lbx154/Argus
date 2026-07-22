"""Shared per-request helpers threaded into every route domain registrar.

``ServerContext`` bundles the small pool of closures that ``create_app`` used
to define inline (auth check, project-root resolution, machine-wide project
listing) so each domain module gets identical behavior without duplicating
it. Built once per ``create_app`` call and passed by reference — cheap and
side-effect free to construct.

This module imports ``fastapi`` at module scope. That is safe here because it
is only ever imported lazily, from inside ``create_app`` (see
:mod:`argus_skill.webapi.server`), well after FastAPI has already been
imported there — never from top-level package/module import, so the optional
``[web]`` extra contract is preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import Header, HTTPException

from ...core import paths as core_paths


class ServerContext:
    """Shared state + helpers for one ``create_app`` instance's route domains."""

    def __init__(
        self,
        *,
        global_root: Path | str | None,
        token: str | None,
        roots: list[Path],
        api_meta: dict[str, Any],
        list_projects: Callable[..., list[dict[str, Any]]],
        list_project_costs: Callable[..., list[dict[str, Any]]],
        project_life_dir: Callable[..., Path | None],
    ) -> None:
        self.global_root = global_root
        self.token = token
        self.roots = roots
        self.api_meta = api_meta
        self._list_projects = list_projects
        self._list_project_costs = list_project_costs
        self._project_life_dir = project_life_dir

    def require_auth(self, authorization: str | None = Header(default=None)) -> None:
        if not self.token:
            return  # unauthenticated (localhost-only) mode
        expected = "Bearer " + str(self.token)
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def root_for_project(self, sid: str) -> Path | None:
        for root in self.roots:
            if self._project_life_dir(sid, global_root=root) is not None:
                return root
        return None

    def project_root_or_404(self, sid: str) -> Path:
        root = self.root_for_project(sid)
        if root is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return root

    def resolve_or_404(self, sid: str) -> Path:
        root = self.project_root_or_404(sid)
        life_dir = self._project_life_dir(sid, global_root=root)
        if life_dir is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return life_dir

    def machine_projects(
        self, *, limit: int, include_empty: bool,
    ) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in self.roots:
            try:
                root_session_ids = {
                    path.name
                    for path in core_paths.session_states_root(root).iterdir()
                    if path.is_dir()
                }
            except OSError:
                root_session_ids = set()
            root_limit = limit + len(seen.intersection(root_session_ids))
            for project in self._list_projects(
                global_root=root,
                limit=root_limit,
                include_empty=include_empty,
            ):
                sid = str(project.get("id") or "")
                if not sid or sid in seen:
                    continue
                # The Web sidebar is a session picker, not a raw life-store
                # browser. Legacy cwd-fingerprint/internal project dirs remain
                # resumable through the CLI, but without session.json they have
                # no stable label/workdir contract and surface as mysterious hex
                # rows in investor/demo sessions.
                if (
                    not sid.startswith("s-")
                    and not (
                        core_paths.session_state_root(sid, root=root) / "session.json"
                    ).is_file()
                ):
                    continue
                projects.append(project)
            # Routing uses the first root containing an ID, so reserve every ID
            # from that root even when its session is empty or outside `limit`.
            seen.update(root_session_ids)
        projects.sort(
            key=lambda project: float(project.get("last_active") or 0.0),
            reverse=True,
        )
        return projects[:limit]

    def machine_project_costs(self, *, limit: int) -> list[dict[str, Any]]:
        costs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in self.roots:
            try:
                root_session_ids = {
                    path.name
                    for path in core_paths.session_states_root(root).iterdir()
                    if path.is_dir()
                }
            except OSError:
                root_session_ids = set()
            root_limit = limit + len(seen.intersection(root_session_ids))
            for row in self._list_project_costs(
                global_root=root,
                limit=root_limit,
                include_empty=False,
            ):
                sid = str(row.get("id") or "")
                if not sid or sid in seen:
                    continue
                costs.append(row)
            seen.update(root_session_ids)
        return costs[:limit]

    @staticmethod
    def not_found_if_none(value, sid: str):
        if value is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return value

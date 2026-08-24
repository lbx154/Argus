from __future__ import annotations

from typing import Any

from .argus_webapi import ArgusWebApiClient, ArgusWebApiError

ArgusConnectionError = ArgusWebApiError


class ArgusHttpClient(ArgusWebApiClient):
    """Backward-compatible route client backed by the hardened WebAPI client."""

    def probe(self) -> dict[str, Any]:
        result = self.test_connection()
        return {
            "ok": result.ok,
            "authentication": {
                "required": result.authentication_required,
                "authenticated": result.authenticated,
            },
            "runtime": dict(result.runtime),
        }

    def list_projects(self, *, limit: int = 100, include_empty: bool = False) -> dict[str, Any]:
        return {"projects": super().list_projects(limit=limit, include_empty=include_empty)}

    def create_campaign(
        self, *, objective: str, name: str, workdir: str = "", launch_cwd: str = ""
    ) -> dict[str, Any]:
        if not workdir:
            raise ValueError("an isolated campaign workdir is required")
        return self.create_daemon(
            objective=objective, name=name, workdir=workdir,
            launch_cwd=launch_cwd or workdir,
        )

    def drain(self, project_id: str, *, force: bool = False) -> dict[str, Any]:
        return self.stop(project_id, drain=not force, force=force)

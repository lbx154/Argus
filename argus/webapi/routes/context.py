"""Per-app authentication, root resolution, caches, and query services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import Header, HTTPException

from ...core import paths as core_paths
from ..daemon_services import DaemonServices
from ..index_cache import IndexCache, QueryExecutor, resolve_snapshot_ttl_seconds


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
        list_trashed_projects: Callable[..., list[dict[str, Any]]],
        project_life_dir: Callable[..., Path | None],
        daemon_services: DaemonServices,
        query_executor: QueryExecutor,
    ) -> None:
        self.global_root = global_root
        self.token = token
        self.roots = roots
        self.api_meta = api_meta
        self.daemon_services = daemon_services
        self.query_executor = query_executor
        self._list_projects = list_projects
        self._list_project_costs = list_project_costs
        self._list_trashed_projects = list_trashed_projects
        self._project_life_dir = project_life_dir
        self.index_cache = IndexCache()
        self.snapshot_cache = IndexCache(ttl_seconds=resolve_snapshot_ttl_seconds())

    def invalidate_read_caches(self) -> None:
        """Detach cached and in-flight reads after a successful mutation."""
        self.index_cache.invalidate()
        self.snapshot_cache.invalidate()

    def require_auth(self, authorization: str | None = Header(default=None)) -> None:
        if not self.token:
            return  # unauthenticated (localhost-only) mode
        expected = "Bearer " + str(self.token)
        if authorization != expected:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def authorize_read(self, authorization: str | None, token_param: str | None) -> None:
        """Accept the bearer header or, for a document a sandboxed iframe loads
        by URL, the same token as a query value. An iframe cannot set a header,
        so a token-protected deployment (a hosted portal supplies the header
        itself) still reaches a read-only, self-sandboxing preview page. The
        comparison is constant time; every other route keeps header-only auth."""
        if not self.token:
            return
        import hmac

        expected = "Bearer " + str(self.token)
        if authorization is not None and hmac.compare_digest(authorization, expected):
            return
        if token_param is not None and hmac.compare_digest(str(token_param), str(self.token)):
            return
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
        self,
        *,
        limit: int,
        include_empty: bool,
    ) -> list[dict[str, Any]]:
        return self.index_cache.get(
            ("machine_projects", limit, include_empty),
            lambda: self._machine_projects_uncached(limit=limit, include_empty=include_empty),
        )

    async def machine_projects_async(self, *, limit: int, include_empty: bool) -> list[dict[str, Any]]:
        return await self.index_cache.get_async(
            ("machine_projects", limit, include_empty),
            lambda: self._machine_projects_uncached(limit=limit, include_empty=include_empty),
            executor=self.query_executor,
        )

    def _machine_projects_uncached(
        self,
        *,
        limit: int,
        include_empty: bool,
    ) -> list[dict[str, Any]]:
        projects = []
        for root, project in self._project_rows(
            self._list_projects, limit=limit, include_empty=include_empty,
        ):
            # Hide metadata-free legacy directories unless their daemon is live.
            sid = str(project["id"])
            if (
                not sid.startswith("s-")
                and not (core_paths.session_state_root(sid, root=root) / "session.json").is_file()
                and not bool(project.get("daemon_alive"))
            ):
                continue
            projects.append(project)
        projects.sort(
            key=lambda project: float(project.get("last_active") or 0.0),
            reverse=True,
        )
        return projects[:limit]

    def machine_project_costs(self, *, limit: int) -> list[dict[str, Any]]:
        return self.index_cache.get(
            ("machine_project_costs", limit),
            lambda: self._machine_project_costs_uncached(limit=limit),
        )

    async def machine_project_costs_async(self, *, limit: int) -> list[dict[str, Any]]:
        return await self.index_cache.get_async(
            ("machine_project_costs", limit),
            lambda: self._machine_project_costs_uncached(limit=limit),
            executor=self.query_executor,
        )

    def _machine_project_costs_uncached(self, *, limit: int) -> list[dict[str, Any]]:
        return [
            row for _root, row in self._project_rows(
                self._list_project_costs, limit=limit, include_empty=False,
            )
        ][:limit]

    def _project_rows(
        self, read: Callable[..., list[dict[str, Any]]], *, limit: int, include_empty: bool,
    ) -> Iterator[tuple[Path, dict[str, Any]]]:
        """Apply the same first-root ownership rule to project and cost lists."""
        seen: set[str] = set()
        for root in self.roots:
            try:
                root_ids = {
                    path.name for path in core_paths.session_states_root(root).iterdir()
                    if path.is_dir()
                }
            except OSError:
                root_ids = set()
            for row in read(
                global_root=root, limit=limit + len(seen.intersection(root_ids)),
                include_empty=include_empty,
            ):
                sid = str(row.get("id") or "")
                if sid and sid not in seen:
                    yield root, row
            # Reserve even empty/limited-out sessions: routing also picks the first root.
            seen.update(root_ids)

    def machine_trash(self) -> list[dict[str, Any]]:
        return self.index_cache.get(
            ("machine_trash",),
            self._machine_trash_uncached,
        )

    async def machine_trash_async(self) -> list[dict[str, Any]]:
        return await self.index_cache.get_async(
            ("machine_trash",), self._machine_trash_uncached, executor=self.query_executor,
        )

    def _machine_trash_uncached(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for index, root in enumerate(self.roots):
            for entry in self._list_trashed_projects(global_root=root):
                entries.append(
                    {
                        **entry,
                        "trash_id": f"{index}:{entry['trash_path']}",
                    }
                )
        entries.sort(
            key=lambda entry: float(entry.get("trashed_at") or 0.0),
            reverse=True,
        )
        return entries

    @staticmethod
    def not_found_if_none(value, sid: str):
        if value is None:
            raise HTTPException(status_code=404, detail=f"unknown project: {sid}")
        return value

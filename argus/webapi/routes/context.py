"""Per-app authentication, root resolution, caches, and query services."""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any, Callable, Iterator

from fastapi import HTTPException, Request, Response
from starlette.requests import HTTPConnection

from ...core import paths as core_paths
from ...core.session import durable_session_activity, read_session_meta
from ..daemon_services import DaemonServices
from ..index_cache import DEFAULT_SNAPSHOT_TTL_SECONDS, IndexCache, QueryExecutor

# A browser offers its token as the hex-encoded ``argus.bearer.`` subprotocol
# beside ``argus``, which a project stream selects.
STREAM_PROTOCOL = "argus"
BEARER_PROTOCOL = "argus.bearer."
BROWSER_COOKIE = "argus_browser_access"
# The project list a browser can open; history search covers exactly these.
PROJECT_LIST_LIMIT = 100


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
        self.snapshot_cache = IndexCache(ttl_seconds=DEFAULT_SNAPSHOT_TTL_SECONDS)

    def invalidate_read_caches(self) -> None:
        """Detach cached and in-flight reads after a successful mutation."""
        self.index_cache.invalidate()
        self.snapshot_cache.invalidate()

    def authorized(self, connection: HTTPConnection, cookie: str | None = None) -> bool:
        """Whether a request or WebSocket presents the token (no token: localhost
        mode). A browser cannot set the bearer header on a WebSocket or on a
        document it loads by URL, so it offers the token as a subprotocol or, for
        such a document, as ``cookie``; never in a URL that servers log."""
        if not self.token:
            return True
        offered = [(connection.headers.get("authorization"), "Bearer " + self.token)]
        offered += [(protocol, BEARER_PROTOCOL + self.token.encode().hex())
                    for protocol in connection.scope.get("subprotocols", ())]
        if cookie:
            offered.append((connection.cookies.get(cookie), self.token))
        if any(value is not None and hmac.compare_digest(value.encode(), expected.encode())
               for value, expected in offered):
            return True
        browser = connection.cookies.get(BROWSER_COOKIE)
        if not browser or not hmac.compare_digest(browser.encode(), self.token.encode()):
            return False
        origin = connection.headers.get("origin")
        if connection.scope["type"] == "websocket" or connection.scope.get("method") not in {"GET", "HEAD", "OPTIONS"}:
            scheme = "https" if connection.url.scheme in {"https", "wss"} else "http"
            return origin == str(connection.base_url.replace(scheme=scheme)).rstrip("/")
        return True

    def require_auth(self, request: Request) -> None:
        if not self.authorized(request):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    def grant_cookie(self, request: Request, response: Response, name: str, path: str, *, max_age: int | None = None) -> None:
        """Let documents the browser loads by URL under ``path`` authenticate."""
        if self.token:
            response.set_cookie(name, self.token, httponly=True, samesite="strict",
                                secure=request.url.scheme == "https", path=path, max_age=max_age)

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
        rows = list(self._project_rows(
            self._list_project_costs, limit=limit, include_empty=False,
        ))

        def last_active(item: tuple[Path, dict[str, Any]]) -> float:
            root, row = item
            sid = str(row["id"])
            meta = read_session_meta(root, sid)
            state_path = self._project_life_dir(sid, global_root=root)
            return max(
                meta.last_active if meta is not None else 0.0,
                durable_session_activity(state_path) if state_path is not None else 0.0,
            )

        # Match the project index's newest-active ordering before truncating,
        # using metadata without the index's more expensive daemon status reads.
        rows.sort(key=last_active, reverse=True)
        return [row for _root, row in rows[:limit]]

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

"""Optional plugin center plus dynamically activated, authenticated workbenches."""

import logging
import threading

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ...core import plugin_manager as manager


class PluginSurface:
    def __init__(self, ctx):
        self.ctx = ctx
        self.apps = {}

    async def __call__(self, scope, receive, send):
        name = scope["path_params"]["plugin_id"]
        spec = manager.catalog().get(name)
        plugin = manager.load_plugin(name, self.ctx.global_root) if spec else None
        if plugin is None:
            response = JSONResponse(
                {"detail": "插件未安装或未启用，请前往插件中心。"}, status_code=404
            )
            return await response(scope, receive, send)
        compatible = manager.compatibility(spec)
        if not compatible["supported"]:
            response = JSONResponse({"detail": compatible["reason"]}, status_code=409)
            return await response(scope, receive, send)
        operation = manager.read_json(
            manager.install_root(self.ctx.global_root) / name / "operation.json"
        )
        if (
            scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
            and operation.get("status") == "running"
            and operation.get("action") != "health"
        ):
            response = JSONResponse({"detail": "插件正在更新，请稍候再执行操作。"}, status_code=409)
            return await response(scope, receive, send)
        key = id(plugin)
        if key not in self.apps:
            host = FastAPI()
            plugin.mount(host, self.ctx)
            self.apps[key] = host
        await self.apps[key](scope, receive, send)


def register_plugin_routes(app, ctx):
    @app.on_event("startup")
    def _prepare_declared_plugins():
        """Install what the deployment declares, without holding up the interface.

        Each install is already a background job; the reconciliation itself runs
        on a thread too, so a catalog read or lock wait never delays serving. The
        thread waits for the outcome so the server log records how it ended.
        """
        names = manager.preinstalled_ids()
        if not names:
            return
        logger = logging.getLogger("uvicorn.error")
        logger.info("Preparing the plugins this deployment declares: %s", ", ".join(names))
        threading.Thread(
            target=manager.preinstall,
            args=(ctx.global_root,),
            kwargs={"logger": logger, "wait": True},
            daemon=True,
            name="plugin-preinstall",
        ).start()

    @app.get("/api/plugins", dependencies=[Depends(ctx.require_auth)])
    def list_plugins():
        return {"plugins": manager.plugin_rows(ctx.global_root)}

    @app.post("/api/plugins/{plugin_id}/manage/{action}", dependencies=[Depends(ctx.require_auth)])
    def manage(
        plugin_id: str, action: str, request: Request, payload: dict = Body(default_factory=dict)
    ):
        origin = request.headers.get("origin")
        from urllib.parse import urlparse

        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Cross-origin plugin management refused")
        try:
            if set(payload) - {"username", "password", "paths", "accept_platform_license"}:
                raise ValueError("Unknown plugin setup field")
            if "accept_platform_license" in payload and not isinstance(
                payload["accept_platform_license"], bool
            ):
                raise ValueError("Invalid platform consent")
            if any(
                not isinstance(payload.get(key, ""), str) or len(payload.get(key, "")) > 500
                for key in ("username", "password")
            ):
                raise ValueError("Invalid credential format")
            return manager.mutate(plugin_id, action, ctx.global_root, payload=payload)
        except (manager.PluginError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/plugins/{plugin_id}/launch", dependencies=[Depends(ctx.require_auth)])
    def launch_plugin(plugin_id: str, request: Request):
        spec = manager.catalog().get(plugin_id)
        if not spec or not manager.load_plugin(plugin_id, ctx.global_root):
            raise HTTPException(404, "plugin is not installed or enabled")
        compatible = manager.compatibility(spec)
        if not compatible["supported"]:
            raise HTTPException(409, compatible["reason"])
        response = JSONResponse({"url": f"/plugins/{plugin_id}/"})
        if ctx.token:
            response.set_cookie(
                "argus_plugin_access",
                ctx.token,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https",
                path=f"/api/plugins/{plugin_id}",
            )
        return response

    # Route an installed version on demand, so install/update/uninstall take
    # effect without restarting Argus or disrupting unrelated sessions.
    surface = PluginSurface(ctx)
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    app.router.add_route("/api/plugins/{plugin_id}/{rest:path}", surface, methods=methods)
    app.router.add_route("/plugins/{plugin_id}/{rest:path}", surface, methods=methods)
    app.router.add_route("/plugins/{plugin_id}", surface, methods=methods)

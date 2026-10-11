"""Serve only the selected application's public, startup-validated assets."""

from ...core.application_brand import load_application_brand


def register_application_brand_routes(app):
    from fastapi import HTTPException
    from starlette.responses import JSONResponse, Response

    brand = load_application_brand()
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

    @app.get("/api/application-brand")
    def application_brand():
        return JSONResponse(brand.public, headers=headers)

    @app.get("/api/application-brand/assets/{name}")
    def application_brand_asset(name: str):
        asset = brand.assets.get(name)
        if asset is None:
            raise HTTPException(status_code=404, detail="Unknown application brand asset")
        content, media_type = asset
        return Response(content, media_type=media_type, headers={
            **headers, "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        })

    @app.get("/api/application-brand/manifest.webmanifest")
    def application_brand_manifest():
        return JSONResponse({
            "id": f"/?application={brand.public['id']}",
            "name": brand.public["name"],
            "short_name": brand.public["name"],
            "description": brand.public["tagline"],
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "icons": [
                {"src": brand.public["assets"][name], "type": brand.assets[name][1], "purpose": "any"}
                for name in ("icon", "icon_dark") if name in brand.assets
            ],
        }, media_type="application/manifest+json", headers=headers)

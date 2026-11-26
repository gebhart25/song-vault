"""FastAPI application: the JSON API under /api plus the built SvelteKit app.

Run with:  uvicorn --factory songvault.main:create_app
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, clips, covers, export, labels, lyrics, notes, projects, recordings, trash, uploads
from .config import Settings, load_settings
from .db import connect, migrate
from .jobs import Worker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.db_path)
    try:
        migrate(conn)
    finally:
        conn.close()
    logging.getLogger("songvault").setLevel(logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = Worker(settings) if settings.start_worker else None
        app.state.worker = worker
        if worker:
            worker.start()
        try:
            yield
        finally:
            if worker:
                worker.stop()

    app = FastAPI(
        title="Song Vault",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.login_limiter = auth.LoginLimiter()
    app.state.worker = None

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(auth.router)
    protected = [Depends(auth.require_auth)]
    for module in (projects, covers, lyrics, labels, clips, recordings, uploads, trash, export, notes):
        app.include_router(module.router, dependencies=protected)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def api_not_found(path: str) -> None:
        raise HTTPException(404, "Not found")

    @app.post("/share-target", include_in_schema=False)
    def share_target_fallback() -> RedirectResponse:
        """Android posts shares here. The service worker normally answers it; if it
        isn't running yet (first launch), send the user to a page that explains."""
        return RedirectResponse("/share?missed=1", status_code=303)

    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve the SvelteKit single-page app. Unknown paths get index.html so
    client-side routes like /projects/12 work on reload."""
    root = static_dir.resolve()
    index = root / "index.html"
    if (root / "_app").is_dir():
        app.mount("/_app", StaticFiles(directory=root / "_app"), name="app-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Response:
        if not index.is_file():
            return PlainTextResponse(
                "Frontend not built. Run `npm run build` in frontend/, or use the Vite dev server.",
                status_code=404,
            )
        if path:
            candidate = (root / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                headers = {"Cache-Control": "no-cache"} if candidate.name == "service-worker.js" else None
                return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

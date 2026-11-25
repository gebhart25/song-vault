"""Song pictures on the Projects page (spec 3.1).

A song's card shows, in order: a picture the user picked, or else its newest clip
thumbnail (automatic). A picked picture is one of:
- a stock picture that ships with the app (`/covers/<name>.svg`, listed in the frontend);
- a copy of one of the song's clip thumbnails;
- an image the user uploaded.
The last two are kept in `.covers/` in the media folder, so the nightly offsite backup
has them, under a new name each time (`<project id>-<uid>.jpg`) so they can be cached.
"""

from __future__ import annotations

import shutil
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import media
from .clips import _file
from .config import Settings
from .deps import get_db
from .projects import Cover, get_project_row, project_cover

router = APIRouter(prefix="/api/projects", tags=["covers"])

COVERS_DIR = ".covers"
MAX_UPLOAD = 15 * 1024 * 1024  # bytes; the app shrinks images before sending them
COVER_MAX_SIDE = 1600  # px, longest side of a stored upload


class CoverIn(BaseModel):
    kind: Literal["auto", "stock", "clip"]
    stock: str | None = Field(default=None, pattern=r"^[a-z0-9-]{1,40}$")
    """A stock picture's name (kind stock)."""
    clip_id: int | None = None
    """One of the song's clips (kind clip): its thumbnail is copied."""


def _new_path(project_id: int) -> str:
    return f"{COVERS_DIR}/{project_id}-{media.new_uid()}.jpg"


def _remove(settings: Settings, rel: str | None) -> None:
    if rel:
        try:
            media.resolve(settings.media_dir, rel).unlink(missing_ok=True)
        except (OSError, media.MediaError):
            pass  # a stray file in .covers is harmless


def _save(
    conn: sqlite3.Connection,
    settings: Settings,
    project: sqlite3.Row,
    *,
    stock: str | None = None,
    path: str | None = None,
    clip_id: int | None = None,
) -> Cover:
    """Store the song's new picture choice and delete the file of the old one."""
    try:
        with conn:
            conn.execute(
                "UPDATE projects SET cover_stock = ?, cover_path = ?, cover_clip_id = ? WHERE id = ?",
                (stock, path, clip_id, project["id"]),
            )
    except Exception:
        _remove(settings, path)
        raise
    if project["cover_path"] != path:
        _remove(settings, project["cover_path"])
    return project_cover(conn, project["id"])


@router.put("/{project_id}/cover")
def set_cover(project_id: int, body: CoverIn, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Cover:
    """Automatic, a stock picture, or a copy of one of the song's clip thumbnails."""
    settings: Settings = request.app.state.settings
    project = get_project_row(conn, project_id)
    if body.kind == "auto":
        return _save(conn, settings, project)
    if body.kind == "stock":
        if not body.stock:
            raise HTTPException(422, "Pick a picture")
        return _save(conn, settings, project, stock=body.stock)

    if body.clip_id is None:
        raise HTTPException(422, "Pick a clip")
    clip = conn.execute(
        "SELECT thumbnail_path FROM clips WHERE id = ? AND project_id = ? AND deleted_at IS NULL",
        (body.clip_id, project_id),
    ).fetchone()
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That clip isn't in this song")
    src = media.resolve(settings.media_dir, clip["thumbnail_path"]) if clip["thumbnail_path"] else None
    if src is None or not src.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "That clip doesn't have a thumbnail yet")
    rel = _new_path(project_id)
    dest = media.resolve(settings.media_dir, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)  # a copy: it stays even if the clip's thumbnail changes or goes
    return _save(conn, settings, project, path=rel, clip_id=body.clip_id)


def _is_image(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8\xff")  # JPEG
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _store_upload(conn: sqlite3.Connection, settings: Settings, project_id: int, data: bytes) -> Cover:
    project = get_project_row(conn, project_id)
    if not _is_image(data):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Use a JPEG, PNG or WebP image")
    rel = _new_path(project_id)
    dest = media.resolve(settings.media_dir, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = dest.with_name(f"{dest.stem}.upload")
    src.write_bytes(data)
    try:
        # Re-encoded: at most COVER_MAX_SIDE, and nothing kept from the original file
        # (location or camera details).
        media.run_ffmpeg(
            ["-i", str(src), "-frames:v", "1", "-map_metadata", "-1",
             "-vf", f"{media._fit(COVER_MAX_SIDE)},format=yuvj420p", "-q:v", "3", str(dest)],
            settings.ffmpeg,
            timeout=120,
        )  # fmt: skip
    except media.MediaError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "Song Vault couldn't read that image") from exc
    finally:
        src.unlink(missing_ok=True)
    return _save(conn, settings, project, path=rel)


@router.put("/{project_id}/cover/image")
async def upload_cover(project_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Cover:
    """An image of the user's own, sent as the request body (JPEG, PNG or WebP)."""
    too_big = HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "That image is too big (15 MB at most)")
    try:
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            raise too_big
    except ValueError:
        pass
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD:
            raise too_big
    return await run_in_threadpool(_store_upload, conn, request.app.state.settings, project_id, bytes(data))


@router.get("/{project_id}/cover")
def get_cover(project_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    """The song's own picture (songs in Trash too). Its URL changes with each new picture."""
    row = conn.execute("SELECT cover_path FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return _file(request, row["cover_path"], headers={"Cache-Control": "private, max-age=2592000"})

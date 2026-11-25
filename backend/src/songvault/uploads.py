"""Resumable uploads using the tus 1.0 protocol (core, creation, termination).

Flow (spec 2.4 and 3.2):
1. POST   /api/uploads              create; Upload-Length + Upload-Metadata (filename, project, parts...)
2. PATCH  /api/uploads/{id}         append bytes at Upload-Offset; repeat (survives dropped Wi-Fi)
   HEAD   /api/uploads/{id}         where to resume
3. POST   /api/uploads/{id}/finish  with the SHA-256 the phone computed. The server hashes the
                                     file itself, and only a match becomes a clip ("Stored on server").
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from . import jobs, media
from .clips import ClipDetail, check_labels, clip_detail, touch_project
from .config import Settings
from .db import connect, now_iso
from .deps import get_db

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

TUS_VERSION = "1.0.0"
MAX_UPLOAD_BYTES = 20 * 1024**3
WRITE_BUFFER = 4 * 1024 * 1024
_locks: dict[str, asyncio.Lock] = {}


def _tus(**headers: object) -> dict[str, str]:
    return {"Tus-Resumable": TUS_VERSION, **{k: str(v) for k, v in headers.items()}}


def parse_metadata(header: str | None) -> dict[str, str]:
    """`filename ZmlsZS5tcDQ=,project_id MQ==` -> {"filename": "file.mp4", "project_id": "1"}"""
    out: dict[str, str] = {}
    for pair in (header or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, _, value = pair.partition(" ")
        try:
            out[key] = base64.b64decode(value, validate=True).decode("utf-8") if value else ""
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bad Upload-Metadata for {key}") from exc
    return out


def _part(settings: Settings, upload_id: str) -> Path:
    return settings.uploads_dir / f"{upload_id}.part"


def _get_upload(conn: sqlite3.Connection, upload_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found", headers=_tus())
    return row


def _discard(conn: sqlite3.Connection, settings: Settings, upload_id: str) -> None:
    _part(settings, upload_id).unlink(missing_ok=True)
    with conn:
        conn.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))


# --- tus endpoints ------------------------------------------------------------------


@router.options("")
def tus_options() -> Response:
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers=_tus(**{"Tus-Version": TUS_VERSION, "Tus-Extension": "creation,termination", "Tus-Max-Size": MAX_UPLOAD_BYTES}),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_upload(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Response:
    settings: Settings = request.app.state.settings
    try:
        length = int(request.headers["upload-length"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload-Length is required", headers=_tus()) from exc
    if length <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty", headers=_tus())
    if length > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The file is too large", headers=_tus())

    meta = parse_metadata(request.headers.get("upload-metadata"))
    filename = (meta.get("filename") or "").strip()
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "filename metadata is required", headers=_tus())
    if media.extension(filename) not in media.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Only video and audio files can be uploaded (mp4, mov, webm, m4a, mp3, wav, ...)",
            headers=_tus(),
        )

    project_id: int | None = None
    if meta.get("project_id"):
        try:
            project_id = int(meta["project_id"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad project_id", headers=_tus()) from exc
        if not conn.execute("SELECT 1 FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)).fetchone():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Project not found", headers=_tus())
    try:
        label_ids = [int(x) for x in meta.get("label_ids", "").split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad label_ids", headers=_tus()) from exc
    label_ids = check_labels(conn, project_id, label_ids)

    last_modified = None
    if meta.get("last_modified", "").isdigit():
        ms = int(meta["last_modified"])
        last_modified = datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    upload_id = secrets.token_urlsafe(18)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    _part(settings, upload_id).touch()
    with conn:
        conn.execute(
            """
            INSERT INTO uploads (id, length, filename, filetype, last_modified, timezone, project_id, label_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                upload_id, length, filename[:255], meta.get("filetype", "")[:100], last_modified,
                meta.get("timezone") or None, project_id, json.dumps(label_ids), now_iso(),
            ),
        )  # fmt: skip
    return Response(status_code=status.HTTP_201_CREATED, headers=_tus(Location=f"/api/uploads/{upload_id}"))


@router.head("/{upload_id}")
def upload_offset(upload_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Response:
    row = _get_upload(conn, upload_id)
    if row["completed_at"]:
        offset = row["length"]
    else:
        part = _part(request.app.state.settings, upload_id)
        if not part.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, headers=_tus())
        offset = part.stat().st_size
    return Response(
        status_code=status.HTTP_200_OK,
        headers=_tus(**{"Upload-Offset": offset, "Upload-Length": row["length"], "Cache-Control": "no-store"}),
    )


@router.patch("/{upload_id}")
async def upload_chunk(upload_id: str, request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/offset+octet-stream":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Expected application/offset+octet-stream", headers=_tus())
    try:
        offset = int(request.headers["upload-offset"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload-Offset is required", headers=_tus()) from exc

    def lookup() -> sqlite3.Row:
        conn = connect(settings.db_path)
        try:
            return _get_upload(conn, upload_id)
        finally:
            conn.close()

    row = await run_in_threadpool(lookup)
    if row["completed_at"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload already finished", headers=_tus(**{"Upload-Offset": row["length"]}))

    lock = _locks.setdefault(upload_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(status.HTTP_409_CONFLICT, "This upload is already in progress", headers=_tus())
    async with lock:
        part = _part(settings, upload_id)
        if not part.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found", headers=_tus())
        size = part.stat().st_size
        if offset != size:
            raise HTTPException(status.HTTP_409_CONFLICT, "Wrong Upload-Offset", headers=_tus(**{"Upload-Offset": size}))

        remaining = row["length"] - size
        written = 0
        too_long = False
        buffer = bytearray()
        with part.open("ab") as fh:
            try:
                async for chunk in request.stream():
                    room = remaining - written - len(buffer)
                    if len(chunk) > room:
                        chunk, too_long = chunk[:room], True
                    buffer += chunk
                    if len(buffer) >= WRITE_BUFFER or too_long:
                        await run_in_threadpool(fh.write, bytes(buffer))
                        written += len(buffer)
                        buffer.clear()
                    if too_long:
                        break
            except ClientDisconnect:
                pass  # keep what arrived; the client resumes from the new offset
            if buffer:
                await run_in_threadpool(fh.write, bytes(buffer))
                written += len(buffer)
    if too_long:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "More data than Upload-Length", headers=_tus(**{"Upload-Offset": size + written}))
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_tus(**{"Upload-Offset": size + written}))


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_upload(upload_id: str, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> Response:
    row = _get_upload(conn, upload_id)
    if row["completed_at"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "Upload already finished", headers=_tus())
    _discard(conn, request.app.state.settings, upload_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_tus())


# --- finish: verify, then store as a clip ---------------------------------------------------


class FinishIn(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


@router.post("/{upload_id}/finish")
def finish_upload(
    upload_id: str, body: FinishIn, request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> ClipDetail:
    settings: Settings = request.app.state.settings
    row = _get_upload(conn, upload_id)
    if row["completed_at"] and row["clip_id"]:
        return clip_detail(conn, row["clip_id"])  # finish is safe to repeat

    part = _part(settings, upload_id)
    size = part.stat().st_size if part.exists() else -1
    if size != row["length"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "The upload isn't complete yet")

    digest = sha256_file(part)
    if digest != body.sha256.lower():
        _discard(conn, settings, upload_id)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The file changed on the way to the server (checksum mismatch). Upload it again.",
        )

    # Already uploaded before (and some of it not in Trash)? Keep one copy.
    existing = conn.execute(
        "SELECT c.id FROM clips c JOIN recordings r ON r.id = c.recording_id "
        "WHERE r.sha256 = ? AND r.size_bytes = ? AND c.deleted_at IS NULL ORDER BY c.start_seconds, c.id LIMIT 1",
        (digest, size),
    ).fetchone()
    if existing:
        part.unlink(missing_ok=True)
        with conn:
            conn.execute(
                "UPDATE uploads SET completed_at = ?, clip_id = ? WHERE id = ?", (now_iso(), existing["id"], upload_id)
            )
        return clip_detail(conn, existing["id"], duplicate=True)

    try:
        info = media.probe(part, settings.ffprobe)
    except media.MediaError as exc:
        _discard(conn, settings, upload_id)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    zone = media.get_zone(row["timezone"], settings.default_timezone)
    last_modified = datetime.fromisoformat(row["last_modified"]) if row["last_modified"] else None
    recorded, source = media.resolve_recorded_at(
        meta=info.creation_time,
        filename=row["filename"],
        duration=info.duration,
        last_modified=last_modified,
        zone=zone,
    )

    project = None
    if row["project_id"] is not None:
        project = conn.execute(
            "SELECT id, slug FROM projects WHERE id = ? AND deleted_at IS NULL", (row["project_id"],)
        ).fetchone()
    project_id = project["id"] if project else None
    label_ids = json.loads(row["label_ids"]) if project or not row["project_id"] else []

    uid = media.new_uid()
    stamp = (recorded or datetime.now(UTC)).astimezone(zone)
    rel = f"{media.folder_for(project['slug'] if project else None)}/{media.original_name(stamp, uid, media.extension(row['filename']))}"
    dest = media.resolve(settings.media_dir, rel)
    media.move_file(part, dest)

    now = now_iso()
    recorded_iso = recorded.isoformat(timespec="milliseconds").replace("+00:00", "Z") if recorded else None
    try:
        with conn:
            # The file is a recording; the clip is all of it (spec 5.10). They share a uid.
            cur = conn.execute(
                """
                INSERT INTO recordings (uid, original_filename, media_type, duration_seconds, width, height,
                  video_codec, is_hdr, recorded_at, recorded_at_source, uploaded_at, size_bytes, sha256,
                  verified_at, original_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid, row["filename"], info.media_type, info.duration, info.width, info.height,
                    info.video_codec, int(info.is_hdr) if info.media_type == "video" else None,
                    recorded_iso, source, now, size, digest, now, rel,
                ),
            )  # fmt: skip
            recording_id = cur.lastrowid
            cur = conn.execute(
                """
                INSERT INTO clips (uid, project_id, recorded_at, recorded_at_source, uploaded_at, media_type,
                  duration_seconds, recording_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, project_id, recorded_iso, source, now, info.media_type, info.duration, recording_id),
            )
            clip_id = cur.lastrowid
            valid = set(label_ids) & {
                r[0] for r in conn.execute("SELECT id FROM labels WHERE project_id IS NULL OR project_id = ?", (project_id,))
            }
            conn.executemany("INSERT INTO clip_labels (clip_id, label_id) VALUES (?, ?)", [(clip_id, lid) for lid in sorted(valid)])
            jobs.enqueue_recording(conn, recording_id, info.media_type)
            jobs.enqueue_clip(conn, clip_id, info.media_type)
            conn.execute("UPDATE uploads SET completed_at = ?, clip_id = ? WHERE id = ?", (now, clip_id, upload_id))
            touch_project(conn, project_id)
    except Exception:
        media.move_file(dest, part)  # back to the partial upload so finish can be retried
        raise

    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return clip_detail(conn, clip_id)

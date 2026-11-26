"""Trash (spec 3.1, 3.2): deleted songs and recordings, restorable for 30 days,
then deleted for good, files included. Notebook notes are listed here too (spec 5.11);
notes.py restores and purges them.

Deleting only sets `deleted_at`. A song in Trash takes its recordings with it;
restoring the song brings them back. The nightly offsite backup moves files that
disappear from the server into a dated archive folder, so a purge doesn't erase
the offsite copy.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import media
from .config import Settings
from .db import connect
from .deps import get_db
from .projects import (
    CLIP_COLUMNS,
    CLIP_FROM,
    NEWEST_THUMB_SQL,
    ClipSummary,
    ProjectStatus,
    clip_summary,
    cover_of,
    touch_song_notes,
)

log = logging.getLogger("songvault.trash")

TRASH_DAYS = 30

router = APIRouter(prefix="/api/trash", tags=["trash"])


class TrashProject(BaseModel):
    id: int
    name: str
    status: ProjectStatus
    clip_count: int
    cover_thumbnail_url: str | None
    deleted_at: str
    purge_at: str
    """When it's deleted for good."""


class TrashClip(ClipSummary):
    project_id: int | None
    project_name: str | None
    project_in_trash: bool
    """The clip's song is in Trash too: restore the song first."""
    deleted_at: str
    purge_at: str


class TrashNote(BaseModel):
    uid: str
    body: str
    deleted_at: str
    purge_at: str


class Trash(BaseModel):
    days: int
    projects: list[TrashProject]
    clips: list[TrashClip]
    notes: list[TrashNote]


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def purge_at(deleted_at: str) -> str:
    deleted = datetime.fromisoformat(deleted_at.replace("Z", "+00:00"))
    return _iso(deleted + timedelta(days=TRASH_DAYS))


@router.get("")
def list_trash(conn: sqlite3.Connection = Depends(get_db)) -> Trash:
    projects = conn.execute(
        f"""
        SELECT p.*,
          (SELECT count(*) FROM clips c WHERE c.project_id = p.id AND c.deleted_at IS NULL) AS clip_count,
          {NEWEST_THUMB_SQL}
        FROM projects p WHERE p.deleted_at IS NOT NULL
        ORDER BY p.deleted_at DESC
        """
    ).fetchall()
    clips = conn.execute(
        f"""
        SELECT {CLIP_COLUMNS}, p.name AS project_name, p.deleted_at AS project_deleted_at
        FROM {CLIP_FROM} LEFT JOIN projects p ON p.id = c.project_id
        WHERE c.deleted_at IS NOT NULL
        ORDER BY c.deleted_at DESC, c.id DESC
        LIMIT 1000
        """
    ).fetchall()
    notes = conn.execute(
        "SELECT uid, body, deleted_at FROM notes WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC LIMIT 1000"
    ).fetchall()
    return Trash(
        days=TRASH_DAYS,
        projects=[
            TrashProject(
                id=p["id"],
                name=p["name"],
                status=p["status"],
                clip_count=p["clip_count"],
                cover_thumbnail_url=cover_of(p).url,
                deleted_at=p["deleted_at"],
                purge_at=purge_at(p["deleted_at"]),
            )
            for p in projects
        ],
        clips=[
            TrashClip(
                **clip_summary(c).model_dump(),
                project_id=c["project_id"],
                project_name=c["project_name"],
                project_in_trash=c["project_deleted_at"] is not None,
                deleted_at=c["deleted_at"],
                purge_at=purge_at(c["deleted_at"]),
            )
            for c in clips
        ],
        notes=[
            TrashNote(uid=n["uid"], body=n["body"], deleted_at=n["deleted_at"], purge_at=purge_at(n["deleted_at"]))
            for n in notes
        ],
    )


def _remove_files(settings: Settings, paths: list[str | None]) -> None:
    """Delete files (a playback copy can be the original itself, so each path once)."""
    for rel in set(paths) - {None}:
        try:
            media.resolve(settings.media_dir, rel).unlink(missing_ok=True)
        except (OSError, media.MediaError):
            log.exception("could not delete %s", rel)


def purge_trash(settings: Settings, now: datetime | None = None) -> tuple[int, int]:
    """Delete for good what has been in Trash for 30 days: songs (with all their
    recordings) and single recordings. Returns (songs, recordings) deleted."""
    cutoff = _iso((now or datetime.now(UTC)) - timedelta(days=TRASH_DAYS))
    conn = connect(settings.db_path)
    try:
        projects = conn.execute(
            "SELECT id, slug, cover_path FROM projects WHERE deleted_at IS NOT NULL AND deleted_at < ?", (cutoff,)
        ).fetchall()
        project_ids = [p["id"] for p in projects]
        clips = conn.execute(
            f"""
            SELECT * FROM clips
            WHERE (deleted_at IS NOT NULL AND deleted_at < ?)
               OR project_id IN ({", ".join("?" * len(project_ids)) or "NULL"})
            """,
            (cutoff, *project_ids),
        ).fetchall()

        # Rows first, then files: a failed file delete only leaves a stray file behind.
        for clip in clips:
            with conn:
                conn.execute("DELETE FROM clips WHERE id = ?", (clip["id"],))
            _remove_files(settings, [clip[c] for c in media.CLIP_FILES])
        # A recording goes once none of its clips are left (spec 5.10): pieces of it in
        # other songs, or still in Trash, keep it.
        for recording in conn.execute(
            "SELECT * FROM recordings r WHERE NOT EXISTS (SELECT 1 FROM clips c WHERE c.recording_id = r.id)"
        ).fetchall():
            with conn:
                conn.execute("DELETE FROM recordings WHERE id = ?", (recording["id"],))
            _remove_files(settings, [recording[c] for c in media.RECORDING_FILES])
        for project in projects:
            with conn:
                touch_song_notes(conn, project["id"])  # its notes lose the link (spec 5.11)
                conn.execute("DELETE FROM projects WHERE id = ?", (project["id"],))
            _remove_files(settings, [project["cover_path"]])  # its picture (spec 3.1)
            folder = settings.media_dir / media.folder_for(project["slug"])
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                log.exception("could not remove %s", folder)
        if clips or projects:
            log.info("trash: deleted %d songs and %d recordings for good", len(projects), len(clips))
        return len(projects), len(clips)
    finally:
        conn.close()

"""Clips: details, editing, the Uncategorized list, upload history and media files.

A clip is a stretch of a recording (spec 5.10): the recording is the uploaded file and
its whole-file copies; the clip is where it starts and ends, plus everything about the
take (song, parts, status, role, notes...). Most queries join the two (CLIP_COLUMNS).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from . import jobs, media
from .config import Settings
from .db import now_iso
from .deps import get_db
from .projects import (
    CLIP_COLUMNS,
    CLIP_FROM,
    PROCESSING_SQL,
    ClipStatus,
    ClipSummary,
    clean_chords,
    clean_text,
    clip_summary,
    get_project_row,
)

router = APIRouter(prefix="/api/clips", tags=["clips"])

# A clip that starts and ends within this of the recording's ends covers all of it.
WHOLE_SLACK = 0.05


# --- models ---------------------------------------------------------------------


class JobInfo(BaseModel):
    type: str
    state: str
    error: str | None


class ClipDetail(ClipSummary):
    notes: str
    original_filename: str
    recorded_at_source: str | None
    size_bytes: int
    sha256: str
    verified_at: str | None
    project_id: int | None
    project_name: str | None
    label_ids: list[int]
    width: int | None
    height: int | None
    video_codec: str | None
    is_hdr: bool
    media_url: str | None
    """The recording's playable copy (the player keeps to the clip's stretch); None while it's being made."""
    original_url: str
    audio_url: str | None
    """Just the sound (spec 5.4): the audio-only copy of a video, or an audio clip's playable copy."""
    cut_url: str | None
    """This stretch as a file (spec 5.10); None when the clip is the whole recording."""
    trimmed: bool
    """The clip is less than the whole recording."""
    pieces: int
    """Clips made from the same recording, this one included (not those in Trash)."""
    thumbnail_at: float | None
    """The chosen poster frame, seconds into the clip; None = automatic."""
    deleted_at: str | None
    jobs: list[JobInfo]
    # Details of this take, when they differ from the song or aren't in the song notes (spec 5.2).
    song_key: str | None
    bpm: float | None
    time_signature: str | None
    capo: int | None
    tuning: str | None
    progressions: list[list[str]]
    """Chord progressions without labels: the clip's parts say where they go."""
    duplicate: bool = False
    """Set by an upload whose file was already in Song Vault."""


class InboxClip(ClipSummary):
    project_id: int | None
    project_name: str | None


class HistoryItem(BaseModel):
    id: int
    """The recording (uploaded file)."""
    original_filename: str
    recorded_at: str | None
    uploaded_at: str
    size_bytes: int
    sha256: str
    verified_at: str | None
    project_name: str | None
    deleted: bool
    """None of its clips are left outside Trash."""
    backed_up: bool
    """In the offsite backup: safe to delete from the phone (spec 2.4)."""


class History(BaseModel):
    last_backup_at: str | None
    items: list[HistoryItem]


class ClipUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=100_000)
    status: ClipStatus | None = None
    recorded_at: datetime | None = None
    project_id: int | None = None
    label_ids: list[int] | None = None
    song_key: str | None = Field(default=None, max_length=40)
    bpm: float | None = Field(default=None, gt=0, le=400)
    time_signature: str | None = Field(default=None, max_length=10)
    capo: int | None = Field(default=None, ge=0, le=24)
    tuning: str | None = Field(default=None, max_length=80)
    progressions: list[list[str]] | None = Field(default=None, max_length=20)
    role: str | None = Field(default=None, max_length=40)
    """Rhythm, Lead, Vocals...; blank or null clears it."""
    thumbnail_at: float | None = Field(default=None, ge=0)
    """Use the frame this many seconds into the clip as its thumbnail; null = automatic."""

    @field_validator("song_key", "time_signature", "tuning", "role")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("progressions")
    @classmethod
    def check_progressions(cls, value: list[list[str]] | None) -> list[list[str]] | None:
        if value is None:
            return None
        for chords in value:
            if not 1 <= len(chords) <= 64:
                raise ValueError("A progression needs 1 to 64 chords")
        return [clean_chords(chords) for chords in value]


DETAIL_COLUMNS = ("song_key", "bpm", "time_signature", "capo", "tuning")


class BulkIds(BaseModel):
    clip_ids: list[int] = Field(min_length=1, max_length=500)


class BulkUpdate(BulkIds):
    """The same change for many clips at once (spec 5.5). Leave a field out to keep
    each clip's own value."""

    project_id: int | None = None
    """Move to this song; null moves to Uncategorized."""
    add_label_ids: list[int] = Field(default_factory=list, max_length=100)
    remove_label_ids: list[int] = Field(default_factory=list, max_length=100)
    status: Literal["idea", "used", "discarded"] | None = None
    """Current isn't offered: it's one take per part and role, so it's set on one clip at a time."""
    role: str | None = Field(default=None, max_length=40)
    """Give them all this role; null takes it away."""

    @field_validator("role")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return clean_text(value)


class BulkResult(BaseModel):
    updated: int


class Summary(BaseModel):
    uncategorized: int
    """Clips that aren't in any project yet."""
    uncategorized_cover_url: str | None
    uncategorized_updated_at: str | None
    """When the newest uncategorized clip was uploaded."""
    processing: int
    trash: int
    """Songs and recordings in Trash."""


# --- helpers ----------------------------------------------------------------------


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shifted(recorded_at: str | None, seconds: float) -> str | None:
    """A recording's start time plus `seconds`: when a stretch of it was played."""
    dt = _parse_iso(recorded_at)
    return iso(dt + timedelta(seconds=seconds)) if dt else None


def read_backup_marker(settings: Settings) -> tuple[datetime, datetime] | None:
    """(started_at, finished_at) of the last successful offsite backup, if any."""
    try:
        data = json.loads(settings.backup_marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    started, finished = _parse_iso(data.get("started_at")), _parse_iso(data.get("finished_at"))
    return (started, finished) if started and finished else None


def get_clip_row(conn: sqlite3.Connection, clip_id: int, *, include_deleted: bool = False) -> sqlite3.Row:
    row = conn.execute(f"SELECT {CLIP_COLUMNS} FROM {CLIP_FROM} WHERE c.id = ?", (clip_id,)).fetchone()
    if row is None or (row["deleted_at"] and not include_deleted):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip not found")
    return row


def is_whole(start: float, end: float | None, duration: float | None) -> bool:
    return start <= WHOLE_SLACK and (end is None or duration is None or end >= duration - WHOLE_SLACK)


def clip_detail(conn: sqlite3.Connection, clip_id: int, *, duplicate: bool = False) -> ClipDetail:
    row = conn.execute(
        f"""
        SELECT {CLIP_COLUMNS}, p.name AS project_name, {PROCESSING_SQL},
          (SELECT count(*) FROM clips s WHERE s.recording_id = c.recording_id AND s.deleted_at IS NULL) AS pieces
        FROM {CLIP_FROM} LEFT JOIN projects p ON p.id = c.project_id
        WHERE c.id = ?
        """,
        (clip_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clip not found")
    label_ids = [r[0] for r in conn.execute("SELECT label_id FROM clip_labels WHERE clip_id = ? ORDER BY label_id", (clip_id,))]
    job_rows = conn.execute(
        "SELECT type, state, error FROM jobs WHERE recording_id = ? OR clip_id = ? ORDER BY id",
        (row["recording_id"], clip_id),
    ).fetchall()
    summary = clip_summary(row)
    whole = is_whole(row["start_seconds"], row["end_seconds"], row["rec_duration"])
    return ClipDetail(
        **summary.model_dump(),
        notes=row["notes"],
        original_filename=row["original_filename"],
        recorded_at_source=row["recorded_at_source"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        verified_at=row["verified_at"],
        project_id=row["project_id"],
        project_name=row["project_name"],
        label_ids=label_ids,
        width=row["width"],
        height=row["height"],
        video_codec=row["video_codec"],
        is_hdr=bool(row["is_hdr"]),
        media_url=f"/api/clips/{clip_id}/media" if row["playback_path"] else None,
        original_url=f"/api/clips/{clip_id}/original",
        audio_url=(
            f"/api/clips/{clip_id}/audio"
            if row["audio_path"] or (row["media_type"] == "audio" and row["playback_path"])
            else None
        ),
        cut_url=None if whole else f"/api/clips/{clip_id}/cut",
        trimmed=not whole,
        pieces=row["pieces"],
        thumbnail_at=row["thumbnail_at"],
        deleted_at=row["deleted_at"],
        jobs=[JobInfo(type=j["type"], state=j["state"], error=j["error"]) for j in job_rows],
        song_key=row["song_key"],
        bpm=row["bpm"],
        time_signature=row["time_signature"],
        capo=row["capo"],
        tuning=row["tuning"],
        progressions=json.loads(row["progressions"]),
        duplicate=duplicate,
    )


def labels_in_scope(conn: sqlite3.Connection, project_id: int | None) -> set[int]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT id FROM labels WHERE project_id IS NULL OR project_id = ?", (project_id,)
        )
    }


def check_labels(conn: sqlite3.Connection, project_id: int | None, label_ids: list[int]) -> list[int]:
    unknown = set(label_ids) - labels_in_scope(conn, project_id)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown part ids: {sorted(unknown)}")
    return sorted(set(label_ids))


def replace_current(conn: sqlite3.Connection, project_id: int, clip_id: int) -> None:
    """One Current take per part and role (spec 5.1): clips in the same song that share
    a part with `clip_id`, have the same role (or none, like it) and were Current go
    back to Idea. A Rhythm and a Lead take of the same solo can both be Current."""
    conn.execute(
        """
        UPDATE clips SET status = 'idea'
        WHERE project_id = :pid AND id != :cid AND status = 'current' AND deleted_at IS NULL
          AND lower(coalesce(role, '')) = (SELECT lower(coalesce(role, '')) FROM clips WHERE id = :cid)
          AND EXISTS (
            SELECT 1 FROM clip_labels mine JOIN clip_labels theirs ON theirs.label_id = mine.label_id
            WHERE mine.clip_id = :cid AND theirs.clip_id = clips.id)
        """,
        {"pid": project_id, "cid": clip_id},
    )


def touch_project(conn: sqlite3.Connection, project_id: int | None) -> None:
    if project_id is not None:
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_iso(), project_id))


def _move_files(settings: Settings, row: sqlite3.Row, columns: tuple[str, ...], folder: str) -> tuple[dict[str, str | None], list[tuple]]:
    """Move the files in `columns` of `row` into `folder`. Returns the new paths and the
    moves done (for undo)."""
    new_paths: dict[str, str | None] = {}
    done: list[tuple] = []
    moved: dict[str, str] = {}
    try:
        for column in columns:
            rel = row[column]
            if rel is None:
                new_paths[column] = None
                continue
            if rel in moved:  # playback copy can be the original itself
                new_paths[column] = moved[rel]
                continue
            target = f"{folder}/{rel.rsplit('/', 1)[-1]}"
            src, dest = media.resolve(settings.media_dir, rel), media.resolve(settings.media_dir, target)
            if src != dest and src.exists():
                media.move_file(src, dest)
                done.append((dest, src))
            moved[rel] = target
            new_paths[column] = target
    except Exception:
        undo_moves(done)
        raise
    return new_paths, done


def undo_moves(done: list[tuple]) -> None:
    for dest, src in reversed(done):
        try:
            media.move_file(dest, src)
        except Exception:  # noqa: BLE001 - best effort
            pass


def recording_is_shared(conn: sqlite3.Connection, recording_id: int) -> bool:
    """Has the recording been split (pieces in Trash count: they can come back)?"""
    return conn.execute("SELECT count(*) FROM clips WHERE recording_id = ?", (recording_id,)).fetchone()[0] > 1


# --- list views (static paths first) ---------------------------------------------------


UNCATEGORIZED_SQL = "c.deleted_at IS NULL AND c.project_id IS NULL"


@router.get("")
def list_clips(
    view: Literal["uncategorized", "trash"] = "uncategorized",
    conn: sqlite3.Connection = Depends(get_db),
) -> list[InboxClip]:
    """uncategorized: clips that aren't in a project yet (spec 3.3). Clips in a
    project without a part show on that song's page instead.
    trash: deleted clips."""
    if view == "uncategorized":
        where = UNCATEGORIZED_SQL
        order = "coalesce(c.recorded_at, c.uploaded_at) DESC, c.id DESC"
    else:
        where = "c.deleted_at IS NOT NULL"
        order = "c.deleted_at DESC"
    rows = conn.execute(
        f"""
        SELECT {CLIP_COLUMNS}, p.name AS project_name, {PROCESSING_SQL}
        FROM {CLIP_FROM} LEFT JOIN projects p ON p.id = c.project_id
        WHERE {where}
        ORDER BY {order}
        LIMIT 1000
        """
    ).fetchall()
    return [
        InboxClip(**clip_summary(r).model_dump(), project_id=r["project_id"], project_name=r["project_name"])
        for r in rows
    ]


@router.get("/roles")
def list_roles(conn: sqlite3.Connection = Depends(get_db)) -> list[str]:
    """Roles given to clips so far, most used first, to offer next to the usual ones."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT role FROM clips WHERE role IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY lower(role) ORDER BY count(*) DESC, role"
        )
    ]


@router.get("/summary")
def clips_summary(conn: sqlite3.Connection = Depends(get_db)) -> Summary:
    count, updated_at = conn.execute(
        f"SELECT count(*), max(c.uploaded_at) FROM clips c WHERE {UNCATEGORIZED_SQL}"
    ).fetchone()
    cover = conn.execute(
        f"""
        SELECT c.id FROM clips c WHERE {UNCATEGORIZED_SQL} AND c.thumbnail_path IS NOT NULL
        ORDER BY coalesce(c.recorded_at, c.uploaded_at) DESC, c.id DESC LIMIT 1
        """
    ).fetchone()
    processing = conn.execute(
        "SELECT count(DISTINCT coalesce('r' || recording_id, 'c' || clip_id)) FROM jobs "
        "WHERE state IN ('queued', 'running') AND type IN ('playback', 'thumbnail')"
    ).fetchone()[0]
    in_trash = conn.execute(
        "SELECT (SELECT count(*) FROM clips WHERE deleted_at IS NOT NULL)"
        " + (SELECT count(*) FROM projects WHERE deleted_at IS NOT NULL)"
    ).fetchone()[0]
    return Summary(
        uncategorized=count,
        uncategorized_cover_url=f"/api/clips/{cover[0]}/thumbnail" if cover else None,
        uncategorized_updated_at=updated_at,
        processing=processing,
        trash=in_trash,
    )


@router.get("/history")
def upload_history(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    conn: sqlite3.Connection = Depends(get_db),
) -> History:
    """Newest uploads (files) first: what is stored and verified on the server, and what
    is also in the offsite backup and so safe to delete from the phone (spec 2.4)."""
    marker = read_backup_marker(_settings(request))
    rows = conn.execute(
        """
        SELECT r.*,
          (SELECT p.name FROM clips c JOIN projects p ON p.id = c.project_id
           WHERE c.recording_id = r.id AND c.deleted_at IS NULL AND p.deleted_at IS NULL
           ORDER BY c.id LIMIT 1) AS project_name,
          NOT EXISTS (SELECT 1 FROM clips c LEFT JOIN projects p ON p.id = c.project_id
                      WHERE c.recording_id = r.id AND c.deleted_at IS NULL AND p.deleted_at IS NULL) AS gone
        FROM recordings r
        ORDER BY r.uploaded_at DESC, r.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items = [
        HistoryItem(
            id=r["id"],
            original_filename=r["original_filename"],
            recorded_at=r["recorded_at"],
            uploaded_at=r["uploaded_at"],
            size_bytes=r["size_bytes"],
            sha256=r["sha256"],
            verified_at=r["verified_at"],
            project_name=r["project_name"],
            deleted=bool(r["gone"]),
            # Files verified before the backup started were part of that run.
            backed_up=bool(marker and (v := _parse_iso(r["verified_at"])) and v < marker[0]),
        )
        for r in rows
    ]
    last = marker[1].isoformat(timespec="seconds").replace("+00:00", "Z") if marker else None
    return History(last_backup_at=last, items=items)


# --- many clips at once (spec 5.5) ----------------------------------------------------------


def _bulk_rows(conn: sqlite3.Connection, clip_ids: list[int]) -> list[sqlite3.Row]:
    ids = list(dict.fromkeys(clip_ids))
    rows = conn.execute(
        f"SELECT {CLIP_COLUMNS} FROM {CLIP_FROM} WHERE c.deleted_at IS NULL AND c.id IN ({', '.join('?' * len(ids))})",
        ids,
    ).fetchall()
    if len(rows) != len(ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Some of these clips are gone. Reload and try again.")
    return rows


@router.post("/bulk")
def bulk_update(body: BulkUpdate, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> BulkResult:
    """Move many clips to a song, add or take away a part, or set their status. All or nothing."""
    fields = body.model_fields_set
    if "status" in fields and body.status is None:
        raise HTTPException(422, "status can't be empty")
    clips = _bulk_rows(conn, body.clip_ids)
    add, remove = set(body.add_label_ids), set(body.remove_label_ids)

    settings = _settings(request)
    plans: list[tuple[sqlite3.Row, dict, Plan]] = []
    moves: list[tuple] = []
    try:
        for clip in clips:
            changes: dict[str, object] = {}
            if "project_id" in fields:
                changes["project_id"] = body.project_id
            if body.status is not None:
                changes["status"] = body.status
            if "role" in fields:
                changes["role"] = body.role
            if add or remove:
                target = changes.get("project_id", clip["project_id"])
                scope = labels_in_scope(conn, target)
                have = {r[0] for r in conn.execute("SELECT label_id FROM clip_labels WHERE clip_id = ?", (clip["id"],))}
                changes["label_ids"] = sorted(((have & scope) | add) - remove)
            plan = _prepare_update(conn, settings, clip, changes)
            moves.extend(plan.moves)
            plans.append((clip, changes, plan))
        with conn:
            for clip, changes, plan in plans:
                _write_update(conn, clip, changes, plan)
    except Exception:
        undo_moves(moves)
        raise
    return BulkResult(updated=len(clips))


@router.post("/bulk/trash")
def bulk_trash(body: BulkIds, conn: sqlite3.Connection = Depends(get_db)) -> BulkResult:
    """Move many clips to Trash (spec 3.2)."""
    clips = _bulk_rows(conn, body.clip_ids)
    deleted_at = now_iso()
    with conn:
        conn.executemany("UPDATE clips SET deleted_at = ? WHERE id = ?", [(deleted_at, c["id"]) for c in clips])
        for project_id in {c["project_id"] for c in clips}:
            touch_project(conn, project_id)
    return BulkResult(updated=len(clips))


# --- single clip -------------------------------------------------------------------------


@router.get("/{clip_id}")
def get_clip(clip_id: int, conn: sqlite3.Connection = Depends(get_db)) -> ClipDetail:
    get_clip_row(conn, clip_id, include_deleted=True)
    return clip_detail(conn, clip_id)


class Plan:
    """What `_prepare_update` worked out for one clip, for `_write_update`."""

    def __init__(self) -> None:
        self.clip: dict[str, object] = {}
        self.recording: dict[str, object] = {}
        self.moves: list[tuple] = []
        self.new_project: int | None = None
        self.recorded_at: str | None = None
        self.thumbnail = False


def _prepare_update(conn: sqlite3.Connection, settings: Settings, clip: sqlite3.Row, changes: dict) -> Plan:
    """The column updates for `changes`. A clip that changes song has its files moved
    here, before anything is written: the whole recording's while it has just this one
    clip, else only the clip's thumbnail (the recording stays where it is)."""
    plan = Plan()
    for column in ("title", "notes", "status", "role", *DETAIL_COLUMNS):
        if column in changes:
            plan.clip[column] = changes[column].strip() if column == "title" else changes[column]
    if "progressions" in changes:
        plan.clip["progressions"] = json.dumps(changes["progressions"])
    if "recorded_at" in changes:
        dt: datetime = changes["recorded_at"]
        plan.recorded_at = iso(dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt)
    if "thumbnail_at" in changes:
        at = changes["thumbnail_at"]
        if at is not None and clip["duration_seconds"] is not None and at > clip["duration_seconds"]:
            raise HTTPException(422, "That frame is past the end of the clip")
        plan.clip["thumbnail_at"] = at
        plan.thumbnail = clip["media_type"] == "video"

    old_project = clip["project_id"]
    plan.new_project = changes.get("project_id", old_project)
    if plan.new_project != old_project:
        folder = media.INBOX_DIR
        if plan.new_project is not None:
            folder = media.folder_for(get_project_row(conn, plan.new_project)["slug"])
        if "label_ids" in changes:  # check before moving any files
            check_labels(conn, plan.new_project, changes["label_ids"])
        try:
            if not recording_is_shared(conn, clip["recording_id"]):
                paths, done = _move_files(settings, clip, media.RECORDING_FILES, folder)
                plan.recording.update(paths)
                plan.moves.extend(done)
            paths, done = _move_files(settings, clip, media.CLIP_FILES, folder)
            plan.clip.update(paths)
            plan.moves.extend(done)
        except Exception:
            undo_moves(plan.moves)
            raise
        plan.clip["project_id"] = plan.new_project
    return plan


def _write_update(conn: sqlite3.Connection, clip: sqlite3.Row, changes: dict, plan: Plan) -> None:
    """Write what `_prepare_update` worked out. Runs inside the caller's transaction."""
    clip_id = clip["id"]
    old_project = clip["project_id"]
    new_project = plan.new_project
    if plan.clip:
        assignments = ", ".join(f"{c} = :{c}" for c in plan.clip)
        conn.execute(f"UPDATE clips SET {assignments} WHERE id = :id", {**plan.clip, "id": clip_id})
    if plan.recording:
        assignments = ", ".join(f"{c} = :{c}" for c in plan.recording)
        conn.execute(f"UPDATE recordings SET {assignments} WHERE id = :id", {**plan.recording, "id": clip["recording_id"]})
    if plan.recorded_at:
        # The date is when the recording was made: set it for the recording, and every
        # piece of it moves along (each piece = recording start + its start).
        start = shifted(plan.recorded_at, -clip["start_seconds"])
        conn.execute(
            "UPDATE recordings SET recorded_at = ?, recorded_at_source = 'manual' WHERE id = ?",
            (start, clip["recording_id"]),
        )
        for piece in conn.execute(
            "SELECT id, start_seconds FROM clips WHERE recording_id = ?", (clip["recording_id"],)
        ).fetchall():
            conn.execute(
                "UPDATE clips SET recorded_at = ?, recorded_at_source = 'manual' WHERE id = ?",
                (shifted(start, piece["start_seconds"]), piece["id"]),
            )
    if "label_ids" in changes or new_project != old_project:
        if "label_ids" in changes:
            keep = check_labels(conn, new_project, changes["label_ids"])
        else:  # moved: keep the parts that still exist in the new project
            scope = labels_in_scope(conn, new_project)
            keep = [r[0] for r in conn.execute("SELECT label_id FROM clip_labels WHERE clip_id = ?", (clip_id,)) if r[0] in scope]
        conn.execute("DELETE FROM clip_labels WHERE clip_id = ?", (clip_id,))
        conn.executemany("INSERT INTO clip_labels (clip_id, label_id) VALUES (?, ?)", [(clip_id, lid) for lid in keep])
    if plan.clip.get("status", clip["status"]) == "current" and new_project is not None:
        replace_current(conn, new_project, clip_id)
    if plan.thumbnail:
        conn.execute("DELETE FROM jobs WHERE clip_id = ? AND type = 'thumbnail' AND state != 'running'", (clip_id,))
        jobs.enqueue_clip(conn, clip_id, "video")
    touch_project(conn, old_project)
    if new_project != old_project:
        touch_project(conn, new_project)


def _wake(request: Request) -> None:
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()


@router.patch("/{clip_id}")
def update_clip(
    clip_id: int, body: ClipUpdate, request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> ClipDetail:
    clip = get_clip_row(conn, clip_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("title", "notes", "status", "recorded_at", "label_ids", "progressions"):
        if required in changes and changes[required] is None:
            raise HTTPException(422, f"{required} can't be empty")
    plan = _prepare_update(conn, _settings(request), clip, changes)
    try:
        with conn:
            _write_update(conn, clip, changes, plan)
    except Exception:
        undo_moves(plan.moves)
        raise
    if plan.thumbnail:
        _wake(request)
    return clip_detail(conn, clip_id)


@router.delete("/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_clip(clip_id: int, conn: sqlite3.Connection = Depends(get_db)) -> None:
    """Move to Trash. Files stay on disk (spec 3.2)."""
    clip = get_clip_row(conn, clip_id)
    with conn:
        conn.execute("UPDATE clips SET deleted_at = ? WHERE id = ?", (now_iso(), clip_id))
        touch_project(conn, clip["project_id"])


@router.post("/{clip_id}/restore")
def restore_clip(clip_id: int, conn: sqlite3.Connection = Depends(get_db)) -> ClipDetail:
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    if clip["deleted_at"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Clip isn't in Trash")
    if clip["project_id"] is not None:
        song = conn.execute(
            "SELECT name, deleted_at FROM projects WHERE id = ?", (clip["project_id"],)
        ).fetchone()
        if song["deleted_at"] is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"“{song['name']}” is in Trash too. Restore the song first."
            )
    with conn:
        conn.execute("UPDATE clips SET deleted_at = NULL WHERE id = ?", (clip_id,))
        touch_project(conn, clip["project_id"])
    return clip_detail(conn, clip_id)


@router.post("/{clip_id}/reprocess")
def reprocess_clip(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> ClipDetail:
    """Queue the recording's copies and the clip's thumbnail again (e.g. after a failure)."""
    clip = get_clip_row(conn, clip_id)
    with conn:
        conn.execute(
            "DELETE FROM jobs WHERE (recording_id = ? OR clip_id = ?) AND state IN ('done', 'failed')",
            (clip["recording_id"], clip_id),
        )
        if not conn.execute("SELECT 1 FROM jobs WHERE recording_id = ?", (clip["recording_id"],)).fetchone():
            jobs.enqueue_recording(conn, clip["recording_id"], clip["media_type"])
        if not conn.execute("SELECT 1 FROM jobs WHERE clip_id = ?", (clip_id,)).fetchone():
            jobs.enqueue_clip(conn, clip_id, clip["media_type"])
    _wake(request)
    return clip_detail(conn, clip_id)


# --- files ---------------------------------------------------------------------------------


def _file(request: Request, rel: str | None, **kwargs) -> FileResponse:
    if not rel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not available yet")
    path = media.resolve(_settings(request).media_dir, rel)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File missing on the server")
    return FileResponse(path, media_type=media.content_type(path), **kwargs)


@router.get("/{clip_id}/thumbnail")
def clip_thumbnail(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    return _file(request, clip["thumbnail_path"], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{clip_id}/media")
def clip_media(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    """The recording's playable copy. Supports Range requests, so the player can seek."""
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    return _file(request, clip["playback_path"], headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{clip_id}/audio")
def clip_audio(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    """Just the sound, to listen to or download: a video's audio-only copy, or an audio clip itself."""
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    rel = clip["audio_path"] or (clip["playback_path"] if clip["media_type"] == "audio" else None)
    name = PurePosixPath(clip["original_filename"]).stem + PurePosixPath(rel or "x.m4a").suffix
    return _file(request, rel, filename=name, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{clip_id}/preview")
def clip_preview(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    return _file(request, clip["preview_path"], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{clip_id}/original")
def clip_original(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    """The whole recording, as uploaded."""
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    return _file(request, clip["original_path"], filename=clip["original_filename"])


def clock_name(seconds: float) -> str:
    """72.4 -> "1m12s", for file names (no colons on Windows)."""
    total = int(seconds)
    return f"{total // 60}m{total % 60:02d}s"


def cut_clip(settings: Settings, clip: sqlite3.Row) -> tuple[media.Path, str]:
    """Cut a clip's stretch out of its original into a temporary file (spec 5.10), without
    re-encoding. Returns (file, download name); the caller deletes the file."""
    src = media.resolve(settings.media_dir, clip["original_path"])
    if not src.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File missing on the server")
    end = clip["end_seconds"] if clip["end_seconds"] is not None else (clip["rec_duration"] or 0)
    folder = settings.media_dir / media.CUTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{media.new_uid()}{src.suffix.lower()}"
    try:
        media.run_ffmpeg(media.cut_args(src, tmp, clip["start_seconds"], end), settings.ffmpeg, timeout=900)
    except media.MediaError as exc:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Couldn't cut this clip: {exc}") from exc
    stem = PurePosixPath(clip["original_filename"]).stem
    return tmp, f"{stem} {clock_name(clip['start_seconds'])}-{clock_name(end)}{src.suffix.lower()}"


@router.get("/{clip_id}/cut")
def clip_cut(clip_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    """Just this clip's stretch as a file. A clip that's the whole recording gets the original."""
    clip = get_clip_row(conn, clip_id, include_deleted=True)
    if is_whole(clip["start_seconds"], clip["end_seconds"], clip["rec_duration"]):
        return clip_original(clip_id, request, conn)
    tmp, name = cut_clip(_settings(request), clip)
    return FileResponse(
        tmp,
        media_type=media.content_type(tmp),
        filename=name,
        background=BackgroundTask(tmp.unlink, missing_ok=True),
    )

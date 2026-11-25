"""Recordings and the clip editor (spec 5.10): trim and split.

A recording is one uploaded file; its clips are stretches of it. The editor loads a
recording with all of its clips (`GET /api/recordings/{id}`) and saves every piece at
once (`PUT /api/recordings/{id}/clips`). Nothing is cut or re-encoded: saving only
moves start and end times, adds clips or moves them to Trash.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from . import jobs, media
from .clips import WHOLE_SLACK, _file, check_labels, replace_current, shifted, touch_project
from .db import now_iso
from .deps import get_db
from .projects import ClipStatus, clean_text

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

MIN_PIECE = 0.5  # seconds


class Piece(BaseModel):
    id: int
    start_seconds: float
    end_seconds: float
    project_id: int | None
    project_name: str | None
    label_ids: list[int]
    role: str | None
    title: str
    status: ClipStatus
    thumbnail_url: str | None


class RecordingDetail(BaseModel):
    id: int
    media_type: str
    original_filename: str
    duration_seconds: float | None
    recorded_at: str | None
    width: int | None
    height: int | None
    media_url: str | None
    """The whole recording's playable copy; None while it's being made."""
    audio_url: str | None
    peaks_url: str | None
    """The detailed waveform: JSON list, 20 points a second, 0-100."""
    peaks_per_second: int
    preview_url: str | None
    """Frames in a grid, `preview_columns` across."""
    preview_frames: int | None
    preview_columns: int
    clips: list[Piece]
    """Its clips not in Trash, in order."""


class PieceIn(BaseModel):
    id: int | None = None
    """An existing clip of this recording; leave out for a new piece."""
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    label_ids: list[int] = Field(default_factory=list, max_length=50)
    role: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=200)
    """Leave out to keep an existing clip's title."""

    @field_validator("role")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return clean_text(value)


class EditIn(BaseModel):
    from_clip: int
    """The clip the editor was opened from: new pieces go in its song, with its details."""
    pieces: list[PieceIn] = Field(min_length=1, max_length=100)
    removed: list[int] = Field(default_factory=list, max_length=100)
    """Clips of this recording to move to Trash. Every clip of it must be in `pieces` or here."""


def _get_recording(conn: sqlite3.Connection, recording_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recording not found")
    return row


def recording_detail(conn: sqlite3.Connection, recording_id: int) -> RecordingDetail:
    r = _get_recording(conn, recording_id)
    rows = conn.execute(
        """
        SELECT c.*, p.name AS project_name FROM clips c LEFT JOIN projects p ON p.id = c.project_id
        WHERE c.recording_id = ? AND c.deleted_at IS NULL ORDER BY c.start_seconds, c.id
        """,
        (recording_id,),
    ).fetchall()
    base = f"/api/recordings/{recording_id}"
    return RecordingDetail(
        id=r["id"],
        media_type=r["media_type"],
        original_filename=r["original_filename"],
        duration_seconds=r["duration_seconds"],
        recorded_at=r["recorded_at"],
        width=r["width"],
        height=r["height"],
        media_url=f"{base}/media" if r["playback_path"] else None,
        audio_url=f"{base}/audio" if r["audio_path"] else None,
        peaks_url=f"{base}/peaks" if r["peaks_path"] else None,
        peaks_per_second=media.PEAKS_PER_SECOND,
        preview_url=f"{base}/preview" if r["preview_path"] else None,
        preview_frames=r["preview_frames"] if r["preview_path"] else None,
        preview_columns=media.PREVIEW_COLUMNS,
        clips=[
            Piece(
                id=c["id"],
                start_seconds=c["start_seconds"],
                end_seconds=c["end_seconds"] if c["end_seconds"] is not None else (r["duration_seconds"] or 0),
                project_id=c["project_id"],
                project_name=c["project_name"],
                label_ids=[x[0] for x in conn.execute("SELECT label_id FROM clip_labels WHERE clip_id = ? ORDER BY label_id", (c["id"],))],
                role=c["role"],
                title=c["title"],
                status=c["status"],
                thumbnail_url=f"/api/clips/{c['id']}/thumbnail" if c["thumbnail_path"] else None,
            )
            for c in rows
        ],
    )


@router.get("/{recording_id}")
def get_recording(recording_id: int, conn: sqlite3.Connection = Depends(get_db)) -> RecordingDetail:
    return recording_detail(conn, recording_id)


@router.get("/{recording_id}/media")
def recording_media(recording_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    return _file(request, _get_recording(conn, recording_id)["playback_path"], headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{recording_id}/audio")
def recording_audio(recording_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    return _file(request, _get_recording(conn, recording_id)["audio_path"], headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{recording_id}/peaks")
def recording_peaks(recording_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    return _file(request, _get_recording(conn, recording_id)["peaks_path"], headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{recording_id}/preview")
def recording_preview(recording_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> FileResponse:
    return _file(request, _get_recording(conn, recording_id)["preview_path"], headers={"Cache-Control": "private, max-age=86400"})


def _same(a: float | None, b: float | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-3)


@router.put("/{recording_id}/clips")
def save_pieces(
    recording_id: int, body: EditIn, request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> RecordingDetail:
    """Trim and split (spec 5.10): every piece of the recording at once, all or nothing."""
    rec = _get_recording(conn, recording_id)
    duration = rec["duration_seconds"]
    if not duration:
        raise HTTPException(status.HTTP_409_CONFLICT, "This recording's length isn't known yet. Try again once it has been processed.")
    live = {
        c["id"]: c
        for c in conn.execute("SELECT * FROM clips WHERE recording_id = ? AND deleted_at IS NULL", (recording_id,))
    }
    origin = live.get(body.from_clip)
    if origin is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That clip isn't part of this recording any more. Reload and try again.")
    listed = [p.id for p in body.pieces if p.id is not None]
    removed = set(body.removed)
    if len(listed) != len(set(listed)) or removed & set(listed):
        raise HTTPException(422, "A clip is listed twice")
    if (set(listed) | removed) - live.keys() or live.keys() - set(listed) - removed:
        raise HTTPException(status.HTTP_409_CONFLICT, "This recording was changed somewhere else. Reload and try again.")
    for p in body.pieces:
        if p.end_seconds > duration + WHOLE_SLACK or p.start_seconds >= p.end_seconds:
            raise HTTPException(422, "A piece is outside the recording")
        if p.end_seconds - p.start_seconds < MIN_PIECE:
            raise HTTPException(422, f"Pieces must be at least {MIN_PIECE:g} s long")

    projects: set[int | None] = set()
    with conn:
        now = now_iso()
        for clip_id in removed:
            conn.execute("UPDATE clips SET deleted_at = ? WHERE id = ?", (now, clip_id))
            projects.add(live[clip_id]["project_id"])
        points = jobs.load_peaks(request.app.state.settings, rec["peaks_path"])
        for p in body.pieces:
            start = 0.0 if p.start_seconds <= WHOLE_SLACK else round(p.start_seconds, 3)
            end = None if p.end_seconds >= duration - WHOLE_SLACK else round(p.end_seconds, 3)
            length = (end if end is not None else duration) - start
            recorded = shifted(rec["recorded_at"], start) if rec["recorded_at"] else None
            if p.id is not None:
                old = live[p.id]
                project_id = old["project_id"]
                labels = check_labels(conn, project_id, p.label_ids)
                moved = not (_same(old["start_seconds"], start) and _same(old["end_seconds"], end))
                updates: dict[str, object] = {"role": p.role}
                if p.title is not None:
                    updates["title"] = p.title.strip()
                if moved:
                    updates.update(start_seconds=start, end_seconds=end, duration_seconds=length, thumbnail_at=None)
                    if recorded:
                        updates["recorded_at"] = recorded
                assignments = ", ".join(f"{c} = :{c}" for c in updates)
                conn.execute(f"UPDATE clips SET {assignments} WHERE id = :id", {**updates, "id": p.id})
                clip_id = p.id
                if moved and old["media_type"] == "video":
                    conn.execute("DELETE FROM jobs WHERE clip_id = ? AND type = 'thumbnail' AND state != 'running'", (clip_id,))
                    jobs.enqueue_clip(conn, clip_id, "video")
                status_ = old["status"]
            else:
                project_id = origin["project_id"]
                labels = check_labels(conn, project_id, p.label_ids)
                cur = conn.execute(
                    """
                    INSERT INTO clips (uid, project_id, title, recorded_at, recorded_at_source, uploaded_at, media_type,
                      duration_seconds, song_key, bpm, time_signature, capo, tuning, progressions, role,
                      recording_id, start_seconds, end_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        media.new_uid(), project_id, (p.title or "").strip(), recorded or origin["recorded_at"],
                        rec["recorded_at_source"], rec["uploaded_at"], rec["media_type"], length,
                        origin["song_key"], origin["bpm"], origin["time_signature"], origin["capo"], origin["tuning"],
                        origin["progressions"], p.role, recording_id, start, end,
                    ),
                )  # fmt: skip
                clip_id = cur.lastrowid
                jobs.enqueue_clip(conn, clip_id, rec["media_type"])
                status_ = "idea"
            conn.execute("DELETE FROM clip_labels WHERE clip_id = ?", (clip_id,))
            conn.executemany("INSERT INTO clip_labels (clip_id, label_id) VALUES (?, ?)", [(clip_id, lid) for lid in labels])
            if points is not None and rec["media_type"] == "audio":
                conn.execute(
                    "UPDATE clips SET waveform = ? WHERE id = ?",
                    (json.dumps(media.clip_waveform(points, start, end)), clip_id),
                )
            if status_ == "current" and project_id is not None:
                replace_current(conn, project_id, clip_id)
            projects.add(project_id)
        for project_id in projects:
            touch_project(conn, project_id)

    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return recording_detail(conn, recording_id)

"""Background media jobs (spec 5.4, 5.9, 5.10).

A recording (one uploaded file) gets a playable copy, then the extras: an audio-only
copy and a preview strip for videos, and a detailed waveform for everything. A clip (a
stretch of a recording) gets its own thumbnail. One worker thread processes the `jobs`
table one job at a time, so ffmpeg never competes with itself. Endpoints call
`Worker.wake()` after queueing work.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import media, trash
from .config import Settings
from .db import connect, now_iso

log = logging.getLogger("songvault.jobs")

# What gets made, in order. Until a recording's playback copy and a clip's thumbnail are
# done a tile shows "Processing" (see projects.PROCESSING_SQL); the rest are extras.
RECORDING_JOBS = {
    "video": ("playback", "audio", "preview", "peaks"),
    "audio": ("playback", "peaks"),
}
CLIP_JOBS = {"video": ("thumbnail",), "audio": ()}
STALE_UPLOAD_AGE = timedelta(days=7)
STALE_CUT_AGE = timedelta(hours=1)


def _queue(conn: sqlite3.Connection, column: str, target: int, types: tuple[str, ...]) -> None:
    now = now_iso()
    conn.executemany(
        f"INSERT INTO jobs ({column}, type, state, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)",
        [(target, t, now, now) for t in types],
    )


def enqueue_recording(conn: sqlite3.Connection, recording_id: int, media_type: str) -> None:
    _queue(conn, "recording_id", recording_id, RECORDING_JOBS[media_type])


def enqueue_clip(conn: sqlite3.Connection, clip_id: int, media_type: str) -> None:
    _queue(conn, "clip_id", clip_id, CLIP_JOBS[media_type])


def _probe(settings: Settings, recording: sqlite3.Row) -> tuple[Path, media.ProbeInfo]:
    src = media.resolve(settings.media_dir, recording["original_path"])
    return src, media.probe(src, settings.ffprobe)


def load_peaks(settings: Settings, peaks_path: str | None) -> list[int] | None:
    """A recording's detailed waveform, if it has been made."""
    if not peaks_path:
        return None
    try:
        return json.loads(media.resolve(settings.media_dir, peaks_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, media.MediaError):
        return None


def update_waveforms(conn: sqlite3.Connection, recording_id: int, points: list[int]) -> None:
    """The bars on the tiles and in the player of a recording's audio clips."""
    for clip in conn.execute(
        "SELECT id, start_seconds, end_seconds, media_type FROM clips WHERE recording_id = ?", (recording_id,)
    ).fetchall():
        if clip["media_type"] == "audio":
            bars = media.clip_waveform(points, clip["start_seconds"], clip["end_seconds"])
            conn.execute("UPDATE clips SET waveform = ? WHERE id = ?", (json.dumps(bars), clip["id"]))


# --- recording jobs ------------------------------------------------------------------------


def make_playback(settings: Settings, conn: sqlite3.Connection, recording: sqlite3.Row) -> None:
    src, info = _probe(settings, recording)
    plan = media.playback_plan(info, media.extension(recording["original_path"]))
    if plan == "original":
        rel = recording["original_path"]
    else:
        suffix = ".play.m4a" if info.media_type == "audio" else ".play.mp4"
        rel = media.derived_path(recording["original_path"], suffix)
        dest = media.resolve(settings.media_dir, rel)
        tmp = dest.with_name(dest.name.replace(".play.", ".play.tmp."))
        media.run_ffmpeg(media.playback_args(src, tmp, info, plan), settings.ffmpeg)
        tmp.replace(dest)
    duration = recording["duration_seconds"]
    if duration is None and plan != "original":
        # Recorded in a browser: the original has no length, the copy does.
        duration = media.probe(media.resolve(settings.media_dir, rel), settings.ffprobe).duration
    with conn:
        conn.execute(
            "UPDATE recordings SET playback_path = ?, duration_seconds = ? WHERE id = ?", (rel, duration, recording["id"])
        )
        if duration is not None:  # clips running to the end now know their length
            conn.execute(
                "UPDATE clips SET duration_seconds = ? - start_seconds WHERE recording_id = ? AND end_seconds IS NULL",
                (duration, recording["id"]),
            )


def make_audio(settings: Settings, conn: sqlite3.Connection, recording: sqlite3.Row) -> None:
    """An audio-only copy of a video (spec 5.4). Audio recordings are played as they are."""
    if recording["media_type"] != "video":
        return
    src, info = _probe(settings, recording)
    if info.audio_codec is None:
        return  # a video without sound has nothing to listen to
    rel = media.derived_path(recording["original_path"], ".audio.m4a")
    dest = media.resolve(settings.media_dir, rel)
    tmp = dest.with_name(dest.name.replace(".audio.", ".audio.tmp."))
    media.run_ffmpeg(media.audio_copy_args(src, tmp, info), settings.ffmpeg, timeout=3600)
    tmp.replace(dest)
    with conn:
        conn.execute("UPDATE recordings SET audio_path = ? WHERE id = ?", (rel, recording["id"]))


def make_preview(settings: Settings, conn: sqlite3.Connection, recording: sqlite3.Row) -> None:
    """Frames in a grid, for scrubbing through a video on its tile and the editor's timeline (spec 5.4)."""
    if recording["media_type"] != "video":
        return
    src, info = _probe(settings, recording)
    duration = info.duration or recording["duration_seconds"]  # the playback copy may have filled it in
    grid = media.preview_grid(duration)
    if not grid[0] or not duration:
        return
    rel = media.derived_path(recording["original_path"], ".preview.jpg")
    dest = media.resolve(settings.media_dir, rel)
    tmp = dest.with_name(dest.stem + ".tmp.jpg")
    tmp.unlink(missing_ok=True)
    # Keyframes only is fast; a short clip may not have enough of them, so then decode it all.
    for keyframes_only in (True, False):
        media.run_ffmpeg(media.preview_args(src, tmp, info, grid, duration, keyframes_only=keyframes_only),
                         settings.ffmpeg, timeout=1800)  # fmt: skip
        if tmp.exists():
            break
    if not tmp.exists():
        raise media.MediaError("ffmpeg made no preview")
    tmp.replace(dest)
    with conn:
        conn.execute(
            "UPDATE recordings SET preview_path = ?, preview_frames = ? WHERE id = ?",
            (rel, grid[0] * grid[1], recording["id"]),
        )


def make_peaks(settings: Settings, conn: sqlite3.Connection, recording: sqlite3.Row) -> None:
    """The detailed waveform (spec 5.9, 5.10): the editor's timeline, and the bars on audio tiles."""
    src = media.resolve(settings.media_dir, recording["original_path"])
    points = media.peaks(src, settings.ffmpeg)
    rel = media.derived_path(recording["original_path"], ".peaks.json")
    dest = media.resolve(settings.media_dir, rel)
    tmp = dest.with_name(dest.stem + ".tmp.json")
    tmp.write_text(json.dumps(points, separators=(",", ":")), encoding="utf-8")
    tmp.replace(dest)
    with conn:
        conn.execute("UPDATE recordings SET peaks_path = ? WHERE id = ?", (rel, recording["id"]))
        update_waveforms(conn, recording["id"], points)


# --- clip jobs -------------------------------------------------------------------------------


def thumbnail_path_for(conn: sqlite3.Connection, clip: sqlite3.Row, recording: sqlite3.Row) -> str:
    """`<song folder>/<recording name>.thumb.jpg` for the clip a recording was uploaded
    as; `...<recording name>.<clip uid>.thumb.jpg` for a piece split off it."""
    slug = None
    if clip["project_id"] is not None:
        row = conn.execute("SELECT slug FROM projects WHERE id = ?", (clip["project_id"],)).fetchone()
        slug = row["slug"] if row else None
    stem = Path(recording["original_path"]).stem
    if clip["uid"] != recording["uid"]:
        stem = f"{stem}.{clip['uid']}"
    return f"{media.folder_for(slug)}/{stem}.thumb.jpg"


def make_thumbnail(settings: Settings, conn: sqlite3.Connection, clip: sqlite3.Row) -> None:
    """A poster frame from the clip's own stretch: the chosen one, or 3 s in (10% of a short clip)."""
    if clip["media_type"] != "video":
        return  # audio clips get a waveform tile
    recording = conn.execute("SELECT * FROM recordings WHERE id = ?", (clip["recording_id"],)).fetchone()
    src, info = _probe(settings, recording)
    length = clip["duration_seconds"] or info.duration or 0
    offset = clip["thumbnail_at"] if clip["thumbnail_at"] is not None else min(3.0, length * 0.1)
    rel = thumbnail_path_for(conn, clip, recording)
    dest = media.resolve(settings.media_dir, rel)
    tmp = dest.with_name(dest.stem + ".tmp.jpg")
    media.run_ffmpeg(media.thumbnail_args(src, tmp, info, at=clip["start_seconds"] + offset), settings.ffmpeg, timeout=300)
    tmp.replace(dest)
    with conn:
        conn.execute("UPDATE clips SET thumbnail_path = ? WHERE id = ?", (rel, clip["id"]))
    if clip["thumbnail_path"] and clip["thumbnail_path"] != rel:  # moved to a new name: tidy the old one
        media.resolve(settings.media_dir, clip["thumbnail_path"]).unlink(missing_ok=True)


RECORDING_HANDLERS = {"playback": make_playback, "audio": make_audio, "preview": make_preview, "peaks": make_peaks}
CLIP_HANDLERS = {"thumbnail": make_thumbnail}


def claim_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    with conn:
        return conn.execute(
            """
            UPDATE jobs SET state = 'running', attempts = attempts + 1, updated_at = ?
            WHERE id = (SELECT id FROM jobs WHERE state = 'queued' ORDER BY id LIMIT 1)
            RETURNING *
            """,
            (now_iso(),),
        ).fetchone()


def process_next(settings: Settings) -> bool:
    """Run one queued job. Returns False when the queue is empty."""
    conn = connect(settings.db_path)
    try:
        job = claim_next(conn)
        if job is None:
            return False
        try:
            if job["recording_id"] is not None:
                target = conn.execute("SELECT * FROM recordings WHERE id = ?", (job["recording_id"],)).fetchone()
                handler = RECORDING_HANDLERS[job["type"]]
            else:
                target = conn.execute("SELECT * FROM clips WHERE id = ?", (job["clip_id"],)).fetchone()
                handler = CLIP_HANDLERS[job["type"]]
            if target is None:
                raise media.MediaError("It no longer exists")
            handler(settings, conn, target)
        except Exception as exc:  # noqa: BLE001 - any failure is recorded on the job
            log.warning(
                "job %s (%s, recording %s, clip %s) failed: %s",
                job["id"], job["type"], job["recording_id"], job["clip_id"], exc,
            )  # fmt: skip
            with conn:
                conn.execute(
                    "UPDATE jobs SET state = 'failed', error = ?, updated_at = ? WHERE id = ?",
                    (str(exc)[-2000:] or type(exc).__name__, now_iso(), job["id"]),
                )
        else:
            with conn:
                conn.execute(
                    "UPDATE jobs SET state = 'done', error = NULL, updated_at = ? WHERE id = ?",
                    (now_iso(), job["id"]),
                )
        return True
    finally:
        conn.close()


def run_all(settings: Settings) -> int:
    """Process every queued job (used by tests and the CLI)."""
    count = 0
    while process_next(settings):
        count += 1
    return count


def recover(settings: Settings) -> None:
    """Jobs left 'running' by a crash or restart go back in the queue."""
    conn = connect(settings.db_path)
    try:
        with conn:
            conn.execute("UPDATE jobs SET state = 'queued', updated_at = ? WHERE state = 'running'", (now_iso(),))
    finally:
        conn.close()


def remove_stale_uploads(settings: Settings) -> int:
    """Delete partial uploads nobody resumed for a week. Finished uploads are never touched."""
    cutoff = (datetime.now(UTC) - STALE_UPLOAD_AGE).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = connect(settings.db_path)
    try:
        stale = conn.execute(
            "SELECT id FROM uploads WHERE completed_at IS NULL AND created_at < ?", (cutoff,)
        ).fetchall()
        for row in stale:
            (settings.uploads_dir / f"{row['id']}.part").unlink(missing_ok=True)
        with conn:
            conn.executemany("DELETE FROM uploads WHERE id = ?", [(r["id"],) for r in stale])
        return len(stale)
    finally:
        conn.close()


def remove_stale_cuts(settings: Settings) -> int:
    """Pieces cut out for a download that was interrupted (the response deletes the rest)."""
    folder = settings.media_dir / media.CUTS_DIR
    cutoff = time.time() - STALE_CUT_AGE.total_seconds()
    removed = 0
    for path in folder.glob("*") if folder.is_dir() else []:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_cleanup = 0.0

    def start(self) -> None:
        recover(self.settings)
        self._thread = threading.Thread(target=self._run, name="songvault-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if time.monotonic() - self._last_cleanup > 3600:
                    self._last_cleanup = time.monotonic()
                    remove_stale_uploads(self.settings)
                    remove_stale_cuts(self.settings)
                    trash.purge_trash(self.settings)
                if process_next(self.settings):
                    continue
            except Exception:  # noqa: BLE001 - keep the worker alive
                log.exception("media worker error")
            self._wake.wait(timeout=30)
            self._wake.clear()

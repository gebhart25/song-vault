"""The Notebook (spec 5.11): lyric lines, rhymes, titles and song ideas.

Notes are written on a device, often away from home with no connection to the server,
and synced later. So:

- A note's uid is made on the device that wrote it. A note sent twice (a sync cut off
  half way) is stored once.
- Every change to a note takes the next number from a counter (`note_clock.seq`), and a
  device asks for the changes after the last number it saw. The number is taken inside
  the transaction that writes the note, and SQLite runs one write at a time, so numbers
  are handed out in the order changes are committed: a device can't miss one.
- A device sends its changes with the revision and the text they started from. If the
  text changed elsewhere since (edited on the PC while the phone was away), the edit is
  refused as a conflict and the device keeps its version as a copy, marked "Edited on
  two devices", rather than overwrite the other one. Stars and Used / fresh simply take
  the last change.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from .config import Settings
from .db import connect, now_iso
from .deps import get_db
from .trash import TRASH_DAYS

router = APIRouter(prefix="/api/notes", tags=["notes"])

NoteStatus = Literal["fresh", "used"]
UID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
MAX_BODY = 50_000
MAX_CHANGES = 500


class Note(BaseModel):
    uid: str
    body: str
    starred: bool
    status: NoteStatus
    revision: int
    copy_of: str | None
    """Set on a copy made because the note was edited on two devices: the other version's uid."""
    created_at: str
    updated_at: str
    deleted_at: str | None
    """Set = in Trash. Sent so a device that has the note can drop it."""


class NotesPage(BaseModel):
    notes: list[Note]
    cursor: int
    """Ask for the changes after this next time (`?since=`)."""
    full: bool
    """The whole notebook (not in Trash): the device replaces what it has, apart from its unsent changes."""


class NoteChange(BaseModel):
    """A device's changes to one note. Fields left out (null) are unchanged."""

    uid: str = Field(pattern=UID_PATTERN)
    created_at: str | None = None
    """When it was written, for a new note."""
    base_revision: int = Field(default=0, ge=0)
    """The revision the device's edit started from (0 = a note the server hasn't seen)."""
    base_body: str | None = Field(default=None, max_length=MAX_BODY)
    """The text the device's edit started from, to tell a real conflict from a change
    elsewhere that didn't touch the text (a star, say)."""
    body: str | None = Field(default=None, max_length=MAX_BODY)
    starred: bool | None = None
    status: NoteStatus | None = None
    copy_of: str | None = Field(default=None, max_length=64)
    """"" clears it (the user dismissed "Edited on two devices")."""
    trash: bool = False


class Changes(BaseModel):
    changes: list[NoteChange] = Field(max_length=MAX_CHANGES)


class ChangeResult(BaseModel):
    uid: str
    note: Note | None
    """The note as the server has it now; None if it doesn't exist (deleted for good)."""
    conflict: bool = False
    """The text changed elsewhere since the device's edit started. Nothing was changed;
    `note` is the server's version, and the device keeps its own as a copy."""


class ChangesResult(BaseModel):
    results: list[ChangeResult]


def _note(row: sqlite3.Row) -> Note:
    return Note(
        uid=row["uid"],
        body=row["body"],
        starred=bool(row["starred"]),
        status=row["status"],
        revision=row["revision"],
        copy_of=row["copy_of"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _written_at(value: str | None, now: str) -> str:
    """The device's time for a new note, if it's believable; else now."""
    if not value:
        return now
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return now
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if not datetime(2000, 1, 1, tzinfo=UTC) <= dt <= datetime.now(UTC) + timedelta(days=1):
        return now
    return _iso(dt)


def _clean(body: str) -> str:
    return body.replace("\r\n", "\n").replace("\r", "\n")


def _next_seq(conn: sqlite3.Connection) -> int:
    """The next change number. Must run inside the transaction that makes the change."""
    return conn.execute("UPDATE note_clock SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchall()[0][0]


def _get(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM notes WHERE uid = ?", (uid,)).fetchone()


def apply_change(conn: sqlite3.Connection, change: NoteChange) -> ChangeResult:
    """Apply one device change in its own transaction."""
    now = now_iso()
    with conn:
        conn.execute("BEGIN IMMEDIATE")  # the check and the write as one step
        row = _get(conn, change.uid)
        body = _clean(change.body) if change.body is not None else None

        if row is None:
            if change.trash:
                return ChangeResult(uid=change.uid, note=None)
            conn.execute(
                """
                INSERT INTO notes (uid, body, starred, status, revision, copy_of, seq, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    change.uid,
                    body or "",
                    int(bool(change.starred)),
                    change.status or "fresh",
                    change.copy_of or None,
                    _next_seq(conn),
                    _written_at(change.created_at, now),
                    now,
                ),
            )
            return ChangeResult(uid=change.uid, note=_note(_get(conn, change.uid)))

        current = _note(row)
        if row["deleted_at"] is not None:
            # Moved to Trash on another device. A new text is kept on the device as a copy;
            # anything else is dropped along with the note.
            conflict = body is not None and body != row["body"] and not change.trash
            return ChangeResult(uid=change.uid, note=current, conflict=conflict)

        if change.trash:
            conn.execute(
                "UPDATE notes SET deleted_at = ?, revision = revision + 1, seq = ?, updated_at = ? WHERE id = ?",
                (now, _next_seq(conn), now, row["id"]),
            )
            return ChangeResult(uid=change.uid, note=_note(_get(conn, change.uid)))

        updates: dict[str, object] = {}
        if body is not None and body != row["body"]:
            stale = change.base_revision != row["revision"]
            changed_elsewhere = change.base_body is None or _clean(change.base_body) != row["body"]
            if stale and changed_elsewhere:
                return ChangeResult(uid=change.uid, note=current, conflict=True)
            updates["body"] = body
        if change.starred is not None and int(change.starred) != row["starred"]:
            updates["starred"] = int(change.starred)
        if change.status is not None and change.status != row["status"]:
            updates["status"] = change.status
        if change.copy_of is not None and (change.copy_of or None) != row["copy_of"]:
            updates["copy_of"] = change.copy_of or None
        if not updates:
            return ChangeResult(uid=change.uid, note=current)

        updates["seq"] = _next_seq(conn)
        updates["updated_at"] = now
        assignments = ", ".join(f"{column} = :{column}" for column in updates)
        conn.execute(
            f"UPDATE notes SET {assignments}, revision = revision + 1 WHERE id = :id", {**updates, "id": row["id"]}
        )
        return ChangeResult(uid=change.uid, note=_note(_get(conn, change.uid)))


@router.get("")
def list_notes(
    since: int | None = Query(default=None, ge=0), conn: sqlite3.Connection = Depends(get_db)
) -> NotesPage:
    """The notes changed after `since` (Trash included, so devices can drop them), or the
    whole notebook. Read the counter first: anything committed after that has a higher
    number and comes next time."""
    cursor, purged = conn.execute("SELECT seq, purged_seq FROM note_clock WHERE id = 1").fetchone()
    # A device that synced before notes were deleted for good, or that comes from before a
    # restored backup, starts again from the whole notebook.
    full = since is None or since < purged or since > cursor
    if full:
        rows = conn.execute("SELECT * FROM notes WHERE deleted_at IS NULL ORDER BY seq").fetchall()
    else:
        rows = conn.execute("SELECT * FROM notes WHERE seq > ? ORDER BY seq", (since,)).fetchall()
    return NotesPage(notes=[_note(r) for r in rows], cursor=cursor, full=full)


@router.post("/changes")
def save_changes(body: Changes, conn: sqlite3.Connection = Depends(get_db)) -> ChangesResult:
    """A device's waiting changes, each applied on its own, in order."""
    return ChangesResult(results=[apply_change(conn, change) for change in body.changes])


@router.post("/{uid}/restore")
def restore_note(uid: str, conn: sqlite3.Connection = Depends(get_db)) -> Note:
    now = now_iso()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _get(conn, uid)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
        if row["deleted_at"] is not None:
            conn.execute(
                "UPDATE notes SET deleted_at = NULL, revision = revision + 1, seq = ?, updated_at = ? WHERE id = ?",
                (_next_seq(conn), now, row["id"]),
            )
    return _note(_get(conn, uid))


def purge_notes(settings: Settings, now: datetime | None = None) -> int:
    """Delete for good the notes that have been in Trash for 30 days. Returns how many."""
    cutoff = _iso((now or datetime.now(UTC)) - timedelta(days=TRASH_DAYS))
    conn = connect(settings.db_path)
    try:
        with conn:
            seqs = [
                r[0]
                for r in conn.execute(
                    "DELETE FROM notes WHERE deleted_at IS NOT NULL AND deleted_at < ? RETURNING seq", (cutoff,)
                ).fetchall()
            ]
            if seqs:
                conn.execute("UPDATE note_clock SET purged_seq = max(purged_seq, ?) WHERE id = 1", (max(seqs),))
        return len(seqs)
    finally:
        conn.close()

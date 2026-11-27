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
  two devices", rather than overwrite the other one. Stars, Used / fresh, the songs a
  note is linked to (step 2) and Move to top (a new date) simply take the last change.
- A note carries its songs' names and whether they're in Trash, so the device can show
  them offline. Renaming a song, or moving it to Trash and back, gives its notes a new
  change number so devices fetch them again (projects.touch_song_notes).
"""

from __future__ import annotations

import json
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


class NoteSong(BaseModel):
    id: int
    name: str
    in_trash: bool


class Note(BaseModel):
    uid: str
    body: str
    starred: bool
    status: NoteStatus
    revision: int
    copy_of: str | None
    """Set on a copy made because the note was edited on two devices: the other version's uid."""
    songs: list[NoteSong]
    """The songs it's linked to (step 2), by name."""
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
    songs: list[int] | None = Field(default=None, max_length=200)
    """The songs it's linked to, all of them (ids of songs that don't exist are left out)."""
    moved_at: str | None = None
    """Move to top: the note's date becomes this (the device's time when it was moved).
    Sent only when moved, so a change from a device that hasn't heard of it can't undo it."""
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


def _songs_by_note(conn: sqlite3.Connection, note_ids: list[int]) -> dict[int, list[NoteSong]]:
    out: dict[int, list[NoteSong]] = {}
    if not note_ids:
        return out
    for r in conn.execute(
        """
        SELECT ns.note_id, p.id, p.name, p.deleted_at IS NOT NULL AS in_trash
        FROM note_songs ns JOIN projects p ON p.id = ns.project_id
        WHERE ns.note_id IN (SELECT value FROM json_each(?))
        ORDER BY p.name COLLATE NOCASE, p.id
        """,
        (json.dumps(note_ids),),
    ):
        out.setdefault(r["note_id"], []).append(NoteSong(id=r["id"], name=r["name"], in_trash=bool(r["in_trash"])))
    return out


def _notes(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[Note]:
    songs = _songs_by_note(conn, [r["id"] for r in rows])
    return [
        Note(
            uid=r["uid"],
            body=r["body"],
            starred=bool(r["starred"]),
            status=r["status"],
            revision=r["revision"],
            copy_of=r["copy_of"],
            songs=songs.get(r["id"], []),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            deleted_at=r["deleted_at"],
        )
        for r in rows
    ]


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


def next_seq(conn: sqlite3.Connection) -> int:
    """The next change number. Must run inside the transaction that makes the change."""
    return conn.execute("UPDATE note_clock SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchall()[0][0]


def _get(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM notes WHERE uid = ?", (uid,)).fetchone()


def _load(conn: sqlite3.Connection, uid: str) -> Note | None:
    row = _get(conn, uid)
    return _notes(conn, [row])[0] if row else None


def _linked(conn: sqlite3.Connection, note_id: int) -> set[int]:
    return {r[0] for r in conn.execute("SELECT project_id FROM note_songs WHERE note_id = ?", (note_id,))}


def _wanted_songs(conn: sqlite3.Connection, wanted: list[int]) -> set[int]:
    """The songs among these that exist (Trash included)."""
    return {
        r[0]
        for r in conn.execute("SELECT id FROM projects WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(wanted),))
    }


def _set_songs(conn: sqlite3.Connection, note_id: int, wanted: list[int], now: str) -> None:
    """Link the note to exactly these songs (the ones that exist, Trash included)."""
    keep = _wanted_songs(conn, wanted)
    conn.execute(
        "DELETE FROM note_songs WHERE note_id = ? AND project_id NOT IN (SELECT value FROM json_each(?))",
        (note_id, json.dumps(sorted(keep))),
    )
    conn.executemany(
        "INSERT OR IGNORE INTO note_songs (note_id, project_id, added_at) VALUES (?, ?, ?)",
        [(note_id, pid, now) for pid in sorted(keep)],
    )


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
            cur = conn.execute(
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
                    next_seq(conn),
                    _written_at(change.created_at, now),
                    now,
                ),
            )
            if change.songs:
                _set_songs(conn, cur.lastrowid, change.songs, now)
            return ChangeResult(uid=change.uid, note=_load(conn, change.uid))

        current = _notes(conn, [row])[0]
        if row["deleted_at"] is not None:
            # Moved to Trash on another device. A new text is kept on the device as a copy;
            # anything else is dropped along with the note.
            conflict = body is not None and body != row["body"] and not change.trash
            return ChangeResult(uid=change.uid, note=current, conflict=conflict)

        if change.trash:
            conn.execute(
                "UPDATE notes SET deleted_at = ?, revision = revision + 1, seq = ?, updated_at = ? WHERE id = ?",
                (now, next_seq(conn), now, row["id"]),
            )
            return ChangeResult(uid=change.uid, note=_load(conn, change.uid))

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
        if change.moved_at is not None:
            moved = _written_at(change.moved_at, now)
            if moved != row["created_at"]:
                updates["created_at"] = moved
        songs_changed = change.songs is not None and _wanted_songs(conn, change.songs) != _linked(conn, row["id"])
        if not updates and not songs_changed:
            return ChangeResult(uid=change.uid, note=current)

        if songs_changed and change.songs is not None:
            _set_songs(conn, row["id"], change.songs, now)
        updates["seq"] = next_seq(conn)
        updates["updated_at"] = now
        assignments = ", ".join(f"{column} = :{column}" for column in updates)
        conn.execute(
            f"UPDATE notes SET {assignments}, revision = revision + 1 WHERE id = :id", {**updates, "id": row["id"]}
        )
        return ChangeResult(uid=change.uid, note=_load(conn, change.uid))


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
    return NotesPage(notes=_notes(conn, rows), cursor=cursor, full=full)


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
                (next_seq(conn), now, row["id"]),
            )
    note = _load(conn, uid)
    assert note is not None
    return note


def song_notes(conn: sqlite3.Connection, project_id: int) -> list[Note]:
    """The notes linked to a song (not in Trash), newest first."""
    rows = conn.execute(
        """
        SELECT n.* FROM notes n JOIN note_songs ns ON ns.note_id = n.id
        WHERE ns.project_id = ? AND n.deleted_at IS NULL
        ORDER BY n.created_at DESC, n.id DESC
        """,
        (project_id,),
    ).fetchall()
    return _notes(conn, rows)


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

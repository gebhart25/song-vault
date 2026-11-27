"""Projects (songs) and the song page's rows (spec 3.1 and 4)."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from . import media
from .config import Settings
from .db import now_iso
from .deps import get_db

ProjectStatus = Literal["idea", "writing", "arranging", "recorded", "done", "shelved"]
ProjectKind = Literal["original", "cowrite", "cover", "arrangement", "practice"]
"""What kind of song it is (spec 3.1): yours, written with someone, someone else's, your
arrangement of a traditional song or a new take on another, or practice material."""
ClipStatus = Literal["idea", "current", "used", "discarded"]

router = APIRouter(prefix="/api/projects", tags=["projects"])


# --- models ---------------------------------------------------------------------


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        raise ValueError("Name can't be empty")
    return value


CHORD_MAX = 16


def clean_chords(value: list[str]) -> list[str]:
    """Trim chord names and reject ones that couldn't be a chord (empty, too long, brackets)."""
    chords = [c.strip() for c in value]
    for chord in chords:
        if not chord or len(chord) > CHORD_MAX or any(ch in chord for ch in "[]\n"):
            raise ValueError(f"“{chord}” isn't a chord name")
    return chords


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace; blank means "not set"."""
    if value is None:
        return None
    return " ".join(value.split()) or None


class Progression(BaseModel):
    """A labeled chord progression in the song notes, e.g. Verse: D - Bm - G - A."""

    label: str = Field(min_length=1, max_length=60)
    chords: list[str] = Field(min_length=1, max_length=64)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Give each progression a label")
        return value

    @field_validator("chords")
    @classmethod
    def check_chords(cls, value: list[str]) -> list[str]:
        return clean_chords(value)


class Strum(BaseModel):
    """A strumming pattern in the song notes, e.g. Verse: D - D U - U D U. The label is optional."""

    label: str = Field(default="", max_length=60)
    pattern: str = Field(min_length=1, max_length=80)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("pattern")
    @classmethod
    def clean_pattern(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("A strumming pattern can't be empty")
        return value


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    status: ProjectStatus = "idea"
    kind: ProjectKind = "original"
    notes: str = Field(default="", max_length=100_000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return _clean_name(value)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ProjectStatus | None = None
    kind: ProjectKind | None = None
    notes: str | None = Field(default=None, max_length=100_000)
    archived: bool | None = None
    song_key: str | None = Field(default=None, max_length=40)
    bpm: float | None = Field(default=None, gt=0, le=400)
    tuning: str | None = Field(default=None, max_length=80)
    capo: int | None = Field(default=None, ge=0, le=24)
    time_signature: str | None = Field(default=None, max_length=10)
    sounds_like: str | None = Field(default=None, max_length=300)
    progressions: list[Progression] | None = Field(default=None, max_length=50)
    strumming: list[Strum] | None = Field(default=None, max_length=20)

    @field_validator("song_key", "tuning", "time_signature", "sounds_like")
    @classmethod
    def blank_is_none(cls, value: str | None) -> str | None:
        return clean_text(value)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return _clean_name(value)


class ClipSummary(BaseModel):
    id: int
    uid: str
    title: str
    status: ClipStatus
    media_type: Literal["video", "audio"]
    recorded_at: str | None
    uploaded_at: str
    duration_seconds: float | None
    thumbnail_url: str | None
    processing: Literal["pending", "failed"] | None = None
    """pending = thumbnail or playback copy still being made; failed = one of those failed.
    The extras (audio copy, preview strip, waveform) don't count."""
    preview_url: str | None = None
    """A strip of `preview_frames` frames side by side, for scrubbing on the tile (spec 5.4)."""
    preview_frames: int | None = None
    waveform: list[int] | None = None
    """Audio clips: loudest point in each of 120 slices, 0-100 (spec 5.9)."""
    role: str | None = None
    """What the take is (Rhythm, Lead, Vocals...): one Current per part and role (spec 5.1)."""
    # A clip is a stretch of a recording (spec 5.10).
    recording_id: int = 0
    start_seconds: float = 0
    end_seconds: float | None = None
    """Where the stretch ends in the recording (its length when the clip runs to the end)."""
    recording_duration: float | None = None


class Cover(BaseModel):
    """A song's picture on the Projects page (spec 3.1)."""

    kind: Literal["auto", "stock", "image"]
    """auto = the newest clip thumbnail; stock = a picture that ships with the app;
    image = the user's own (a copied clip thumbnail or an upload)."""
    url: str | None
    stock: str | None = None
    clip_id: int | None = None
    """For an image copied from a clip's thumbnail: that clip."""


class ProjectSummary(BaseModel):
    id: int
    name: str
    slug: str
    status: ProjectStatus
    kind: ProjectKind
    archived: bool
    created_at: str
    updated_at: str
    clip_count: int
    cover_thumbnail_url: str | None
    """What the card shows: the same as `cover.url`."""
    cover: Cover


class Row(BaseModel):
    label_id: int
    name: str
    is_full_song: bool
    is_custom: bool
    collapsed: bool
    clips: list[ClipSummary]


class ProjectDetail(BaseModel):
    id: int
    name: str
    slug: str
    status: ProjectStatus
    kind: ProjectKind
    archived: bool
    notes: str
    song_key: str | None
    bpm: float | None
    tuning: str | None
    capo: int | None
    time_signature: str | None
    sounds_like: str | None
    progressions: list[Progression]
    strumming: list[Strum]
    lyric_sections: int
    """Number of sections on the lyrics page."""
    created_at: str
    updated_at: str
    clip_count: int
    cover: Cover
    unlabeled: list[ClipSummary]
    rows: list[Row]


class RowState(BaseModel):
    label_id: int
    collapsed: bool = False


class RowsUpdate(BaseModel):
    rows: list[RowState]


# --- helpers ----------------------------------------------------------------------


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name).strip("-").lower()
    return slug[:60].strip("-") or "song"


def unique_slug(conn: sqlite3.Connection, name: str, exclude_id: int | None = None) -> str:
    base = slugify(name)
    slug, n = base, 2
    while conn.execute(
        "SELECT 1 FROM projects WHERE slug = ? AND id IS NOT ?", (slug, exclude_id)
    ).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


# A clip with its recording's file facts (spec 5.10). Use as `SELECT {CLIP_COLUMNS} FROM
# {CLIP_FROM}`. Recording columns that clips also have get a `rec_` name.
CLIP_COLUMNS = """c.*, r.uid AS rec_uid, r.original_filename, r.duration_seconds AS rec_duration,
    r.width, r.height, r.video_codec, r.is_hdr, r.recorded_at AS rec_recorded_at,
    r.recorded_at_source AS rec_recorded_at_source, r.uploaded_at AS rec_uploaded_at, r.size_bytes,
    r.sha256, r.verified_at, r.original_path, r.playback_path, r.audio_path, r.preview_path,
    r.preview_frames, r.peaks_path"""
CLIP_FROM = "clips c JOIN recordings r ON r.id = c.recording_id"

# Adds a `processing` column for clip rows selected as `c`: the recording's playback copy
# or the clip's thumbnail still being made (or failed). The extras don't count.
PROCESSING_SQL = """
(SELECT CASE
    WHEN max(j.state IN ('queued', 'running')) = 1 THEN 'pending'
    WHEN max(j.state = 'failed') = 1 THEN 'failed'
 END FROM jobs j
 WHERE (j.recording_id = c.recording_id AND j.type = 'playback')
    OR (j.clip_id = c.id AND j.type = 'thumbnail')) AS processing
"""


def clip_summary(row: sqlite3.Row) -> ClipSummary:
    return ClipSummary(
        id=row["id"],
        uid=row["uid"],
        title=row["title"],
        status=row["status"],
        media_type=row["media_type"],
        recorded_at=row["recorded_at"],
        uploaded_at=row["uploaded_at"],
        duration_seconds=row["duration_seconds"],
        thumbnail_url=f"/api/clips/{row['id']}/thumbnail" if row["thumbnail_path"] else None,
        processing=row["processing"] if "processing" in row.keys() else None,
        preview_url=f"/api/clips/{row['id']}/preview" if row["preview_path"] else None,
        preview_frames=row["preview_frames"] if row["preview_path"] else None,
        waveform=json.loads(row["waveform"]) if row["waveform"] else None,
        role=row["role"],
        recording_id=row["recording_id"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"] if row["end_seconds"] is not None else row["rec_duration"],
        recording_duration=row["rec_duration"],
    )


# The id of a song's newest clip with a thumbnail, for projects selected as `p`: its
# automatic cover.
NEWEST_THUMB_SQL = """
(SELECT c.id FROM clips c
 WHERE c.project_id = p.id AND c.deleted_at IS NULL AND c.thumbnail_path IS NOT NULL
 ORDER BY coalesce(c.recorded_at, c.uploaded_at) DESC, c.id DESC LIMIT 1) AS newest_thumb_clip_id
"""


def cover_of(row: sqlite3.Row) -> Cover:
    """A song's picture: the chosen one, else its newest thumbnail. `row` is a project
    row with NEWEST_THUMB_SQL."""
    if row["cover_path"]:
        # The file name changes with every new picture, so the URL can be cached for long.
        version = row["cover_path"].rsplit("-", 1)[-1].split(".")[0]
        return Cover(kind="image", url=f"/api/projects/{row['id']}/cover?v={version}", clip_id=row["cover_clip_id"])
    if row["cover_stock"]:
        return Cover(kind="stock", url=f"/covers/{row['cover_stock']}.svg", stock=row["cover_stock"])
    newest = row["newest_thumb_clip_id"]
    return Cover(kind="auto", url=f"/api/clips/{newest}/thumbnail" if newest else None)


def project_cover(conn: sqlite3.Connection, project_id: int) -> Cover:
    row = conn.execute(f"SELECT p.*, {NEWEST_THUMB_SQL} FROM projects p WHERE p.id = ?", (project_id,)).fetchone()
    return cover_of(row)


def get_project_row(conn: sqlite3.Connection, project_id: int, *, deleted: bool = False) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ? AND (deleted_at IS NOT NULL) = ?",
        (project_id, deleted),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return row


# Labels that apply to a project, in song-page order: Full Song first, then the
# project's saved order, then any labels without a saved position by default order.
# A part can have its own name in this song (e.g. Verse shown as "Verse 1").
SCOPE_LABELS_SQL = """
SELECT l.id, coalesce(plo.name, l.name) AS name, l.name AS own_name, l.project_id, l.is_full_song,
       coalesce(plo.collapsed, 0) AS collapsed, plo.position
FROM labels l
LEFT JOIN project_label_order plo ON plo.label_id = l.id AND plo.project_id = :pid
WHERE l.project_id IS NULL OR l.project_id = :pid
ORDER BY l.is_full_song DESC, plo.position IS NULL, plo.position, l.default_order, l.id
"""


def project_detail(conn: sqlite3.Connection, project_id: int) -> ProjectDetail:
    p = get_project_row(conn, project_id)
    labels = conn.execute(SCOPE_LABELS_SQL, {"pid": project_id}).fetchall()
    in_scope = {label["id"] for label in labels}

    # Newest first, except that a row's Current take is pinned to its left (spec 4.3).
    clips = conn.execute(
        """
        SELECT {CLIP_COLUMNS}, cl.label_id, {PROCESSING_SQL}
        FROM {CLIP_FROM}
        LEFT JOIN clip_labels cl ON cl.clip_id = c.id
        WHERE c.project_id = ? AND c.deleted_at IS NULL
        ORDER BY c.status = 'current' DESC, coalesce(c.recorded_at, c.uploaded_at) DESC, c.id DESC
        """.format(CLIP_COLUMNS=CLIP_COLUMNS, CLIP_FROM=CLIP_FROM, PROCESSING_SQL=PROCESSING_SQL),
        (project_id,),
    ).fetchall()

    by_label: dict[int, list[ClipSummary]] = {}
    placed: set[int] = set()
    seen: dict[int, sqlite3.Row] = {}
    for row in clips:
        seen.setdefault(row["id"], row)
        if row["label_id"] in in_scope:
            by_label.setdefault(row["label_id"], []).append(clip_summary(row))
            placed.add(row["id"])
    unlabeled = [clip_summary(r) for cid, r in seen.items() if cid not in placed]
    lyric_sections = conn.execute(
        "SELECT json_array_length(sections) FROM lyrics WHERE project_id = ?", (project_id,)
    ).fetchone()

    return ProjectDetail(
        id=p["id"],
        name=p["name"],
        slug=p["slug"],
        status=p["status"],
        kind=p["kind"],
        archived=bool(p["archived"]),
        notes=p["notes"],
        song_key=p["song_key"],
        bpm=p["bpm"],
        tuning=p["tuning"],
        capo=p["capo"],
        time_signature=p["time_signature"],
        sounds_like=p["sounds_like"],
        progressions=json.loads(p["progressions"]),
        strumming=json.loads(p["strumming"]),
        lyric_sections=lyric_sections[0] if lyric_sections else 0,
        created_at=p["created_at"],
        updated_at=p["updated_at"],
        clip_count=len(seen),
        cover=project_cover(conn, project_id),
        unlabeled=unlabeled,
        rows=[
            Row(
                label_id=label["id"],
                name=label["name"],
                is_full_song=bool(label["is_full_song"]),
                is_custom=label["project_id"] is not None,
                collapsed=bool(label["collapsed"]),
                clips=by_label.get(label["id"], []),
            )
            for label in labels
        ],
    )


def touch_song_notes(conn: sqlite3.Connection, project_id: int) -> None:
    """Notebook notes show their songs' names and whether they're in Trash (spec 5.11).
    When that changes, give the song's notes new change numbers so devices fetch them
    again. Runs inside the caller's transaction (as notes.next_seq does)."""
    for (note_id,) in conn.execute("SELECT note_id FROM note_songs WHERE project_id = ?", (project_id,)).fetchall():
        seq = conn.execute("UPDATE note_clock SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchall()[0][0]
        conn.execute("UPDATE notes SET seq = ? WHERE id = ?", (seq, note_id))


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- routes ------------------------------------------------------------------------


@router.get("")
def list_projects(
    q: str | None = Query(default=None, max_length=200),
    status_filter: ProjectStatus | None = Query(default=None, alias="status"),
    kind_filter: ProjectKind | None = Query(default=None, alias="kind"),
    include_archived: bool = False,
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ProjectSummary]:
    where = ["p.deleted_at IS NULL"]
    params: dict[str, object] = {}
    if not include_archived:
        where.append("p.archived = 0")
    if status_filter:
        where.append("p.status = :status")
        params["status"] = status_filter
    if kind_filter:
        where.append("p.kind = :kind")
        params["kind"] = kind_filter
    if q and q.strip():
        # Song name, general notes, or the words on the lyrics page.
        where.append(
            "(p.name LIKE :q ESCAPE '\\' OR p.notes LIKE :q ESCAPE '\\' OR EXISTS ("
            "SELECT 1 FROM lyrics ly WHERE ly.project_id = p.id AND ly.plain_text LIKE :q ESCAPE '\\'))"
        )
        params["q"] = _like(q.strip())

    rows = conn.execute(
        f"""
        SELECT p.*,
          (SELECT count(*) FROM clips c
             WHERE c.project_id = p.id AND c.deleted_at IS NULL) AS clip_count,
          {NEWEST_THUMB_SQL}
        FROM projects p
        WHERE {" AND ".join(where)}
        ORDER BY p.updated_at DESC, p.id DESC
        """,
        params,
    ).fetchall()
    summaries = []
    for r in rows:
        cover = cover_of(r)
        summaries.append(
            ProjectSummary(
                id=r["id"],
                name=r["name"],
                slug=r["slug"],
                status=r["status"],
                kind=r["kind"],
                archived=bool(r["archived"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                clip_count=r["clip_count"],
                cover_thumbnail_url=cover.url,
                cover=cover,
            )
        )
    return summaries


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, conn: sqlite3.Connection = Depends(get_db)) -> ProjectDetail:
    now = now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO projects (name, slug, status, kind, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body.name, unique_slug(conn, body.name), body.status, body.kind, body.notes, now, now),
        )
    return project_detail(conn, cur.lastrowid)


@router.get("/{project_id}")
def get_project(project_id: int, conn: sqlite3.Connection = Depends(get_db)) -> ProjectDetail:
    return project_detail(conn, project_id)


def rename_media_folder(settings: Settings, conn: sqlite3.Connection, project_id: int, old: str, new: str) -> None:
    """Rename <media>/<old> to <media>/<new> and repoint every path in it: recordings'
    files and clips' thumbnails. Runs inside the caller's transaction; the folder is
    renamed back if that fails."""
    src = settings.media_dir / old
    dest = settings.media_dir / new
    moved = False
    if src.is_dir():
        if dest.exists():
            raise HTTPException(status.HTTP_409_CONFLICT, f"A media folder named {new} already exists")
        src.rename(dest)
        moved = True
    try:
        n = len(old) + 1
        params = {"n": n, "prefix": f"{old}/", "new": new}

        def repoint(columns: tuple[str, ...]) -> str:
            return ", ".join(
                f"{c} = CASE WHEN substr({c}, 1, :n) = :prefix THEN :new || substr({c}, :n) ELSE {c} END"
                for c in columns
            )

        conn.execute(f"UPDATE recordings SET {repoint(media.RECORDING_FILES)}", params)
        conn.execute(f"UPDATE clips SET {repoint(media.CLIP_FILES)}", params)
    except Exception:
        if moved:
            dest.rename(src)
        raise


@router.patch("/{project_id}")
def update_project(
    project_id: int, body: ProjectUpdate, request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> ProjectDetail:
    current = get_project_row(conn, project_id)
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "status", "kind", "notes", "archived", "progressions", "strumming"):
        if required in changes and changes[required] is None:
            raise HTTPException(422, f"{required} can't be empty")
    if not changes:
        return project_detail(conn, project_id)
    if "name" in changes:
        changes["slug"] = unique_slug(conn, changes["name"], exclude_id=project_id)
    if "archived" in changes:
        changes["archived"] = int(changes["archived"])
    for column in ("progressions", "strumming"):
        if column in changes:
            changes[column] = json.dumps(changes[column])
    changes["updated_at"] = now_iso()
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    with conn:
        if changes.get("slug", current["slug"]) != current["slug"]:
            rename_media_folder(request.app.state.settings, conn, project_id, current["slug"], changes["slug"])
        conn.execute(f"UPDATE projects SET {assignments} WHERE id = :id", {**changes, "id": project_id})
        if changes.get("name", current["name"]) != current["name"]:
            touch_song_notes(conn, project_id)
    return project_detail(conn, project_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, conn: sqlite3.Connection = Depends(get_db)) -> None:
    """Move the project (and with it, its clips) to Trash. Files are untouched."""
    get_project_row(conn, project_id)
    with conn:
        conn.execute("UPDATE projects SET deleted_at = ? WHERE id = ?", (now_iso(), project_id))
        touch_song_notes(conn, project_id)


@router.post("/{project_id}/restore")
def restore_project(project_id: int, conn: sqlite3.Connection = Depends(get_db)) -> ProjectDetail:
    get_project_row(conn, project_id, deleted=True)
    with conn:
        conn.execute(
            "UPDATE projects SET deleted_at = NULL, updated_at = ? WHERE id = ?", (now_iso(), project_id)
        )
        touch_song_notes(conn, project_id)
    return project_detail(conn, project_id)


@router.put("/{project_id}/rows")
def save_rows(project_id: int, body: RowsUpdate, conn: sqlite3.Connection = Depends(get_db)) -> ProjectDetail:
    """Save the song page's row order and which rows are collapsed."""
    get_project_row(conn, project_id)
    in_scope = {r["id"] for r in conn.execute(SCOPE_LABELS_SQL, {"pid": project_id})}
    ids = [r.label_id for r in body.rows]
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A part is listed twice")
    unknown = set(ids) - in_scope
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown part ids: {sorted(unknown)}")
    with conn:
        # Update in place: a part's name in this song is kept.
        conn.executemany(
            """
            INSERT INTO project_label_order (project_id, label_id, position, collapsed) VALUES (?, ?, ?, ?)
            ON CONFLICT (project_id, label_id) DO UPDATE SET position = excluded.position, collapsed = excluded.collapsed
            """,
            [(project_id, r.label_id, i, int(r.collapsed)) for i, r in enumerate(body.rows)],
        )
    return project_detail(conn, project_id)


# --- a song's parts: names in this song, numbered repeats (spec 3.3) -----------------------

NUMBERED_PART = re.compile(r"^(.*\S)\s+(\d{1,3})$")


def split_number(name: str) -> tuple[str, int | None]:
    """"Verse 2" -> ("Verse", 2); "Pre-Chorus" -> ("Pre-Chorus", None)."""
    m = NUMBERED_PART.match(name)
    return (m.group(1), int(m.group(2))) if m else (name, None)


class PartRename(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Name can't be empty")
        return value


def _song_parts(conn: sqlite3.Connection, project_id: int) -> list[sqlite3.Row]:
    return conn.execute(SCOPE_LABELS_SQL, {"pid": project_id}).fetchall()


def _keep_order(conn: sqlite3.Connection, project_id: int, parts: list[sqlite3.Row]) -> None:
    """Give every part of the song a saved position (in its current order), so one
    part's saved name or a new part doesn't move the others."""
    if all(p["position"] is not None for p in parts):
        return
    conn.executemany(
        """
        INSERT INTO project_label_order (project_id, label_id, position, collapsed) VALUES (?, ?, ?, ?)
        ON CONFLICT (project_id, label_id) DO UPDATE SET position = excluded.position
        """,
        [(project_id, p["id"], i, p["collapsed"]) for i, p in enumerate(parts)],
    )


def _set_part_name(conn: sqlite3.Connection, project_id: int, part: sqlite3.Row, name: str) -> None:
    """Rename a part in this song: a song's own part is renamed; a shared part (Verse,
    Chorus...) gets a name for this song only."""
    if part["project_id"] == project_id:
        conn.execute("UPDATE labels SET name = ? WHERE id = ?", (name, part["id"]))
    else:
        conn.execute(
            "UPDATE project_label_order SET name = ? WHERE project_id = ? AND label_id = ?",
            (None if name == part["own_name"] else name, project_id, part["id"]),
        )


def _check_part_name(parts: list[sqlite3.Row], name: str, exclude_id: int | None = None) -> None:
    if any(p["name"].lower() == name.lower() and p["id"] != exclude_id for p in parts):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This song already has a part called {name}")


@router.patch("/{project_id}/rows/{label_id}")
def rename_part(
    project_id: int, label_id: int, body: PartRename, conn: sqlite3.Connection = Depends(get_db)
) -> ProjectDetail:
    """Rename a part in this song only (other songs keep the shared name)."""
    get_project_row(conn, project_id)
    parts = _song_parts(conn, project_id)
    part = next((p for p in parts if p["id"] == label_id), None)
    if part is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Part not found")
    if part["is_full_song"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Full Song can't be renamed")
    _check_part_name(parts, body.name, exclude_id=label_id)
    with conn:
        _keep_order(conn, project_id, parts)
        _set_part_name(conn, project_id, part, body.name)
    return project_detail(conn, project_id)


@router.post("/{project_id}/rows/{label_id}/another")
def add_another_part(project_id: int, label_id: int, conn: sqlite3.Connection = Depends(get_db)) -> ProjectDetail:
    """A second (third...) row for a part that repeats with differences: the song's
    Verse becomes "Verse 1" and "Verse 2" goes in right after it."""
    get_project_row(conn, project_id)
    parts = _song_parts(conn, project_id)
    source = next((p for p in parts if p["id"] == label_id), None)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Part not found")
    if source["is_full_song"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Full Song can't have a second row")
    base = split_number(source["name"])[0]
    group = [p for p in parts if split_number(p["name"])[0].lower() == base.lower()]
    numbers = [split_number(p["name"])[1] for p in group]
    new_name = f"{base} {max(n or 1 for n in numbers) + 1}"
    if any(p["name"].lower() == new_name.lower() for p in parts) or conn.execute(
        "SELECT 1 FROM labels WHERE name = ? COLLATE NOCASE AND (project_id IS NULL OR project_id = ?)",
        (new_name, project_id),
    ).fetchone():
        raise HTTPException(status.HTTP_409_CONFLICT, f"This song already has a part called {new_name}")

    with conn:
        _keep_order(conn, project_id, parts)
        if 1 not in numbers:  # the first one gets its number too: Verse -> Verse 1
            for p in group:
                if split_number(p["name"])[1] is None:
                    _set_part_name(conn, project_id, p, f"{base} 1")
                    break
        # In right after the last of its group (saved positions can have gaps).
        group_ids = {p["id"] for p in group}
        last = [p["id"] for p in parts if p["id"] in group_ids][-1]
        after = conn.execute(
            "SELECT position FROM project_label_order WHERE project_id = ? AND label_id = ?", (project_id, last)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO labels (name, project_id, default_order) "
            "VALUES (?, ?, 1000 + (SELECT count(*) FROM labels WHERE project_id = ?))",
            (new_name, project_id, project_id),
        )
        conn.execute(
            "UPDATE project_label_order SET position = position + 1 WHERE project_id = ? AND position > ?",
            (project_id, after),
        )
        conn.execute(
            "INSERT INTO project_label_order (project_id, label_id, position) VALUES (?, ?, ?)",
            (project_id, cur.lastrowid, after + 1),
        )
    return project_detail(conn, project_id)

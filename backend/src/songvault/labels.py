"""Labels = song parts (spec 3.3). Global labels apply to every project;
custom labels can also belong to a single project."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from .deps import get_db
from .projects import SCOPE_LABELS_SQL, get_project_row

router = APIRouter(prefix="/api/labels", tags=["labels"])


class Label(BaseModel):
    id: int
    name: str
    project_id: int | None
    is_full_song: bool
    builtin: bool


def _clean(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Name can't be empty")
    return value


class LabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    project_id: int | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean(value)


class LabelUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean(value)


def _to_label(row: sqlite3.Row) -> Label:
    return Label(
        id=row["id"],
        name=row["name"],
        project_id=row["project_id"],
        is_full_song=bool(row["is_full_song"]),
        builtin=bool(row["builtin"]),
    )


def _get(conn: sqlite3.Connection, label_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Part not found")
    return row


def _check_name_free(
    conn: sqlite3.Connection, name: str, project_id: int | None, exclude_id: int | None = None
) -> None:
    """A name must be unique among global labels plus the labels of the same project.
    A global label must also not clash with any project's custom label."""
    if project_id is None:
        clash = conn.execute(
            "SELECT 1 FROM labels WHERE name = ? COLLATE NOCASE AND id IS NOT ?", (name, exclude_id)
        ).fetchone()
    else:
        clash = conn.execute(
            "SELECT 1 FROM labels WHERE name = ? COLLATE NOCASE AND id IS NOT ? "
            "AND (project_id IS NULL OR project_id = ?)",
            (name, exclude_id, project_id),
        ).fetchone()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a part called {name}")


@router.get("")
def list_labels(project_id: int | None = None, conn: sqlite3.Connection = Depends(get_db)) -> list[Label]:
    """The shared parts, or a song's parts as that song names and orders them."""
    rows = conn.execute(
        """
        SELECT l.id, coalesce(plo.name, l.name) AS name, l.project_id, l.is_full_song, l.builtin
        FROM labels l
        LEFT JOIN project_label_order plo ON plo.label_id = l.id AND plo.project_id = :pid
        WHERE l.project_id IS NULL OR l.project_id = :pid
        ORDER BY l.is_full_song DESC, plo.position IS NULL, plo.position, l.default_order, l.id
        """,
        {"pid": project_id},
    ).fetchall()
    return [_to_label(r) for r in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_label(body: LabelCreate, conn: sqlite3.Connection = Depends(get_db)) -> Label:
    if body.project_id is not None:
        get_project_row(conn, body.project_id)
        # Also not the name a shared part has in this song (Verse shown as "Verse 1").
        for part in conn.execute(SCOPE_LABELS_SQL, {"pid": body.project_id}):
            if part["name"].lower() == body.name.lower():
                raise HTTPException(status.HTTP_409_CONFLICT, f"There's already a part called {body.name}")
    _check_name_free(conn, body.name, body.project_id)
    if body.project_id is None:
        order = conn.execute(
            "SELECT coalesce(max(default_order), 0) + 1 FROM labels WHERE project_id IS NULL"
        ).fetchone()[0]
    else:
        # Project-specific parts go after the global ones until the rows are rearranged.
        order = conn.execute(
            "SELECT 1000 + count(*) FROM labels WHERE project_id = ?", (body.project_id,)
        ).fetchone()[0]
    with conn:
        cur = conn.execute(
            "INSERT INTO labels (name, project_id, default_order) VALUES (?, ?, ?)",
            (body.name, body.project_id, order),
        )
    return _to_label(_get(conn, cur.lastrowid))


@router.patch("/{label_id}")
def rename_label(label_id: int, body: LabelUpdate, conn: sqlite3.Connection = Depends(get_db)) -> Label:
    row = _get(conn, label_id)
    if row["builtin"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Built-in parts can't be renamed")
    _check_name_free(conn, body.name, row["project_id"], exclude_id=label_id)
    with conn:
        conn.execute("UPDATE labels SET name = ? WHERE id = ?", (body.name, label_id))
    return _to_label(_get(conn, label_id))


@router.delete("/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_label(label_id: int, conn: sqlite3.Connection = Depends(get_db)) -> None:
    row = _get(conn, label_id)
    if row["builtin"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Built-in parts can't be deleted")
    if conn.execute("SELECT 1 FROM clip_labels WHERE label_id = ? LIMIT 1", (label_id,)).fetchone():
        raise HTTPException(status.HTTP_409_CONFLICT, "Move the clips out of this part first")
    with conn:
        conn.execute("DELETE FROM labels WHERE id = ?", (label_id,))

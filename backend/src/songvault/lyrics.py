"""The lyrics page: one chord/lyric sheet per project (spec 5.6).

A sheet is a list of labeled sections. Chords sit inline in square brackets in
front of the syllable they land on (ChordPro style): "[D]Hello [Bm]darkness".

Saves carry the revision they were based on. If another device saved in the
meantime, the save is refused (409) instead of silently overwriting it.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from .db import now_iso
from .deps import get_db
from .projects import get_project_row

router = APIRouter(prefix="/api/projects", tags=["lyrics"])

CHORD_MARK = re.compile(r"\[[^\[\]\n]{1,16}\]")


class Section(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(default="", max_length=60)
    kind: Literal["lyrics", "chords"] = "lyrics"
    """chords = an instrumental section: `text` is just chord lines, e.g. "D - Bm - G - A"."""
    text: str = Field(default="", max_length=50_000)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        return " ".join(value.split())


class Lyrics(BaseModel):
    sections: list[Section]
    revision: int
    updated_at: str | None


class LyricsSave(BaseModel):
    sections: list[Section] = Field(max_length=200)
    revision: int = Field(ge=0)
    """The revision this edit started from."""


def plain_text(sections: list[Section]) -> str:
    """Labels and words without chord marks, for search."""
    return "\n\n".join(
        f"{s.label}\n{CHORD_MARK.sub('', s.text) if s.kind == 'lyrics' else ''}".strip() for s in sections
    )


def read_lyrics(conn: sqlite3.Connection, project_id: int) -> Lyrics:
    row = conn.execute(
        "SELECT sections, revision, updated_at FROM lyrics WHERE project_id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return Lyrics(sections=[], revision=0, updated_at=None)
    return Lyrics(sections=json.loads(row["sections"]), revision=row["revision"], updated_at=row["updated_at"])


@router.get("/{project_id}/lyrics")
def get_lyrics(project_id: int, conn: sqlite3.Connection = Depends(get_db)) -> Lyrics:
    get_project_row(conn, project_id)
    return read_lyrics(conn, project_id)


@router.put("/{project_id}/lyrics")
def save_lyrics(project_id: int, body: LyricsSave, conn: sqlite3.Connection = Depends(get_db)) -> Lyrics:
    get_project_row(conn, project_id)
    ids = [s.id for s in body.sections]
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Two sections have the same id")
    now = now_iso()
    sections = json.dumps([s.model_dump() for s in body.sections])
    with conn:
        current = conn.execute("SELECT revision FROM lyrics WHERE project_id = ?", (project_id,)).fetchone()
        if (current["revision"] if current else 0) != body.revision:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "These lyrics were changed on another device. Reload to see them."
            )
        conn.execute(
            """
            INSERT INTO lyrics (project_id, sections, plain_text, revision, updated_at)
            VALUES (:pid, :sections, :plain, :revision, :now)
            ON CONFLICT (project_id) DO UPDATE SET
              sections = excluded.sections, plain_text = excluded.plain_text,
              revision = excluded.revision, updated_at = excluded.updated_at
            """,
            {
                "pid": project_id,
                "sections": sections,
                "plain": plain_text(body.sections),
                "revision": body.revision + 1,
                "now": now,
            },
        )
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, project_id))
    return read_lyrics(conn, project_id)

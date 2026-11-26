"""Export a song as a zip (spec 5.8): its original recordings, each trimmed or split clip
cut out of them (spec 5.10, without re-encoding), the song notes, the lyrics page (as a
plain-text chord sheet and as ChordPro), the Notebook notes linked to the song (spec
5.11), and a JSON manifest tying it together.

The zip is streamed while it's built. Recordings are stored as they are (video doesn't
compress further), so a song with gigabytes of video never has to fit in memory or on
the server's disk first.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import zipfile
from collections.abc import Callable, Iterator
from functools import partial
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from . import media
from .clips import clock_name, is_whole
from .config import Settings
from .deps import get_db
from .notes import Note, song_notes
from .projects import CLIP_COLUMNS, CLIP_FROM, SCOPE_LABELS_SQL, get_project_row

log = logging.getLogger("songvault.export")

router = APIRouter(prefix="/api/projects", tags=["export"])

CHORD_MARK = re.compile(r"\[([^\[\]\n]{1,16})\]")
CHORD_SEPARATORS = re.compile(r"[\s,|–—-]+")
FORMAT_VERSION = 1


# --- text versions of the song ---------------------------------------------------------


def _facts(song: sqlite3.Row) -> list[tuple[str, str]]:
    bpm = song["bpm"]
    values = [
        ("Key", song["song_key"]),
        ("BPM", f"{bpm:g}" if bpm else None),
        ("Time signature", song["time_signature"]),
        ("Capo", str(song["capo"]) if song["capo"] is not None else None),
        ("Tuning", song["tuning"]),
        ("Sounds like", song["sounds_like"]),
    ]
    return [(label, value) for label, value in values if value]


def song_notes_text(song: sqlite3.Row) -> str:
    lines = [song["name"], f"Status: {song['status'].capitalize()}"]
    lines += [f"{label}: {value}" for label, value in _facts(song)]
    progressions = json.loads(song["progressions"])
    if progressions:
        lines += ["", "Chord progressions"]
        lines += [f"  {p['label']}: {' - '.join(p['chords'])}" for p in progressions]
    strumming = json.loads(song["strumming"])
    if strumming:
        lines += ["", "Strumming (D down, U up, X muted, - miss)"]
        lines += [f"  {s['label']}: {s['pattern']}" if s["label"] else f"  {s['pattern']}" for s in strumming]
    if song["notes"].strip():
        lines += ["", "Notes", song["notes"].strip()]
    return "\n".join(lines) + "\n"


def chord_sheet_line(line: str) -> list[str]:
    """`[D]Hello [Bm]darkness` -> chords over the letters they land on, for a monospace font:
    ["D     Bm", "Hello darkness"]. Chords that would touch are pushed apart."""
    text, chords, last = "", [], 0
    for m in CHORD_MARK.finditer(line):
        text += line[last : m.start()]
        chords.append((len(text), m.group(1).strip()))
        last = m.end()
    text += line[last:]
    if not chords:
        return [text.rstrip()]
    row = ""
    for pos, chord in chords:
        start = max(pos, len(row) + 1) if row else pos
        row = row.ljust(start) + chord
    return [row.rstrip(), text.rstrip()] if text.strip() else [row.rstrip()]


def sheet_facts(song: sqlite3.Row) -> str:
    """The line under the title, as on the lyrics page: "Key D · 92 BPM · 4/4 · Capo 2"."""
    facts = []
    if song["song_key"]:
        facts.append(f"Key {song['song_key']}")
    if song["bpm"]:
        facts.append(f"{song['bpm']:g} BPM")
    if song["time_signature"]:
        facts.append(song["time_signature"])
    if song["capo"]:
        facts.append(f"Capo {song['capo']}")
    if song["tuning"] and song["tuning"] != "Standard":
        facts.append(song["tuning"])
    return " · ".join(facts)


def chord_sheet_text(song: sqlite3.Row, sections: list[dict]) -> str:
    facts = sheet_facts(song)
    lines = [song["name"]] + ([facts] if facts else [])
    for s in sections:
        lines += ["", f"[{s['label']}]" if s["label"] else ""]
        for line in s["text"].split("\n"):
            lines += [line.strip()] if s.get("kind") == "chords" else chord_sheet_line(line)
    return "\n".join(lines).rstrip() + "\n"


def notebook_text(song: sqlite3.Row, notes: list[Note]) -> str:
    """The song's Notebook notes, newest first, each under the date it was written."""
    lines = [f"{song['name']}: notes from the Notebook"]
    for n in notes:
        used = " (used)" if n.status == "used" else ""
        lines += ["", f"{n.created_at[:10]}{used}", n.body.rstrip()]
    return "\n".join(lines) + "\n"


def chordpro_text(song: sqlite3.Row, sections: list[dict]) -> str:
    """The lyrics page in ChordPro, which other chord-sheet apps can open."""
    lines = [f"{{title: {song['name']}}}"]
    for directive, column in (("key", "song_key"), ("tempo", "bpm"), ("time", "time_signature"), ("capo", "capo")):
        value = song[column]
        if value is not None and value != "":
            lines.append(f"{{{directive}: {value:g}}}" if isinstance(value, float) else f"{{{directive}: {value}}}")
    for s in sections:
        label = s["label"]
        kind = next((k for k in ("chorus", "verse", "bridge") if label.lower().startswith(k)), None)
        lines.append("")
        if kind:
            lines.append(f"{{start_of_{kind}: {label}}}")
        elif label:
            lines.append(f"{{comment: {label}}}")
        for line in s["text"].split("\n"):
            if s.get("kind") == "chords":
                chords = [c for c in CHORD_SEPARATORS.split(line) if c]
                lines.append(" ".join(f"[{c}]" for c in chords))
            else:
                lines.append(line)
        if kind:
            lines.append(f"{{end_of_{kind}}}")
    return "\n".join(lines).rstrip() + "\n"


# --- streaming the zip ---------------------------------------------------------------------


class _Pipe:
    """A write-only file for ZipFile: holds what's written until the response sends it.
    It can't seek, so ZipFile writes each entry's sizes after its data."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._parts.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def cut_to_temp(settings: Settings, src: Path, start: float, end: float) -> Path:
    """A stretch of a recording in a temporary file (deleted once it's in the zip)."""
    folder = settings.media_dir / media.CUTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{media.new_uid()}{src.suffix.lower()}"
    media.run_ffmpeg(media.cut_args(src, tmp, start, end), settings.ffmpeg, timeout=900)
    return tmp


FileSource = Path | Callable[[], Path]


def zip_stream(texts: list[tuple[str, str]], files: list[tuple[str, FileSource]]) -> Iterator[bytes]:
    """Each file is a path, or a function that makes a temporary file (a cut clip)."""
    pipe = _Pipe()
    now = datetime.now().timetuple()[:6]
    with zipfile.ZipFile(pipe, "w") as zf:
        for name, text in texts:
            info = zipfile.ZipInfo(name, now)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8"))
            yield pipe.take()
        for name, source in files:
            temp = None
            if callable(source):
                try:
                    source = temp = source()
                except Exception:  # noqa: BLE001 - leave this one out, keep going
                    log.exception("export: couldn't cut %s", name)
                    continue
            try:
                info = zipfile.ZipInfo.from_file(source, name)
                info.compress_type = zipfile.ZIP_STORED
                with source.open("rb") as src, zf.open(info, "w", force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as dest:
                    while chunk := src.read(1 << 20):
                        dest.write(chunk)
                        yield pipe.take()
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
    yield pipe.take()


@router.get("/{project_id}/export")
def export_project(project_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> StreamingResponse:
    """Download the song as `<slug>.zip`. Clips in Trash are left out. A recording's
    original is in once however many clips were made from it."""
    settings = request.app.state.settings
    song = get_project_row(conn, project_id)
    slug = song["slug"]
    lyrics_row = conn.execute("SELECT sections, revision FROM lyrics WHERE project_id = ?", (project_id,)).fetchone()
    sections = json.loads(lyrics_row["sections"]) if lyrics_row else []
    notebook = song_notes(conn, project_id)
    parts = {label["id"]: label["name"] for label in conn.execute(SCOPE_LABELS_SQL, {"pid": project_id})}
    clip_rows = conn.execute(
        f"SELECT {CLIP_COLUMNS} FROM {CLIP_FROM} WHERE c.project_id = ? AND c.deleted_at IS NULL "
        "ORDER BY coalesce(c.recorded_at, c.uploaded_at), c.id",
        (project_id,),
    ).fetchall()

    files: list[tuple[str, FileSource]] = []
    originals: dict[int, str | None] = {}
    clips = []
    for c in clip_rows:
        src = media.resolve(settings.media_dir, c["original_path"])
        if c["recording_id"] not in originals:
            name = f"{slug}/originals/{PurePosixPath(c['original_path']).name}"
            originals[c["recording_id"]] = name if src.is_file() else None
            if src.is_file():
                files.append((name, src))
        original = originals[c["recording_id"]]
        end = c["end_seconds"] if c["end_seconds"] is not None else c["rec_duration"]
        cut = None
        if original and end and not is_whole(c["start_seconds"], c["end_seconds"], c["rec_duration"]):
            cut = (f"{slug}/clips/{PurePosixPath(c['original_path']).stem} "
                   f"{clock_name(c['start_seconds'])}-{clock_name(end)}{src.suffix.lower()}")  # fmt: skip
            files.append((cut, partial(cut_to_temp, settings, src, c["start_seconds"], end)))
        label_ids = [r[0] for r in conn.execute("SELECT label_id FROM clip_labels WHERE clip_id = ?", (c["id"],))]
        clips.append({
            "file": (cut or original or "").removeprefix(f"{slug}/") or None,
            "recording": original.removeprefix(f"{slug}/") if original else None,
            "start_seconds": c["start_seconds"],
            "end_seconds": end,
            "original_filename": c["original_filename"],
            "title": c["title"],
            "notes": c["notes"],
            "status": c["status"],
            "role": c["role"],
            "parts": [parts[i] for i in parts if i in label_ids],
            "recorded_at": c["recorded_at"],
            "recorded_at_source": c["recorded_at_source"],
            "uploaded_at": c["uploaded_at"],
            "media_type": c["media_type"],
            "duration_seconds": c["duration_seconds"],
            "size_bytes": c["size_bytes"],
            "sha256": c["sha256"],
            "details": {
                "key": c["song_key"], "bpm": c["bpm"], "time_signature": c["time_signature"],
                "capo": c["capo"], "tuning": c["tuning"], "progressions": json.loads(c["progressions"]),
            },
        })  # fmt: skip

    manifest = {
        "format": "songvault-export",
        "version": FORMAT_VERSION,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "song": {
            "name": song["name"], "status": song["status"], "archived": bool(song["archived"]),
            "created_at": song["created_at"], "updated_at": song["updated_at"],
            "key": song["song_key"], "bpm": song["bpm"], "time_signature": song["time_signature"],
            "capo": song["capo"], "tuning": song["tuning"], "sounds_like": song["sounds_like"],
            "progressions": json.loads(song["progressions"]), "strumming": json.loads(song["strumming"]),
            "notes": song["notes"],
        },
        "parts": list(parts.values()),
        "lyrics": sections,
        "clips": clips,
        "notebook": [
            {"text": n.body, "written_at": n.created_at, "status": n.status, "starred": n.starred} for n in notebook
        ],
    }  # fmt: skip
    texts = [
        (f"{slug}/manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"),
        (f"{slug}/song-notes.txt", song_notes_text(song)),
    ]
    if sections:
        texts += [
            (f"{slug}/lyrics.txt", chord_sheet_text(song, sections)),
            (f"{slug}/lyrics.cho", chordpro_text(song, sections)),
        ]
    if notebook:
        texts.append((f"{slug}/notebook.txt", notebook_text(song, notebook)))
    return StreamingResponse(
        zip_stream(texts, files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"', "Cache-Control": "no-store"},
    )

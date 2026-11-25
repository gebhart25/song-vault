"""Song pictures on the Projects page (spec 3.1): automatic, stock, a clip's thumbnail,
or an upload."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from songvault import covers
from songvault.config import Settings
from songvault.db import MIGRATIONS_DIR, connect, migrate
from songvault.trash import purge_trash

from .test_rows_and_labels import _add_clip
from .test_trash import _age

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed"
)


def _card(authed: TestClient, project_id: int) -> dict:
    return next(p for p in authed.get("/api/projects").json() if p["id"] == project_id)


def _clip_with_thumbnail(settings: Settings, project_id: int, uid: str, when: str, content: bytes = b"thumb") -> int:
    conn = connect(settings.db_path)
    clip_id = _add_clip(conn, project_id, uid, when, [])
    rel = f"midnight-drive/{uid}.thumb.jpg"
    path = settings.media_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with conn:
        conn.execute("UPDATE clips SET thumbnail_path = ? WHERE id = ?", (rel, clip_id))
    conn.close()
    return clip_id


def _cover_files(settings: Settings) -> list[str]:
    folder = settings.media_dir / covers.COVERS_DIR
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def test_migration_adds_cover_columns(tmp_path: Path) -> None:
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS_DIR.glob("000[1-8]_*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 8")
    with conn:
        conn.execute(
            "INSERT INTO projects (name, slug, status, notes, created_at, updated_at) "
            "VALUES ('Old', 'old', 'idea', '', '2026-11-01T00:00:00.000Z', '2026-11-01T00:00:00.000Z')"
        )
    assert migrate(conn) >= 9
    row = conn.execute("SELECT cover_stock, cover_path, cover_clip_id FROM projects").fetchone()
    assert tuple(row) == (None, None, None)
    conn.close()


def test_automatic_cover_is_the_newest_thumbnail(authed: TestClient, project: dict, settings: Settings) -> None:
    assert _card(authed, project["id"])["cover"] == {"kind": "auto", "url": None, "stock": None, "clip_id": None}
    _clip_with_thumbnail(settings, project["id"], "old", "2026-10-01T12:00:00.000Z")
    newest = _clip_with_thumbnail(settings, project["id"], "new", "2026-11-20T12:00:00.000Z")
    card = _card(authed, project["id"])
    assert card["cover"]["kind"] == "auto"
    assert card["cover_thumbnail_url"] == card["cover"]["url"] == f"/api/clips/{newest}/thumbnail"
    assert authed.get(f"/api/projects/{project['id']}").json()["cover"]["url"] == f"/api/clips/{newest}/thumbnail"


def test_stock_cover(authed: TestClient, project: dict) -> None:
    pid = project["id"]
    r = authed.put(f"/api/projects/{pid}/cover", json={"kind": "stock", "stock": "night-drive"})
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "stock", "url": "/covers/night-drive.svg", "stock": "night-drive", "clip_id": None}
    assert _card(authed, pid)["cover_thumbnail_url"] == "/covers/night-drive.svg"

    for bad in ("../x", "Sunset", "a/b", ""):
        assert authed.put(f"/api/projects/{pid}/cover", json={"kind": "stock", "stock": bad}).status_code == 422
    assert authed.put(f"/api/projects/{pid}/cover", json={"kind": "stock"}).status_code == 422
    assert authed.put("/api/projects/999/cover", json={"kind": "auto"}).status_code == 404

    r = authed.put(f"/api/projects/{pid}/cover", json={"kind": "auto"})
    assert r.json()["kind"] == "auto"


def test_cover_from_a_clip_is_a_copy(authed: TestClient, project: dict, settings: Settings) -> None:
    pid = project["id"]
    clip_id = _clip_with_thumbnail(settings, pid, "take", "2026-11-20T12:00:00.000Z", b"frame")
    r = authed.put(f"/api/projects/{pid}/cover", json={"kind": "clip", "clip_id": clip_id})
    assert r.status_code == 200, r.text
    cover = r.json()
    assert cover["kind"] == "image" and cover["clip_id"] == clip_id
    assert cover["url"].startswith(f"/api/projects/{pid}/cover?v=")
    assert _card(authed, pid)["cover"] == cover
    assert authed.get(cover["url"]).content == b"frame"
    [first] = _cover_files(settings)

    # The copy stays when the clip's own thumbnail goes.
    (settings.media_dir / "midnight-drive" / "take.thumb.jpg").unlink()
    assert authed.get(cover["url"]).content == b"frame"

    # A new picture replaces the file; going back to automatic deletes it.
    other = _clip_with_thumbnail(settings, pid, "other", "2026-11-21T12:00:00.000Z", b"other")
    second = authed.put(f"/api/projects/{pid}/cover", json={"kind": "clip", "clip_id": other}).json()
    assert second["url"] != cover["url"]
    assert first not in _cover_files(settings) and len(_cover_files(settings)) == 1
    assert authed.put(f"/api/projects/{pid}/cover", json={"kind": "stock", "stock": "vinyl"}).status_code == 200
    assert _cover_files(settings) == []
    assert authed.get(f"/api/projects/{pid}/cover").status_code == 404

    # Only the song's own clips, and only ones with a thumbnail.
    assert authed.put(f"/api/projects/{pid}/cover", json={"kind": "clip", "clip_id": clip_id}).status_code == 409
    elsewhere = authed.post("/api/projects", json={"name": "Elsewhere"}).json()
    theirs = _clip_with_thumbnail(settings, elsewhere["id"], "theirs", "2026-11-22T12:00:00.000Z")
    assert authed.put(f"/api/projects/{pid}/cover", json={"kind": "clip", "clip_id": theirs}).status_code == 404
    assert authed.put(f"/api/projects/{pid}/cover", json={"kind": "clip"}).status_code == 422


def _image(tmp_path: Path, name: str, size: str) -> bytes:
    path = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}",
         "-frames:v", "1", str(path)],
        check=True,
    )  # fmt: skip
    return path.read_bytes()


def _size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    w, h = out.split(",")
    return int(w), int(h)


@needs_ffmpeg
def test_uploaded_cover(authed: TestClient, project: dict, settings: Settings, tmp_path: Path) -> None:
    pid = project["id"]
    for name in ("big.png", "photo.jpg", "art.webp"):
        r = authed.put(
            f"/api/projects/{pid}/cover/image", content=_image(tmp_path, name, "2400x1200"),
            headers={"Content-Type": "application/octet-stream"},
        )  # fmt: skip
        assert r.status_code == 200, (name, r.text)
        cover = r.json()
        assert cover["kind"] == "image" and cover["clip_id"] is None
        [stored] = _cover_files(settings)
        assert stored.endswith(".jpg")
        assert _size(settings.media_dir / covers.COVERS_DIR / stored) == (1600, 800)
        assert authed.get(cover["url"]).content[:3] == b"\xff\xd8\xff"

    r = authed.put(f"/api/projects/{pid}/cover/image", content=b"not a picture at all")
    assert r.status_code == 415
    r = authed.put(f"/api/projects/{pid}/cover/image", content=b"\x89PNG\r\n\x1a\nbroken")
    assert r.status_code == 422
    assert len(_cover_files(settings)) == 1  # nothing left behind by the failures
    assert authed.put("/api/projects/999/cover/image", content=b"\xff\xd8\xff").status_code == 404


def test_upload_size_limit(authed: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(covers, "MAX_UPLOAD", 100)
    r = authed.put(f"/api/projects/{project['id']}/cover/image", content=b"\xff\xd8\xff" + b"x" * 200)
    assert r.status_code == 413


def test_trash_shows_the_cover_and_purge_deletes_it(authed: TestClient, project: dict, settings: Settings) -> None:
    pid = project["id"]
    clip_id = _clip_with_thumbnail(settings, pid, "take", "2026-11-20T12:00:00.000Z")
    cover = authed.put(f"/api/projects/{pid}/cover", json={"kind": "clip", "clip_id": clip_id}).json()
    assert authed.delete(f"/api/projects/{pid}").status_code == 204
    [trashed] = authed.get("/api/trash").json()["projects"]
    assert trashed["cover_thumbnail_url"] == cover["url"]
    assert authed.get(cover["url"]).status_code == 200

    _age(settings, "projects", pid, 31)
    purge_trash(settings)
    assert _cover_files(settings) == []


def test_covers_need_sign_in(client: TestClient) -> None:
    assert client.put("/api/projects/1/cover", json={"kind": "auto"}).status_code == 401
    assert client.put("/api/projects/1/cover/image", content=b"x").status_code == 401
    assert client.get("/api/projects/1/cover").status_code == 401

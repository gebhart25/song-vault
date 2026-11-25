"""Clip editing (spec 5.10): a clip is a stretch of a recording; trim and split."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from songvault import jobs, media
from songvault.config import Settings
from songvault.db import MIGRATIONS_DIR, connect, migrate
from songvault.trash import purge_trash

from .test_rows_and_labels import label_id
from .test_trash import _age
from .test_uploads import upload

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed"
)


@pytest.fixture(scope="module")
def take(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """8 s of video; the sound is a tone, silent from 3 s to 5 s (a gap between two parts)."""
    path = tmp_path_factory.mktemp("take") / "20261124_101500.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=8",
         "-f", "lavfi", "-i", "aevalsrc='if(between(t,3,5),0,0.5*sin(2*PI*440*t))':s=44100:d=8",
         "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _upload_take(authed: TestClient, settings: Settings, take: Path, project: dict, part: str = "Verse") -> dict:
    _, r = upload(authed, take, project_id=project["id"], label_ids=[label_id(project, part)])
    assert r.status_code == 200, r.text
    jobs.run_all(settings)
    return authed.get(f"/api/clips/{r.json()['id']}").json()


def _row(authed: TestClient, project_id: int, name: str) -> list[dict]:
    return next(r for r in authed.get(f"/api/projects/{project_id}").json()["rows"] if r["name"] == name)["clips"]


# --- the migration ----------------------------------------------------------------------------


def test_migration_turns_clips_into_recordings(tmp_path: Path) -> None:
    conn = connect(tmp_path / "old.db")
    for path in sorted(MIGRATIONS_DIR.glob("000[1-7]_*.sql")):
        conn.executescript(f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {int(path.name[:4])};\nCOMMIT;")
    with conn:
        conn.execute(
            "INSERT INTO clips (uid, recorded_at, uploaded_at, original_filename, media_type, duration_seconds, "
            "original_path, playback_path, audio_path, thumbnail_path, preview_path, preview_frames, size_bytes, "
            "sha256, verified_at, role) VALUES ('abc', '2026-11-01T10:00:00.000Z', '2026-11-02T10:00:00.000Z', "
            "'VID.mp4', 'video', 12.5, '_inbox/a.mp4', '_inbox/a.mp4', '_inbox/a.audio.m4a', '_inbox/a.thumb.jpg', "
            "'_inbox/a.preview.jpg', 6, 99, 'f00', '2026-11-02T10:00:01.000Z', 'Lead')"
        )
        conn.execute(
            "INSERT INTO jobs (clip_id, type, state, created_at, updated_at) VALUES (1, 'playback', 'done', 'x', 'x')"
        )
    assert migrate(conn) >= 8

    rec = conn.execute("SELECT * FROM recordings").fetchone()
    assert (rec["id"], rec["uid"], rec["original_path"], rec["playback_path"], rec["sha256"], rec["duration_seconds"]) == (
        1, "abc", "_inbox/a.mp4", "_inbox/a.mp4", "f00", 12.5
    )
    assert rec["preview_path"] is None  # remade as a grid
    clip = conn.execute("SELECT * FROM clips").fetchone()
    assert (clip["recording_id"], clip["start_seconds"], clip["end_seconds"], clip["role"]) == (1, 0, None, "Lead")
    assert clip["thumbnail_path"] == "_inbox/a.thumb.jpg"
    assert "original_path" not in clip.keys()
    queued = {(j["recording_id"], j["clip_id"], j["type"]) for j in conn.execute("SELECT * FROM jobs")}
    assert queued == {(1, None, "preview"), (1, None, "peaks")}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# --- trim and split ----------------------------------------------------------------------------


@needs_ffmpeg
def test_waveform_and_preview_of_a_recording(authed: TestClient, project: dict, settings: Settings, take: Path) -> None:
    clip = _upload_take(authed, settings, take, project)
    rec = authed.get(f"/api/recordings/{clip['recording_id']}").json()
    assert 7.9 < rec["duration_seconds"] < 8.2
    assert rec["peaks_per_second"] == 20 and rec["preview_columns"] == 10
    assert rec["preview_frames"] == 8  # about a frame a second (one row: fewer than 10)
    assert [c["id"] for c in rec["clips"]] == [clip["id"]]

    points = authed.get(rec["peaks_url"]).json()
    assert 155 <= len(points) <= 165  # 20 a second
    assert max(points[70:90]) < 5 < min(points[10:50])  # the gap is quiet, the tone loud
    assert authed.get(rec["preview_url"]).headers["content-type"] == "image/jpeg"
    assert authed.get(rec["media_url"]).status_code == 200


@needs_ffmpeg
def test_trim_and_split(authed: TestClient, project: dict, settings: Settings, take: Path) -> None:
    pid = project["id"]
    verse, chorus = label_id(project, "Verse"), label_id(project, "Chorus")
    clip = _upload_take(authed, settings, take, project)
    rid = clip["recording_id"]
    authed.patch(f"/api/clips/{clip['id']}", json={"song_key": "D", "bpm": 92, "status": "current", "notes": "first take"})

    r = authed.put(f"/api/recordings/{rid}/clips", json={
        "from_clip": clip["id"],
        "pieces": [
            {"id": clip["id"], "start_seconds": 0.2, "end_seconds": 2.9, "label_ids": [verse]},
            {"start_seconds": 5.1, "end_seconds": 7.9, "label_ids": [chorus], "role": "Lead", "title": "Chorus lick"},
        ],
    })  # fmt: skip
    assert r.status_code == 200, r.text
    pieces = r.json()["clips"]
    assert [(p["start_seconds"], round(p["end_seconds"], 1)) for p in pieces] == [(0.2, 2.9), (5.1, 7.9)]

    first = authed.get(f"/api/clips/{clip['id']}").json()
    assert first["trimmed"] and first["pieces"] == 2 and first["cut_url"]
    assert round(first["duration_seconds"], 1) == 2.7
    assert (first["status"], first["notes"], first["label_ids"]) == ("current", "first take", [verse])

    second = authed.get(f"/api/clips/{pieces[1]['id']}").json()
    assert (second["role"], second["title"], second["status"], second["label_ids"]) == ("Lead", "Chorus lick", "idea", [chorus])
    assert (second["song_key"], second["bpm"], second["project_id"]) == ("D", 92, pid)  # details from the clip it came from
    assert second["notes"] == ""
    # Dated when it was played: the recording's start plus 5.1 s.
    start = datetime.fromisoformat(first["recorded_at"].replace("Z", "+00:00")) - timedelta(seconds=0.2)
    assert datetime.fromisoformat(second["recorded_at"].replace("Z", "+00:00")) == start + timedelta(seconds=5.1)

    assert [c["id"] for c in _row(authed, pid, "Verse")] == [clip["id"]]
    assert [c["id"] for c in _row(authed, pid, "Chorus")] == [second["id"]]
    assert second["processing"] == "pending"  # its thumbnail
    jobs.run_all(settings)
    second = authed.get(f"/api/clips/{second['id']}").json()
    assert second["processing"] is None and second["thumbnail_url"]
    assert (settings.media_dir / "midnight-drive").glob(f"*.{second['uid']}.thumb.jpg")

    # Download just the stretch: cut without re-encoding (starts at the keyframe before 5.1 s).
    cut = authed.get(second["cut_url"])
    assert cut.status_code == 200 and cut.headers["content-type"] == "video/mp4"
    assert "0m05s-0m07s" in cut.headers["content-disposition"]
    got = settings.data_dir / "cut.mp4"
    got.write_bytes(cut.content)
    assert 2.6 <= media.probe(got).duration <= 3.9
    assert not any((settings.media_dir / media.CUTS_DIR).iterdir())  # the temporary file is gone
    assert authed.get(clip["original_url"]).content == take.read_bytes()  # the original is untouched

    # The upload history still lists the file once.
    assert [i["original_filename"] for i in authed.get("/api/clips/history").json()["items"]] == [take.name]

    # Export: the original once, plus each cut-out clip.
    zf = zipfile.ZipFile(io.BytesIO(authed.get(f"/api/projects/{pid}/export").content))
    names = zf.namelist()
    assert sum(n.startswith("midnight-drive/originals/") for n in names) == 1
    assert sum(n.startswith("midnight-drive/clips/") for n in names) == 2
    manifest = json.loads(zf.read("midnight-drive/manifest.json"))
    assert {(c["start_seconds"], c["file"].split("/")[0]) for c in manifest["clips"]} == {(0.2, "clips"), (5.1, "clips")}
    assert all(c["recording"].startswith("originals/") for c in manifest["clips"])


@needs_ffmpeg
def test_edit_rules(authed: TestClient, project: dict, settings: Settings, take: Path) -> None:
    verse = label_id(project, "Verse")
    clip = _upload_take(authed, settings, take, project)
    rid, cid = clip["recording_id"], clip["id"]

    def put(pieces: list[dict], removed: list[int] | None = None) -> object:
        return authed.put(f"/api/recordings/{rid}/clips", json={"from_clip": cid, "pieces": pieces, "removed": removed or []})

    assert put([{"id": cid, "start_seconds": 1, "end_seconds": 1.3}]).status_code == 422  # too short
    assert put([{"id": cid, "start_seconds": 1, "end_seconds": 20}]).status_code == 422  # past the end
    assert put([{"start_seconds": 1, "end_seconds": 3}]).status_code == 409  # the existing clip isn't accounted for
    assert put([{"id": cid, "start_seconds": 0, "end_seconds": 8}], removed=[cid]).status_code == 422
    assert put([{"id": cid, "start_seconds": 0, "end_seconds": 8, "label_ids": [999]}]).status_code == 400

    # Keep the whole take too: the clip stays whole, the new pieces overlap it.
    length = authed.get(f"/api/recordings/{rid}").json()["duration_seconds"]
    r = put([
        {"id": cid, "start_seconds": 0, "end_seconds": length, "label_ids": [verse]},
        {"start_seconds": 0.5, "end_seconds": 2.5, "label_ids": [verse]},
    ])  # fmt: skip
    assert r.status_code == 200, r.text
    whole = authed.get(f"/api/clips/{cid}").json()
    assert (whole["start_seconds"], whole["trimmed"], whole["cut_url"]) == (0, False, None)
    piece = next(p for p in r.json()["clips"] if p["id"] != cid)

    # Removing a piece in the editor puts it in Trash.
    assert put([{"id": cid, "start_seconds": 0, "end_seconds": 8, "label_ids": [verse]}], removed=[piece["id"]]).status_code == 200
    assert authed.get(f"/api/clips/{piece['id']}").json()["deleted_at"] is not None


@needs_ffmpeg
def test_date_thumbnail_move_and_purge(authed: TestClient, project: dict, settings: Settings, take: Path) -> None:
    verse = label_id(project, "Verse")
    clip = _upload_take(authed, settings, take, project)
    rid, cid = clip["recording_id"], clip["id"]
    r = authed.put(f"/api/recordings/{rid}/clips", json={"from_clip": cid, "pieces": [
        {"id": cid, "start_seconds": 0, "end_seconds": 3, "label_ids": [verse]},
        {"start_seconds": 5, "end_seconds": 8, "label_ids": [verse]},
    ]})  # fmt: skip
    other_id = next(p["id"] for p in r.json()["clips"] if p["id"] != cid)
    jobs.run_all(settings)

    # A new date moves the whole recording: the other piece keeps its 5 s offset.
    authed.patch(f"/api/clips/{cid}", json={"recorded_at": "2026-10-01T12:00:00Z"})
    assert authed.get(f"/api/clips/{other_id}").json()["recorded_at"] == "2026-10-01T12:00:05.000Z"

    # Choose the thumbnail: a frame from within the clip.
    assert authed.patch(f"/api/clips/{other_id}", json={"thumbnail_at": 9}).status_code == 422
    chosen = authed.patch(f"/api/clips/{other_id}", json={"thumbnail_at": 1.5}).json()
    assert (chosen["thumbnail_at"], chosen["processing"]) == (1.5, "pending")
    jobs.run_all(settings)
    assert authed.get(f"/api/clips/{other_id}").json()["processing"] is None

    # Moving one piece of a split recording: the recording stays, the piece's thumbnail moves.
    song = authed.post("/api/projects", json={"name": "Other Song"}).json()
    moved = authed.patch(f"/api/clips/{other_id}", json={"project_id": song["id"]}).json()
    original = settings.media_dir / "midnight-drive" / f"2026-11-24_101500_{clip['uid']}.mp4"
    assert original.is_file()
    assert list((settings.media_dir / "other-song").glob("*.thumb.jpg"))
    assert authed.get(moved["media_url"]).status_code == 200

    # Purge: the recording's files stay while any piece is left, and go with the last one.
    authed.delete(f"/api/clips/{other_id}")
    _age(settings, "clips", other_id, 31)
    purge_trash(settings)
    assert original.is_file() and authed.get(f"/api/clips/{cid}").status_code == 200
    authed.delete(f"/api/clips/{cid}")
    _age(settings, "clips", cid, 31)
    purge_trash(settings)
    assert not original.exists()
    conn = connect(settings.db_path)
    assert conn.execute("SELECT count(*) FROM recordings").fetchone()[0] == 0
    conn.close()
    assert not [p for p in settings.media_dir.rglob("*") if p.is_file() and clip["uid"] in p.name]


def test_editor_needs_a_known_length(authed: TestClient, project: dict, settings: Settings) -> None:
    from .test_rows_and_labels import _add_clip

    conn = connect(settings.db_path)
    cid = _add_clip(conn, project["id"], "nolen", "2026-11-20T12:00:00.000Z", [])
    with conn:
        conn.execute("UPDATE recordings SET duration_seconds = NULL WHERE id = (SELECT recording_id FROM clips WHERE id = ?)", (cid,))
    rid = conn.execute("SELECT recording_id FROM clips WHERE id = ?", (cid,)).fetchone()[0]
    conn.close()
    r = authed.put(f"/api/recordings/{rid}/clips", json={"from_clip": cid, "pieces": [{"id": cid, "start_seconds": 0, "end_seconds": 1}]})
    assert r.status_code == 409
    assert authed.get("/api/recordings/9999").status_code == 404


def test_recordings_need_sign_in(client: TestClient) -> None:
    assert client.get("/api/recordings/1").status_code == 401
    assert client.get("/api/clips/1/cut").status_code == 401

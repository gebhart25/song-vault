from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.db import connect
from songvault.trash import purge_trash

from .test_rows_and_labels import _add_clip, label_id


def _clip_with_files(settings: Settings, project_id: int | None, uid: str, labels: list[int] | None = None) -> int:
    """A clip whose original, playback copy and thumbnail exist on disk."""
    conn = connect(settings.db_path)
    cid = _add_clip(conn, project_id, uid, "2026-11-20T12:00:00.000Z", labels or [])
    folder = "trash-song" if project_id else "_inbox"
    recording = {
        "original_path": f"{folder}/{uid}.mp4",
        "playback_path": f"{folder}/{uid}.play.mp4",
        "audio_path": f"{folder}/{uid}.audio.m4a",
        "preview_path": f"{folder}/{uid}.preview.jpg",
        "peaks_path": f"{folder}/{uid}.peaks.json",
    }
    thumbnail = f"{folder}/{uid}.thumb.jpg"
    for rel in [*recording.values(), thumbnail]:
        path = settings.media_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    with conn:
        conn.execute(
            "UPDATE recordings SET original_path = :original_path, playback_path = :playback_path, "
            "audio_path = :audio_path, preview_path = :preview_path, peaks_path = :peaks_path "
            "WHERE id = (SELECT recording_id FROM clips WHERE id = :id)",
            {**recording, "id": cid},
        )
        conn.execute("UPDATE clips SET thumbnail_path = ? WHERE id = ?", (thumbnail, cid))
    conn.close()
    return cid


def _age(settings: Settings, table: str, row_id: int, days: int) -> None:
    """Pretend the row went to Trash `days` days ago."""
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = connect(settings.db_path)
    with conn:
        conn.execute(f"UPDATE {table} SET deleted_at = ? WHERE id = ?", (when, row_id))
    conn.close()


def test_trash_lists_songs_and_recordings(authed: TestClient, project: dict, settings: Settings) -> None:
    song = authed.post("/api/projects", json={"name": "Trash Song"}).json()
    in_song = _clip_with_files(settings, song["id"], "insong")
    loose = _clip_with_files(settings, None, "loose")
    assert authed.get("/api/trash").json() == {"days": 30, "projects": [], "clips": [], "notes": []}
    assert authed.get("/api/clips/summary").json()["trash"] == 0

    authed.delete(f"/api/clips/{loose}")
    authed.delete(f"/api/projects/{song['id']}")
    trash = authed.get("/api/trash").json()
    assert [(p["name"], p["clip_count"]) for p in trash["projects"]] == [("Trash Song", 1)]
    assert [c["id"] for c in trash["clips"]] == [loose]
    clip = trash["clips"][0]
    deleted = datetime.fromisoformat(clip["deleted_at"].replace("Z", "+00:00"))
    assert clip["purge_at"] == (deleted + timedelta(days=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert clip["project_in_trash"] is False
    assert authed.get("/api/clips/summary").json()["trash"] == 2

    # Recordings in a song that's in Trash show as deleted in the upload history.
    history = {i["id"]: i["deleted"] for i in authed.get("/api/clips/history").json()["items"]}
    assert history[in_song] is True and history[loose] is True

    # Restoring brings them back.
    assert authed.post(f"/api/clips/{loose}/restore").status_code == 200
    assert authed.post(f"/api/projects/{song['id']}/restore").status_code == 200
    assert authed.get("/api/trash").json()["projects"] == []
    assert [p["name"] for p in authed.get("/api/projects").json()].count("Trash Song") == 1
    assert authed.get(f"/api/projects/{song['id']}").json()["clip_count"] == 1


def test_recording_in_a_trashed_song_needs_the_song_back_first(
    authed: TestClient, project: dict, settings: Settings
) -> None:
    cid = _clip_with_files(settings, project["id"], "take", [label_id(project, "Verse")])
    authed.delete(f"/api/clips/{cid}")
    authed.delete(f"/api/projects/{project['id']}")
    clip = authed.get("/api/trash").json()["clips"][0]
    assert clip["project_in_trash"] is True

    r = authed.post(f"/api/clips/{cid}/restore")
    assert r.status_code == 409
    assert r.json()["detail"] == "“Midnight Drive” is in Trash too. Restore the song first."

    authed.post(f"/api/projects/{project['id']}/restore")
    assert authed.post(f"/api/clips/{cid}/restore").status_code == 200


def test_purge_after_30_days(authed: TestClient, project: dict, settings: Settings) -> None:
    old_loose = _clip_with_files(settings, None, "oldloose")
    new_loose = _clip_with_files(settings, None, "newloose")
    kept = _clip_with_files(settings, None, "kept")
    song = authed.post("/api/projects", json={"name": "Trash Song"}).json()
    song_clip = _clip_with_files(settings, song["id"], "songclip")
    authed.put(f"/api/projects/{song['id']}/lyrics", json={"sections": [{"id": "a", "text": "la"}], "revision": 0})
    authed.post("/api/labels", json={"name": "Breakdown", "project_id": song["id"]})

    for cid in (old_loose, new_loose):
        authed.delete(f"/api/clips/{cid}")
    authed.delete(f"/api/projects/{song['id']}")
    _age(settings, "clips", old_loose, 31)
    _age(settings, "clips", new_loose, 29)
    _age(settings, "projects", song["id"], 31)

    assert purge_trash(settings) == (1, 2)  # the song (with its recording) and the old loose clip

    remaining = {c["id"] for c in authed.get("/api/trash").json()["clips"]}
    assert remaining == {new_loose}
    assert authed.get("/api/trash").json()["projects"] == []
    for cid in (old_loose, song_clip):
        assert authed.get(f"/api/clips/{cid}").status_code == 404
    assert authed.get(f"/api/clips/{kept}").status_code == 200

    inbox = settings.media_dir / "_inbox"
    assert not list(inbox.glob("oldloose*"))
    assert len(list(inbox.glob("newloose*"))) == 6 and len(list(inbox.glob("kept*"))) == 6
    assert not (settings.media_dir / "trash-song").exists()  # the song's emptied folder is removed

    conn = connect(settings.db_path)
    for table, column in (("projects", "id"), ("lyrics", "project_id"), ("labels", "project_id")):
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE {column} = ?", (song["id"],)).fetchone()[0] == 0
    conn.close()

    assert purge_trash(settings) == (0, 0)  # nothing else is old enough

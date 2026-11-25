"""End-to-end upload tests: tus upload -> checksum verify -> clip -> ffmpeg jobs -> playback.
Needs ffmpeg/ffprobe on PATH; small test files are generated once per session."""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from songvault import jobs
from songvault.config import Settings
from songvault.db import connect

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed"
)

TUS = {"Tus-Resumable": "1.0.0"}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def samples(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("samples")
    src = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    # Like the Samsung phone: HEVC + AAC, local start time in the name, UTC end time in metadata.
    _ffmpeg(*src, "-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
            "-c:a", "aac", "-shortest", "-metadata", "creation_time=2026-11-02T17:34:16Z", str(d / "20261102_113414.mp4"))  # fmt: skip
    _ffmpeg(*src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "h264.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=2", "-c:a", "aac", "-metadata",
            "creation_time=2026-11-17T03:15:05Z", str(d / "Recording.m4a"))  # fmt: skip
    # Like recordings made in the app (spec 5.7): WebM written as a stream, so with no length.
    for name, inputs, codecs in (
        ("REC_20261124_101500.webm", src, ["-c:v", "libvpx", "-b:v", "300k", "-c:a", "libopus", "-shortest"]),
        ("REC_20261124_101800.webm", ["-f", "lavfi", "-i", "sine=frequency=330:duration=2"], ["-c:a", "libopus"]),
    ):
        with (d / name).open("wb") as out:
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", *inputs, *codecs, "-f", "webm", "pipe:1"],
                           stdout=out, check=True)  # fmt: skip
    (d / "not-a-video.mp4").write_bytes(b"this is not a video" * 100)
    return {p.name: p for p in d.iterdir()}


def b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def upload(
    client: TestClient,
    path: Path,
    *,
    project_id: int | None = None,
    label_ids: list[int] | None = None,
    chunk: int = 50_000,
    sha: str | None = None,
):
    data = path.read_bytes()
    meta = {
        "filename": path.name,
        "filetype": "video/mp4",
        "timezone": "America/Chicago",
        "last_modified": "1795000000000",
    }
    if project_id is not None:
        meta["project_id"] = str(project_id)
    if label_ids:
        meta["label_ids"] = ",".join(map(str, label_ids))
    r = client.post(
        "/api/uploads",
        headers={**TUS, "Upload-Length": str(len(data)), "Upload-Metadata": ",".join(f"{k} {b64(v)}" for k, v in meta.items())},
    )
    assert r.status_code == 201, r.text
    url = r.headers["location"]
    for start in range(0, len(data), chunk):
        r = client.patch(
            url,
            content=data[start : start + chunk],
            headers={**TUS, "Upload-Offset": str(start), "Content-Type": "application/offset+octet-stream"},
        )
        assert r.status_code == 204, r.text
        assert int(r.headers["upload-offset"]) == min(start + chunk, len(data))
    return url, client.post(f"{url}/finish", json={"sha256": sha or hashlib.sha256(data).hexdigest()})


def label_id(client: TestClient, project_id: int, name: str) -> int:
    rows = client.get(f"/api/projects/{project_id}").json()["rows"]
    return next(r["label_id"] for r in rows if r["name"] == name)


def test_samsung_video_into_a_song_part(
    authed: TestClient, project: dict, settings: Settings, samples: dict[str, Path]
) -> None:
    verse = label_id(authed, project["id"], "Verse")
    _, r = upload(authed, samples["20261102_113414.mp4"], project_id=project["id"], label_ids=[verse])
    assert r.status_code == 200, r.text
    clip = r.json()
    assert clip["recorded_at"] == "2026-11-02T17:34:14.000Z"  # start time from the name
    assert clip["recorded_at_source"] == "filename"
    assert clip["verified_at"] is not None
    assert clip["video_codec"] == "hevc"
    assert clip["label_ids"] == [verse]
    assert clip["processing"] == "pending"
    assert clip["media_url"] is None  # HEVC needs a playback copy first

    stored = settings.media_dir / "midnight-drive" / f"2026-11-02_113414_{clip['uid']}.mp4"
    assert stored.is_file()  # named in local time, inside the song's folder
    assert not any(settings.uploads_dir.iterdir())

    # The recording's playback copy, audio copy, preview strip and waveform; the clip's thumbnail.
    assert jobs.run_all(settings) == 5
    clip = authed.get(f"/api/clips/{clip['id']}").json()
    assert clip["processing"] is None
    assert [(j["type"], j["state"]) for j in clip["jobs"]] == [
        ("playback", "done"), ("audio", "done"), ("preview", "done"), ("peaks", "done"), ("thumbnail", "done")
    ]
    assert (clip["start_seconds"], clip["trimmed"], clip["cut_url"], clip["pieces"]) == (0, False, None, 1)
    assert clip["thumbnail_url"] and clip["media_url"]
    assert (stored.parent / f"2026-11-02_113414_{clip['uid']}.thumb.jpg").is_file()
    assert (stored.parent / f"2026-11-02_113414_{clip['uid']}.play.mp4").is_file()

    # The extras (spec 5.4): the sound on its own, and frames for scrubbing on the tile.
    assert (stored.parent / f"2026-11-02_113414_{clip['uid']}.audio.m4a").is_file()
    sound = authed.get(clip["audio_url"])
    assert (sound.status_code, sound.headers["content-type"]) == (200, "audio/mp4")
    assert "20261102_113414.m4a" in sound.headers["content-disposition"]
    assert (clip["preview_frames"], clip["waveform"]) == (4, None)  # 2 s clip: the minimum of 4 frames
    strip = authed.get(clip["preview_url"])
    assert (strip.status_code, strip.headers["content-type"]) == (200, "image/jpeg")

    thumb = authed.get(clip["thumbnail_url"])
    assert (thumb.status_code, thumb.headers["content-type"]) == (200, "image/jpeg")
    full = authed.get(clip["media_url"])
    assert (full.status_code, full.headers["content-type"]) == (200, "video/mp4")
    part = authed.get(clip["media_url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    original = authed.get(clip["original_url"])
    assert original.content == samples["20261102_113414.mp4"].read_bytes()
    assert "20261102_113414.mp4" in original.headers["content-disposition"]

    row = next(r for r in authed.get(f"/api/projects/{project['id']}").json()["rows"] if r["name"] == "Verse")
    assert [c["id"] for c in row["clips"]] == [clip["id"]]


def test_h264_and_audio_play_without_a_copy(authed: TestClient, settings: Settings, samples: dict[str, Path]) -> None:
    for name in ("h264.mp4", "Recording.m4a"):
        _, r = upload(authed, samples[name])
        assert r.status_code == 200, r.text
    jobs.run_all(settings)
    inbox = authed.get("/api/clips").json()
    assert {c["media_type"] for c in inbox} == {"video", "audio"}
    for c in inbox:
        detail = authed.get(f"/api/clips/{c['id']}").json()
        assert detail["media_url"]
        # playback_path is the original itself: no extra .play file was written
    assert not list(settings.media_dir.rglob("*.play.*"))
    audio = next(c for c in inbox if c["media_type"] == "audio")
    assert audio["thumbnail_url"] is None
    assert audio["recorded_at"] == "2026-11-17T03:15:05.000Z"

    # Audio clips get a waveform (spec 5.9) and play as they are for "audio only".
    assert len(audio["waveform"]) == 120 and max(audio["waveform"]) == 100 and min(audio["waveform"]) >= 0
    assert audio["preview_url"] is None
    detail = authed.get(f"/api/clips/{audio['id']}").json()
    assert [j["type"] for j in detail["jobs"]] == ["playback", "peaks"]
    assert authed.get(detail["audio_url"]).content == samples["Recording.m4a"].read_bytes()
    video = authed.get(f"/api/clips/{next(c['id'] for c in inbox if c['media_type'] == 'video')}").json()
    assert video["audio_url"] and video["preview_frames"] == 4


def test_recordings_from_the_browser_get_a_length(
    authed: TestClient, settings: Settings, samples: dict[str, Path]
) -> None:
    """A WebM straight from a browser's recorder has no length; its playback copy supplies it."""
    for name, when in (("REC_20261124_101500.webm", "16:15:00"), ("REC_20261124_101800.webm", "16:18:00")):
        _, r = upload(authed, samples[name])
        assert r.status_code == 200, r.text
        assert r.json()["duration_seconds"] is None
        assert r.json()["recorded_at"] == f"2026-11-24T{when}.000Z"  # local time in the name (Chicago)
    jobs.run_all(settings)
    clips = {c["media_type"]: c for c in authed.get("/api/clips").json()}
    video, audio = clips["video"], clips["audio"]
    assert 1.5 < video["duration_seconds"] < 2.5 and 1.5 < audio["duration_seconds"] < 2.5
    assert video["preview_frames"] == 4 and len(audio["waveform"]) == 120
    for c in (video, audio):
        detail = authed.get(f"/api/clips/{c['id']}").json()
        assert detail["processing"] is None and detail["media_url"] and detail["audio_url"]


def test_extras_dont_hold_up_the_tile(authed: TestClient, settings: Settings, samples: dict[str, Path]) -> None:
    """A failed or pending audio copy / preview strip doesn't show the tile as processing."""
    _, r = upload(authed, samples["h264.mp4"])
    clip = r.json()
    conn = connect(settings.db_path)
    with conn:
        conn.execute("UPDATE jobs SET state = 'done' WHERE clip_id = ? OR (recording_id = ? AND type = 'playback')",
                     (clip["id"], clip["recording_id"]))
        conn.execute("UPDATE jobs SET state = 'failed', error = 'x' WHERE recording_id = ? AND type = 'preview'",
                     (clip["recording_id"],))
    conn.close()
    assert authed.get(f"/api/clips/{clip['id']}").json()["processing"] is None
    assert authed.get("/api/clips").json()[0]["processing"] is None


def test_resume_after_a_dropped_connection(authed: TestClient, samples: dict[str, Path]) -> None:
    data = samples["h264.mp4"].read_bytes()
    r = authed.post("/api/uploads", headers={**TUS, "Upload-Length": str(len(data)), "Upload-Metadata": f"filename {b64('h264.mp4')}"})
    url = r.headers["location"]
    authed.patch(url, content=data[:1000], headers={**TUS, "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"})

    # The client lost track; the server tells it where to continue.
    stale = authed.patch(url, content=data[:1000], headers={**TUS, "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"})
    assert stale.status_code == 409
    head = authed.head(url, headers=TUS)
    assert (head.headers["upload-offset"], head.headers["upload-length"]) == ("1000", str(len(data)))

    early = authed.post(f"{url}/finish", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert early.status_code == 409

    authed.patch(url, content=data[1000:], headers={**TUS, "Upload-Offset": "1000", "Content-Type": "application/offset+octet-stream"})
    done = authed.post(f"{url}/finish", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert done.status_code == 200
    again = authed.post(f"{url}/finish", json={"sha256": hashlib.sha256(data).hexdigest()})
    assert again.json()["id"] == done.json()["id"]  # finishing twice is harmless


def test_checksum_mismatch_is_rejected(authed: TestClient, settings: Settings, samples: dict[str, Path]) -> None:
    url, r = upload(authed, samples["h264.mp4"], sha="0" * 64)
    assert r.status_code == 422
    assert "checksum" in r.json()["detail"]
    assert authed.head(url, headers=TUS).status_code == 404
    assert authed.get("/api/clips").json() == []
    assert not any(settings.uploads_dir.iterdir())


def test_non_media_file_is_rejected(authed: TestClient, samples: dict[str, Path]) -> None:
    _, r = upload(authed, samples["not-a-video.mp4"])
    assert r.status_code == 422


def test_wrong_file_type_is_refused_up_front(authed: TestClient) -> None:
    r = authed.post("/api/uploads", headers={**TUS, "Upload-Length": "10", "Upload-Metadata": f"filename {b64('notes.pdf')}"})
    assert r.status_code == 415


def test_duplicate_upload_keeps_one_copy(authed: TestClient, settings: Settings, samples: dict[str, Path]) -> None:
    _, first = upload(authed, samples["h264.mp4"])
    _, second = upload(authed, samples["h264.mp4"])
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]
    assert len(list((settings.media_dir / "_inbox").glob("*.mp4"))) == 1


def test_cancel_upload(authed: TestClient, settings: Settings) -> None:
    r = authed.post("/api/uploads", headers={**TUS, "Upload-Length": "100", "Upload-Metadata": f"filename {b64('a.mp4')}"})
    url = r.headers["location"]
    assert authed.delete(url, headers=TUS).status_code == 204
    assert authed.head(url, headers=TUS).status_code == 404
    assert not any(settings.uploads_dir.iterdir())


def test_uploads_need_sign_in(client: TestClient) -> None:
    r = client.post("/api/uploads", headers={**TUS, "Upload-Length": "10", "Upload-Metadata": f"filename {b64('a.mp4')}"})
    assert r.status_code == 401


def test_inbox_sort_into_song_and_rename(
    authed: TestClient, project: dict, settings: Settings, samples: dict[str, Path]
) -> None:
    _, r = upload(authed, samples["20261102_113414.mp4"])
    clip = r.json()
    jobs.run_all(settings)
    summary = authed.get("/api/clips/summary").json()
    assert summary["uncategorized"] == 1
    assert summary["uncategorized_cover_url"] == f"/api/clips/{clip['id']}/thumbnail"
    assert summary["uncategorized_updated_at"] == clip["uploaded_at"]
    assert [c["id"] for c in authed.get("/api/clips?view=uncategorized").json()] == [clip["id"]]
    assert (settings.media_dir / "_inbox" / f"2026-11-02_113414_{clip['uid']}.mp4").is_file()

    chorus = label_id(authed, project["id"], "Chorus")
    moved = authed.patch(f"/api/clips/{clip['id']}", json={"project_id": project["id"], "label_ids": [chorus], "title": " Riff idea "})
    assert moved.status_code == 200, moved.text
    assert moved.json()["title"] == "Riff idea"
    assert authed.get("/api/clips").json() == []
    folder = settings.media_dir / "midnight-drive"
    assert len(list(folder.glob(f"*{clip['uid']}*"))) == 6  # original, copies, preview, waveform and thumbnail: together
    assert not list((settings.media_dir / "_inbox").glob(f"*{clip['uid']}*"))

    # Renaming the song renames its folder; the files still play.
    authed.patch(f"/api/projects/{project['id']}", json={"name": "Night Drive"})
    assert (settings.media_dir / "night-drive").is_dir() and not folder.exists()
    detail = authed.get(f"/api/clips/{clip['id']}").json()
    assert authed.get(detail["media_url"]).status_code == 200
    assert authed.get(detail["thumbnail_url"]).status_code == 200
    assert authed.get(detail["audio_url"]).status_code == 200
    assert authed.get(detail["preview_url"]).status_code == 200

    # In a song but without a part: shown on the song page, not in Uncategorized.
    authed.patch(f"/api/clips/{clip['id']}", json={"label_ids": []})
    assert authed.get("/api/clips").json() == []
    assert [c["id"] for c in authed.get(f"/api/projects/{project['id']}").json()["unlabeled"]] == [clip["id"]]
    assert authed.get("/api/clips/summary").json()["uncategorized"] == 0

    # Back to Uncategorized: project-specific parts are dropped, files move back.
    back = authed.patch(f"/api/clips/{clip['id']}", json={"project_id": None})
    assert back.json()["project_id"] is None
    assert len(list((settings.media_dir / "_inbox").glob(f"*{clip['uid']}*"))) == 6


def test_manual_date_trash_restore_history(authed: TestClient, samples: dict[str, Path]) -> None:
    _, r = upload(authed, samples["Recording.m4a"])
    cid = r.json()["id"]
    dated = authed.patch(f"/api/clips/{cid}", json={"recorded_at": "2026-11-16T21:15:00-06:00"}).json()
    assert (dated["recorded_at"], dated["recorded_at_source"]) == ("2026-11-17T03:15:00.000Z", "manual")

    assert authed.delete(f"/api/clips/{cid}").status_code == 204
    assert authed.get("/api/clips").json() == []
    assert [c["id"] for c in authed.get("/api/clips", params={"view": "trash"}).json()] == [cid]
    history = authed.get("/api/clips/history").json()["items"]
    assert history[0]["original_filename"] == "Recording.m4a" and history[0]["deleted"] is True
    assert authed.post(f"/api/clips/{cid}/restore").status_code == 200
    assert [c["id"] for c in authed.get("/api/clips").json()] == [cid]


def test_failed_job_can_be_retried(authed: TestClient, settings: Settings, samples: dict[str, Path]) -> None:
    _, r = upload(authed, samples["20261102_113414.mp4"])
    clip = r.json()
    # Break ffmpeg for the first run.
    broken = Settings(**{**settings.__dict__, "ffmpeg": "ffmpeg-does-not-exist"})
    jobs.run_all(broken)
    detail = authed.get(f"/api/clips/{clip['id']}").json()
    assert detail["processing"] == "failed"
    assert "not installed" in detail["jobs"][0]["error"]

    assert authed.post(f"/api/clips/{clip['id']}/reprocess").json()["processing"] == "pending"
    jobs.run_all(settings)
    assert authed.get(f"/api/clips/{clip['id']}").json()["processing"] is None


def test_backup_marker_marks_safe_to_delete(
    authed: TestClient, settings: Settings, samples: dict[str, Path]
) -> None:
    upload(authed, samples["Recording.m4a"])
    history = authed.get("/api/clips/history").json()
    assert history["last_backup_at"] is None
    assert history["items"][0]["backed_up"] is False

    # A backup that started after the upload covers it...
    settings.backup_marker.write_text(
        '{"started_at": "2099-01-01T04:00:00Z", "finished_at": "2099-01-01T04:10:00Z"}', encoding="utf-8"
    )
    history = authed.get("/api/clips/history").json()
    assert history["last_backup_at"] == "2099-01-01T04:10:00Z"
    assert history["items"][0]["backed_up"] is True

    # ...one that started before it doesn't, and a broken marker is ignored.
    settings.backup_marker.write_text(
        '{"started_at": "2000-01-01T04:00:00Z", "finished_at": "2000-01-01T04:10:00Z"}', encoding="utf-8"
    )
    assert authed.get("/api/clips/history").json()["items"][0]["backed_up"] is False
    settings.backup_marker.write_text("not json", encoding="utf-8")
    assert authed.get("/api/clips/history").json()["last_backup_at"] is None

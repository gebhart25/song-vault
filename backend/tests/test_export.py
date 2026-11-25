from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.export import chord_sheet_line

from .test_rows_and_labels import label_id
from .test_trash import _clip_with_files


def test_chord_sheet_line() -> None:
    assert chord_sheet_line("[D]Hello [Bm]darkness") == ["D     Bm", "Hello darkness"]
    # Chords before the words that would touch are pushed apart; one after the line sits at its end.
    assert chord_sheet_line("[G] [D]Hi there [A]") == ["G D       A", " Hi there"]
    assert chord_sheet_line("No chords here") == ["No chords here"]
    assert chord_sheet_line("[G] [C]") == ["G C"]  # a line of chords alone


def test_export_a_song(authed: TestClient, project: dict, settings: Settings) -> None:
    pid = project["id"]
    verse, chorus = label_id(project, "Verse"), label_id(project, "Chorus")
    a = _clip_with_files(settings, pid, "a", [verse])
    b = _clip_with_files(settings, pid, "b", [chorus, verse])
    trashed = _clip_with_files(settings, pid, "gone", [verse])
    authed.delete(f"/api/clips/{trashed}")
    authed.patch(f"/api/clips/{b}", json={"title": "Chorus idea", "status": "current", "bpm": 96})
    authed.patch(f"/api/projects/{pid}", json={
        "song_key": "D", "bpm": 92, "capo": 2, "notes": "Try the bridge half-time.",
        "progressions": [{"label": "Verse", "chords": ["D", "Bm", "G", "A"]}],
        "strumming": [{"label": "Verse", "pattern": "D - D U - U D U"}],
    })  # fmt: skip
    authed.put(f"/api/projects/{pid}/lyrics", json={"revision": 0, "sections": [
        {"id": "i", "label": "Intro", "kind": "chords", "text": "D - Bm - G - A"},
        {"id": "v", "label": "Verse 1", "text": "[D]Headlights on the c[Bm]ounty road"},
    ]})  # fmt: skip

    r = authed.get(f"/api/projects/{pid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert 'filename="midnight-drive.zip"' in r.headers["content-disposition"]

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None
    names = set(zf.namelist())
    assert names == {
        "midnight-drive/manifest.json", "midnight-drive/song-notes.txt", "midnight-drive/lyrics.txt",
        "midnight-drive/lyrics.cho", "midnight-drive/originals/a.mp4", "midnight-drive/originals/b.mp4",
    }  # fmt: skip
    assert zf.read("midnight-drive/originals/a.mp4") == b"x"  # the original's bytes, as stored

    manifest = json.loads(zf.read("midnight-drive/manifest.json"))
    assert manifest["format"] == "songvault-export" and manifest["song"]["name"] == "Midnight Drive"
    assert manifest["song"]["strumming"] == [{"label": "Verse", "pattern": "D - D U - U D U"}]
    clips = {c["file"]: c for c in manifest["clips"]}
    assert set(clips) == {"originals/a.mp4", "originals/b.mp4"}  # the one in Trash is left out
    assert clips["originals/b.mp4"]["parts"] == ["Verse", "Chorus"]  # song-page order
    assert (clips["originals/b.mp4"]["title"], clips["originals/b.mp4"]["status"]) == ("Chorus idea", "current")
    assert clips["originals/b.mp4"]["details"]["bpm"] == 96

    notes = zf.read("midnight-drive/song-notes.txt").decode()
    assert "Key: D" in notes and "Verse: D - Bm - G - A" in notes and "Verse: D - D U - U D U" in notes
    assert notes.rstrip().endswith("Try the bridge half-time.")

    sheet = zf.read("midnight-drive/lyrics.txt").decode().splitlines()
    assert sheet[:2] == ["Midnight Drive", "Key D · 92 BPM · Capo 2"]
    assert sheet[sheet.index("[Verse 1]") + 1 :] == ["D                  Bm", "Headlights on the county road"]

    cho = zf.read("midnight-drive/lyrics.cho").decode()
    assert "{title: Midnight Drive}" in cho and "{key: D}" in cho and "{tempo: 92}" in cho
    assert "{comment: Intro}\n[D] [Bm] [G] [A]" in cho
    assert "{start_of_verse: Verse 1}\n[D]Headlights on the c[Bm]ounty road\n{end_of_verse}" in cho


def test_export_needs_sign_in(client: TestClient, project: dict) -> None:
    client.post("/api/auth/logout")
    assert client.get(f"/api/projects/{project['id']}/export").status_code == 401

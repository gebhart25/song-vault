from __future__ import annotations

from fastapi.testclient import TestClient


def test_song_notes_fields(authed: TestClient, project: dict) -> None:
    assert project["progressions"] == []
    assert project["sounds_like"] is None
    assert project["lyric_sections"] == 0

    r = authed.patch(
        f"/api/projects/{project['id']}",
        json={
            "song_key": " D ",
            "bpm": 92,
            "time_signature": "4/4",
            "capo": 2,
            "tuning": "Drop  D",
            "sounds_like": "Tom Petty, slower",
            "progressions": [
                {"label": " Verse ", "chords": ["D", "Bm", "G", "A"]},
                {"label": "Chorus Turnaround", "chords": ["Em", " A7 ", "D/F#"]},
            ],
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["song_key"], p["bpm"], p["time_signature"], p["capo"], p["tuning"]) == ("D", 92, "4/4", 2, "Drop D")
    assert p["sounds_like"] == "Tom Petty, slower"
    assert p["progressions"] == [
        {"label": "Verse", "chords": ["D", "Bm", "G", "A"]},
        {"label": "Chorus Turnaround", "chords": ["Em", "A7", "D/F#"]},
    ]
    assert authed.get(f"/api/projects/{project['id']}").json()["progressions"] == p["progressions"]

    # Clearing fields: blank text and null both remove the value.
    p = authed.patch(f"/api/projects/{project['id']}", json={"song_key": "  ", "bpm": None, "progressions": []}).json()
    assert (p["song_key"], p["bpm"], p["progressions"]) == (None, None, [])


def test_progressions_need_a_label_and_chords(authed: TestClient, project: dict) -> None:
    url = f"/api/projects/{project['id']}"
    bad = [
        [{"label": "  ", "chords": ["D"]}],
        [{"label": "Verse", "chords": []}],
        [{"label": "Verse", "chords": ["D", " "]}],
        [{"label": "Verse", "chords": ["[D]"]}],
        [{"label": "Verse", "chords": ["x" * 17]}],
    ]
    for progressions in bad:
        assert authed.patch(url, json={"progressions": progressions}).status_code == 422, progressions
    assert authed.patch(url, json={"progressions": None}).status_code == 422
    assert authed.patch(url, json={"bpm": 0}).status_code == 422


def test_strumming_patterns(authed: TestClient, project: dict) -> None:
    url = f"/api/projects/{project['id']}"
    assert project["strumming"] == []
    r = authed.patch(url, json={"strumming": [
        {"label": " Verse ", "pattern": "D  - D U - U D U"},
        {"pattern": "D D U"},  # the label is optional
    ]})
    assert r.status_code == 200, r.text
    assert r.json()["strumming"] == [
        {"label": "Verse", "pattern": "D - D U - U D U"},
        {"label": "", "pattern": "D D U"},
    ]
    # Other song notes don't touch it, and it can be cleared.
    assert authed.patch(url, json={"bpm": 90}).json()["strumming"][1]["pattern"] == "D D U"
    assert authed.patch(url, json={"strumming": []}).json()["strumming"] == []

    for bad in ([{"label": "Verse", "pattern": "   "}], [{"pattern": "x" * 81}], [{"label": "x" * 61, "pattern": "D"}], None):
        assert authed.patch(url, json={"strumming": bad}).status_code == 422, bad


def test_lyrics_save_and_conflict(authed: TestClient, project: dict) -> None:
    url = f"/api/projects/{project['id']}/lyrics"
    assert authed.get(url).json() == {"sections": [], "revision": 0, "updated_at": None}

    sections = [
        {"id": "a1", "label": " Verse  1 ", "text": "[D]Hello [Bm]darkness my [G]old friend\n[A]I've come to talk"},
        {"id": "b2", "label": "Chorus", "text": "[G]Sing it [A]loud"},
    ]
    r = authed.put(url, json={"sections": sections, "revision": 0})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["revision"] == 1
    assert saved["sections"][0]["label"] == "Verse 1"
    assert saved["sections"][0]["text"] == sections[0]["text"]
    assert authed.get(f"/api/projects/{project['id']}").json()["lyric_sections"] == 2

    # A device still on revision 0 can't overwrite the newer sheet.
    r = authed.put(url, json={"sections": [], "revision": 0})
    assert r.status_code == 409
    assert authed.get(url).json()["revision"] == 1

    r = authed.put(url, json={"sections": sections[:1], "revision": 1})
    assert r.json()["revision"] == 2
    assert len(authed.get(url).json()["sections"]) == 1


def test_chords_only_sections(authed: TestClient, project: dict) -> None:
    url = f"/api/projects/{project['id']}/lyrics"
    sections = [
        {"id": "i", "label": "Intro", "kind": "chords", "text": "D - Bm - G - A"},
        {"id": "v", "label": "Verse", "text": "[D]Hello there"},
    ]
    saved = authed.put(url, json={"sections": sections, "revision": 0}).json()
    assert [s["kind"] for s in saved["sections"]] == ["chords", "lyrics"]  # lyrics is the default
    assert saved["sections"][0]["text"] == "D - Bm - G - A"
    # Chord lines aren't words: search skips them, but still finds the label.
    assert authed.get("/api/projects", params={"q": "Bm - G"}).json() == []
    assert [p["id"] for p in authed.get("/api/projects", params={"q": "Intro"}).json()] == [project["id"]]
    bad = [{"id": "x", "kind": "solo", "text": ""}]
    assert authed.put(url, json={"sections": bad, "revision": 1}).status_code == 422


def test_lyrics_validation(authed: TestClient, project: dict) -> None:
    url = f"/api/projects/{project['id']}/lyrics"
    dupe = [{"id": "a", "label": "", "text": ""}, {"id": "a", "label": "", "text": ""}]
    assert authed.put(url, json={"sections": dupe, "revision": 0}).status_code == 400
    assert authed.put(url, json={"sections": [{"id": "../x", "text": ""}], "revision": 0}).status_code == 422
    assert authed.get("/api/projects/999/lyrics").status_code == 404


def test_search_finds_words_in_lyrics(authed: TestClient, project: dict) -> None:
    other = authed.post("/api/projects", json={"name": "Other Song"}).json()
    authed.put(
        f"/api/projects/{project['id']}/lyrics",
        json={"sections": [{"id": "a", "label": "Verse", "text": "[D]Hello dark[Bm]ness my old friend"}], "revision": 0},
    )
    found = authed.get("/api/projects", params={"q": "darkness my"}).json()
    assert [p["id"] for p in found] == [project["id"]]  # chord marks don't get in the way
    assert other["id"] not in [p["id"] for p in authed.get("/api/projects", params={"q": "Bm"}).json()]
    assert [p["id"] for p in authed.get("/api/projects", params={"q": "Other"}).json()] == [other["id"]]

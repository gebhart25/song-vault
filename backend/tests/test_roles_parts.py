from __future__ import annotations

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.db import connect

from .test_rows_and_labels import _add_clip, label_id


def _status(authed: TestClient, cid: int) -> str:
    return authed.get(f"/api/clips/{cid}").json()["status"]


def _names(detail: dict) -> list[str]:
    return [r["name"] for r in detail["rows"]]


def test_one_current_per_part_and_role(authed: TestClient, project: dict, settings: Settings) -> None:
    solo = label_id(project, "Solo")
    conn = connect(settings.db_path)
    ids = {uid: _add_clip(conn, project["id"], uid, "2026-11-20T12:00:00.000Z", [solo])
           for uid in ("rhythm", "lead", "rhythm2", "plain1", "plain2")}  # fmt: skip
    conn.close()

    def patch(uid: str, **changes: object) -> dict:
        r = authed.patch(f"/api/clips/{ids[uid]}", json=changes)
        assert r.status_code == 200, r.text
        return r.json()

    assert patch("rhythm", role=" Rhythm ")["role"] == "Rhythm"
    patch("lead", role="Lead")
    patch("rhythm2", role="rhythm")  # roles match whatever the case
    patch("rhythm", status="current")
    patch("lead", status="current")
    assert (_status(authed, ids["rhythm"]), _status(authed, ids["lead"])) == ("current", "current")

    patch("rhythm2", status="current")  # replaces the other Rhythm take, not the Lead one
    assert [_status(authed, ids[u]) for u in ("rhythm", "rhythm2", "lead")] == ["idea", "current", "current"]

    patch("plain1", status="current")  # takes without a role are a group of their own
    patch("plain2", status="current")
    assert [_status(authed, ids[u]) for u in ("plain1", "plain2", "lead")] == ["idea", "current", "current"]

    patch("lead", role="Rhythm")  # a Current take moving into a role that has one replaces it
    assert [_status(authed, ids[u]) for u in ("rhythm2", "lead")] == ["idea", "current"]
    assert patch("lead", role="  ")["role"] is None  # blank clears it

    row = next(r for r in authed.get(f"/api/projects/{project['id']}").json()["rows"] if r["name"] == "Solo")
    assert {c["id"]: c["role"] for c in row["clips"]}[ids["rhythm"]] == "Rhythm"
    assert set(authed.get("/api/clips/roles").json()) >= {"Rhythm"}
    assert authed.patch(f"/api/clips/{ids['lead']}", json={"role": "x" * 41}).status_code == 422


def test_bulk_role(authed: TestClient, project: dict, settings: Settings) -> None:
    verse = label_id(project, "Verse")
    conn = connect(settings.db_path)
    a = _add_clip(conn, project["id"], "a", "2026-11-20T12:00:00.000Z", [verse])
    b = _add_clip(conn, project["id"], "b", "2026-11-21T12:00:00.000Z", [verse])
    conn.close()
    assert authed.post("/api/clips/bulk", json={"clip_ids": [a, b], "role": "Vocals"}).status_code == 200
    assert {authed.get(f"/api/clips/{c}").json()["role"] for c in (a, b)} == {"Vocals"}
    authed.post("/api/clips/bulk", json={"clip_ids": [a], "status": "idea"})  # leaving role out keeps it
    assert authed.get(f"/api/clips/{a}").json()["role"] == "Vocals"
    authed.post("/api/clips/bulk", json={"clip_ids": [a, b], "role": None})
    assert {authed.get(f"/api/clips/{c}").json()["role"] for c in (a, b)} == {None}


def test_add_another_part(authed: TestClient, project: dict, settings: Settings) -> None:
    pid = project["id"]
    verse = label_id(project, "Verse")
    conn = connect(settings.db_path)
    clip = _add_clip(conn, pid, "v", "2026-11-20T12:00:00.000Z", [verse])
    conn.close()
    other = authed.post("/api/projects", json={"name": "Other Song"}).json()

    r = authed.post(f"/api/projects/{pid}/rows/{verse}/another")
    assert r.status_code == 200, r.text
    names = _names(r.json())
    assert names[names.index("Verse 1") + 1] == "Verse 2"
    assert "Verse" not in names and names.index("Verse 1") == 2  # in place, after Full Song and Intro
    rows = {row["name"]: row for row in r.json()["rows"]}
    assert rows["Verse 1"]["label_id"] == verse and [c["id"] for c in rows["Verse 1"]["clips"]] == [clip]
    assert rows["Verse 2"]["is_custom"] and rows["Verse 2"]["clips"] == []

    # Again, from either one: Verse 3 goes after Verse 2.
    names = _names(authed.post(f"/api/projects/{pid}/rows/{verse}/another").json())
    assert names[names.index("Verse 1") : names.index("Verse 1") + 3] == ["Verse 1", "Verse 2", "Verse 3"]

    # Only this song: elsewhere it's still Verse, and the song's part list uses its names.
    assert "Verse" in _names(authed.get(f"/api/projects/{other['id']}").json())
    assert "Verse 1" in [label["name"] for label in authed.get(f"/api/labels?project_id={pid}").json()]
    assert "Verse" in [label["name"] for label in authed.get("/api/labels").json()]

    # Saving the row order (collapsing a row) keeps the names.
    detail = authed.get(f"/api/projects/{pid}").json()
    saved = authed.put(f"/api/projects/{pid}/rows", json={"rows": [
        {"label_id": row["label_id"], "collapsed": row["name"] == "Verse 2"} for row in detail["rows"]
    ]}).json()  # fmt: skip
    assert _names(saved) == _names(detail)

    full_song = label_id(project, "Full Song")
    assert authed.post(f"/api/projects/{pid}/rows/{full_song}/another").status_code == 400
    # A custom part can't take a name the song already uses.
    assert authed.post("/api/labels", json={"name": "verse 1", "project_id": pid}).status_code == 409


def test_rename_part_in_one_song(authed: TestClient, project: dict) -> None:
    pid = project["id"]
    chorus = label_id(project, "Chorus")
    other = authed.post("/api/projects", json={"name": "Other Song"}).json()

    r = authed.patch(f"/api/projects/{pid}/rows/{chorus}", json={"name": " Hook "})
    assert r.status_code == 200, r.text
    names = _names(r.json())
    assert "Hook" in names and "Chorus" not in names
    assert names.index("Hook") == _names(project).index("Chorus")  # same place
    assert "Chorus" in _names(authed.get(f"/api/projects/{other['id']}").json())

    assert authed.patch(f"/api/projects/{pid}/rows/{chorus}", json={"name": "Verse"}).status_code == 409
    back = authed.patch(f"/api/projects/{pid}/rows/{chorus}", json={"name": "Chorus"}).json()
    assert "Chorus" in _names(back)

    custom = authed.post("/api/labels", json={"name": "Breakdown", "project_id": pid}).json()["id"]
    renamed = authed.patch(f"/api/projects/{pid}/rows/{custom}", json={"name": "Breakdown A"}).json()
    assert "Breakdown A" in _names(renamed)
    assert authed.patch(f"/api/projects/{pid}/rows/{label_id(project, 'Full Song')}", json={"name": "All"}).status_code == 400

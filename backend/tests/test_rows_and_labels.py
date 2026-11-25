from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.db import connect, now_iso

DEFAULT_PARTS = [
    "Full Song", "Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge",
    "Solo", "Lead Ideas", "Riff", "Outro", "Lyrics", "Misc",
]


def row_names(detail: dict) -> list[str]:
    return [r["name"] for r in detail["rows"]]


def label_id(detail: dict, name: str) -> int:
    return next(r["label_id"] for r in detail["rows"] if r["name"] == name)


def test_new_project_has_default_rows(project: dict) -> None:
    assert row_names(project) == DEFAULT_PARTS
    assert project["rows"][0]["is_full_song"] is True
    assert all(r["clips"] == [] and r["collapsed"] is False for r in project["rows"])


def test_custom_part_only_shows_in_its_project(authed: TestClient, project: dict) -> None:
    other = authed.post("/api/projects", json={"name": "Other"}).json()
    r = authed.post("/api/labels", json={"name": "Breakdown", "project_id": project["id"]})
    assert r.status_code == 201
    assert r.json()["builtin"] is False

    detail = authed.get(f"/api/projects/{project['id']}").json()
    assert row_names(detail)[-1] == "Breakdown"
    assert detail["rows"][-1]["is_custom"] is True
    assert "Breakdown" not in row_names(authed.get(f"/api/projects/{other['id']}").json())


def test_part_names_must_be_unique(authed: TestClient, project: dict) -> None:
    assert authed.post("/api/labels", json={"name": "verse", "project_id": project["id"]}).status_code == 409
    authed.post("/api/labels", json={"name": "Breakdown", "project_id": project["id"]})
    assert authed.post("/api/labels", json={"name": "BREAKDOWN", "project_id": project["id"]}).status_code == 409
    # A global part can't take a name a project already uses.
    assert authed.post("/api/labels", json={"name": "Breakdown"}).status_code == 409


def test_builtin_parts_are_protected(authed: TestClient, project: dict) -> None:
    verse = label_id(project, "Verse")
    assert authed.patch(f"/api/labels/{verse}", json={"name": "Verses"}).status_code == 400
    assert authed.delete(f"/api/labels/{verse}").status_code == 400


def test_rename_and_delete_custom_part(authed: TestClient, project: dict) -> None:
    lid = authed.post("/api/labels", json={"name": "Brekdown", "project_id": project["id"]}).json()["id"]
    assert authed.patch(f"/api/labels/{lid}", json={"name": "Breakdown"}).json()["name"] == "Breakdown"
    assert authed.delete(f"/api/labels/{lid}").status_code == 204
    assert "Breakdown" not in row_names(authed.get(f"/api/projects/{project['id']}").json())


def test_reorder_and_collapse_rows(authed: TestClient, project: dict) -> None:
    pid = project["id"]
    order = ["Chorus", "Verse", "Full Song", "Solo"]
    body = {"rows": [{"label_id": label_id(project, n), "collapsed": n == "Solo"} for n in order]}
    r = authed.put(f"/api/projects/{pid}/rows", json=body)
    assert r.status_code == 200

    names = row_names(r.json())
    # Full Song stays pinned, the saved order follows, unsaved parts keep their default order.
    assert names[:4] == ["Full Song", "Chorus", "Verse", "Solo"]
    assert names[4:] == [n for n in DEFAULT_PARTS if n not in order]
    solo = next(row for row in r.json()["rows"] if row["name"] == "Solo")
    assert solo["collapsed"] is True
    assert row_names(authed.get(f"/api/projects/{pid}").json()) == names


def test_rows_reject_bad_label_ids(authed: TestClient, project: dict) -> None:
    pid = project["id"]
    verse = label_id(project, "Verse")
    assert authed.put(f"/api/projects/{pid}/rows", json={"rows": [{"label_id": 9999}]}).status_code == 400
    dupes = {"rows": [{"label_id": verse}, {"label_id": verse}]}
    assert authed.put(f"/api/projects/{pid}/rows", json=dupes).status_code == 400


def _add_clip(
    conn: sqlite3.Connection, project_id: int | None, uid: str, recorded_at: str, labels: list[int],
    *, duration: float = 10.0,
) -> int:
    """A 10 s video recording, and a clip covering all of it (spec 5.10)."""
    with conn:
        rec = conn.execute(
            "INSERT INTO recordings (uid, original_filename, media_type, duration_seconds, recorded_at, "
            "recorded_at_source, uploaded_at, size_bytes, sha256, original_path) "
            "VALUES (?, ?, 'video', ?, ?, 'metadata', ?, 1, 'x', ?)",
            (uid, f"{uid}.mp4", duration, recorded_at, now_iso(), f"p/{uid}.mp4"),
        )
        cur = conn.execute(
            "INSERT INTO clips (uid, project_id, recorded_at, recorded_at_source, uploaded_at, media_type, "
            "duration_seconds, recording_id) VALUES (?, ?, ?, 'metadata', ?, 'video', ?, ?)",
            (uid, project_id, recorded_at, now_iso(), duration, rec.lastrowid),
        )
        conn.executemany(
            "INSERT INTO clip_labels (clip_id, label_id) VALUES (?, ?)", [(cur.lastrowid, lid) for lid in labels]
        )
    return cur.lastrowid


def test_clips_fill_rows_newest_first(authed: TestClient, project: dict, settings: Settings) -> None:
    verse, chorus = label_id(project, "Verse"), label_id(project, "Chorus")
    conn = connect(settings.db_path)
    _add_clip(conn, project["id"], "old", "2026-10-15T12:00:00.000Z", [verse])
    _add_clip(conn, project["id"], "new", "2026-11-23T12:00:00.000Z", [verse, chorus])
    _add_clip(conn, project["id"], "loose", "2026-11-01T12:00:00.000Z", [])
    conn.close()

    detail = authed.get(f"/api/projects/{project['id']}").json()
    rows = {r["name"]: [c["uid"] for c in r["clips"]] for r in detail["rows"]}
    assert rows["Verse"] == ["new", "old"]
    assert rows["Chorus"] == ["new"]  # a clip with two labels shows in both rows
    assert [c["uid"] for c in detail["unlabeled"]] == ["loose"]
    assert detail["clip_count"] == 3

    summary = authed.get("/api/projects").json()[0]
    assert summary["clip_count"] == 3
    assert summary["cover_thumbnail_url"] is None  # no thumbnails generated yet

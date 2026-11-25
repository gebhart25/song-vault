from __future__ import annotations

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.db import connect

from .test_rows_and_labels import _add_clip, label_id
from .test_trash import _clip_with_files


def _row(authed: TestClient, project_id: int, name: str) -> list[str]:
    detail = authed.get(f"/api/projects/{project_id}").json()
    return [c["uid"] for c in next(r for r in detail["rows"] if r["name"] == name)["clips"]]


def test_current_take_is_pinned_left(authed: TestClient, project: dict, settings: Settings) -> None:
    verse = label_id(project, "Verse")
    conn = connect(settings.db_path)
    oldest = _add_clip(conn, project["id"], "oldest", "2026-10-01T12:00:00.000Z", [verse])
    _add_clip(conn, project["id"], "middle", "2026-11-01T12:00:00.000Z", [verse])
    _add_clip(conn, project["id"], "newest", "2026-11-20T12:00:00.000Z", [verse])
    conn.close()

    assert _row(authed, project["id"], "Verse") == ["newest", "middle", "oldest"]
    authed.patch(f"/api/clips/{oldest}", json={"status": "current"})
    assert _row(authed, project["id"], "Verse") == ["oldest", "newest", "middle"]


def test_bulk_move_parts_and_status(authed: TestClient, project: dict, settings: Settings) -> None:
    verse, chorus, bridge = (label_id(project, n) for n in ("Verse", "Chorus", "Bridge"))
    a = _clip_with_files(settings, project["id"], "a", [verse])
    b = _clip_with_files(settings, project["id"], "b", [verse, chorus])
    untouched = _clip_with_files(settings, project["id"], "c", [verse])

    r = authed.post("/api/clips/bulk", json={"clip_ids": [a, b, a], "add_label_ids": [bridge], "remove_label_ids": [verse]})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2}
    assert authed.get(f"/api/clips/{a}").json()["label_ids"] == [bridge]
    assert authed.get(f"/api/clips/{b}").json()["label_ids"] == sorted([chorus, bridge])
    assert authed.get(f"/api/clips/{untouched}").json()["label_ids"] == [verse]

    r = authed.post("/api/clips/bulk", json={"clip_ids": [a, b], "status": "discarded"})
    assert [authed.get(f"/api/clips/{c}").json()["status"] for c in (a, b, untouched)] == ["discarded", "discarded", "idea"]

    # Moving to another song moves the files and keeps the parts that exist there.
    other = authed.post("/api/projects", json={"name": "Other Song"}).json()
    r = authed.post("/api/clips/bulk", json={"clip_ids": [a, b], "project_id": other["id"]})
    assert r.status_code == 200, r.text
    for cid, uid in ((a, "a"), (b, "b")):
        clip = authed.get(f"/api/clips/{cid}").json()
        assert clip["project_id"] == other["id"]
        assert (settings.media_dir / "other-song" / f"{uid}.mp4").is_file()
        assert not (settings.media_dir / "trash-song" / f"{uid}.mp4").exists()
    assert authed.get(f"/api/clips/{b}").json()["label_ids"] == sorted([chorus, bridge])  # global parts

    # ...and null moves them to Uncategorized.
    r = authed.post("/api/clips/bulk", json={"clip_ids": [a], "project_id": None})
    assert authed.get(f"/api/clips/{a}").json()["project_id"] is None
    assert (settings.media_dir / "_inbox" / "a.mp4").is_file()


def test_bulk_is_all_or_nothing(authed: TestClient, project: dict, settings: Settings) -> None:
    verse = label_id(project, "Verse")
    a = _clip_with_files(settings, project["id"], "a", [verse])
    b = _clip_with_files(settings, project["id"], "b", [verse])
    other = authed.post("/api/projects", json={"name": "Other Song"}).json()
    custom = authed.post("/api/labels", json={"name": "Breakdown", "project_id": project["id"]}).json()["id"]

    # A part that doesn't exist in the target song: nothing moves.
    r = authed.post("/api/clips/bulk", json={"clip_ids": [a, b], "project_id": other["id"], "add_label_ids": [custom]})
    assert r.status_code == 400
    for cid, uid in ((a, "a"), (b, "b")):
        assert authed.get(f"/api/clips/{cid}").json()["project_id"] == project["id"]
        assert (settings.media_dir / "trash-song" / f"{uid}.mp4").is_file()

    assert authed.post("/api/clips/bulk", json={"clip_ids": [a], "status": "current"}).status_code == 422
    assert authed.post("/api/clips/bulk", json={"clip_ids": [a], "status": None}).status_code == 422
    assert authed.post("/api/clips/bulk", json={"clip_ids": []}).status_code == 422
    assert authed.post("/api/clips/bulk", json={"clip_ids": [a, 9999], "status": "used"}).status_code == 404
    assert authed.get(f"/api/clips/{a}").json()["status"] == "idea"


def test_bulk_trash(authed: TestClient, project: dict, settings: Settings) -> None:
    verse = label_id(project, "Verse")
    a = _clip_with_files(settings, project["id"], "a", [verse])
    b = _clip_with_files(settings, None, "b")
    keep = _clip_with_files(settings, project["id"], "c", [verse])

    r = authed.post("/api/clips/bulk/trash", json={"clip_ids": [a, b]})
    assert r.json() == {"updated": 2}
    trash = authed.get("/api/trash").json()
    assert {c["id"] for c in trash["clips"]} == {a, b}
    assert _row(authed, project["id"], "Verse") == ["c"]
    assert authed.get(f"/api/clips/{keep}").json()["deleted_at"] is None
    # Already in Trash: the whole request is refused.
    assert authed.post("/api/clips/bulk/trash", json={"clip_ids": [a, keep]}).status_code == 404

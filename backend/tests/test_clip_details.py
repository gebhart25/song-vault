from __future__ import annotations

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.db import connect

from .test_rows_and_labels import _add_clip, label_id


def _clips(authed: TestClient, project: dict, settings: Settings, parts: dict[str, list[str]]) -> dict[str, int]:
    conn = connect(settings.db_path)
    ids = {
        uid: _add_clip(conn, project["id"], uid, "2026-11-20T12:00:00.000Z", [label_id(project, p) for p in names])
        for uid, names in parts.items()
    }
    conn.close()
    return ids


def status(authed: TestClient, clip_id: int) -> str:
    return authed.get(f"/api/clips/{clip_id}").json()["status"]


def test_clip_details_and_unlabeled_progressions(authed: TestClient, project: dict, settings: Settings) -> None:
    cid = _clips(authed, project, settings, {"a": ["Verse"]})["a"]
    fresh = authed.get(f"/api/clips/{cid}").json()
    assert (fresh["song_key"], fresh["bpm"], fresh["capo"], fresh["progressions"]) == (None, None, None, [])

    r = authed.patch(
        f"/api/clips/{cid}",
        json={"song_key": " E ", "bpm": 100, "capo": 0, "tuning": "  ", "time_signature": "6/8",
              "progressions": [["D", " Bm ", "G"], ["A7"]]},
    )
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["song_key"], c["bpm"], c["capo"], c["tuning"], c["time_signature"]) == ("E", 100, 0, None, "6/8")
    assert c["progressions"] == [["D", "Bm", "G"], ["A7"]]

    c = authed.patch(f"/api/clips/{cid}", json={"song_key": None, "progressions": []}).json()
    assert (c["song_key"], c["progressions"], c["bpm"]) == (None, [], 100)

    for bad in ({"progressions": [[]]}, {"progressions": [["[D]"]]}, {"progressions": None}, {"bpm": 500}, {"capo": -1}):
        assert authed.patch(f"/api/clips/{cid}", json=bad).status_code == 422, bad


def test_one_current_take_per_part(authed: TestClient, project: dict, settings: Settings) -> None:
    ids = _clips(
        authed, project, settings,
        {"a": ["Verse"], "b": ["Verse", "Chorus"], "c": ["Chorus"], "d": ["Bridge"], "loose": []},
    )
    other = authed.post("/api/projects", json={"name": "Other Song"}).json()
    elsewhere = _clips(authed, other, settings, {"x": ["Verse"]})["x"]
    authed.patch(f"/api/clips/{elsewhere}", json={"status": "current"})

    def mark(uid: str) -> None:
        assert authed.patch(f"/api/clips/{ids[uid]}", json={"status": "current"}).status_code == 200

    mark("a")
    mark("b")  # shares Verse with a
    assert (status(authed, ids["a"]), status(authed, ids["b"])) == ("idea", "current")
    mark("c")  # shares Chorus with b
    assert (status(authed, ids["b"]), status(authed, ids["c"])) == ("idea", "current")
    mark("d")  # Bridge: no overlap, both stay
    mark("loose")  # no part: nothing to replace
    assert [status(authed, ids[u]) for u in ("c", "d", "loose")] == ["current"] * 3
    assert status(authed, elsewhere) == "current"  # other songs aren't touched

    # Moving a Current take into a part that has one replaces that one.
    authed.patch(f"/api/clips/{ids['d']}", json={"label_ids": [label_id(project, "Chorus")]})
    assert (status(authed, ids["c"]), status(authed, ids["d"])) == ("idea", "current")

    # The song page's tiles carry the status for the badge.
    chorus = next(r for r in authed.get(f"/api/projects/{project['id']}").json()["rows"] if r["name"] == "Chorus")
    assert {c["id"]: c["status"] for c in chorus["clips"]} == {ids["b"]: "idea", ids["c"]: "idea", ids["d"]: "current"}

    # Un-marking is just another status.
    assert authed.patch(f"/api/clips/{ids['d']}", json={"status": "idea"}).json()["status"] == "idea"

"""The Notebook (spec 5.11): notes written on a device, synced by change number,
with conflicts kept as copies rather than overwritten."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.db import connect
from songvault.notes import purge_notes

A = "note-aaaaaaaa"
B = "note-bbbbbbbb"


def send(client: TestClient, *changes: dict) -> list[dict]:
    r = client.post("/api/notes/changes", json={"changes": list(changes)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def new(uid: str, body: str, **extra: object) -> dict:
    return {"uid": uid, "body": body, "created_at": "2026-11-26T09:30:00.000Z", **extra}


def edit(note: dict, **fields: object) -> dict:
    """A change made on a device that last saw `note`."""
    return {"uid": note["uid"], "base_revision": note["revision"], "base_body": note["body"], **fields}


def test_needs_sign_in(client: TestClient) -> None:
    assert client.get("/api/notes").status_code == 401
    assert client.post("/api/notes/changes", json={"changes": []}).status_code == 401


def test_new_notes_and_syncing_by_change_number(authed: TestClient) -> None:
    first = authed.get("/api/notes").json()
    assert first == {"notes": [], "cursor": 0, "full": True}

    [a] = send(authed, new(A, "Headlights on the county road\n#hook"))
    assert a["conflict"] is False
    note = a["note"]
    assert note["body"] == "Headlights on the county road\n#hook"
    assert (note["revision"], note["starred"], note["status"], note["deleted_at"]) == (1, False, "fresh", None)
    assert note["created_at"] == "2026-11-26T09:30:00.000Z"  # the device's time

    page = authed.get("/api/notes?since=0").json()
    assert [n["uid"] for n in page["notes"]] == [A] and page["full"] is False
    cursor = page["cursor"]
    assert authed.get(f"/api/notes?since={cursor}").json() == {"notes": [], "cursor": cursor, "full": False}

    send(authed, new(B, "rhymes: road / owed / slowed"))
    page = authed.get(f"/api/notes?since={cursor}").json()
    assert [n["uid"] for n in page["notes"]] == [B]

    # The same new note sent twice (a sync cut off half way) is stored once.
    [again] = send(authed, new(A, "Headlights on the county road\n#hook"))
    assert again["note"]["revision"] == 1
    assert len(authed.get("/api/notes").json()["notes"]) == 2


def test_edits_carry_their_starting_point(authed: TestClient) -> None:
    [r] = send(authed, new(A, "first line"))
    v1 = r["note"]

    [r] = send(authed, edit(v1, body="first line, better"))
    v2 = r["note"]
    assert (v2["body"], v2["revision"], r["conflict"]) == ("first line, better", 2, False)

    # The same edit again (its answer was lost): no conflict, nothing changes.
    [r] = send(authed, edit(v1, body="first line, better"))
    assert (r["conflict"], r["note"]["revision"]) == (False, 2)

    # Another device, still on v1, changes the text differently: a conflict, nothing changed.
    [r] = send(authed, edit(v1, body="first line, other phone"))
    assert r["conflict"] is True
    assert r["note"]["body"] == "first line, better" and r["note"]["revision"] == 2

    # Starring elsewhere doesn't make the text stale: an edit from before the star still lands.
    [r] = send(authed, edit(v2, starred=True))
    v3 = r["note"]
    [r] = send(authed, edit(v2, body="first line, best"))
    assert r["conflict"] is False
    assert (r["note"]["body"], r["note"]["starred"], r["note"]["revision"]) == ("first line, best", True, 4)

    # Stars and used / fresh take the last change, stale or not.
    [r] = send(authed, edit(v3, starred=False, status="used"))
    assert (r["note"]["starred"], r["note"]["status"]) == (False, "used")

    # A change that changes nothing doesn't count as a change.
    before = authed.get("/api/notes").json()["cursor"]
    [r] = send(authed, edit(r["note"], status="used"))
    assert authed.get("/api/notes").json()["cursor"] == before


def test_a_copy_from_two_devices(authed: TestClient) -> None:
    [r] = send(authed, new(B, "my version", copy_of=A))
    assert r["note"]["copy_of"] == A
    [r] = send(authed, edit(r["note"], copy_of=""))  # "Got it"
    assert r["note"]["copy_of"] is None


def test_line_endings_and_times(authed: TestClient) -> None:
    [r] = send(authed, new(A, "one\r\ntwo\rthree"))
    assert r["note"]["body"] == "one\ntwo\nthree"

    future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    for i, when in enumerate(("not a date", future, "1999-01-01T00:00:00Z")):
        [r] = send(authed, {"uid": f"time-test-{i}", "body": "x", "created_at": when})
        written = datetime.fromisoformat(r["note"]["created_at"])
        assert abs(written - datetime.now(UTC)) < timedelta(minutes=1)
    [r] = send(authed, {"uid": "local-time", "body": "x", "created_at": "2026-11-26T10:00:00+02:00"})
    assert r["note"]["created_at"] == "2026-11-26T08:00:00.000Z"


def test_bad_changes_are_refused(authed: TestClient) -> None:
    for change in ({"uid": "short", "body": "x"}, {"uid": "has spaces in it", "body": "x"}, {"uid": A, "status": "nope"}):
        assert authed.post("/api/notes/changes", json={"changes": [change]}).status_code == 422
    too_many = [new(f"bulk-{i:05d}", "x") for i in range(501)]
    assert authed.post("/api/notes/changes", json={"changes": too_many}).status_code == 422


def test_trash_restore_and_sync(authed: TestClient) -> None:
    [r] = send(authed, new(A, "keep me"))
    send(authed, new(B, "bin me"))
    cursor = authed.get("/api/notes").json()["cursor"]

    [r] = send(authed, {"uid": B, "trash": True})
    assert r["note"]["deleted_at"] is not None
    assert authed.get("/api/clips/summary").json()["trash"] == 1

    # Devices hear about it, so they can drop it; the whole notebook leaves it out.
    changed = authed.get(f"/api/notes?since={cursor}").json()["notes"]
    assert [(n["uid"], n["deleted_at"] is not None) for n in changed] == [(B, True)]
    assert [n["uid"] for n in authed.get("/api/notes").json()["notes"]] == [A]

    trash = authed.get("/api/trash").json()
    assert [(n["uid"], n["body"]) for n in trash["notes"]] == [(B, "bin me")]
    deleted = datetime.fromisoformat(trash["notes"][0]["deleted_at"])
    assert datetime.fromisoformat(trash["notes"][0]["purge_at"]) == deleted + timedelta(days=30)

    # A device that edited it meanwhile keeps its text as a copy; other changes are dropped.
    [r] = send(authed, {"uid": B, "base_revision": 1, "base_body": "bin me", "body": "bin me? no!"})
    assert r["conflict"] is True
    [r] = send(authed, {"uid": B, "base_revision": 1, "base_body": "bin me", "starred": True})
    assert r["conflict"] is False and r["note"]["starred"] is False

    # Trashing it again, or a note the server never had, is harmless.
    assert send(authed, {"uid": B, "trash": True})[0]["note"]["deleted_at"] is not None
    assert send(authed, {"uid": "never-seen", "trash": True})[0]["note"] is None

    restored = authed.post(f"/api/notes/{B}/restore")
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None
    assert {n["uid"] for n in authed.get("/api/notes").json()["notes"]} == {A, B}
    assert authed.get("/api/trash").json()["notes"] == []
    assert authed.post("/api/notes/missing-note/restore").status_code == 404


def _age(settings: Settings, uid: str, days: int) -> None:
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = connect(settings.db_path)
    with conn:
        conn.execute("UPDATE notes SET deleted_at = ? WHERE uid = ?", (when, uid))
    conn.close()


def test_purge_after_30_days_resets_devices_that_missed_it(authed: TestClient, settings: Settings) -> None:
    send(authed, new(A, "old"), new(B, "newer"), new("note-cccccccc", "kept"))
    early = authed.get("/api/notes").json()["cursor"]  # a device that synced before the deletes
    send(authed, {"uid": A, "trash": True}, {"uid": B, "trash": True})
    _age(settings, A, 31)
    _age(settings, B, 29)
    late = authed.get("/api/notes").json()["cursor"]  # one that synced after them

    assert purge_notes(settings) == 1
    assert purge_notes(settings) == 0
    assert [n["uid"] for n in authed.get("/api/trash").json()["notes"]] == [B]

    # The early device can't be told A is gone, so it gets the whole notebook again.
    page = authed.get(f"/api/notes?since={early}").json()
    assert page["full"] is True
    assert [n["uid"] for n in page["notes"]] == ["note-cccccccc"]
    # The late device already dropped A when it went to Trash.
    assert authed.get(f"/api/notes?since={late}").json() == {"notes": [], "cursor": late, "full": False}
    # A cursor from the future (a database restored from a backup) also starts again.
    assert authed.get(f"/api/notes?since={late + 50}").json()["full"] is True

    # A device that still has A with an unsent edit puts it back as a new note.
    [r] = send(authed, {"uid": A, "base_revision": 1, "base_body": "old", "body": "old, but edited"})
    assert r["note"]["body"] == "old, but edited" and r["note"]["revision"] == 1

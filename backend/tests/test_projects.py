from __future__ import annotations

from fastapi.testclient import TestClient

from songvault.projects import slugify


def test_slugify() -> None:
    assert slugify("Midnight Drive") == "midnight-drive"
    assert slugify("  Café   Señorita!! ") == "cafe-senorita"
    assert slugify("???") == "song"


def test_create_project(project: dict) -> None:
    assert project["name"] == "Midnight Drive"
    assert project["slug"] == "midnight-drive"
    assert project["status"] == "idea"
    assert project["archived"] is False
    assert project["clip_count"] == 0
    assert project["created_at"].endswith("Z")


def test_duplicate_names_get_unique_slugs(authed: TestClient, project: dict) -> None:
    second = authed.post("/api/projects", json={"name": "Midnight  Drive"}).json()
    third = authed.post("/api/projects", json={"name": "midnight drive"}).json()
    assert second["name"] == "Midnight Drive"  # whitespace collapsed
    assert second["slug"] == "midnight-drive-2"
    assert third["slug"] == "midnight-drive-3"


def test_blank_name_rejected(authed: TestClient) -> None:
    assert authed.post("/api/projects", json={"name": "   "}).status_code == 422


def test_list_is_newest_updated_first(authed: TestClient) -> None:
    a = authed.post("/api/projects", json={"name": "A"}).json()
    authed.post("/api/projects", json={"name": "B"})
    names = [p["name"] for p in authed.get("/api/projects").json()]
    assert names == ["B", "A"]

    authed.patch(f"/api/projects/{a['id']}", json={"notes": "touched"})
    names = [p["name"] for p in authed.get("/api/projects").json()]
    assert names == ["A", "B"]


def test_search_and_status_filter(authed: TestClient) -> None:
    authed.post("/api/projects", json={"name": "Blue Window", "notes": "open G tuning"})
    authed.post("/api/projects", json={"name": "Red Room", "status": "writing"})
    authed.post("/api/projects", json={"name": "100% Proof"})

    def names(**params: str) -> list[str]:
        return sorted(p["name"] for p in authed.get("/api/projects", params=params).json())

    assert names(q="blue") == ["Blue Window"]
    assert names(q="tuning") == ["Blue Window"]  # notes are searched too
    assert names(q="%") == ["100% Proof"]  # LIKE wildcards are literal
    assert names(status="writing") == ["Red Room"]
    assert authed.get("/api/projects", params={"status": "bogus"}).status_code == 422


def test_update_fields_and_rename(authed: TestClient, project: dict) -> None:
    r = authed.patch(
        f"/api/projects/{project['id']}",
        json={"name": "Midnight Drive (Slow)", "status": "arranging", "bpm": 84, "song_key": "D minor"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "midnight-drive-slow"
    assert (body["status"], body["bpm"], body["song_key"]) == ("arranging", 84, "D minor")
    assert body["updated_at"] >= project["updated_at"]

    cleared = authed.patch(f"/api/projects/{project['id']}", json={"song_key": None}).json()
    assert cleared["song_key"] is None
    assert authed.patch(f"/api/projects/{project['id']}", json={"name": None}).status_code == 422


def test_archive_hides_from_default_list(authed: TestClient, project: dict) -> None:
    authed.patch(f"/api/projects/{project['id']}", json={"archived": True})
    assert authed.get("/api/projects").json() == []
    archived = authed.get("/api/projects", params={"include_archived": "true"}).json()
    assert [p["archived"] for p in archived] == [True]


def test_delete_moves_to_trash_and_restore(authed: TestClient, project: dict) -> None:
    pid = project["id"]
    assert authed.delete(f"/api/projects/{pid}").status_code == 204
    assert authed.get(f"/api/projects/{pid}").status_code == 404
    assert authed.get("/api/projects").json() == []

    # The slug stays reserved while the project is in Trash.
    again = authed.post("/api/projects", json={"name": "Midnight Drive"}).json()
    assert again["slug"] == "midnight-drive-2"

    assert authed.post(f"/api/projects/{pid}/restore").status_code == 200
    assert authed.get(f"/api/projects/{pid}").status_code == 200
    assert authed.post(f"/api/projects/{pid}/restore").status_code == 404


def test_missing_project_is_404(authed: TestClient) -> None:
    assert authed.get("/api/projects/999").status_code == 404
    assert authed.patch("/api/projects/999", json={"notes": "x"}).status_code == 404
    assert authed.delete("/api/projects/999").status_code == 404

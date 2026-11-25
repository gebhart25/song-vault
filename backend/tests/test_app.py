from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from songvault.cli import main as cli_main
from songvault.config import Settings
from songvault.db import connect, migrate
from songvault.main import create_app

from .conftest import PIN


def test_migrations_are_idempotent(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True)
    conn = connect(settings.db_path)
    latest = migrate(conn)
    assert latest >= 2
    assert migrate(conn) == latest
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("SELECT count(*) FROM labels WHERE builtin = 1").fetchone()[0] == 12
    conn.close()


def test_unknown_api_path_is_json_404(authed: TestClient) -> None:
    r = authed.get("/api/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}


def test_frontend_not_built_message(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 404
    assert "Frontend not built" in r.text


def _build_fake_frontend(static: Path) -> None:
    (static / "_app" / "immutable").mkdir(parents=True)
    (static / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (static / "_app" / "immutable" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (static / "manifest.webmanifest").write_text("{}", encoding="utf-8")


def test_spa_routes_and_assets(settings: Settings, tmp_path: Path) -> None:
    _build_fake_frontend(settings.static_dir)
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with TestClient(create_app(settings)) as c:
        assert c.get("/").text == "<html>spa</html>"
        assert c.get("/projects/12").text == "<html>spa</html>"  # client-side route
        assert c.get("/_app/immutable/app.js").text == "console.log(1)"
        assert c.get("/manifest.webmanifest").text == "{}"
        assert c.get("/../secret.txt").text == "<html>spa</html>"
        assert c.get("/%2e%2e/secret.txt").text == "<html>spa</html>"


def test_cli_reset_pin(settings: Settings, monkeypatch) -> None:
    monkeypatch.setenv("SONGVAULT_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("SONGVAULT_MEDIA_DIR", str(settings.media_dir))
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/setup", json={"pin": PIN})
        assert c.get("/api/auth/status").json()["authenticated"] is True

        assert cli_main(["reset-pin"]) == 0

        assert c.get("/api/auth/status").json() == {"setup_required": True, "authenticated": False, "pin_set": False}


def test_share_target_without_service_worker(client: TestClient) -> None:
    r = client.post("/share-target", files={"media": ("a.mp4", b"x", "video/mp4")}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/share?missed=1")


def test_cli_snapshot_db(settings: Settings, monkeypatch, tmp_path: Path) -> None:
    import sqlite3

    monkeypatch.setenv("SONGVAULT_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("SONGVAULT_MEDIA_DIR", str(settings.media_dir))
    with TestClient(create_app(settings)) as c:
        c.post("/api/auth/setup", json={"pin": PIN})
        c.post("/api/projects", json={"name": "Snapshot Song"})
    dest = tmp_path / "backup" / "songvault.db"
    assert cli_main(["snapshot-db", str(dest)]) == 0
    assert cli_main(["snapshot-db", str(dest)]) == 0  # overwrites cleanly
    copy = sqlite3.connect(dest)
    assert copy.execute("SELECT name FROM projects").fetchall() == [("Snapshot Song",)]
    copy.close()
    assert not dest.with_name("songvault.db.tmp").exists()

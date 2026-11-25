from __future__ import annotations

import sqlite3

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from songvault.auth import SESSION_COOKIE, LoginLimiter
from songvault.config import Settings
from songvault.main import create_app

from .conftest import PIN

OLD_PASSWORD = "correct horse battery"


def status(c: TestClient) -> dict:
    return c.get("/api/auth/status").json()


def test_fresh_install_requires_setup(client: TestClient) -> None:
    assert status(client) == {"setup_required": True, "authenticated": False, "pin_set": False}


def test_api_is_locked_until_signed_in(client: TestClient) -> None:
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_setup_signs_in_and_sets_a_safe_cookie(client: TestClient) -> None:
    r = client.post("/api/auth/setup", json={"pin": PIN})
    assert r.status_code == 200
    assert r.json() == {"setup_required": False, "authenticated": True, "pin_set": True}
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=31536000" in cookie
    assert "Secure" not in cookie  # plain HTTP request
    assert client.get("/api/projects").status_code == 200


def test_cookie_is_secure_over_https(settings: Settings) -> None:
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        r = c.post("/api/auth/setup", json={"pin": PIN})
        assert "Secure" in r.headers["set-cookie"]


def test_setup_only_works_once(authed: TestClient) -> None:
    assert authed.post("/api/auth/setup", json={"pin": "1111"}).status_code == 409


@pytest.mark.parametrize("pin", ["", "123", "1234567", "12a4", "12 34", "١٢٣٤"])
def test_setup_needs_4_to_6_digits(client: TestClient, pin: str) -> None:
    r = client.post("/api/auth/setup", json={"pin": pin})
    assert r.status_code == 422
    assert r.json()["detail"] == "Use 4 to 6 digits"
    assert status(client)["setup_required"] is True


@pytest.mark.parametrize("pin", ["0000", "12345", "999999"])
def test_setup_accepts_4_to_6_digits(client: TestClient, pin: str) -> None:
    assert client.post("/api/auth/setup", json={"pin": pin}).status_code == 200


def test_logout_then_login(authed: TestClient) -> None:
    assert authed.post("/api/auth/logout").status_code == 204
    assert authed.get("/api/projects").status_code == 401

    r = authed.post("/api/auth/login", json={"pin": "0000"})
    assert (r.status_code, r.json()["detail"]) == (401, "Wrong PIN")
    assert authed.get("/api/projects").status_code == 401

    r = authed.post("/api/auth/login", json={"pin": PIN})
    assert r.status_code == 200
    assert authed.get("/api/projects").status_code == 200


def test_forged_cookie_is_rejected(authed: TestClient) -> None:
    authed.cookies.clear()
    authed.cookies.set(SESSION_COOKIE, "not-a-real-token")
    assert authed.get("/api/projects").status_code == 401


def test_login_is_rate_limited(authed: TestClient) -> None:
    authed.post("/api/auth/logout")
    for _ in range(5):
        assert authed.post("/api/auth/login", json={"pin": "0000"}).status_code == 401
    r = authed.post("/api/auth/login", json={"pin": PIN})
    assert r.status_code == 429


def test_limiter_also_caps_all_clients_together() -> None:
    limiter = LoginLimiter(max_failures=5, max_total=20)
    for i in range(19):
        limiter.record_failure(f"10.0.0.{i}")
    assert not limiter.blocked("10.0.0.200")
    limiter.record_failure("10.0.0.19")
    assert limiter.blocked("10.0.0.200")  # a "new" address doesn't get fresh tries


def test_login_before_setup(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"pin": PIN}).status_code == 409


def test_change_pin(authed: TestClient) -> None:
    r = authed.post("/api/auth/pin", json={"current": "0000", "pin": "2468"})
    assert (r.status_code, r.json()["detail"]) == (403, "Current PIN is wrong")
    assert authed.post("/api/auth/pin", json={"pin": "2468"}).status_code == 403
    assert authed.post("/api/auth/pin", json={"current": PIN, "pin": "24"}).status_code == 422

    r = authed.post("/api/auth/pin", json={"current": PIN, "pin": "2468"})
    assert r.status_code == 200
    assert r.json() == {"setup_required": False, "authenticated": True, "pin_set": True}
    assert authed.get("/api/projects").status_code == 200  # still signed in

    authed.post("/api/auth/logout")
    assert authed.post("/api/auth/login", json={"pin": PIN}).status_code == 401
    assert authed.post("/api/auth/login", json={"pin": "2468"}).status_code == 200


def test_change_pin_needs_sign_in(authed: TestClient) -> None:
    authed.post("/api/auth/logout")
    assert authed.post("/api/auth/pin", json={"current": PIN, "pin": "2468"}).status_code == 401


def test_old_password_signs_in_once_then_switches_to_a_pin(client: TestClient, settings: Settings) -> None:
    """Installs from before the PIN have a password hash. It still signs in, and the
    app then has the user choose a PIN without asking for the password again."""
    db = sqlite3.connect(settings.db_path)
    with db:
        db.execute("INSERT INTO settings (key, value) VALUES ('password_hash', ?)", (PasswordHasher().hash(OLD_PASSWORD),))
    db.close()

    assert status(client) == {"setup_required": False, "authenticated": False, "pin_set": False}
    assert client.post("/api/auth/setup", json={"pin": PIN}).status_code == 409
    r = client.post("/api/auth/login", json={"pin": "wrong"})
    assert (r.status_code, r.json()["detail"]) == (401, "Wrong password")

    r = client.post("/api/auth/login", json={"pin": OLD_PASSWORD})
    assert r.json() == {"setup_required": False, "authenticated": True, "pin_set": False}

    r = client.post("/api/auth/pin", json={"pin": PIN})
    assert r.status_code == 200
    assert r.json()["pin_set"] is True

    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"pin": OLD_PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"pin": PIN}).status_code == 200

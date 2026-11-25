"""Single-PIN login (spec 2.6).

- A 4-6 digit PIN is chosen on first launch and stored only as an argon2 hash.
- Installs from before the PIN existed have a password instead. It still signs
  in, and the app then asks for a PIN once (`pin_set` in the status).
- A successful login creates a random session token. The browser keeps it in
  an HttpOnly cookie for about a year; the database keeps only its sha256.
- Failed logins are rate-limited per client address and overall, since a PIN
  has few combinations.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from .db import now_iso
from .deps import get_db

SESSION_COOKIE = "sv_session"
PIN_RE = re.compile(r"[0-9]{4,6}")
PIN_RULE = "Use 4 to 6 digits"
PIN_KEY = "pin_hash"
LEGACY_KEY = "password_hash"  # installs from before the PIN
_hasher = PasswordHasher()


# --- PIN ------------------------------------------------------------------------


def _get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def pin_is_set(conn: sqlite3.Connection) -> bool:
    return _get(conn, PIN_KEY) is not None


def setup_required(conn: sqlite3.Connection) -> bool:
    return _get(conn, PIN_KEY) is None and _get(conn, LEGACY_KEY) is None


def valid_pin(pin: str) -> bool:
    return PIN_RE.fullmatch(pin) is not None


def set_pin(conn: sqlite3.Connection, pin: str) -> None:
    if not valid_pin(pin):
        raise ValueError(PIN_RULE)
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (PIN_KEY, _hasher.hash(pin)),
        )
        conn.execute("DELETE FROM settings WHERE key = ?", (LEGACY_KEY,))


def check_secret(conn: sqlite3.Connection, secret: str) -> bool:
    """True if `secret` is the PIN (or, before a PIN is chosen, the old password)."""
    key = PIN_KEY if pin_is_set(conn) else LEGACY_KEY
    stored = _get(conn, key)
    if stored is None:
        return False
    try:
        _hasher.verify(stored, secret)
    except (VerificationError, InvalidHashError):
        return False
    if _hasher.check_needs_rehash(stored):
        with conn:
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (_hasher.hash(secret), key))
    return True


def reset_pin(conn: sqlite3.Connection) -> None:
    """Forget the PIN and sign out every session. The next visit shows setup."""
    with conn:
        conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (PIN_KEY, LEGACY_KEY))
        conn.execute("DELETE FROM sessions")


# --- sessions -----------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_session(conn: sqlite3.Connection, days: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    with conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
        conn.execute(
            "INSERT INTO sessions (token_hash, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), _iso(now), _iso(now + timedelta(days=days)), _iso(now)),
        )
    return token


def session_is_valid(conn: sqlite3.Connection, token: str | None) -> bool:
    if not token:
        return False
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)
    ).fetchone()
    return row is not None and row["expires_at"] > now_iso()


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def _set_session_cookie(request: Request, response: Response, token: str, days: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=days * 24 * 3600,
        httponly=True,
        samesite="lax",
        # Secure whenever the app is reached over HTTPS (uvicorn trusts the
        # proxy's X-Forwarded-Proto). Plain-HTTP LAN access still works.
        secure=request.url.scheme == "https",
        path="/",
    )


# --- rate limiting --------------------------------------------------------------


class LoginLimiter:
    """At most ``max_failures`` failed attempts per client, and ``max_total`` from
    all clients together, within ``window`` seconds. The overall cap matters because
    the client address comes from a proxy header that a LAN device could fake."""

    TOTAL = "*"

    def __init__(self, max_failures: int = 5, max_total: int = 20, window: float = 15 * 60) -> None:
        self.max_failures = max_failures
        self.max_total = max_total
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            return (
                len(self._prune(key, now)) >= self.max_failures
                or len(self._prune(self.TOTAL, now)) >= self.max_total
            )

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now).append(now)
            self._prune(self.TOTAL, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


# --- dependency and routes --------------------------------------------------------


def require_auth(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> None:
    if not session_is_valid(conn, request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")


router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthStatus(BaseModel):
    setup_required: bool
    authenticated: bool
    pin_set: bool
    """False on installs that still use the old password: the app asks for a PIN."""


class PinIn(BaseModel):
    pin: str = Field(max_length=1024)  # long enough for an old password at sign-in


class PinChange(BaseModel):
    current: str | None = Field(default=None, max_length=1024)
    """The current PIN. Not needed when switching from the old password."""
    pin: str = Field(max_length=64)


def _status(conn: sqlite3.Connection, token: str | None) -> AuthStatus:
    return AuthStatus(
        setup_required=setup_required(conn),
        authenticated=session_is_valid(conn, token),
        pin_set=pin_is_set(conn),
    )


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _sign_in(request: Request, response: Response, conn: sqlite3.Connection) -> AuthStatus:
    days = request.app.state.settings.session_days
    _set_session_cookie(request, response, create_session(conn, days), days)
    return AuthStatus(setup_required=False, authenticated=True, pin_set=pin_is_set(conn))


@router.get("/status")
def auth_status(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> AuthStatus:
    return _status(conn, request.cookies.get(SESSION_COOKIE))


@router.post("/setup")
def setup(body: PinIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)) -> AuthStatus:
    if not setup_required(conn):
        raise HTTPException(status.HTTP_409_CONFLICT, "A PIN is already set")
    if not valid_pin(body.pin):
        raise HTTPException(422, PIN_RULE)
    set_pin(conn, body.pin)
    return _sign_in(request, response, conn)


@router.post("/login")
def login(body: PinIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)) -> AuthStatus:
    limiter: LoginLimiter = request.app.state.login_limiter
    client = _client(request)
    if limiter.blocked(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in 15 minutes.")
    if setup_required(conn):
        raise HTTPException(status.HTTP_409_CONFLICT, "No PIN set yet")
    if not check_secret(conn, body.pin):
        limiter.record_failure(client)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong PIN" if pin_is_set(conn) else "Wrong password")
    limiter.reset(client)
    return _sign_in(request, response, conn)


@router.post("/pin", dependencies=[Depends(require_auth)])
def change_pin(body: PinChange, request: Request, conn: sqlite3.Connection = Depends(get_db)) -> AuthStatus:
    """Choose a new PIN. Other signed-in devices stay signed in."""
    if pin_is_set(conn):
        limiter: LoginLimiter = request.app.state.login_limiter
        client = _client(request)
        if limiter.blocked(client):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in 15 minutes.")
        if not body.current or not check_secret(conn, body.current):
            limiter.record_failure(client)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Current PIN is wrong")
    if not valid_pin(body.pin):
        raise HTTPException(422, PIN_RULE)
    set_pin(conn, body.pin)
    return _status(conn, request.cookies.get(SESSION_COOKIE))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)) -> None:
    delete_session(conn, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")

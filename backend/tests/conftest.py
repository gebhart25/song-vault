from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from songvault.config import Settings
from songvault.main import create_app

PIN = "482613"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        media_dir=tmp_path / "media",
        static_dir=tmp_path / "static",
        start_worker=False,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    r = client.post("/api/auth/setup", json={"pin": PIN})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def project(authed: TestClient) -> dict:
    r = authed.post("/api/projects", json={"name": "Midnight Drive"})
    assert r.status_code == 201, r.text
    return r.json()

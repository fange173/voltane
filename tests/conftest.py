import os
import tempfile
import uuid

import pytest

_TMP = tempfile.mkdtemp(prefix="voltane-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["BACKUP_DIR"] = f"{_TMP}/backups"
os.environ["DISABLE_JOBS"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def _app():
    with TestClient(app) as c:
        yield c


def _login(username, password):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(_app):
    return _login("admin", "admin123")


@pytest.fixture
def make_user(admin):
    def _make(prefix="u"):
        name = f"{prefix}{uuid.uuid4().hex[:6]}"
        r = admin.post("/api/admin/users", json={"username": name, "password": "secret123", "display_name": name})
        assert r.status_code == 200, r.text
        c = _login(name, "secret123")
        c.username = name
        c.uid = r.json()["id"]
        return c
    return _make


PHEV = {"name": "秦PLUS", "energy_type": "phev", "tank_capacity_l": 48, "battery_usable_kwh": 18.3}


@pytest.fixture
def vehicle(make_user):
    """一个用户 + 一辆插混车。"""
    c = make_user("owner")
    r = c.post("/api/vehicles", json=PHEV)
    assert r.status_code == 200, r.text
    c.vid = r.json()["id"]
    return c

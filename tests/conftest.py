import os
from secrets import token_urlsafe
from unittest.mock import patch

from tests.auth_data import ADMIN_LOGIN
import tempfile
import uuid

import pytest

_TMP = tempfile.mkdtemp(prefix="voltane-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["BACKUP_DIR"] = f"{_TMP}/backups"
os.environ["DISABLE_JOBS"] = "1"
os.environ["SECRET_KEY"] = token_urlsafe(32)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def _app():
    with patch("app.database.DEFAULT_ADMIN", ADMIN_LOGIN), TestClient(app) as c:
        yield c


def _login(username, password):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(_app):
    return _login(*ADMIN_LOGIN)


@pytest.fixture
def make_user(admin):
    def _make(prefix="u"):
        name = f"{prefix}{uuid.uuid4().hex[:6]}"
        password = token_urlsafe(24)
        r = admin.post("/api/admin/users", json={"username": name, "password": password, "display_name": name})
        assert r.status_code == 200, r.text
        c = _login(name, password)
        c.test_password = password
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

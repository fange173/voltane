"""iOS 客户端接口：Bearer 令牌、refresh、logout-all、改密换令牌、health 版本字段。"""
import uuid
from datetime import datetime

from fastapi.testclient import TestClient

from app.database import utcnow
from app.main import app


def new_client():
    return TestClient(app)


def ios_login(username="admin", password="admin123"):
    c = new_client()
    r = c.post("/api/auth/login", json={"username": username, "password": password, "client": "ios"})
    assert r.status_code == 200, r.text
    return c, r.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def mk_user(admin, prefix="ios"):
    name = f"{prefix}{uuid.uuid4().hex[:6]}"
    r = admin.post("/api/admin/users", json={"username": name, "password": "secret123", "display_name": name})
    assert r.status_code == 200, r.text
    return name


def test_web_login_never_returns_token(_app):
    c = new_client()
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    assert "token" not in r.json() and "expires_at" not in r.json()
    assert "voltane_token" in r.cookies


def test_ios_login_returns_token_without_cookie(_app):
    c, body = ios_login()
    assert body["token"] and body["expires_at"].endswith("Z")
    assert "voltane_token" not in c.cookies
    exp = datetime.fromisoformat(body["expires_at"].rstrip("Z"))
    assert 89 <= (exp - utcnow()).days <= 90
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/auth/me", headers=bearer(body["token"])).json()["username"] == "admin"


def test_invalid_bearer_does_not_fall_back_to_cookie(_app):
    web = new_client()
    web.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert web.get("/api/auth/me").status_code == 200
    assert web.get("/api/auth/me", headers=bearer("garbage")).status_code == 401
    assert web.get("/api/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert web.get("/api/auth/me", headers={"Authorization": "Bearer"}).status_code == 401


def test_refresh_issues_new_token_and_requires_bearer(_app):
    c, body = ios_login()
    r = c.post("/api/auth/refresh", headers=bearer(body["token"]))
    assert r.status_code == 200 and r.json()["token"] and r.json()["expires_at"].endswith("Z")
    assert c.get("/api/auth/me", headers=bearer(r.json()["token"])).status_code == 200
    assert c.post("/api/auth/refresh").status_code == 401
    web = new_client()
    web.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    r = web.post("/api/auth/refresh")
    assert r.status_code == 400 and "token" not in r.json()


def test_logout_all_invalidates_every_session(admin):
    name = mk_user(admin)
    c1, b1 = ios_login(name, "secret123")
    c2, b2 = ios_login(name, "secret123")
    web = new_client()
    web.post("/api/auth/login", json={"username": name, "password": "secret123"})
    assert web.get("/api/auth/me").status_code == 200
    assert c1.post("/api/auth/logout-all", headers=bearer(b1["token"])).status_code == 200
    assert c2.get("/api/auth/me", headers=bearer(b2["token"])).status_code == 401
    assert c1.get("/api/auth/me", headers=bearer(b1["token"])).status_code == 401
    assert web.get("/api/auth/me").status_code == 401


def test_change_password_swaps_token_for_bearer_only(admin):
    name = mk_user(admin)
    c, b = ios_login(name, "secret123")
    r = c.post("/api/auth/change-password", headers=bearer(b["token"]),
               json={"old_password": "secret123", "new_password": "newsecret1"})
    assert r.status_code == 200 and r.json()["token"] and r.json()["expires_at"].endswith("Z")
    assert c.get("/api/auth/me", headers=bearer(b["token"])).status_code == 401
    assert c.get("/api/auth/me", headers=bearer(r.json()["token"])).status_code == 200

    web = new_client()
    web.post("/api/auth/login", json={"username": name, "password": "newsecret1"})
    r = web.post("/api/auth/change-password", json={"old_password": "newsecret1", "new_password": "another123"})
    assert r.status_code == 200 and "token" not in r.json()
    assert web.get("/api/auth/me").status_code == 200


def test_disabled_user_bearer_rejected(admin):
    name = mk_user(admin)
    c, b = ios_login(name, "secret123")
    uid = c.get("/api/auth/me", headers=bearer(b["token"])).json()["id"]
    assert admin.put(f"/api/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert c.get("/api/auth/me", headers=bearer(b["token"])).status_code == 401


def test_signup_ios_returns_token_web_does_not(vehicle, admin):
    assert admin.put("/api/admin/settings", json={"allow_signup": True}).status_code == 200
    out = {}
    for client in ("web", "ios"):
        inv = vehicle.post(f"/api/vehicles/{vehicle.vid}/invites",
                           json={"role": "viewer", "allow_signup": True, "max_uses": 1}).json()
        c = new_client()
        name = f"su{uuid.uuid4().hex[:6]}"
        r = c.post("/api/auth/signup", json={"code": inv["code"], "username": name, "password": "secret123",
                                             "client": client})
        assert r.status_code == 200, r.text
        out[client] = (c, r.json())
    assert "token" not in out["web"][1] and "voltane_token" in out["web"][0].cookies
    c, body = out["ios"]
    assert body["token"] and "voltane_token" not in c.cookies
    assert c.get("/api/auth/me", headers=bearer(body["token"])).status_code == 200


def test_bearer_works_on_business_endpoints(vehicle):
    c, b = ios_login(vehicle.username, "secret123")
    r = c.get("/api/vehicles", headers=bearer(b["token"]))
    assert r.status_code == 200 and any(v["id"] == vehicle.vid for v in r.json())


def test_health_has_version_fields(_app):
    h = new_client().get("/api/health").json()
    assert h["ok"] is True and h["api_version"] == 1 and h["tz"]
    datetime.fromisoformat(h["server_time"])
    assert "+" not in h["server_time"] and not h["server_time"].endswith("Z")


def test_legacy_super_charge_type_is_stored_as_dc(vehicle):
    r = vehicle.post("/api/records", json={"vehicle_id": vehicle.vid, "kind": "charge", "occurred_at": "2026-09-01T08:00",
                                           "odometer_km": 100, "charge_type": "super", "charge_kwh": 10, "paid_cents": 1000})
    assert r.status_code == 200 and r.json()["record"]["charge_type"] == "dc"

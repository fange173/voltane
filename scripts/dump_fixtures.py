"""
dump_fixtures.py — 导出接口契约样例，给 iOS 客户端的解码测试使用。

在临时库里生成演示数据（秦PLUS、Model Y、数据很少的宋PLUS），以 Bearer 方式登录，请求全部 GET 接口
和典型错误响应，每个响应存成 <接口名>__<参数说明>.json，另外写出 openapi.json 和 manifest.json。
不触碰真实数据库。

用法：.venv/bin/python scripts/dump_fixtures.py --out /path/to/Fixtures
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# 必须在导入 app 之前指定临时库
_TMP = tempfile.mkdtemp(prefix="voltane-fixtures-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/fixtures.db"
os.environ["BACKUP_DIR"] = f"{_TMP}/backups"
os.environ["DISABLE_JOBS"] = "1"
os.environ["SECRET_KEY"] = "fixtures-secret-key-0123456789abcdef"
os.environ.setdefault("TZ", "Asia/Shanghai")

from fastapi.testclient import TestClient  # noqa: E402

import seed_demo  # noqa: E402
from app import auth as auth_mod  # noqa: E402
from app.main import API_VERSION, app  # noqa: E402

TOKEN_PLACEHOLDER = "FIXTURE-TOKEN"
manifest: list[dict] = []
out_dir: Path


def slug(path: str) -> str:
    parts = [p.strip("{}") for p in path.replace("/api/", "", 1).split("/") if p]
    return "_".join(p if not p.isdigit() else "id" for p in parts)


def save(name: str, desc: str, method: str, path: str, params: dict | None, resp, redact: bool = False) -> None:
    ext = "json"
    try:
        body = resp.json()
        if redact and isinstance(body, dict) and "token" in body:
            body["token"] = TOKEN_PLACEHOLDER
        text = json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True)
    except ValueError:
        ext, text = "csv", resp.text
    fname = f"{name}__{desc}.{ext}"
    (out_dir / fname).write_text(text + "\n", encoding="utf-8")
    manifest.append({"file": fname, "method": method, "path": path, "params": params or {}, "status": resp.status_code})


class Dumper:
    def __init__(self, client: TestClient, token: str) -> None:
        self.c = client
        self.h = {"Authorization": f"Bearer {token}"}

    def get(self, path: str, desc: str, params: dict | None = None, name: str | None = None,
            template: str | None = None, headers: dict | None = None):
        r = self.c.get(path, params=params, headers=self.h if headers is None else headers)
        save(name or slug(template or path), desc, "GET", template or path, params, r)
        return r

    def send(self, method: str, path: str, body: dict | None = None, headers: dict | None = None):
        return self.c.request(method, path, json=body, headers=self.h if headers is None else headers)


def main() -> None:
    global out_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="样例输出目录（会先清空其中的 .json/.csv 文件）")
    args = ap.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in list(out_dir.glob("*.json")) + list(out_dir.glob("*.csv")):
        f.unlink()

    with TestClient(app) as c:
        seed_demo.main()
        login = c.post("/api/auth/login", json={"username": "admin", "password": "admin123", "client": "ios"})
        assert login.status_code == 200, login.text
        save("auth_login", "ios", "POST", "/api/auth/login", {"client": "ios"}, login, redact=True)
        d = Dumper(c, login.json()["token"])

        web_login = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        save("auth_login", "web_no_token", "POST", "/api/auth/login", {"client": "web"}, web_login)
        save("auth_refresh", "ok", "POST", "/api/auth/refresh", None, d.send("POST", "/api/auth/refresh"), redact=True)

        d.get("/api/health", "ok", headers={})
        d.get("/api/auth/me", "admin")
        d.get("/api/reminders/templates", "all", headers={})

        vehicles = d.get("/api/vehicles", "all").json()
        by_name = {v["name"]: v["id"] for v in vehicles}
        phev = next(i for n, i in by_name.items() if "秦PLUS" in n)
        bev = next(i for n, i in by_name.items() if "Model Y" in n)
        sparse = next(i for n, i in by_name.items() if "少量数据" in n)
        cars = {"phev": phev, "bev": bev, "sparse": sparse}

        # 成员、邀请码、其它用户
        member = f"member{uuid.uuid4().hex[:4]}"
        assert d.send("POST", "/api/admin/users", {"username": member, "password": "secret123", "display_name": "家人"}
                      ).status_code == 200
        assert d.send("POST", f"/api/vehicles/{phev}/members", {"username": member, "role": "editor"}).status_code == 200
        inv = d.send("POST", f"/api/vehicles/{phev}/invites", {"role": "viewer", "allow_signup": False, "max_uses": 3}).json()

        # 车辆相关
        for tag, vid in cars.items():
            d.get(f"/api/vehicles/{vid}", tag, template="/api/vehicles/{vid}")
            d.get(f"/api/vehicles/{vid}/members", tag, template="/api/vehicles/{vid}/members")
            d.get(f"/api/vehicles/{vid}/calibration", tag, template="/api/vehicles/{vid}/calibration")
            d.get(f"/api/vehicles/{vid}/health", tag, template="/api/vehicles/{vid}/health")
            d.get(f"/api/vehicles/{vid}/odometer", tag, template="/api/vehicles/{vid}/odometer")
            d.get(f"/api/vehicles/{vid}/activity", tag, {"limit": 15}, template="/api/vehicles/{vid}/activity")
            d.get(f"/api/vehicles/{vid}/invites", tag, template="/api/vehicles/{vid}/invites")
        d.get(f"/api/invites/{inv['code']}", "viewer", headers={}, template="/api/invites/{code}")

        # 概览与统计
        for rng in ("month", "30d", "year", "all"):
            d.get("/api/stats/overview", f"phev_{rng}", {"vehicle_id": phev, "range": rng})
        d.get("/api/stats/overview", "bev_month", {"vehicle_id": bev, "range": "month"})
        d.get("/api/stats/overview", "sparse_month", {"vehicle_id": sparse, "range": "month"})
        for energy in ("elec", "fuel", "combined"):
            d.get("/api/stats/segments", f"phev_{energy}", {"vehicle_id": phev, "energy": energy})
        d.get("/api/stats/segments", "bev_elec", {"vehicle_id": bev, "energy": "elec"})
        d.get("/api/stats/segments", "sparse_elec", {"vehicle_id": sparse, "energy": "elec"})
        today = date.today()
        month = today.strftime("%Y-%m")
        prev_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        for tag, vid in cars.items():
            d.get("/api/stats/energy", tag, {"vehicle_id": vid})
            d.get("/api/stats/cost", tag, {"vehicle_id": vid})
            d.get("/api/stats/mileage", tag, {"vehicle_id": vid})
            d.get("/api/stats/calendar", f"{tag}_current", {"vehicle_id": vid, "month": month})
            d.get("/api/stats/monthly-report", f"{tag}_current", {"vehicle_id": vid, "month": month})
            d.get("/api/stats/yearly-report", f"{tag}_current", {"vehicle_id": vid, "year": today.year})
        d.get("/api/stats/energy", "phev_range", {"vehicle_id": phev, "date_from": (today - timedelta(days=60)).isoformat(),
                                                   "date_to": today.isoformat()})
        d.get("/api/stats/cost", "all_vehicles", {})
        d.get("/api/stats/cost", "phev_range", {"vehicle_id": phev, "date_from": (today - timedelta(days=60)).isoformat(),
                                                 "date_to": today.isoformat()})
        d.get("/api/stats/calendar", "phev_previous", {"vehicle_id": phev, "month": prev_month})
        d.get("/api/stats/monthly-report", "phev_previous", {"vehicle_id": phev, "month": prev_month})
        d.get("/api/stats/yearly-report", "phev_last_year", {"vehicle_id": phev, "year": today.year - 1})

        # 记录列表（含游标分页）与详情
        first = d.get("/api/records", "phev_default", {"vehicle_id": phev, "limit": 20}).json()
        page_key = next((k for k in first if k != "items" and first[k]), None)
        cursor = first.get(page_key) if page_key else None
        if cursor:
            d.get("/api/records", "phev_page2", {"vehicle_id": phev, "limit": 20, "before": cursor})
        for kind in ("fuel", "charge", "snapshot", "expense", "estimated"):
            d.get("/api/records", f"phev_kind-{kind}", {"vehicle_id": phev, "kind": kind, "limit": 20})
        d.get("/api/records", "phev_mine", {"vehicle_id": phev, "mine": "true", "limit": 20})
        d.get("/api/records", "phev_anomaly", {"vehicle_id": phev, "anomaly": "true", "limit": 20})
        d.get("/api/records", "phev_search", {"vehicle_id": phev, "q": "demo", "limit": 20})
        d.get("/api/records", "phev_dates", {"vehicle_id": phev, "date_from": (today - timedelta(days=30)).isoformat(),
                                              "date_to": today.isoformat(), "limit": 20})
        d.get("/api/records", "bev_default", {"vehicle_id": bev, "limit": 20})
        d.get("/api/records", "sparse_default", {"vehicle_id": sparse, "limit": 20})

        def items_of(vid: int, **kw) -> list:
            return d.c.get("/api/records", params={"vehicle_id": vid, "limit": 200, **kw}, headers=d.h).json()["items"]

        def first_where(items: list, **cond):
            return next((i for i in items if all(i.get(k) == v for k, v in cond.items())), None)

        phev_items = items_of(phev)
        samples = {
            "fuel": first_where(phev_items, type="record", kind="fuel"),
            "charge": first_where(phev_items, type="record", kind="charge"),
            "snapshot": first_where(phev_items, type="record", kind="snapshot"),
            "delivery": first_where(phev_items, type="record", snapshot_tag="delivery"),
        }
        for desc, item in samples.items():
            if item:
                d.get(f"/api/records/{item['id']}", desc, template="/api/records/{rid}")
        sparse_charge = first_where(items_of(sparse), type="record", kind="charge")
        if sparse_charge:
            d.get(f"/api/records/{sparse_charge['id']}", "sparse_charge", template="/api/records/{rid}")
        expense = first_where(phev_items, type="expense")
        if expense:
            d.get(f"/api/expenses/{expense['id']}", "phev", template="/api/expenses/{eid}")
        insurance = next((i for i in phev_items if i.get("type") == "expense" and i.get("sub_items")), None)
        if insurance:
            d.get(f"/api/expenses/{insurance['id']}", "with_sub_items", template="/api/expenses/{eid}")

        for tag in ("fuel", "charge", "snapshot"):
            d.get("/api/records/prefill", f"phev_{tag}", {"vehicle_id": phev, "kind": tag})
        d.get("/api/records/prefill", "bev_charge", {"vehicle_id": bev, "kind": "charge"})
        d.get("/api/records/prefill", "sparse_charge", {"vehicle_id": sparse, "kind": "charge"})
        d.get("/api/trash", "phev", {"vehicle_id": phev})
        d.get("/api/summaries", "phev", {"vehicle_id": phev})
        d.get("/api/summaries", "bev", {"vehicle_id": bev})

        # 提醒与地点
        d.get("/api/reminders", "all", {})
        d.get("/api/reminders", "phev", {"vehicle_id": phev})
        d.get("/api/reminders", "sparse", {"vehicle_id": sparse})
        d.get("/api/reminders/due", "all")
        d.get("/api/places", "all", {})
        d.get("/api/places", "phev", {"vehicle_id": phev})
        d.get("/api/places", "sparse", {"vehicle_id": sparse})

        # 管理与导出
        d.get("/api/admin/users", "all")
        d.get("/api/admin/system", "default")
        for fmt in ("json", "csv", "expenses_csv"):
            d.get("/api/export", f"phev_{fmt}", {"vehicle_id": phev, "format": fmt})

        # 进行中的充电：放在常规样例之后，免得污染前面的概览
        started = d.send("POST", "/api/records", {
            "vehicle_id": phev, "kind": "charge", "status": "in_progress", "client_id": "fixture-inprogress",
            "occurred_at": today.strftime("%Y-%m-%dT") + "08:30", "odometer_km": 99999, "soc_before_pct": 20,
            "charge_type": "home", "note": "[demo] 进行中"})
        assert started.status_code == 200, started.text
        d.get("/api/stats/overview", "phev_in_progress", {"vehicle_id": phev, "range": "month"})
        d.get(f"/api/records/{started.json()['record']['id']}", "in_progress", template="/api/records/{rid}")
        d.get("/api/records", "phev_in_progress", {"vehicle_id": phev, "limit": 5})

        # 保存响应样例（POST 也有契约）
        saved = d.send("POST", "/api/records", {
            "vehicle_id": phev, "kind": "fuel", "occurred_at": today.strftime("%Y-%m-%dT") + "09:00",
            "odometer_km": 100100, "fuel_liters": 20, "fuel_unit_price": 7.5, "amount_cents": 15000, "paid_cents": 15000,
            "fuel_is_full": True, "client_id": "fixture-save-1", "note": "[demo] 样例"})
        save("records_create", "fuel_ok", "POST", "/api/records", None, saved)
        replay = d.send("POST", "/api/records", {
            "vehicle_id": phev, "kind": "fuel", "occurred_at": today.strftime("%Y-%m-%dT") + "09:00",
            "odometer_km": 100100, "fuel_liters": 20, "fuel_unit_price": 7.5, "amount_cents": 15000, "paid_cents": 15000,
            "fuel_is_full": True, "client_id": "fixture-save-1", "note": "[demo] 样例"})
        save("records_create", "fuel_replayed", "POST", "/api/records", None, replay)

        # 典型错误响应
        errors = {}
        errors["401"] = c.get("/api/auth/me")
        errors["401_bad_bearer"] = c.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
        stranger = f"stranger{uuid.uuid4().hex[:4]}"
        assert d.send("POST", "/api/admin/users", {"username": stranger, "password": "secret123"}).status_code == 200
        s_token = c.post("/api/auth/login", json={"username": stranger, "password": "secret123", "client": "ios"}).json()["token"]
        s_head = {"Authorization": f"Bearer {s_token}"}
        errors["404_no_access"] = c.get(f"/api/vehicles/{phev}", headers=s_head)
        errors["403_not_admin"] = c.get("/api/admin/users", headers=s_head)
        errors["400_bev_fuel"] = d.send("POST", "/api/records", {
            "vehicle_id": bev, "kind": "fuel", "occurred_at": today.strftime("%Y-%m-%dT") + "10:00",
            "odometer_km": 1, "fuel_liters": 10})
        dup_body = {"vehicle_id": phev, "kind": "fuel", "occurred_at": today.strftime("%Y-%m-%dT") + "09:05",
                    "odometer_km": 100100, "fuel_liters": 20, "fuel_unit_price": 7.5, "amount_cents": 15000,
                    "paid_cents": 15000, "fuel_is_full": True}
        errors["409_duplicate"] = d.send("POST", "/api/records", dup_body)
        rec = saved.json()["record"]
        upd = {"vehicle_id": phev, "kind": "fuel", "occurred_at": rec["occurred_at"], "odometer_km": 100101,
               "fuel_liters": 20, "version": rec["version"]}
        assert d.send("PUT", f"/api/records/{rec['id']}", upd).status_code == 200
        errors["409_conflict"] = d.send("PUT", f"/api/records/{rec['id']}", upd)
        errors["422_missing_param"] = c.get("/api/records", headers=d.h)
        errors["422_bad_value"] = d.send("POST", "/api/records", {
            "vehicle_id": phev, "kind": "fuel", "occurred_at": "not-a-date", "odometer_km": -5})
        for k, r in errors.items():
            save("error", k, r.request.method, str(r.request.url.path), None, r)

        # 登录限流放最后，并在结束前解锁
        for _ in range(auth_mod.LOGIN_MAX_FAILURES):
            c.post("/api/auth/login", json={"username": "admin", "password": "wrong-password", "client": "ios"})
        limited = c.post("/api/auth/login", json={"username": "admin", "password": "wrong-password", "client": "ios"})
        save("error", "429_login_locked", "POST", "/api/auth/login", None, limited)
        auth_mod.clear_login_failures("testclient")

        # logout-all 会让 admin 令牌作废，放在所有请求之后
        d.get("/api/health", "after", headers={})
        save("auth_logout_all", "ok", "POST", "/api/auth/logout-all", None, d.send("POST", "/api/auth/logout-all"))
        save("auth_logout_all", "token_revoked", "GET", "/api/auth/me", None, c.get("/api/auth/me", headers=d.h))

        (out_dir / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")

    (out_dir / "manifest.json").write_text(json.dumps({
        "api_version": API_VERSION,
        "generated_on": date.today().isoformat(),
        "note": "演示数据按生成当天的日期铺开，解码测试不要依赖具体数值；token 已替换成占位符。",
        "files": sorted(manifest, key=lambda m: m["file"]),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(_TMP, ignore_errors=True)
    print(f"已导出 {len(manifest)} 个样例到 {out_dir}")


if __name__ == "__main__":
    main()

"""接口测试：记录、共享权限、向导、提醒、统计在空数据和不完整数据下都不报错。"""
import io
import json

from tests.conftest import PHEV


def rec(c, **kw):
    body = {"vehicle_id": c.vid, "kind": "fuel", "occurred_at": "2026-09-01T08:00", **kw}
    return c.post("/api/records", json=body)


def seed_a(c):
    """方案文档算例 A。"""
    assert rec(c, odometer_km=10000, fuel_liters=30, amount_cents=22500, fuel_is_full=True, soc_before_pct=30).status_code == 200
    assert rec(c, kind="charge", occurred_at="2026-09-03T20:00", odometer_km=10150, soc_before_pct=12,
               soc_after_pct=100, charge_kwh=17.6, amount_cents=528, charge_type="home").status_code == 200
    assert rec(c, kind="charge", occurred_at="2026-09-07T20:00", odometer_km=10330, soc_before_pct=8,
               soc_after_pct=100, charge_kwh=18.4, amount_cents=552, charge_type="home").status_code == 200
    return rec(c, occurred_at="2026-09-12T08:00", odometer_km=10820, fuel_liters=22, amount_cents=16720,
               fuel_is_full=True, soc_before_pct=20)


def test_record_flow_and_segment_preview(vehicle):
    r = seed_a(vehicle)
    assert r.status_code == 200
    segs = {s["energy"]: s for s in r.json()["segments"]}
    assert abs(segs["combined"]["per100"] - 2.68) < 0.01
    assert abs(segs["combined"]["elec_per100"] - 4.63) < 0.01
    rid = r.json()["record"]["id"]
    d = vehicle.get(f"/api/records/{rid}").json()
    combined = next(s for s in d["segments"] if s["energy"] == "combined")
    assert combined["explain"]["parts"][0]["start"] == "加满"
    lst = vehicle.get(f"/api/records?vehicle_id={vehicle.vid}").json()
    assert len(lst["items"]) == 4
    assert lst["items"][0]["created_by"]["username"] == vehicle.username


def test_mark_combined_also_marks_fuel(vehicle):
    rid = seed_a(vehicle).json()["record"]["id"]
    body = {"vehicle_id": vehicle.vid, "end_record_id": rid, "energy": "combined", "tag": "winter", "excluded": True}
    assert vehicle.patch("/api/segments", json=body).status_code == 200
    segs = {s["energy"]: s for s in vehicle.get(f"/api/records/{rid}").json()["segments"]}
    assert segs["combined"]["tag"] == segs["fuel"]["tag"] == "winter"
    assert segs["combined"]["excluded"] and segs["fuel"]["excluded"]
    assert vehicle.patch("/api/segments", json={**body, "tag": "normal", "excluded": False}).status_code == 200
    segs = {s["energy"]: s for s in vehicle.get(f"/api/records/{rid}").json()["segments"]}
    assert segs["combined"]["tag"] == segs["fuel"]["tag"] == "normal"


def test_bev_cannot_fuel(make_user):
    c = make_user()
    c.vid = c.post("/api/vehicles", json={"name": "Y", "energy_type": "bev"}).json()["id"]
    assert rec(c, odometer_km=10).status_code == 400


def test_duplicate_and_force(vehicle):
    body = dict(odometer_km=500, fuel_liters=20, amount_cents=15000, fuel_is_full=True)
    assert rec(vehicle, **body).status_code == 200
    r = rec(vehicle, occurred_at="2026-09-01T08:05", **body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate"
    assert rec(vehicle, occurred_at="2026-09-01T08:05", force=True, **body).status_code == 200


def test_client_id_idempotent(vehicle):
    a = rec(vehicle, odometer_km=100, fuel_liters=10, amount_cents=7500, client_id="abc-1")
    b = rec(vehicle, odometer_km=100, fuel_liters=10, amount_cents=7500, client_id="abc-1")
    assert a.json()["record"]["id"] == b.json()["record"]["id"] and b.json()["replayed"]


def test_version_conflict(vehicle):
    r = rec(vehicle, odometer_km=100, fuel_liters=10, amount_cents=7500).json()["record"]
    body = {"vehicle_id": vehicle.vid, "kind": "fuel", "occurred_at": "2026-09-01T08:00", "odometer_km": 101,
            "version": r["version"]}
    assert vehicle.put(f"/api/records/{r['id']}", json=body).status_code == 200
    assert vehicle.put(f"/api/records/{r['id']}", json=body).json()["detail"]["code"] == "conflict"


def test_odometer_warning(vehicle):
    rec(vehicle, odometer_km=1000, fuel_liters=10, amount_cents=7500)
    r = rec(vehicle, occurred_at="2026-09-02T08:00", odometer_km=900, fuel_liters=10, amount_cents=7600)
    assert any("里程小于" in w for w in r.json()["warnings"])


def test_sharing_permissions(vehicle, make_user):
    stranger = make_user("x")
    assert stranger.get(f"/api/vehicles/{vehicle.vid}").status_code == 404
    stranger.vid = vehicle.vid
    assert rec(stranger, odometer_km=1).status_code == 404

    viewer = make_user("v")
    assert vehicle.post(f"/api/vehicles/{vehicle.vid}/members",
                        json={"username": viewer.username, "role": "viewer"}).status_code == 200
    viewer.vid = vehicle.vid
    assert viewer.get(f"/api/stats/overview?vehicle_id={vehicle.vid}").status_code == 200
    assert rec(viewer, odometer_km=1).status_code == 403

    inv = vehicle.post(f"/api/vehicles/{vehicle.vid}/invites", json={"role": "editor"}).json()
    editor = make_user("e")
    assert editor.post(f"/api/invites/{inv['code']}/accept").status_code == 200
    editor.vid = vehicle.vid
    mine = rec(editor, odometer_km=50, fuel_liters=5, amount_cents=3800).json()["record"]["id"]
    theirs = rec(vehicle, occurred_at="2026-09-02T08:00", odometer_km=60, fuel_liters=5, amount_cents=3900).json()["record"]["id"]
    assert editor.delete(f"/api/records/{theirs}").status_code == 403
    assert editor.delete(f"/api/records/{mine}").status_code == 200
    # 邀请码只能用一次
    assert make_user("y").post(f"/api/invites/{inv['code']}/accept").status_code == 404
    # 最后一位车主不能退出或降级
    assert vehicle.post(f"/api/vehicles/{vehicle.vid}/leave").status_code == 400
    assert vehicle.put(f"/api/vehicles/{vehicle.vid}/members/{vehicle.uid}", json={"role": "editor"}).status_code == 400
    assert editor.post(f"/api/vehicles/{vehicle.vid}/members", json={"username": "admin"}).status_code == 403
    acts = vehicle.get(f"/api/vehicles/{vehicle.vid}/activity").json()
    assert any("通过邀请码加入" in a["summary"] for a in acts)


def test_long_term_invite(vehicle, make_user):
    inv = vehicle.post(f"/api/vehicles/{vehicle.vid}/invites",
                       json={"role": "viewer", "days": None, "max_uses": None}).json()
    assert inv["expires_at"] is None and inv["max_uses"] is None and inv["last_used_at"] is None
    for n in ("l1", "l2", "l3"):
        assert make_user(n).post(f"/api/invites/{inv['code']}/accept").status_code == 200
    listed = vehicle.get(f"/api/vehicles/{vehicle.vid}/invites").json()
    row = next(i for i in listed if i["id"] == inv["id"])
    assert row["used_count"] == 3 and row["last_used_at"]
    # 作废后不能再用，已加入的成员保留
    assert vehicle.delete(f"/api/invites/{inv['id']}").status_code == 200
    assert make_user("l4").post(f"/api/invites/{inv['code']}/accept").status_code == 404
    assert all(i["id"] != inv["id"] for i in vehicle.get(f"/api/vehicles/{vehicle.vid}/invites").json())
    assert len(vehicle.get(f"/api/vehicles/{vehicle.vid}/members").json()) == 4
    # 有期限的照旧
    timed = vehicle.post(f"/api/vehicles/{vehicle.vid}/invites", json={"days": 30, "max_uses": 5}).json()
    assert timed["expires_at"] and timed["max_uses"] == 5


def test_migrate_v3_rebuilds_invites():
    from sqlalchemy import create_engine, text

    from app.database import _migrate_v3

    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE invites (id INTEGER PRIMARY KEY, code VARCHAR(16) NOT NULL UNIQUE, vehicle_id INTEGER, "
                       "role VARCHAR(8), created_by INTEGER, expires_at DATETIME NOT NULL, max_uses INTEGER NOT NULL, "
                       "used_count INTEGER, allow_signup BOOLEAN, revoked_at DATETIME, created_at DATETIME)"))
        c.execute(text("INSERT INTO invites VALUES (1, 'ABCD2345', 1, 'editor', 1, '2026-11-01 00:00:00', 1, 0, 0, NULL, "
                       "'2026-10-01 00:00:00')"))
        _migrate_v3(c)
        _migrate_v3(c)
        cols = {r[1]: r[3] for r in c.execute(text("PRAGMA table_info(invites)"))}
        c.execute(text("INSERT INTO invites (code, vehicle_id, role, created_by, used_count, allow_signup, created_at) "
                       "VALUES ('LONG2345', 1, 'viewer', 1, 0, 0, '2026-10-27 00:00:00')"))
        rows = c.execute(text("SELECT code, expires_at, max_uses FROM invites ORDER BY id")).all()
    assert cols["expires_at"] == 0 and cols["max_uses"] == 0 and "last_used_at" in cols
    assert rows[0][0] == "ABCD2345" and rows[0][2] == 1 and rows[1][1:] == (None, None)


def test_wizard_existing_vehicle_example_c(make_user):
    c = make_user()
    body = {**PHEV, "delivery_date": "2026-04-20",
            "start": {"mode": "existing", "delivery_odo": 12, "delivery_fill_l": 30, "delivery_fill_cents": 22500,
                      "current_odo": 9800, "current_fuel_pct": 40, "current_soc": 60},
            "summary": {"fuel_cents": 30000, "fuel_price": 7.6, "elec_kwh": 1250, "elec_cents": 45500},
            "reminders": [{"template_key": "insurance", "due_date": "2027-04-19", "interval_months": 12},
                          {"template_key": "maintenance"},
                          {"template_key": "tires", "due_odometer_km": 40000, "interval_km": 45000}]}
    r = c.post("/api/vehicles", json=body)
    assert r.status_code == 200, r.text
    c.vid = r.json()["id"]
    ov = c.get(f"/api/stats/overview?vehicle_id={c.vid}").json()
    assert ov["latest"]["fuel"]["quality"] == "C"
    assert abs(ov["history"]["fuel"]["per100"] - 0.56) < 0.02
    assert ov["latest"]["elec"]["quality"] == "C"
    rems = c.get(f"/api/reminders?vehicle_id={c.vid}").json()
    # 没填到期日期和里程的不建；填了的完全按用户给的值，不套模板默认周期
    assert not any(x["template_key"] == "maintenance" for x in rems)
    tires = next(x for x in rems if x["template_key"] == "tires")
    assert tires["due_odometer_km"] == 40000 and tires["interval_km"] == 45000 and tires["interval_months"] is None
    h = c.get(f"/api/vehicles/{c.vid}/health").json()
    assert any(i["key"] == "no_full" for i in h["issues"])


def test_stats_on_empty_vehicle(vehicle):
    vid = vehicle.vid
    for url in (f"/api/stats/overview?vehicle_id={vid}", f"/api/stats/energy?vehicle_id={vid}",
                f"/api/stats/cost?vehicle_id={vid}", f"/api/stats/mileage?vehicle_id={vid}",
                f"/api/stats/calendar?vehicle_id={vid}&month=2026-10", f"/api/stats/yearly-report?vehicle_id={vid}",
                f"/api/vehicles/{vid}/health", f"/api/records/prefill?vehicle_id={vid}&kind=charge",
                "/api/stats/cost", "/api/reminders/due"):
        r = vehicle.get(url)
        assert r.status_code == 200, (url, r.text)
        assert "NaN" not in r.text


def test_stats_with_data(vehicle):
    seed_a(vehicle)
    vid = vehicle.vid
    e = vehicle.get(f"/api/stats/energy?vehicle_id={vid}").json()
    sep = next(m for m in e["months"] if m["month"] == "2026-09")
    assert abs(sep["fuel"]["drive"]["per100"] - 2.68) < 0.01
    assert e["charging"]["k"] == 0.2
    cost = vehicle.get(f"/api/stats/cost?vehicle_id={vid}").json()
    assert cost["total_cents"] == 22500 + 528 + 552 + 16720
    cal = vehicle.get(f"/api/stats/calendar?vehicle_id={vid}&month=2026-09").json()
    assert "2026-09-03" in cal["days"]
    assert cal["report"]["sentence"]


def test_segment_mark_and_missed_fix(make_user):
    c = make_user()
    c.vid = c.post("/api/vehicles", json={"name": "Y", "energy_type": "bev", "battery_kwh": 60,
                                          "battery_usable_kwh": 60}).json()["id"]
    rec(c, kind="snapshot", odometer_km=100, soc_before_pct=50)
    last = rec(c, kind="snapshot", occurred_at="2026-09-03T08:00", odometer_km=300, soc_before_pct=80).json()
    h = c.get(f"/api/vehicles/{c.vid}/health").json()
    fix = next(i for i in h["issues"] if i["action"] == "fix_missed")
    r = c.post(f"/api/vehicles/{c.vid}/fix/missed", json={"after_id": fix["after_id"], "before_id": fix["before_id"]})
    assert r.status_code == 200 and r.json()["qty"] > 0
    rid = last["record"]["id"]
    assert c.patch("/api/segments", json={"vehicle_id": c.vid, "end_record_id": rid, "energy": "elec",
                                          "tag": "idle"}).status_code == 200


def test_insurance_creates_reminder_and_complete(vehicle):
    r = vehicle.post("/api/expenses", json={"vehicle_id": vehicle.vid, "category": "insurance",
                                            "occurred_at": "2026-05-01T10:00", "amount_cents": 350000,
                                            "period_start": "2026-05-02", "period_end": "2027-05-01"})
    assert r.json()["insurance_reminder"]["due_date"] == "2027-05-01"
    rid = vehicle.post("/api/reminders", json={"vehicle_id": vehicle.vid, "title": "保养", "template_key": "maintenance",
                                               "due_odometer_km": 10000, "interval_km": 10000,
                                               "interval_months": 12}).json()["id"]
    done = vehicle.post(f"/api/reminders/{rid}/complete", json={"done_at": "2026-10-01", "odometer_km": 9800,
                                                               "expense_cents": 50000}).json()
    assert done["due_odometer_km"] == 19800 and done["due_date"] == "2027-10-01"
    sug = vehicle.post("/api/expenses", json={"vehicle_id": vehicle.vid, "category": "maintenance",
                                              "occurred_at": "2026-10-02T10:00", "amount_cents": 100}).json()
    assert sug["suggest_complete"][0]["id"] == rid


def test_summary_and_export_import(vehicle, make_user):
    seed_a(vehicle)
    s = vehicle.post("/api/summaries", json={"vehicle_id": vehicle.vid, "start_date": "2026-09-01",
                                             "end_date": "2026-09-30", "mode": "total", "elec_kwh": 50})
    assert s.json()["diff"]["elec_kwh"] == 14.0
    data = vehicle.get(f"/api/export?vehicle_id={vehicle.vid}&format=json").content
    assert vehicle.get(f"/api/export?vehicle_id={vehicle.vid}&format=csv").status_code == 200
    other = make_user()
    other.vid = other.post("/api/vehicles", json=PHEV).json()["id"]
    r = other.post(f"/api/import?vehicle_id={other.vid}", files={"file": ("a.json", io.BytesIO(data))})
    assert r.json()["records"] == 4
    assert other.delete(f"/api/import/{r.json()['batch']}?vehicle_id={other.vid}").json()["records"] == 4


def test_in_progress_charge_not_counted(vehicle):
    seed_a(vehicle)
    r = rec(vehicle, kind="charge", status="in_progress", occurred_at="2026-09-13T20:00", odometer_km=10900,
            soc_before_pct=30, charge_type="home")
    assert r.json()["segments"] == []
    ov = vehicle.get(f"/api/stats/overview?vehicle_id={vehicle.vid}").json()
    assert len(ov["in_progress"]) == 1


def test_signup_requires_setting(admin, vehicle):
    inv = vehicle.post(f"/api/vehicles/{vehicle.vid}/invites", json={"role": "viewer", "allow_signup": True}).json()
    from fastapi.testclient import TestClient
    from app.main import app

    anon = TestClient(app)
    body = {"code": inv["code"], "username": "newbie1", "password": "secret123"}
    assert anon.post("/api/auth/signup", json=body).status_code == 403
    admin.put("/api/admin/settings", json={"allow_signup": True})
    assert anon.get(f"/api/invites/{inv['code']}").json()["allow_signup"] is True
    assert anon.post("/api/auth/signup", json=body).status_code == 200
    admin.put("/api/admin/settings", json={"allow_signup": False})


def test_fmt_num_keeps_integer_zeros():
    from app.services import fmt_num

    assert fmt_num(30.0, 0) == "30"
    assert fmt_num(100, 0) == "100"
    assert fmt_num(22.50) == "22.5"


def test_login_reports_cookie_secure_flag(_app):
    from fastapi.testclient import TestClient
    from app.main import app

    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.json()["cookie_secure"] is False


def _wizard(c, start, summary=None, **v):
    body = {**PHEV, **v, "start": start, "summary": summary}
    return c.post("/api/vehicles", json=body)


def test_wizard_midlife_without_delivery_info(make_user):
    """中途接入、不记得提车信息：只建记账起点，不猜提车里程，各页面正常。"""
    c = make_user()
    r = _wizard(c, {"mode": "existing", "current_odo": 23000, "current_fuel_pct": 50, "current_soc": 80})
    assert r.status_code == 200, r.text
    c.vid = r.json()["id"]
    assert r.json()["initial_odometer_km"] is None
    recs = c.get(f"/api/records?vehicle_id={c.vid}").json()["items"]
    assert [x["snapshot_tag"] for x in recs] == ["ledger_start"]
    ov = c.get(f"/api/stats/overview?vehicle_id={c.vid}").json()
    assert ov["latest"]["fuel"]["quality"] == "D"
    # 补录记账起点之前的加油（带里程），不应把记录搅乱
    assert rec(c, occurred_at="2026-03-01T10:00", odometer_km=20000, fuel_liters=40, amount_cents=30000,
               fuel_is_full=True).status_code == 200
    h = c.get(f"/api/vehicles/{c.vid}/health").json()
    assert not any(i["key"] in ("delivery_odo", "odo_order") for i in h["issues"])
    assert c.get(f"/api/stats/mileage?vehicle_id={c.vid}").status_code == 200


def test_wizard_delivery_needs_both_date_and_odo(make_user):
    c = make_user()
    r = _wizard(c, {"mode": "existing", "current_odo": 9800}, delivery_date="2026-04-20")
    c.vid = r.json()["id"]
    tags = [x["snapshot_tag"] for x in c.get(f"/api/records?vehicle_id={c.vid}").json()["items"]]
    assert "delivery" not in tags
    bad = _wizard(c, {"mode": "existing", "current_odo": 9800, "delivery_fill_l": 30})
    assert bad.status_code == 400
    no_start = _wizard(c, {"mode": "existing", "current_odo": 9800}, summary={"elec_kwh": 500})
    assert no_start.status_code == 400
    ok = _wizard(c, {"mode": "existing", "current_odo": 9800}, summary={"elec_kwh": 500, "start_date": "2026-05-01"})
    assert ok.status_code == 200
    ov = c.get(f"/api/stats/overview?vehicle_id={ok.json()['id']}").json()
    assert ov["history"] is None or ov["history"]["scope"] == "ledger"


def test_fix_delivery_odometer(make_user):
    """旧版向导把提车里程记成当前里程：体检提示，修正后快照和提车加油一起改。"""
    c = make_user()
    r = _wizard(c, {"mode": "existing", "delivery_odo": 9800, "delivery_fill_l": 30, "delivery_fill_cents": 22500,
                    "current_odo": 9800, "current_fuel_pct": 40}, delivery_date="2026-04-20")
    c.vid = r.json()["id"]
    issue = next(i for i in c.get(f"/api/vehicles/{c.vid}/health").json()["issues"] if i["key"] == "delivery_odo")
    assert issue["action"] == "fix_delivery"
    assert c.post(f"/api/vehicles/{c.vid}/delivery", json={"odometer_km": 12}).json()["updated"] == 2
    items = c.get(f"/api/records?vehicle_id={c.vid}").json()["items"]
    assert sorted(x["odometer_km"] for x in items) == [12, 12, 9800]
    assert not any(i["key"] == "delivery_odo" for i in c.get(f"/api/vehicles/{c.vid}/health").json()["issues"])


def test_health_flags_odometer_order_conflict(vehicle):
    rec(vehicle, odometer_km=5000, fuel_liters=10, amount_cents=7500)
    rec(vehicle, occurred_at="2026-09-05T08:00", odometer_km=4000, fuel_liters=10, amount_cents=7600)
    h = vehicle.get(f"/api/vehicles/{vehicle.vid}/health").json()
    assert any(i["key"] == "odo_order" for i in h["issues"])


def test_edit_delivery_snapshot_keeps_estimated_values(make_user):
    """编辑向导建的提车快照（油量是估算值）能保存，估算标记保留。"""
    c = make_user()
    r = _wizard(c, {"mode": "existing", "delivery_odo": 12, "current_odo": 9800}, delivery_date="2026-04-20")
    c.vid = r.json()["id"]
    snap = next(x for x in c.get(f"/api/records?vehicle_id={c.vid}").json()["items"] if x["snapshot_tag"] == "delivery")
    body = {"vehicle_id": c.vid, "kind": "snapshot", "occurred_at": snap["occurred_at"], "odometer_km": 15,
            "fuel_level_before_pct": snap["fuel_level_before_pct"], "snapshot_tag": "delivery",
            "estimated_fields": snap["estimated_fields"], "source": snap["source"], "version": snap["version"]}
    saved = c.put(f"/api/records/{snap['id']}", json=body).json()["record"]
    assert saved["odometer_km"] == 15 and saved["estimated_fields"] == ["fuel_before_pct"]


def test_add_delivery_later_for_midlife_vehicle(make_user):
    """中途接入时没填提车信息，之后在车辆参数里补录。"""
    c = make_user()
    c.vid = _wizard(c, {"mode": "existing", "current_odo": 23000}).json()["id"]
    assert c.post(f"/api/vehicles/{c.vid}/delivery", json={"odometer_km": 30000, "delivery_date": "2025-01-01"}).status_code == 400
    r = c.post(f"/api/vehicles/{c.vid}/delivery", json={"odometer_km": 20, "delivery_date": "2025-01-01"}).json()
    assert r["created"] is True
    v = c.get(f"/api/vehicles/{c.vid}").json()
    assert v["initial_odometer_km"] == 20 and v["delivery_date"] == "2025-01-01"
    ov = c.get(f"/api/stats/overview?vehicle_id={c.vid}").json()
    assert ov["history"]["scope"] == "delivery"


def test_new_car_mode_creates_delivery_anchor(make_user):
    c = make_user()
    r = _wizard(c, {"mode": "new", "current_odo": 15, "current_soc": 90})
    c.vid = r.json()["id"]
    items = c.get(f"/api/records?vehicle_id={c.vid}").json()["items"]
    assert [x["snapshot_tag"] for x in items] == ["delivery"]
    v = c.get(f"/api/vehicles/{c.vid}").json()
    assert v["initial_odometer_km"] == 15 and v["delivery_date"]


def test_complete_km_reminder_without_odometer_uses_latest(vehicle):
    rec(vehicle, odometer_km=12345, fuel_liters=10, amount_cents=7500)
    rid = vehicle.post("/api/reminders", json={"vehicle_id": vehicle.vid, "title": "保养", "due_odometer_km": 12000,
                                               "interval_km": 8000}).json()["id"]
    done = vehicle.post(f"/api/reminders/{rid}/complete", json={"done_at": "2026-10-01"}).json()
    assert done["due_odometer_km"] == 12345 + 8000


def test_deleting_delivery_snapshot_clears_initial_odometer(make_user):
    c = make_user()
    c.vid = _wizard(c, {"mode": "existing", "delivery_odo": 12, "current_odo": 9800}, delivery_date="2026-04-20").json()["id"]
    snap = next(x for x in c.get(f"/api/records?vehicle_id={c.vid}").json()["items"] if x["snapshot_tag"] == "delivery")
    c.delete(f"/api/records/{snap['id']}")
    assert c.get(f"/api/vehicles/{c.vid}").json()["initial_odometer_km"] is None
    c.post(f"/api/records/{snap['id']}/restore")
    assert c.get(f"/api/vehicles/{c.vid}").json()["initial_odometer_km"] == 12


def test_new_place_links_same_name_records_and_rename_syncs(vehicle):
    c = vehicle
    ids = []
    for i, name in enumerate(["云快充", "云快充", "别处"]):
        r = c.post("/api/records", json={"vehicle_id": c.vid, "kind": "charge", "occurred_at": f"2026-09-0{i + 1}T08:00",
                                         "odometer_km": 100 + i * 50, "charge_kwh": 10, "charge_type": "dc", "place_name": name})
        ids.append(r.json()["record"]["id"])
    c.delete(f"/api/records/{ids[1]}")
    p = c.post("/api/places", json={"vehicle_id": c.vid, "shared": True, "name": "云快充", "kind": "charge_station"}).json()
    assert p["use_count"] == 1
    recs = {x["id"]: x for x in c.get(f"/api/records?vehicle_id={c.vid}").json()["items"] if x["type"] == "record"}
    assert recs[ids[0]]["place_id"] == p["id"] and recs[ids[2]]["place_id"] is None
    body = {"vehicle_id": c.vid, "shared": True, "name": "云快充（周村）", "kind": "charge_station"}
    assert c.put(f"/api/places/{p['id']}", json=body).status_code == 200
    rec = c.get(f"/api/records/{ids[0]}").json()
    assert rec["place_name"] == "云快充（周村）" and rec["place_id"] == p["id"]
    c.post(f"/api/records/{ids[1]}/restore")
    assert c.get(f"/api/records/{ids[1]}").json()["place_name"] == "云快充（周村）"


def test_archived_vehicle_hides_delete(vehicle):
    rid = rec(vehicle, odometer_km=100, fuel_liters=10, amount_cents=7500).json()["record"]["id"]
    eid = vehicle.post("/api/expenses", json={"vehicle_id": vehicle.vid, "category": "parking",
                                              "occurred_at": "2026-09-02T10:00", "amount_cents": 500}).json()["expense"]["id"]
    assert vehicle.get(f"/api/records/{rid}").json()["can_delete"] is True
    vehicle.post(f"/api/vehicles/{vehicle.vid}/archive", json={"archived": True})
    assert vehicle.get(f"/api/records/{rid}").json()["can_delete"] is False
    assert vehicle.get(f"/api/expenses/{eid}").json()["can_delete"] is False


def test_super_charge_type_saved_as_dc(vehicle):
    r = rec(vehicle, kind="charge", odometer_km=100, charge_kwh=10, charge_type="super").json()["record"]
    assert vehicle.get(f"/api/records/{r['id']}").json()["charge_type"] == "dc"
    p = vehicle.post("/api/places", json={"vehicle_id": vehicle.vid, "name": "超充站", "kind": "charge_station",
                                          "default_charge_type": "super"}).json()
    assert p["default_charge_type"] == "dc"
    body = {**PHEV, "default_charge_type": "super"}
    assert vehicle.put(f"/api/vehicles/{vehicle.vid}", json=body).status_code == 200
    assert vehicle.get(f"/api/vehicles/{vehicle.vid}").json()["default_charge_type"] == "dc"


def test_import_old_json_uses_paid_and_merges_super(vehicle):
    data = {"app": "voltane", "records": [
        {"kind": "charge", "occurred_at": "2026-09-01T08:00", "odometer_km": 100, "charge_kwh": 20,
         "amount_cents": 3000, "paid_cents": 2400, "charge_type": "super"}]}
    r = vehicle.post(f"/api/import?vehicle_id={vehicle.vid}",
                     files={"file": ("a.json", io.BytesIO(json.dumps(data).encode()))})
    assert r.json()["records"] == 1
    item = next(x for x in vehicle.get(f"/api/records?vehicle_id={vehicle.vid}").json()["items"] if x["type"] == "record")
    d = vehicle.get(f"/api/records/{item['id']}").json()
    assert d["amount_cents"] == 2400 and d["charge_type"] == "dc"


def test_idle_drain_shows_in_pending(make_user):
    c = make_user()
    c.vid = c.post("/api/vehicles", json={"name": "Y", "energy_type": "bev", "battery_usable_kwh": 60}).json()["id"]
    soc, odo = 95, 1000
    for i, drop in enumerate([2, 2, 2, 2, 8]):
        for day, s in ((i * 3 + 1, soc), (i * 3 + 2, soc - drop)):
            assert rec(c, kind="snapshot", occurred_at=f"2026-05-{day:02d}T08:00", odometer_km=odo,
                       soc_before_pct=s).status_code == 200
        soc -= drop + 10
        odo += 100
    ov = c.get(f"/api/stats/overview?vehicle_id={c.vid}").json()
    [item] = [x for x in ov["pending"] if x["type"] == "anomaly"]
    assert item["anomaly"] == "idle_high" and "停车耗电明显偏高" in item["text"] and "kWh" in item["text"]


def test_restore_runs_migrations(admin, tmp_path):
    import sqlite3

    from app.database import MIGRATIONS
    from app.routers.admin import DB_PATH

    old = tmp_path / "old.db"
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(old)
    src.backup(dst)
    src.close()
    dst.execute("PRAGMA user_version = 1")
    dst.commit()
    dst.close()
    r = admin.post("/api/admin/restore", files={"file": ("old.db", old.read_bytes())})
    assert r.status_code == 200, r.text
    con = sqlite3.connect(DB_PATH)
    assert con.execute("PRAGMA user_version").fetchone()[0] == MIGRATIONS[-1][0]
    con.close()

"""接口测试：记录、共享权限、向导、提醒、统计在空数据和不完整数据下都不报错。"""
import io
import json

from tests.conftest import PHEV


def rec(c, **kw):
    body = {"vehicle_id": c.vid, "kind": "fuel", "occurred_at": "2026-09-01T08:00", **kw}
    return c.post("/api/records", json=body)


def seed_a(c):
    """方案文档算例 A。"""
    assert rec(c, odometer_km=10000, fuel_liters=30, paid_cents=22500, fuel_is_full=True, soc_before_pct=30).status_code == 200
    assert rec(c, kind="charge", occurred_at="2026-09-03T20:00", odometer_km=10150, soc_before_pct=12,
               soc_after_pct=100, charge_kwh=17.6, paid_cents=528, charge_type="home").status_code == 200
    assert rec(c, kind="charge", occurred_at="2026-09-07T20:00", odometer_km=10330, soc_before_pct=8,
               soc_after_pct=100, charge_kwh=18.4, paid_cents=552, charge_type="home").status_code == 200
    return rec(c, occurred_at="2026-09-12T08:00", odometer_km=10820, fuel_liters=22, paid_cents=16720,
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


def test_bev_cannot_fuel(make_user):
    c = make_user()
    c.vid = c.post("/api/vehicles", json={"name": "Y", "energy_type": "bev"}).json()["id"]
    assert rec(c, odometer_km=10).status_code == 400


def test_duplicate_and_force(vehicle):
    body = dict(odometer_km=500, fuel_liters=20, paid_cents=15000, fuel_is_full=True)
    assert rec(vehicle, **body).status_code == 200
    r = rec(vehicle, occurred_at="2026-09-01T08:05", **body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate"
    assert rec(vehicle, occurred_at="2026-09-01T08:05", force=True, **body).status_code == 200


def test_client_id_idempotent(vehicle):
    a = rec(vehicle, odometer_km=100, fuel_liters=10, paid_cents=7500, client_id="abc-1")
    b = rec(vehicle, odometer_km=100, fuel_liters=10, paid_cents=7500, client_id="abc-1")
    assert a.json()["record"]["id"] == b.json()["record"]["id"] and b.json()["replayed"]


def test_version_conflict(vehicle):
    r = rec(vehicle, odometer_km=100, fuel_liters=10, paid_cents=7500).json()["record"]
    body = {"vehicle_id": vehicle.vid, "kind": "fuel", "occurred_at": "2026-09-01T08:00", "odometer_km": 101,
            "version": r["version"]}
    assert vehicle.put(f"/api/records/{r['id']}", json=body).status_code == 200
    assert vehicle.put(f"/api/records/{r['id']}", json=body).json()["detail"]["code"] == "conflict"


def test_odometer_warning(vehicle):
    rec(vehicle, odometer_km=1000, fuel_liters=10, paid_cents=7500)
    r = rec(vehicle, occurred_at="2026-09-02T08:00", odometer_km=900, fuel_liters=10, paid_cents=7600)
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
    mine = rec(editor, odometer_km=50, fuel_liters=5, paid_cents=3800).json()["record"]["id"]
    theirs = rec(vehicle, occurred_at="2026-09-02T08:00", odometer_km=60, fuel_liters=5, paid_cents=3900).json()["record"]["id"]
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


def test_wizard_existing_vehicle_example_c(make_user):
    c = make_user()
    body = {**PHEV, "delivery_date": "2026-04-20",
            "start": {"mode": "existing", "delivery_odo": 12, "delivery_fill_l": 30, "delivery_fill_cents": 22500,
                      "current_odo": 9800, "current_fuel_pct": 40, "current_soc": 60},
            "summary": {"fuel_cents": 30000, "fuel_price": 7.6, "elec_kwh": 1250, "elec_cents": 45500},
            "reminders": [{"template_key": "insurance", "due_date": "2027-04-19"}, {"template_key": "maintenance"}]}
    r = c.post("/api/vehicles", json=body)
    assert r.status_code == 200, r.text
    c.vid = r.json()["id"]
    ov = c.get(f"/api/stats/overview?vehicle_id={c.vid}").json()
    assert ov["latest"]["fuel"]["quality"] == "C"
    assert abs(ov["history"]["fuel"]["per100"] - 0.56) < 0.02
    assert ov["latest"]["elec"]["quality"] == "C"
    rems = c.get(f"/api/reminders?vehicle_id={c.vid}").json()
    maint = next(x for x in rems if x["template_key"] == "maintenance")
    assert maint["due_odometer_km"] == 19800
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
    assert rec(c, occurred_at="2026-03-01T10:00", odometer_km=20000, fuel_liters=40, paid_cents=30000,
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
    rec(vehicle, odometer_km=5000, fuel_liters=10, paid_cents=7500)
    rec(vehicle, occurred_at="2026-09-05T08:00", odometer_km=4000, fuel_liters=10, paid_cents=7600)
    h = vehicle.get(f"/api/vehicles/{vehicle.vid}/health").json()
    assert any(i["key"] == "odo_order" for i in h["issues"])

"""能耗引擎测试：方案文档 2.8 的算例 A、B、C 与边界情况。"""
from datetime import date, datetime

import pytest

from app.engine.aggregate import (
    combined_totals,
    energy_totals,
    latest_energy,
    mileage,
    odo_observations,
    period_report,
    rough_best,
    segment_dict,
)
from app.engine.ledger import build_ledger
from app.engine.reminders import ReminderIn, add_months, evaluate, next_due
from app.engine.types import Mark, Rec, Summary, VehicleParams

PHEV = dict(energy_type="phev", tank_l=48, battery_kwh=18.3)


def dt(s):
    return datetime.fromisoformat(s)


def fuel(id, ts, odo, liters, paid, full=True, soc=None, **kw):
    return Rec(id=id, kind="fuel", ts=dt(ts), odo=odo, fuel_l=liters, paid_cents=paid,
               fuel_full=full, soc_before=soc, **kw)


def charge(id, ts, odo, s0, s1, kwh, paid, ctype="home", **kw):
    return Rec(id=id, kind="charge", ts=dt(ts), odo=odo, soc_before=s0, soc_after=s1,
               charge_kwh=kwh, paid_cents=paid, charge_type=ctype, **kw)


def snap(id, ts, odo, soc=None, fuel_pct=None, tag=None, **kw):
    return Rec(id=id, kind="snapshot", ts=dt(ts), odo=odo, soc_before=soc,
               fuel_before_pct=fuel_pct, snapshot_tag=tag, **kw)


# ---------- 算例 A：插混 ----------


def example_a():
    return [
        fuel(1, "2026-09-01T08:00", 10000, 30.0, 22500, soc=30),
        charge(2, "2026-09-03T20:00", 10150, 12, 100, 17.6, 528),
        charge(3, "2026-09-07T20:00", 10330, 8, 100, 18.4, 552),
        fuel(4, "2026-09-12T08:00", 10820, 22.0, 16720, soc=20),
    ]


def test_example_a_combined():
    led = build_ledger(VehicleParams(**PHEV), example_a())
    assert led.calib.k == pytest.approx(0.2)
    assert led.calib.source == "learned"
    [c] = led.combined_segments
    assert c.distance == 820
    assert c.amount == pytest.approx(22.0)
    assert c.elec_kwh == pytest.approx(38.0)
    assert c.quality == "A"
    assert c.cost_cents == 17860
    d = segment_dict(c)
    assert d["per100"] == pytest.approx(2.68, abs=0.01)
    assert d["elec_per100"] == pytest.approx(4.63, abs=0.01)
    assert d["cost_per_km"] == pytest.approx(0.218, abs=0.001)
    assert d["equiv_per100"] == pytest.approx(3.20, abs=0.01)
    assert d["elec_share"] == pytest.approx(0.163, abs=0.001)


def test_example_a_elec_segments_sum_to_combined():
    led = build_ledger(VehicleParams(**PHEV), example_a())
    amounts = [s.amount for s in led.elec_segments]
    assert amounts == pytest.approx([3.6, 18.4, 16.0])
    assert sum(amounts) == pytest.approx(38.0)
    assert all(s.quality == "A" for s in led.elec_segments)
    [f] = led.fuel_segments
    assert f.quality == "A" and f.amount == pytest.approx(22.0)


# ---------- 算例 B：纯电 + 哨兵 ----------


def example_b():
    return [
        charge(1, "2026-10-01T20:00", 5000, 20, 90, 57.4, 2000),
        snap(2, "2026-10-05T09:00", 5210, soc=52),
        snap(3, "2026-10-08T09:00", 5210, soc=41),
        charge(4, "2026-10-10T20:00", 5400, 16, 96, 65.6, 2300),
    ]


def test_example_b_idle_split():
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=75), example_b())
    assert led.calib.k == pytest.approx(0.82)
    kinds = [(s.kind, round(s.amount, 2)) for s in led.elec_segments]
    assert kinds == [("drive", 31.16), ("idle", 9.02), ("drive", 20.5)]
    t = energy_totals(led.elec_segments)
    assert t["drive"]["per100"] == pytest.approx(12.92, abs=0.01)
    assert t["overall"]["per100"] == pytest.approx(15.17, abs=0.01)
    assert t["idle"]["per_day"] == pytest.approx(3.01, abs=0.01)
    assert t["idle"]["share"] == pytest.approx(0.149, abs=0.001)


def test_excluded_segment_counts_only_in_overall():
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=75), example_b(),
                       marks={(4, "elec"): Mark(tag="winter", excluded=True)})
    t = energy_totals(led.elec_segments)
    assert t["drive"]["distance"] == 210
    assert t["overall"]["distance"] == 400


# ---------- 算例 C：中途接入、历史不全 ----------


def example_c(with_f1=True):
    recs = [
        snap(1, "2026-04-20T10:00", 12, fuel_pct=5 / 48 * 100, tag="delivery"),
        fuel(2, "2026-04-20T10:01", 12, 30.0, 22500, full=False),
        snap(3, "2026-10-24T09:00", 9800, soc=60, fuel_pct=40, tag="ledger_start"),
        charge(4, "2026-10-26T20:00", 9880, 45, 100, 11.0, 330),
    ]
    if with_f1:
        recs.append(fuel(5, "2026-11-02T08:00", 10250, 32.0, 24160, soc=35))
    return recs


def summary_c():
    return [Summary(id=1, start=date(2026, 4, 21), end=date(2026, 10, 23), fuel_cents=30000,
                    fuel_price=7.6, elec_kwh=1250, elec_cents=45500)]


def test_example_c_history_rough():
    led = build_ledger(VehicleParams(**PHEV), example_c(with_f1=False), summaries=summary_c())
    r = rough_best(led, "fuel")
    assert r["scope"] == "delivery"
    # 5 + 30 + 39.47 − 19.2 = 55.27L / 9788km
    assert r["per100"] == pytest.approx(0.56, abs=0.01)
    assert r["low100"] < 0.52 and r["high100"] > 0.6
    e = rough_best(led, "elec")
    assert e["per100"] == pytest.approx(12.77, abs=0.02)
    # 跨越汇总期的油表段被标为不完整，不参与趋势
    assert all(s.status != "ok" for s in led.fuel_segments)


def test_example_c_after_start():
    led = build_ledger(VehicleParams(**PHEV), example_c(), summaries=summary_c())
    assert led.calib.k == pytest.approx(0.2)
    drive = [s for s in led.elec_segments if s.status == "ok"]
    assert [round(s.amount, 2) for s in drive] == [3.0, 13.0]
    assert all(s.quality == "A" for s in drive)
    gauge = [s for s in led.fuel_segments if s.start_id == 3]
    assert gauge and gauge[0].status == "uncertain"
    fuel_latest = latest_energy(led, "fuel")
    assert fuel_latest["quality"] == "C"
    assert fuel_latest["hint"] == "need_second_full"
    elec_latest = latest_energy(led, "elec")
    assert elec_latest["quality"] == "A"
    assert elec_latest["per100"] == pytest.approx(16 / 450 * 100, abs=0.01)


# ---------- 边界情况 ----------


def test_single_record_and_empty():
    p = VehicleParams(**PHEV)
    led = build_ledger(p, [fuel(1, "2026-01-01T08:00", 100, 40, 30000)])
    assert led.fuel_segments == [] and led.combined_segments == []
    assert latest_energy(led, "fuel") == {"quality": "D", "per100": None, "hint": "need_second_full"}
    empty = build_ledger(p, [])
    assert latest_energy(empty, "elec")["hint"] == "need_start"
    rep = period_report(empty, [], date(2026, 1, 1), date(2026, 1, 31))
    assert rep["mileage"] == 0 and rep["energy_cost_per_km"] is None


def test_missed_breaks_chain():
    recs = example_a()
    recs[3].missed_before = True
    led = build_ledger(VehicleParams(**PHEV), recs)
    assert led.fuel_segments[0].status == "missed"
    assert latest_energy(led, "fuel")["quality"] != "A"


def test_negative_consumption_flagged():
    recs = [
        snap(1, "2026-01-01T08:00", 100, soc=50),
        snap(2, "2026-01-02T08:00", 200, soc=70),
    ]
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=60), recs)
    assert led.elec_segments[0].status == "negative"
    assert led.hints and led.hints[0].kind == "missed_charge"
    assert energy_totals(led.elec_segments)["drive"] is None


def test_short_segments_merge():
    recs = [
        charge(1, "2026-01-01T20:00", 1000, 20, 100, 16, 480),
        snap(2, "2026-01-02T08:00", 1010, soc=95),
        snap(3, "2026-01-03T08:00", 1100, soc=70),
    ]
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=18), recs)
    [s] = led.elec_segments
    assert s.distance == 100 and s.amount == pytest.approx(30 * 0.2)


def test_charge_without_soc_uses_full_to_full():
    p = VehicleParams(energy_type="bev")  # 没有电池容量、没有 SOC
    recs = [
        Rec(id=1, kind="charge", ts=dt("2026-01-01T20:00"), odo=1000, charge_kwh=50, charge_full=True, paid_cents=1500),
        Rec(id=2, kind="charge", ts=dt("2026-01-05T20:00"), odo=1300, charge_kwh=40, charge_full=True, paid_cents=1200),
    ]
    led = build_ledger(p, recs)
    assert led.calib.k is None
    [s] = led.elec_segments
    assert s.amount == pytest.approx(40) and s.quality == "A"


def test_no_capacity_no_samples():
    led = build_ledger(VehicleParams(energy_type="bev"), [snap(1, "2026-01-01T08:00", 10, soc=50)])
    assert led.calib.k is None and led.elec_segments == []


def test_out_of_order_and_estimated_odometer():
    recs = example_a()
    recs.append(Rec(id=9, kind="charge", ts=dt("2026-09-05T12:00"), odo=None, odo_estimated=True,
                    charge_kwh=5, paid_cents=500, charge_type="public_ac"))
    led = build_ledger(VehicleParams(**PHEV), list(reversed(recs)))
    ids = [p.id for p in led.points]
    assert ids.index(9) > ids.index(2) and ids.index(9) < ids.index(3)
    # 估算里程的记录不作为端点，补能量计入所在长段
    assert all(9 not in (s.start_id, s.end_id) for s in led.elec_segments)
    [c] = led.combined_segments
    assert c.elec_kwh == pytest.approx(43.0)


def test_summary_total_mode_subtracts_recorded():
    recs = example_a()
    s = Summary(id=1, start=date(2026, 9, 1), end=date(2026, 9, 30), elec_kwh=50, mode="total")
    led = build_ledger(VehicleParams(**PHEV), recs, summaries=[s])
    assert led.summary_diffs[1]["elec_kwh"] == pytest.approx(14.0)
    s2 = Summary(id=2, start=date(2026, 9, 1), end=date(2026, 9, 30), elec_kwh=10, mode="total")
    led2 = build_ledger(VehicleParams(**PHEV), recs, summaries=[s2])
    assert led2.summary_diffs[2]["elec_kwh"] == 0
    assert "elec_negative" in led2.summary_diffs[2]["warnings"]


def test_hev_fuel_only():
    led = build_ledger(VehicleParams(energy_type="hev", tank_l=43), example_a())
    assert led.elec_segments == [] and led.combined_segments == []
    assert led.fuel_segments[0].amount == pytest.approx(22.0)


def test_anomaly_detection():
    recs = []
    soc_drop = [20, 20, 20, 20, 45]
    odo = 0
    for i, d in enumerate(soc_drop):
        recs.append(snap(2 * i + 1, f"2026-02-{i * 2 + 1:02d}T08:00", odo, soc=90))
        odo += 100
        recs.append(snap(2 * i + 2, f"2026-02-{i * 2 + 1:02d}T18:00", odo, soc=90 - d))
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=60), recs)
    drives = [s for s in led.elec_segments if s.kind == "drive" and s.status == "ok"]
    assert drives[-1].anomaly == "high"


def test_mileage_and_combined_totals():
    led = build_ledger(VehicleParams(**PHEV), example_a())
    obs = odo_observations(led, [])
    assert mileage(obs, date(2026, 9, 1), date(2026, 9, 30)) == 820
    assert mileage(obs, date(2026, 9, 4), date(2026, 9, 30)) == 670
    t = combined_totals(led.combined_segments)
    assert t["cost_per_km"] == pytest.approx(0.218, abs=0.001)


# ---------- 提醒 ----------


def test_reminder_status_and_next_due():
    r = ReminderIn(due_date=date(2026, 11, 1), due_odo=15000, interval_months=12, interval_km=10000,
                   last_done_date=date(2025, 11, 1), last_done_odo=5000)
    ev = evaluate(r, date(2026, 10, 20), 14700, 30)
    assert ev["status"] == "soon" and ev["km_left"] == 300 and ev["eta_date"] == "2026-10-30"
    assert evaluate(r, date(2026, 10, 20), 15100, 30)["status"] == "overdue"
    assert next_due(r, date(2026, 10, 25), 14800) == (date(2027, 10, 25), 24800)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

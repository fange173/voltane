"""能耗引擎测试：方案文档 2.8 的算例 A、B、C 与边界情况。"""
from datetime import date, datetime

import pytest

from app.engine.aggregate import (
    Exp,
    combined_totals,
    energy_totals,
    latest_energy,
    latest_pair,
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
    return Rec(id=id, kind="fuel", ts=dt(ts), odo=odo, fuel_l=liters, cost_cents=paid,
               fuel_full=full, soc_before=soc, **kw)


def charge(id, ts, odo, s0, s1, kwh, paid, ctype="home", **kw):
    return Rec(id=id, kind="charge", ts=dt(ts), odo=odo, soc_before=s0, soc_after=s1,
               charge_kwh=kwh, cost_cents=paid, charge_type=ctype, **kw)


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
        Rec(id=1, kind="charge", ts=dt("2026-01-01T20:00"), odo=1000, charge_kwh=50, charge_full=True, cost_cents=1500),
        Rec(id=2, kind="charge", ts=dt("2026-01-05T20:00"), odo=1300, charge_kwh=40, charge_full=True, cost_cents=1200),
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
                    charge_kwh=5, cost_cents=500, charge_type="public_ac"))
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


def test_combined_from_gauge_readings_without_full():
    """不加满也能出综合能耗：两次加油都记了加油前油量，按油表读数算，质量 B。"""
    recs = [
        fuel(1, "2026-03-01T08:00", 1000, 30.0, 22500, full=False, soc=50, fuel_before_pct=20),
        charge(2, "2026-03-03T20:00", 1200, 20, 100, 16.0, 480),
        fuel(3, "2026-03-06T08:00", 1400, 20.0, 15000, full=False, soc=30, fuel_before_pct=30),
    ]
    led = build_ledger(VehicleParams(**PHEV), recs)
    [c] = led.combined_segments
    assert c.status == "ok" and c.quality == "B"
    # 起点 9.6 + 30 = 39.6L，终点 14.4L；电 10 − 6 + 16 = 20kWh
    assert c.amount == pytest.approx(25.2) and c.elec_kwh == pytest.approx(20.0)
    assert combined_totals(led.combined_segments)["distance"] == 400


def test_hybrid_anomaly_uses_combined_equivalent():
    recs = [fuel(i + 1, f"2026-04-{i * 5 + 1:02d}T08:00", 1000 + i * 500, liters, 15000, soc=50)
            for i, liters in enumerate([20, 20, 20, 20, 20, 45])]
    led = build_ledger(VehicleParams(**PHEV), recs)
    # 单能源不判断偏高偏低，综合段按折合油耗判断
    assert all(s.anomaly is None for s in led.fuel_segments)
    assert led.combined_segments[-1].anomaly == "high"
    assert all(s.anomaly is None for s in led.combined_segments[:-1])


def test_idle_drain_anomaly():
    recs = []
    soc, odo = 95, 1000
    for i, drop in enumerate([2, 2, 2, 2, 8]):
        recs.append(snap(2 * i + 1, f"2026-05-{i * 3 + 1:02d}T08:00", odo, soc=soc))
        soc -= drop
        recs.append(snap(2 * i + 2, f"2026-05-{i * 3 + 2:02d}T08:00", odo, soc=soc))
        soc -= 10
        odo += 100
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=60), recs)
    idle = [s for s in led.elec_segments if s.kind == "idle"]
    assert [s.anomaly for s in idle] == [None, None, None, None, "idle_high"]


def test_mileage_and_combined_totals():
    led = build_ledger(VehicleParams(**PHEV), example_a())
    obs = odo_observations(led, [])
    assert mileage(obs, date(2026, 9, 1), date(2026, 9, 30)) == 820
    # 9-03 20:00 到 9-07 20:00 之间按时间插值：9-04 零点约 10157.5
    assert mileage(obs, date(2026, 9, 4), date(2026, 9, 30)) == 662.5
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


def test_mileage_spreads_gap_between_records():
    obs = [(datetime(2026, 1, 1), 0.0), (datetime(2026, 3, 2), 6000.0)]
    # 60 天走了 6000km：2 月 28 天约 2800km，不会全部算在 3 月
    assert mileage(obs, date(2026, 2, 1), date(2026, 2, 28)) == 2800
    assert mileage(obs, date(2026, 3, 1), date(2026, 3, 31)) == 100
    # 录错的回退里程不会算成负数
    assert mileage([(datetime(2026, 1, 1), 100.0), (datetime(2026, 1, 2), 90.0)], date(2026, 1, 1), date(2026, 1, 31)) == 0


# ---------- 标成静置的段、跨月分摊、保险摊销、首页同区间 ----------


def example_tagged_idle():
    """三段正常行驶 20kWh/100km，第四段 100km 用了 30kWh（哨兵开着），被标成静置。"""
    recs = []
    soc, odo = 90, 1000
    for i, (km_, drop) in enumerate([(100, 25), (100, 25), (100, 25), (100, 37.5)]):
        recs.append(snap(2 * i + 1, f"2026-06-{i * 5 + 1:02d}T08:00", odo, soc=soc))
        odo += km_
        recs.append(snap(2 * i + 2, f"2026-06-{i * 5 + 5:02d}T08:00", odo, soc=soc - drop))
    return recs


def test_tagged_idle_with_mileage_splits_drive_part():
    params = VehicleParams(energy_type="bev", battery_kwh=80)
    led = build_ledger(params, example_tagged_idle(), marks={(8, "elec"): Mark(tag="idle")})
    seg = [s for s in led.elec_segments if s.end_id == 8][0]
    assert seg.kind == "idle"
    k = led.calib.k
    # 行驶 100km 按平时 25% × k 估算，多出的 12.5% × k 算静置
    assert seg.drive_part == pytest.approx(25 * k, abs=0.01)
    assert seg.idle_amount == pytest.approx(12.5 * k, abs=0.01)
    t = energy_totals(led.elec_segments)
    assert t["drive"]["distance"] == 400 and t["drive"]["per100"] == pytest.approx(25 * k, abs=0.01)
    assert t["idle"]["amount"] == pytest.approx(12.5 * k, abs=0.01)
    assert t["overall"]["amount"] == pytest.approx(112.5 * k, abs=0.01)
    assert t["idle"]["per_day"] is None
    assert t["idle"]["estimated_amount"] == t["idle"]["amount"]
    assert segment_dict(seg)["per_day"] is None

    measured = snap(9, "2026-06-22T08:00", 1400, soc=42.5)
    led = build_ledger(params, [*example_tagged_idle(), measured], marks={(8, "elec"): Mark(tag="idle")})
    idle = energy_totals(led.elec_segments)["idle"]
    assert idle["days"] == 2
    assert idle["measured_amount"] == pytest.approx(10 * k, abs=0.01)
    assert idle["per_day"] == pytest.approx(5 * k, abs=0.01)


def test_tagged_idle_excluded_drops_drive_part():
    params = VehicleParams(energy_type="bev", battery_kwh=80)
    led = build_ledger(params, example_tagged_idle(), marks={(8, "elec"): Mark(tag="idle", excluded=True)})
    t = energy_totals(led.elec_segments)
    assert t["drive"]["distance"] == 300
    assert t["idle"]["amount"] == pytest.approx(12.5 * led.calib.k, abs=0.01)


def test_segment_spanning_months_is_prorated_by_mileage():
    recs = [
        charge(1, "2026-07-20T08:00", 1000, 20, 100, 16.0, 500),
        snap(2, "2026-08-01T00:00", 1100, None),
        charge(3, "2026-08-20T08:00", 1500, 20, 100, 16.0, 500),
    ]
    params = VehicleParams(energy_type="bev", battery_kwh=20)
    led = build_ledger(params, recs)
    [seg] = [s for s in led.elec_segments if s.kind == "drive"]
    obs = odo_observations(led, [])
    jul = energy_totals(led.elec_segments, date(2026, 7, 1), date(2026, 7, 31), obs)["drive"]
    aug = energy_totals(led.elec_segments, date(2026, 8, 1), date(2026, 8, 31), obs)["drive"]
    # 500km 的段里 7 月 100km、8 月 400km，能耗按里程拆开，每百公里不变
    assert jul["distance"] == pytest.approx(100) and aug["distance"] == pytest.approx(400)
    assert jul["per100"] == pytest.approx(seg.per100, abs=0.01)
    assert jul["amount"] + aug["amount"] == pytest.approx(seg.amount, abs=0.01)


def test_insurance_amortized_in_cost_per_km():
    led = build_ledger(VehicleParams(**PHEV), example_a())
    ins = Exp(1, "insurance", dt("2026-09-01T10:00"), 365_00, period_start=date(2026, 9, 1), period_end=date(2027, 8, 31))
    rep = period_report(led, [ins], date(2026, 9, 1), date(2026, 9, 30))
    # 现金口径全额计入，每公里总成本只摊 30 天：365 元 × 30/365 = 30 元
    assert rep["total_cents"] == rep["energy_cents"] + 365_00
    assert rep["amortized_cents"] == rep["energy_cents"] + 30_00
    assert rep["total_cost_per_km"] == pytest.approx(rep["amortized_cents"] / 100 / rep["mileage"], abs=0.001)


def test_latest_pair_uses_same_interval_when_no_combined_segment():
    """提车加了一次不满的油，之后只充电、没有油表读数：没有综合段，油电都按提车至今同一区间算。"""
    recs = [
        snap(1, "2026-05-01T10:00", 10, soc=80, fuel_pct=10, tag="delivery"),
        fuel(2, "2026-05-01T10:05", 10, 30.0, 22500, full=False),
        charge(3, "2026-07-01T20:00", 900, 20, 100, 15.0, 1500),
        snap(4, "2026-10-20T09:00", 1740, soc=50),
    ]
    led = build_ledger(VehicleParams(**PHEV), recs)
    assert not [s for s in led.combined_segments if s.status == "ok"]
    p = latest_pair(led)
    assert p["quality"] == "C" and p["scope"] in ("delivery", "ledger")
    f = rough_best(led, "fuel")
    assert p["per100"] == f["per100"] and p["distance"] == f["distance"]
    assert p["elec_kwh"] is not None and p["equiv_per100"] is not None


def test_unchanged_gauge_does_not_publish_zero_fuel_consumption():
    records = [
        snap(1, "2026-11-14T08:00", 2000, soc=70, fuel_pct=15),
        snap(2, "2026-11-14T14:00", 2061, soc=25, fuel_pct=15),
    ]
    led = build_ledger(VehicleParams(**PHEV), records)
    [seg] = led.combined_segments
    assert seg.status == "uncertain"
    assert seg.per100 is None
    assert segment_dict(seg)["equiv_per100"] is None
    assert latest_pair(led) is None
    assert latest_energy(led, "elec")["per100"] > 0

    exact = build_ledger(VehicleParams(**PHEV, gauge_err_pct=0), records)
    [seg] = exact.fuel_segments
    assert seg.status == "ok" and seg.per100 == 0


def test_free_charge_has_zero_segment_price_and_missing_price_stays_unknown():
    records = [
        charge(1, "2026-11-10T08:00", 1000, 20, 80, 12, 0),
        charge(2, "2026-11-11T08:00", 1100, 20, 80, 12, 1200),
        snap(3, "2026-11-12T08:00", 1200, soc=20),
    ]
    params = VehicleParams(energy_type="bev", battery_kwh=18)
    first, second = build_ledger(params, records).elec_segments
    assert first.cost_cents == 0  # 终点的付费充电属于下一段
    assert second.cost_cents == 600  # 库存变化按免费与付费记录的加权单价计
    for r in records:
        r.cost_cents = None
    assert all(s.cost_cents is None for s in build_ledger(params, records).elec_segments)


def test_home_does_not_select_excluded_combined_interval():
    led = build_ledger(VehicleParams(**PHEV), example_a(),
                       marks={(4, "combined"): Mark(excluded=True, start_id=1)})
    assert latest_pair(led) is None


def test_charging_to_limit_keeps_calibration_and_historical_soc():
    params = VehicleParams(energy_type="bev", battery_kwh=45, charge_limit=90)
    records = [
        charge(1, "2026-11-01T08:00", 1000, 20, 100, 20, 2000,
               charge_full=True, estimated_fields=frozenset({"soc_after"})),
        charge(2, "2026-11-05T08:00", 1200, 30, 100, 17.5, 1750,
               charge_full=True, estimated_fields=frozenset({"soc_after"})),
    ]
    led = build_ledger(params, records)
    assert led.calib.source == "learned" and led.calib.k == pytest.approx(0.25)
    assert len(led.calib.samples) == 2
    [segment] = led.elec_segments
    assert segment.quality == "A" and segment.per100 == pytest.approx(8.75)

    records[0].charge_full = False
    led = build_ledger(params, records)
    assert len(led.calib.samples) == 1
    assert led.elec_segments[0].quality == "B"
    records[1].estimated_fields = frozenset({"soc_after", "charge_kwh"})
    assert build_ledger(params, records).calib.source == "default"


def test_home_rough_fallback_does_not_cross_excluded_segments():
    records = example_a() + [snap(5, "2026-09-20T08:00", 11200, soc=20)]
    led = build_ledger(VehicleParams(**PHEV), records,
                       marks={(4, "fuel"): Mark(excluded=True, start_id=1),
                              (4, "combined"): Mark(excluded=True, start_id=1)})
    assert rough_best(led, "fuel")["per100"] == pytest.approx(2.68)
    assert latest_pair(led) is None
    assert latest_energy(led, "fuel")["per100"] is None

    # 粗略范围覆盖排除段的一部分，不能重新带回被排除的能耗。
    recs = [
        snap(1, "2026-09-01T08:00", 10000, soc=50, fuel_pct=50),
        fuel(2, "2026-09-02T08:00", 10100, 20, 15000, soc=45),
        fuel(3, "2026-09-10T08:00", 10800, 25, 18750, soc=30),
        snap(4, "2026-09-20T08:00", 11000, soc=20, fuel_pct=10),
    ]
    led = build_ledger(VehicleParams(**PHEV, allow_gauge_anchor=False), recs,
                       marks={(3, "fuel"): Mark(excluded=True, start_id=2),
                              (3, "combined"): Mark(excluded=True, start_id=2)})
    assert rough_best(led, "fuel") is not None
    assert rough_best(led, "fuel", respect_exclusions=True) is None
    assert latest_pair(led) is None


@pytest.mark.parametrize("energy", ["fuel", "elec"])
def test_partial_readings_only_compare_known_sides_for_missed_hints(energy):
    if energy == "fuel":
        partial = fuel(2, "2026-01-02", 100, None, None, full=False, fuel_before_pct=65)
    else:
        partial = charge(2, "2026-01-02", 100, 65, None, None, None)
    recs = [snap(1, "2026-01-01", 0, soc=40, fuel_pct=40), partial,
            snap(3, "2026-01-03", 200, soc=60, fuel_pct=60),
            snap(4, "2026-01-04", 300, soc=85, fuel_pct=85)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    kind = "missed_fuel" if energy == "fuel" else "missed_charge"
    assert [(h.after_id, h.before_id) for h in led.hints if h.kind == kind] == [(1, 2), (3, 4)]


@pytest.mark.parametrize("energy", ["fuel", "elec"])
def test_unknown_intermediate_quantity_is_not_zero(energy):
    mid = (fuel(2, "2026-01-02", 50, None, 1000, full=False) if energy == "fuel" else
           charge(2, "2026-01-02", 50, None, None, None, 1000))
    recs = [fuel(1, "2026-01-01", 0, 20, 15000, soc=80), mid,
            fuel(3, "2026-01-03", 100, 10, 7500, soc=60)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    [single] = led.fuel_segments if energy == "fuel" else led.elec_segments
    assert not single.valid and single.amount is None and single.cost_cents is None
    [both] = led.combined_segments
    assert not both.valid and both.cost_cents is None
    assert (both.amount if energy == "fuel" else both.elec_kwh) is None


@pytest.mark.parametrize("kwh, missed", [(None, False), (8, True), (0, False), (8, False)])
def test_combined_without_endpoint_soc_distinguishes_unknown_missed_and_zero(kwh, missed):
    recs = [fuel(1, "2026-01-01", 0, 20, 15000),
            charge(2, "2026-01-02", 50, None, None, kwh, 0, missed_before=missed),
            fuel(3, "2026-01-03", 100, 10, 7500)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    assert led.fuel_segments[0].valid
    [both] = led.combined_segments
    if missed or kwh is None:
        assert not both.valid and both.elec_kwh is None and both.cost_cents is None
        if missed:
            assert both.status == "missed"
    else:
        assert both.quality == "B" and both.elec_kwh == kwh
        # 实充量可知，不等于缺端点 SOC 时能证实行驶耗电。
        assert both.status == "uncertain" and not both.valid and both.cost_cents is None


def test_combined_with_measured_soc_respects_missed_charge_and_electric_summary():
    recs = example_a()
    recs[1].missed_before = True
    led = build_ledger(VehicleParams(**PHEV), recs)
    assert led.fuel_segments[0].valid
    assert led.combined_segments[0].status == "missed"
    recs[1].missed_before = False
    summary = Summary(id=1, start=date(2026, 9, 3), end=date(2026, 9, 7), elec_kwh=8)
    led = build_ledger(VehicleParams(**PHEV), recs, summaries=[summary])
    assert led.fuel_segments[0].valid
    [both] = led.combined_segments
    assert both.status == "incomplete" and both.cost_cents is None


@pytest.mark.parametrize("missed_id, statuses", [(2, ["missed", "ok"]), (3, ["ok", "missed"])])
def test_short_fuel_interval_never_merges_with_a_missed_interval(missed_id, statuses):
    recs = [fuel(1, "2026-01-01", 0, 20, 15000, soc=60),
            fuel(2, "2026-01-02", 10, 1, 750, soc=60, missed_before=missed_id == 2),
            fuel(3, "2026-01-03", 110, 10, 7500, soc=60, missed_before=missed_id == 3)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    for segs in (led.fuel_segments, led.combined_segments):
        assert [(s.start_id, s.end_id) for s in segs] == [(1, 2), (2, 3)]
        assert [s.status for s in segs] == statuses


def test_combined_without_soc_or_charge_evidence_has_no_electric_consumption():
    recs = [fuel(1, "2026-01-01", 0, 20, 15000), fuel(2, "2026-01-02", 100, 10, 7500)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    [both] = led.combined_segments
    assert led.fuel_segments[0].valid
    assert not both.valid and both.elec_kwh is None and both.cost_cents is None


def test_short_combined_interval_restarts_after_missed_charge():
    recs = [fuel(1, "2026-01-01", 0, 20, 15000, soc=60),
            charge(2, "2026-01-02", 5, 55, 70, 3, 300, missed_before=True),
            fuel(3, "2026-01-03", 10, 1, 750, soc=65),
            fuel(4, "2026-01-04", 150, 12, 9000, soc=30)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    assert [(s.start_id, s.end_id, s.status) for s in led.combined_segments] == [
        (1, 3, "missed"), (3, 4, "ok")]


def test_combined_estimates_from_soc_only_charge_without_inventing_zero_cost():
    recs = [fuel(1, "2026-01-01", 0, 20, 15000),
            charge(2, "2026-01-02", 50, 20, 70, None, None),
            fuel(3, "2026-01-03", 100, 10, 7500)]
    led = build_ledger(VehicleParams(**PHEV), recs)
    [both] = led.combined_segments
    assert both.valid and both.elec_kwh == pytest.approx(50 * led.calib.k, abs=0.001)
    assert both.cost_cents is None


@pytest.mark.parametrize("distance", [99, 100, 200])
def test_audit_gauge_zero_requires_exact_reading(distance):
    recs = [snap(1, "2026-01-01", 0, soc=80, fuel_pct=50),
            snap(2, "2026-02-01", distance, soc=20, fuel_pct=50)]
    params = VehicleParams(energy_type="phev", tank_l=50, gauge_err_pct=5, battery_kwh=18)
    led = build_ledger(params, recs)
    f = latest_energy(led, "fuel")
    assert f["per100"] is None and f["hint"] == "below_gauge_resolution"
    assert latest_pair(led) is None
    if distance == 200:
        assert latest_energy(led, "elec")["per100"] == 6
    params.gauge_err_pct = 0
    assert latest_energy(build_ledger(params, recs), "fuel")["per100"] == 0


@pytest.mark.parametrize("limits,capacity,expected", [((80, 100), None, None), ((100, 80), None, None),
                                                      ((80, 80), None, 40), ((80, 100), 45, 30)])
def test_audit_full_charge_stock_reference(limits, capacity, expected):
    recs = [charge(i, f"2026-01-0{i}", 100 * (i - 1), None, limit, 40, 1000,
                   charge_full=True, estimated_fields=frozenset({"soc_after"}))
            for i, limit in enumerate(limits, 1)]
    led = build_ledger(VehicleParams(energy_type="bev", battery_kwh=capacity), recs)
    if expected is None:
        assert not any(s.valid for s in led.elec_segments)
        assert latest_energy(led, "elec")["quality"] != "A"
    else:
        [s] = led.elec_segments
        assert s.valid and s.quality == "A" and s.per100 == expected


@pytest.mark.parametrize("amount,equiv", [(1, None), (8, 10.90)])
def test_audit_combined_electric_error_bound(amount, equiv):
    recs = [fuel(1, "2026-01-01", 0, 20, 10000),
            charge(2, "2026-01-02", 50, None, None, amount, 100),
            fuel(3, "2026-01-03", 100, 10, 5000)]
    led = build_ledger(VehicleParams(energy_type="phev", tank_l=50, battery_kwh=18), recs)
    assert latest_energy(led, "fuel")["per100"] == 10
    assert segment_dict(led.combined_segments[0])["equiv_per100"] == equiv
    pair = latest_pair(led)
    assert (pair and pair["equiv_per100"]) == equiv
    assert period_report(led, [], date(2026, 1, 1), date(2026, 1, 31))["elec_kwh"] == amount
    # 同一组数据也走没有综合分段的首页粗估路径。
    led.combined_segments.clear()
    led.fuel_segments.clear()
    recs[-1].odo = 100
    pair = latest_pair(led)
    assert (pair and pair["equiv_per100"]) == equiv


@pytest.mark.parametrize("energy", ["fuel", "elec"])
def test_audit_directional_anchors(energy):
    if energy == "fuel":
        a = fuel(1, "2026-01-01", 0, None, None)
        b = fuel(2, "2026-01-03", 100, 8, 5600)
        expected = 8
    else:
        a = charge(1, "2026-01-01", 0, None, 80, None, None)
        b = charge(2, "2026-01-03", 100, 20, None, None, None)
        expected = 12
    led = build_ledger(VehicleParams(energy_type="phev", tank_l=50, battery_kwh=18), [a, b])
    [s] = led.fuel_segments if energy == "fuel" else led.elec_segments
    assert s.valid and s.quality == "A" and s.amount == expected
    if energy == "fuel":
        b.fuel_l = None
    else:
        b.soc_before = None
    led = build_ledger(led.params, [a, b])
    assert not any(s.valid for s in (led.fuel_segments if energy == "fuel" else led.elec_segments))


def test_audit_short_gauge_span_is_carried_forward():
    params = VehicleParams(energy_type="ice", tank_l=50, gauge_err_pct=0)
    a, b, c = [snap(i, f"2026-01-0{i}", odo, fuel_pct=pct)
               for i, odo, pct in [(1, 0, 90), (2, 10, 85), (3, 100, 50)]]
    for records in ([a, b, c], [a, c]):
        [s] = build_ledger(params, records).fuel_segments
        assert (s.start_id, s.end_id, s.amount, s.per100) == (1, 3, 20, 20)
    missed = fuel(4, "2026-01-02T12:00", 11, 1, 600, full=False, missed_before=True)
    assert not any(s.start_id == 1 and s.end_id == 3 and s.valid
                   for s in build_ledger(params, [a, b, missed, c]).fuel_segments)


def test_audit_recent_daily_does_not_reuse_last_year():
    from app.engine.aggregate import avg_daily_km

    today = date(2026, 11, 25)
    assert avg_daily_km([(dt("2025-01-01"), 0), (dt("2025-01-11"), 500)], today) is None
    assert avg_daily_km([(dt("2026-11-01"), 0), (dt("2026-11-11"), 500)], today) == 50


@pytest.mark.parametrize("modes,expected", [([("dc", 8)], 0), ([("home", 8)], 1),
                                            ([("dc", None)], None), ([(None, 8)], None),
                                            ([("dc", 8), ("home", None)], None)])
def test_audit_home_share_needs_known_modes_and_quantities(modes, expected):
    led = build_ledger(VehicleParams(energy_type="bev"), [
        charge(i, f"2026-01-0{i}", i * 100, None, None, kwh, 100, ctype=kind)
        for i, (kind, kwh) in enumerate(modes, 1)])
    assert period_report(led, [], date(2026, 1, 1), date(2026, 1, 31))["home_share"] == expected


def test_audit_parked_electricity_carries_consumption_cost():
    records = [fuel(1, "2026-01-15", 0, 10, 7000, soc=100),
               snap(2, "2026-01-31", 100, soc=80),
               snap(3, "2026-03-01", 100, soc=50),
               fuel(4, "2026-03-15", 200, 10, 7000, soc=30)]
    records.insert(0, charge(5, "2026-01-14", 0, None, None, 10, 1000))
    led = build_ledger(VehicleParams(energy_type="phev", tank_l=50, battery_kwh=18), records)
    from app.engine.aggregate import month_bounds
    months = [period_report(led, [], *month_bounds(2026, m))["combined"] for m in (1, 2, 3)]
    assert [m["distance"] for m in months] == [100, 0, 100]
    assert [m["cost_cents"] for m in months] == [3921, 579, 3900]
    assert [m["cost_per_km"] for m in months] == [0.392, None, 0.390]
    assert months[1]["elec_kwh"] == 5.79
    assert sum(m["cost_cents"] for m in months) == 8400


def test_audit_summary_cents_are_additive_across_months():
    summary = Summary(1, date(2026, 1, 31), date(2026, 2, 1), fuel_cents=101)
    led = build_ledger(VehicleParams(energy_type="ice"), [], summaries=[summary])
    jan = period_report(led, [], date(2026, 1, 1), date(2026, 1, 31))
    feb = period_report(led, [], date(2026, 2, 1), date(2026, 2, 28))
    both = period_report(led, [], date(2026, 1, 1), date(2026, 2, 28))
    for key in ("fuel_cents", "total_cents", "summary_cents", "amortized_cents"):
        assert sorted([jan[key], feb[key]]) == [50, 51]
        assert jan[key] + feb[key] == both[key] == 101

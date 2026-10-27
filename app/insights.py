"""
insights.py — 数据体检、待确认事项、段计算过程说明
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import DismissedHint, Record, User, Vehicle, localnow
from app.engine.aggregate import ledger_start_index, odo_observations
from app.engine.ledger import Ledger, Point, equiv_per100
from app.engine.types import Segment
from app.services import fmt_num, fuel_level_text, load_expenses, load_ledger


def latest_odo_of(obs: list, v: Vehicle) -> Optional[float]:
    vals = [o for _, o in obs]
    if v.initial_odometer_km is not None:
        vals.append(v.initial_odometer_km)
    return max(vals) if vals else None


def dismissed_keys(db: Session, vid: int, user: User) -> set:
    return set(db.scalars(select(DismissedHint.hint_key).where(
        DismissedHint.vehicle_id == vid, DismissedHint.user_id == user.id)))


ANOMALY_TEXT = {
    "high": "能耗明显偏高",
    "low": "能耗明显偏低，可能漏记了补能",
    "parked": "疑似长时间停放耗电",
    "idle_high": "停车耗电明显偏高，检查哨兵、空调等是否忘关",
}


def pending_items(db: Session, v: Vehicle, user: User, led: Optional[Ledger] = None) -> list[dict]:
    """首页「待确认」：疑似漏记、能耗异常，排除已点过「不再提示」的。"""
    led = led or load_ledger(db, v)
    dismissed = dismissed_keys(db, v.id, user)
    out = []
    for h in led.hints:
        if h.key in dismissed:
            continue
        if h.kind == "missed_charge":
            text = f"疑似漏记一次充电（电量 {fmt_num(h.from_value, 0)}% → {fmt_num(h.to_value, 0)}%）"
        else:
            text = f"疑似漏记一次加油（油量 {fuel_level_text(h.from_value, v)} → {fuel_level_text(h.to_value, v)}）"
        out.append({"key": h.key, "type": h.kind, "after_id": h.after_id, "before_id": h.before_id, "text": text})
    for energy, segs in (("elec", led.elec_segments), ("fuel", led.fuel_segments), ("combined", led.combined_segments)):
        for s in segs:
            if not s.anomaly:
                continue
            key = f"anomaly:{energy}:{s.end_id}"
            if key in dismissed:
                continue
            if s.anomaly == "idle_high":
                value = f"日均 {fmt_num(s.amount / s.days)} kWh"
            elif energy == "combined":
                value = f"折合 {fmt_num(equiv_per100(s, led.params.kwh_per_l))} L/100km"
            else:
                value = f"{fmt_num(s.per100)} {'kWh' if energy == 'elec' else 'L'}/100km"
            label = ("综合" if energy == "combined" else "") + ANOMALY_TEXT[s.anomaly]
            out.append({
                "key": key, "type": "anomaly", "anomaly": s.anomaly, "energy": energy, "end_id": s.end_id,
                "text": f"{s.end_ts:%m-%d} 这段{label}（{value}）",
            })
    return out


def health_report(db: Session, v: Vehicle, user: User) -> dict:
    led = load_ledger(db, v)
    obs = odo_observations(led, load_expenses(db, v.id))
    li = ledger_start_index(led)
    real = [p for p in led.points if not p.odo_estimated]
    ledger_km = (real[-1].odo - led.points[li].odo) if (li is not None and real) else 0
    coverage = {}
    for energy, segs, on in (("fuel", led.fuel_segments, led.params.has_fuel),
                             ("elec", led.elec_segments, led.params.has_elec)):
        if not on:
            continue
        a_km = sum(s.distance for s in segs if s.status == "ok" and s.quality == "A" and s.kind != "idle")
        coverage[energy] = round(min(a_km / ledger_km, 1), 3) if ledger_km >= 1 else None
    issues = []
    dismissed = dismissed_keys(db, v.id, user)

    def add(key, level, text, action=None, **extra):
        if key not in dismissed:
            issues.append({"key": key, "level": level, "text": text, "action": action, **extra})

    for item in pending_items(db, v, user, led):
        if item["type"] in ("missed_charge", "missed_fuel"):
            add(item["key"], "warn", item["text"], "fix_missed", after_id=item["after_id"],
                before_id=item["before_id"], kind="charge" if item["type"] == "missed_charge" else "fuel")
    if led.params.has_fuel:
        fulls = [p for p in real if p.rec.kind == "fuel" and p.rec.fuel_full]
        if not fulls:
            add("no_full", "info", "还没有「加满」记录：下次加油建议加满，之后油耗就准了", "record_fuel")
        elif len(fulls) == 1:
            add("one_full", "info", "再加满一次，就能算出第一段准确油耗", "record_fuel")
        if not v.tank_capacity_l:
            add("no_tank", "info", "没填油箱容积：油表读数和「加满自动算油量」用不了", "edit_vehicle")
    if led.params.has_elec:
        if led.calib.k is None:
            add("no_k", "info", "电池容量没填，也还没有带 SOC 的充电记录，电耗暂时只能按充满到充满计算", "edit_vehicle")
        elif led.calib.source == "default":
            add("k_default", "info", "电耗目前按电池容量估算；充电时填一下开始和结束 SOC，会自动校准", None)
    # 提车快照和记账起点挤在同一里程上（旧版向导没填提车里程时会这样）
    delivery = next((p for p in led.points if p.rec.snapshot_tag == "delivery"), None)
    start = next((p for p in led.points if p.rec.snapshot_tag == "ledger_start"), None)
    if delivery and start and delivery.odo >= start.odo and (start.ts - delivery.ts).days >= 2:
        add("delivery_odo", "warn", f"提车记录的里程（{delivery.odo:,.0f} km）和记账起点一样，提车到现在的里程会算不出来，请修正提车里程",
            "fix_delivery", odometer_km=delivery.odo, delivery_date=delivery.ts.date().isoformat())
    # 时间在后、里程却更小的记录
    conflicts = []
    top = None
    for r in sorted((p.rec for p in led.points if not p.odo_estimated), key=lambda r: (r.ts, r.id)):
        if top is not None and r.odo < top[0] - 0.5:
            conflicts.append(f"{r.ts:%m-%d} 的 {r.odo:,.0f} km（之前 {top[1]:%m-%d} 已到 {top[0]:,.0f} km）")
        if top is None or r.odo > top[0]:
            top = (r.odo, r.ts)
    if conflicts:
        add("odo_order", "warn", f"有 {len(conflicts)} 条记录的里程比更早的记录还小，能耗会算乱：" + "；".join(conflicts[:3]),
            "open_records")
    est = [p for p in led.points if p.odo_estimated]
    if est:
        add("est_odo", "info", f"有 {len(est)} 条记录的里程是估算的", "filter_estimated")
    for sid, d in led.summary_diffs.items():
        for w in d.get("warnings", []):
            add(f"summary_neg:{sid}:{w}", "warn", "有一条汇总的总量小于已逐条记录的量，请检查", "open_summaries")
    stale = db.scalars(select(Record).where(
        Record.vehicle_id == v.id, Record.status == "in_progress", Record.deleted_at.is_(None),
        Record.occurred_at < localnow() - timedelta(hours=24)))
    for r in stale:
        add(f"stale:{r.id}", "warn", f"{r.occurred_at:%m-%d %H:%M} 开始的充电还没结束", "finish_charge", record_id=r.id)
    return {
        "coverage": coverage,
        "ledger_km": round(ledger_km, 1),
        "latest_odo": latest_odo_of(obs, v),
        "issues": issues,
        "k": led.calib.k,
        "k_source": led.calib.source,
    }


# ---------- 段计算过程 ----------


def _level_text(p: Point, energy: str, side: int, led: Ledger, v: Optional[Vehicle] = None) -> str:
    lv = p.levels(energy)[side]
    if lv is None:
        return "?"
    r = p.rec
    k = led.calib.k
    if energy == "fuel":
        if side == 1 and r.kind == "fuel" and r.fuel_full:
            return "加满"
        if side == 0 and r.kind == "fuel" and r.fuel_full and lv.conf == "exact":
            return f"满 − {fmt_num(r.fuel_l)}L"
        tank = led.params.tank_l
        return f"油量 {fuel_level_text(lv.value / tank * 100, v)}" if tank else f"{fmt_num(lv.value)}L"
    if k:
        return f"SOC {fmt_num(lv.value / k, 0)}%"
    return "充满" if side == 1 else f"满 − {fmt_num(r.charge_kwh)}kWh"


def explain(led: Ledger, s: Segment, v: Optional[Vehicle] = None) -> dict:
    """把一段的计算拆成「起点存量 − 终点存量 + 期间补能 = 消耗」，前端照着展示算式。"""
    idx = led.point_index()
    energies = ["fuel", "elec"] if s.energy == "combined" else [s.energy]
    parts = []
    for energy in energies:
        a, b = led.points[idx[s.start_id]], led.points[idx[s.end_id]]
        la, lb = a.levels(energy)[1], b.levels(energy)[0]
        amount = s.amount if energy == s.energy or (s.energy == "combined" and energy == "fuel") else s.elec_kwh
        if amount is None:
            continue
        if la is None or lb is None:
            parts.append({"energy": energy, "text": f"期间充电合计 {fmt_num(amount)}kWh（两端没有电量读数）",
                          "amount": amount})
            continue
        added = amount - (la.value - lb.value)
        unit = "L" if energy == "fuel" else "kWh"
        parts.append({
            "energy": energy,
            "start": _level_text(a, energy, 1, led, v),
            "end": _level_text(b, energy, 0, led, v),
            "start_value": round(la.value, 2),
            "end_value": round(lb.value, 2),
            "added": round(added, 2),
            "amount": round(amount, 2),
            "unit": unit,
            "k": led.calib.k if energy == "elec" else None,
        })
    return {"distance": s.distance, "parts": parts}

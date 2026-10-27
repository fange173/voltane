"""
ledger.py — 能耗计算核心：时点排序、各时点存量、区间消耗、分段、异常与漏记识别

消耗量(A→B) = A 补能后存量 − B 补能前存量 + A、B 之间的补能量
油的单位是升；电的单位是电网侧 kWh（SOC% × k）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Optional

from app.engine.calibration import Calibration, calibrate
from app.engine.types import Level, Mark, Rec, Segment, Summary, VehicleParams, worse

# 里程小于该值的段视为静置
IDLE_KM = 1.0
# 估算段相对误差超过该值时不出数
MAX_REL_ERR = 0.5
# 异常识别：基线最少段数、倍数阈值、停放判定
ANOMALY_MIN_BASE = 3
ANOMALY_HIGH = 1.5
ANOMALY_LOW = 0.5
PARKED_MIN_DAYS = 2.0
PARKED_KM_PER_DAY = 5.0
# 静置耗电异常：段至少半天、日均耗电至少 0.3 kWh 才判断
IDLE_MIN_DAYS = 0.5
IDLE_MIN_KWH_DAY = 0.3
# 补录估算数量的相对误差
EST_QTY_ERR = 0.05
# 出厂自带油量的估算误差（升）
DELIVERY_FUEL_ERR = 3.0
# 综合段两端没有电量读数时，按 SOC 差 ±20 个百分点计误差
COMBINED_SOC_ERR_PCT = 20.0


@dataclass
class Point:
    rec: Rec
    odo: float  # 有效里程（未知时为插值）
    odo_estimated: bool
    fuel: tuple = (None, None)  # (before, after) Level
    elec: tuple = (None, None)

    @property
    def id(self) -> int:
        return self.rec.id

    @property
    def ts(self) -> datetime:
        return self.rec.ts

    def levels(self, energy: str) -> tuple:
        return self.fuel if energy == "fuel" else self.elec


@dataclass
class Hint:
    key: str
    kind: str  # missed_charge | missed_fuel
    after_id: int
    before_id: int
    from_value: float
    to_value: float


@dataclass
class Ledger:
    params: VehicleParams
    calib: Calibration
    points: list
    fuel_segments: list = field(default_factory=list)
    elec_segments: list = field(default_factory=list)
    combined_segments: list = field(default_factory=list)
    hints: list = field(default_factory=list)
    summaries: list = field(default_factory=list)
    summary_diffs: dict = field(default_factory=dict)

    def point_index(self) -> dict:
        return {p.id: i for i, p in enumerate(self.points)}


# ---------- 排序与里程插值 ----------


def order_points(records: list[Rec]) -> list[Point]:
    """按 (里程, 时间, id) 排序；里程未知的按时间在前后两个已知里程之间线性插值。"""
    by_ts = sorted(records, key=lambda r: (r.ts, r.id))
    known = [(r.ts, r.odo) for r in by_ts if r.odo is not None and not r.odo_estimated]
    pts = []
    for r in by_ts:
        if r.odo is not None and not r.odo_estimated:
            pts.append(Point(r, float(r.odo), False))
            continue
        prev = next((k for k in reversed(known) if k[0] <= r.ts), None)
        nxt = next((k for k in known if k[0] > r.ts), None)
        if prev and nxt:
            span = (nxt[0] - prev[0]).total_seconds()
            frac = (r.ts - prev[0]).total_seconds() / span if span > 0 else 0
            odo = prev[1] + (nxt[1] - prev[1]) * frac
        elif prev:
            odo = prev[1]
        elif nxt:
            odo = nxt[1]
        elif r.odo is not None:
            odo = float(r.odo)
        else:
            continue
        pts.append(Point(r, round(odo, 1), True))
    pts.sort(key=lambda p: (p.odo, p.ts, p.id))
    return pts


# ---------- 各时点存量 ----------


def fuel_levels(r: Rec, p: VehicleParams) -> tuple:
    T = p.tank_l
    gerr = p.gauge_err_pct / 100 * T if T else 0.0
    est = r.estimated_fields

    def pct_level(pct):
        if pct is None or not T:
            return None
        err = max(gerr, DELIVERY_FUEL_ERR) if r.snapshot_tag == "delivery" else gerr
        return Level(pct / 100 * T, err, "estimated")

    if r.kind != "fuel":
        lv = pct_level(r.fuel_before_pct)
        return lv, lv

    liters = r.fuel_l
    # 油箱容积未知时以「满」为 0 点，满到满时容积会被抵消
    full_ref = T if T else 0.0
    before = after = None
    if r.fuel_full:
        after = Level(full_ref, 0.0, "exact")
        if liters is not None and "fuel_l" not in est:
            before = Level(full_ref - liters, 0.0, "exact")
        elif r.fuel_before_pct is not None and T:
            before = pct_level(r.fuel_before_pct)
        elif liters is not None:
            before = Level(full_ref - liters, gerr, "estimated")
    else:
        before = pct_level(r.fuel_before_pct)
        if r.fuel_after_pct is not None:
            after = pct_level(r.fuel_after_pct)
        elif before is not None and liters is not None:
            after = Level(before.value + liters, before.err, before.conf)
        if before is None and after is not None and liters is not None:
            before = Level(after.value - liters, after.err, after.conf)
    return before, after


def elec_levels(r: Rec, p: VehicleParams, k: Optional[float]) -> tuple:
    est = r.estimated_fields
    full_ref = p.charge_limit * k if k else 0.0

    def soc_level(v, name):
        if v is None or not k:
            return None
        if name in est:
            return Level(v * k, 3 * k, "estimated")
        return Level(v * k, 1 * k, "measured")

    if r.kind != "charge":
        lv = soc_level(r.soc_before, "soc_before")
        return lv, lv

    kwh = r.charge_kwh
    kwh_conf = "estimated" if "charge_kwh" in est else None
    before = soc_level(r.soc_before, "soc_before")
    after = soc_level(r.soc_after, "soc_after")
    if after is None and r.charge_full:
        after = Level(full_ref, 1 * k if k else 0.0, "exact")
    if after is None and before is not None and kwh is not None:
        after = Level(before.value + kwh, before.err, kwh_conf or before.conf)
    if before is None and after is not None and kwh is not None:
        before = Level(after.value - kwh, after.err, kwh_conf or after.conf)
    return before, after


def raw_added(r: Rec, energy: str) -> float:
    if energy == "fuel":
        return float(r.fuel_l or 0) if r.kind == "fuel" else 0.0
    return float(r.charge_kwh or 0) if r.kind == "charge" else 0.0


def is_energy_rec(r: Rec, energy: str) -> bool:
    return r.kind == ("fuel" if energy == "fuel" else "charge")


def qty_estimated(r: Rec, energy: str) -> bool:
    name = "fuel_l" if energy == "fuel" else "charge_kwh"
    return is_energy_rec(r, energy) and name in r.estimated_fields


# ---------- 区间消耗 ----------


@dataclass
class Span:
    amount: Optional[float]
    err: float
    quality: str
    status: str


def span(points: list[Point], i: int, j: int, energy: str) -> Span:
    """计算 points[i] 补能后到 points[j] 补能前的消耗量。"""
    a, b = points[i], points[j]
    la = a.levels(energy)[1]
    lb = b.levels(energy)[0]
    if la is None or lb is None:
        return Span(None, 0.0, "D", "unknown")
    amount = la.value - lb.value
    err = la.err + lb.err
    quality = "A" if la.precise and lb.precise else "B"
    for m in range(i + 1, j):
        p = points[m]
        before, after = p.levels(energy)
        if before is not None and after is not None:
            amount += after.value - before.value
            if not (before.precise and after.precise):
                quality = "B"
        else:
            q = raw_added(p.rec, energy)
            amount += q
            if qty_estimated(p.rec, energy):
                err += q * EST_QTY_ERR
                quality = "B"
    for m in range(i + 1, j + 1):
        r = points[m].rec
        if r.missed_before and is_energy_rec(r, energy):
            return Span(round(amount, 3), round(err, 3), quality, "missed")
    status = "ok"
    if amount < 0:
        status = "negative"
    elif quality == "B" and amount > 0 and err / amount > MAX_REL_ERR:
        status = "uncertain"
    return Span(round(amount, 3), round(err, 3), quality, status)


def _make_segment(points, i, j, energy, sp: Span) -> Segment:
    a, b = points[i], points[j]
    seg = Segment(
        energy=energy,
        start_id=a.id,
        end_id=b.id,
        start_ts=a.ts,
        end_ts=b.ts,
        start_odo=a.odo,
        end_odo=b.odo,
        amount=sp.amount,
        err=sp.err,
        quality=sp.quality,
        status=sp.status,
        record_ids=[points[m].id for m in range(i, j + 1)],
    )
    seg._i, seg._j = i, j  # 供合并短段时重算
    return seg


def _usable_anchor(p: Point, energy: str) -> bool:
    before, after = p.levels(energy)
    return before is not None and after is not None and not p.odo_estimated


def _strict_fuel_anchor(p: Point) -> bool:
    return _usable_anchor(p, "fuel") and p.rec.kind == "fuel" and p.rec.fuel_full


# ---------- 汇总补差 ----------


def summary_diffs(summaries: list[Summary], records: list[Rec], fuel_price: float, elec_price: float) -> dict:
    """算出每条汇总里「没有逐条记录」的补能量和金额。
    extra 模式直接取填的数；total 模式用总数减去同期已逐条记录的部分，差额为负时记 0 并给出警告。"""
    out = {}
    for s in summaries:
        inside = [r for r in records if s.start <= r.ts.date() <= s.end]
        rec = {
            "fuel_l": sum(r.fuel_l or 0 for r in inside if r.kind == "fuel"),
            "fuel_cents": sum(r.cost_cents for r in inside if r.kind == "fuel"),
            "elec_kwh": sum(r.charge_kwh or 0 for r in inside if r.kind == "charge"),
            "elec_cents": sum(r.cost_cents for r in inside if r.kind == "charge"),
        }
        fuel_total, fuel_est = s.fuel_l, False
        if fuel_total is None and s.fuel_cents:
            fuel_total, fuel_est = s.fuel_cents / 100 / (s.fuel_price or fuel_price), True
        elec_total, elec_est = s.elec_kwh, False
        if elec_total is None and s.elec_cents:
            elec_total, elec_est = s.elec_cents / 100 / (s.elec_price or elec_price), True
        d = {
            "mode": s.mode,
            "fuel_estimated": fuel_est,
            "elec_estimated": elec_est,
            "recorded_fuel_l": round(rec["fuel_l"], 2),
            "recorded_fuel_cents": rec["fuel_cents"],
            "recorded_elec_kwh": round(rec["elec_kwh"], 2),
            "recorded_elec_cents": rec["elec_cents"],
            "warnings": [],
        }
        for key, total in (
            ("fuel_l", fuel_total),
            ("fuel_cents", s.fuel_cents),
            ("elec_kwh", elec_total),
            ("elec_cents", s.elec_cents),
        ):
            if total is None:
                d[key] = None
                continue
            diff = total - rec[key] if s.mode == "total" else total
            if diff < -0.01 and key in ("fuel_l", "elec_kwh"):
                d["warnings"].append(key.split("_")[0] + "_negative")
            diff = max(diff, 0)
            d[key] = round(diff) if key.endswith("cents") else round(diff, 2)
        out[s.id] = d
    return out


def _summary_overlaps(seg: Segment, summaries, diffs, energy: str) -> bool:
    key = "fuel_l" if energy == "fuel" else "elec_kwh"
    for s in summaries:
        if not diffs.get(s.id, {}).get(key):
            continue
        if seg.start_ts.date() <= s.end and seg.end_ts.date() >= s.start:
            return True
    return False


# ---------- 价格 ----------


def _avg_price(recs: list[Rec], energy: str) -> Optional[float]:
    qty = cents = 0.0
    for r in recs:
        q = raw_added(r, energy)
        if q > 0 and r.cost_cents:
            qty += q
            cents += r.cost_cents
    return cents / 100 / qty if qty > 0 else None


def recent_price(points: list[Point], upto: int, energy: str, n: int = 5) -> Optional[float]:
    recs = [p.rec for p in points[: upto + 1] if is_energy_rec(p.rec, energy)][-n:]
    return _avg_price(recs, energy)


def _segment_cost(points, i, j, energy, amount) -> Optional[int]:
    if amount is None or amount < 0:
        return None
    recs = [points[m].rec for m in range(i + 1, j + 1) if is_energy_rec(points[m].rec, energy)]
    price = _avg_price(recs, energy) or recent_price(points, j, energy)
    return round(amount * price * 100) if price else None


# ---------- 分段 ----------


def _fuel_segments(points, p: VehicleParams) -> list[Segment]:
    strict = [i for i, pt in enumerate(points) if _strict_fuel_anchor(pt)]
    segs = []
    covered = []
    k = 0
    while k < len(strict) - 1:
        i = strict[k]
        # 加满点间距不足最小段里程时跳到下一个加满点（短段合并）
        m = k + 1
        while m < len(strict) - 1 and points[strict[m]].odo - points[i].odo < p.min_segment_km:
            m += 1
        j = strict[m]
        sp = span(points, i, j, "fuel")
        seg = _make_segment(points, i, j, "fuel", sp)
        if points[j].odo - points[i].odo < p.min_segment_km:
            seg.kind = "short"
        segs.append(seg)
        if sp.status == "ok":
            covered.append((i, j))
        k = m
    if p.allow_gauge_anchor:
        strict_set = set(strict)
        loose = [i for i, pt in enumerate(points) if _usable_anchor(pt, "fuel")]
        for a, b in zip(loose, loose[1:]):
            if a in strict_set and b in strict_set:
                continue
            if any(ci <= a and b <= cj for ci, cj in covered):
                continue
            if points[b].odo - points[a].odo < p.min_segment_km:
                continue
            segs.append(_make_segment(points, a, b, "fuel", span(points, a, b, "fuel")))
    segs.sort(key=lambda s: (s.end_odo, s.end_ts))
    return segs


def _elec_segments(points, p: VehicleParams) -> list[Segment]:
    anchors = [i for i, pt in enumerate(points) if _usable_anchor(pt, "elec")]
    raw = []
    for a, b in zip(anchors, anchors[1:]):
        sp = span(points, a, b, "elec")
        seg = _make_segment(points, a, b, "elec", sp)
        if points[b].odo - points[a].odo < IDLE_KM:
            seg.kind = "idle"
        raw.append(seg)
    # 合并相邻的短行驶段：直接从首段起点到下一段终点重算
    out = []
    idx = 0
    while idx < len(raw):
        seg = raw[idx]
        while (
            seg.kind == "drive"
            and seg.status == "ok"
            and seg.distance < p.min_segment_km
            and idx + 1 < len(raw)
            and raw[idx + 1].kind == "drive"
            and raw[idx + 1].status == "ok"
        ):
            nxt = raw[idx + 1]
            seg = _make_segment(points, seg._i, nxt._j, "elec", span(points, seg._i, nxt._j, "elec"))
            idx += 1
        if seg.kind == "drive" and seg.distance < p.min_segment_km:
            seg.kind = "short"
        out.append(seg)
        idx += 1
    return out


def _combined_segments(points, fuel_segs: list[Segment], k: Optional[float]) -> list[Segment]:
    """综合段与油耗段一一对应（加满到加满，以及油表读数之间），同一区间再算期间用电。"""
    segs = []
    for fs in fuel_segs:
        i, j = fs._i, fs._j
        fsp = span(points, i, j, "fuel")
        seg = _make_segment(points, i, j, "combined", fsp)
        seg.kind = fs.kind
        esp = span(points, i, j, "elec")
        if esp.status == "unknown":
            # 两端没有电量读数：只用期间充电量，误差按 SOC 差 ±20 个百分点计
            kwh = sum(raw_added(points[x].rec, "elec") for x in range(i + 1, j))
            err = COMBINED_SOC_ERR_PCT * k if k else None
            missed = any(points[x].rec.missed_before and points[x].rec.kind == "charge" for x in range(i + 1, j + 1))
            seg.elec_kwh = None if missed else round(kwh, 3)
            seg.elec_err = err
            seg.elec_quality = "B"
        elif esp.status in ("ok", "uncertain"):
            seg.elec_kwh, seg.elec_err, seg.elec_quality = esp.amount, esp.err, esp.quality
        fuel_cost = _segment_cost(points, i, j, "fuel", fsp.amount) if fsp.status == "ok" else None
        elec_cost = _segment_cost(points, i, j, "elec", seg.elec_kwh) if seg.elec_kwh is not None else 0
        if fuel_cost is not None and elec_cost is not None:
            seg.cost_cents = fuel_cost + elec_cost
        if seg.elec_quality:
            seg.quality = worse(seg.quality, seg.elec_quality)
        segs.append(seg)
    return segs


def _apply_marks(segs: list[Segment], marks: dict, energy: str) -> None:
    for s in segs:
        m = marks.get((s.end_id, energy))
        if not m:
            continue
        s.tag = m.tag
        s.excluded = m.excluded
        if m.tag == "idle" and s.kind != "idle":
            s.kind = "idle"


def equiv_per100(s: Segment, kwh_per_l: float) -> Optional[float]:
    """综合段折合油耗：油 + 电按热值折成升。"""
    if s.amount is None or s.elec_kwh is None or s.distance < 1:
        return None
    return (s.amount + s.elec_kwh / kwh_per_l) / s.distance * 100


def _detect_anomalies(segs: list[Segment], energy: str, hybrid: bool = False, kwh_per_l: float = 8.9) -> None:
    """插混/增程每段油电比例随驾驶模式变化，单能源能耗偏高偏低没有意义：
    单能源只识别停放耗电，偏高偏低改看综合段的折合油耗。"""
    history = []
    for s in segs:
        if s.status != "ok" or s.kind != "drive":
            continue
        v = equiv_per100(s, kwh_per_l) if energy == "combined" else s.per100
        if v is None:
            continue
        if s.tag == "normal" and not s.excluded:
            if energy == "elec" and s.days >= PARKED_MIN_DAYS and s.distance / s.days < PARKED_KM_PER_DAY:
                s.anomaly = "parked"
            elif not hybrid and len(history) >= ANOMALY_MIN_BASE:
                base = median(history[-20:])
                if v > base * ANOMALY_HIGH:
                    s.anomaly = "high"
                elif v < base * ANOMALY_LOW:
                    s.anomaly = "low"
        if s.anomaly is None and s.tag == "normal" and not s.excluded:
            history.append(v)


def _detect_idle_anomalies(segs: list[Segment]) -> None:
    """静置段日均耗电明显高于以往静置（如哨兵、空调忘关），与车型无关。"""
    history = []
    for s in segs:
        if s.status != "ok" or s.kind != "idle" or s.amount is None or s.days < IDLE_MIN_DAYS:
            continue
        v = s.amount / s.days
        if s.tag == "normal" and not s.excluded and len(history) >= ANOMALY_MIN_BASE:
            if v > median(history[-20:]) * ANOMALY_HIGH and v >= IDLE_MIN_KWH_DAY:
                s.anomaly = "idle_high"
        if s.anomaly is None and not s.excluded:
            history.append(v)


def _detect_missed(points, p: VehicleParams, k: Optional[float]) -> list[Hint]:
    hints = []
    if p.has_elec and k:
        prev = None
        for idx, pt in enumerate(points):
            before, after = pt.elec
            if before is None or pt.odo_estimated:
                if pt.rec.kind == "charge":
                    prev = None
                continue
            if prev is not None:
                pi, pafter = prev
                between_charge = any(points[x].rec.kind == "charge" for x in range(pi + 1, idx))
                if not between_charge and before.value - pafter.value > 3 * k:
                    hints.append(Hint(
                        f"missed_charge:{points[pi].id}:{pt.id}", "missed_charge", points[pi].id, pt.id,
                        round(pafter.value / k, 1), round(before.value / k, 1),
                    ))
            prev = (idx, after)
    if p.has_fuel and p.tank_l:
        thresh = max(0.10 * p.tank_l, 2 * p.gauge_err_pct / 100 * p.tank_l)
        prev = None
        for idx, pt in enumerate(points):
            before, after = pt.fuel
            if before is None or pt.odo_estimated:
                if pt.rec.kind == "fuel":
                    prev = None
                continue
            if prev is not None:
                pi, pafter = prev
                between = any(points[x].rec.kind == "fuel" for x in range(pi + 1, idx))
                if not between and before.value - pafter.value > thresh:
                    hints.append(Hint(
                        f"missed_fuel:{points[pi].id}:{pt.id}", "missed_fuel", points[pi].id, pt.id,
                        round(pafter.value / p.tank_l * 100, 1), round(before.value / p.tank_l * 100, 1),
                    ))
            prev = (idx, after)
    return hints


def build_ledger(
    params: VehicleParams,
    records: list[Rec],
    summaries: Optional[list[Summary]] = None,
    marks: Optional[dict] = None,
) -> Ledger:
    summaries = summaries or []
    marks = marks or {}
    calib = calibrate(params, records)
    k = calib.k
    points = order_points(records)
    for pt in points:
        if params.has_fuel:
            pt.fuel = fuel_levels(pt.rec, params)
        if params.has_elec:
            pt.elec = elec_levels(pt.rec, params, k)

    fuel_price = _avg_price([r for r in records if r.kind == "fuel"], "fuel") or 7.5
    elec_price = _avg_price([r for r in records if r.kind == "charge"], "elec") or 0.6
    diffs = summary_diffs(summaries, records, fuel_price, elec_price)
    led = Ledger(params, calib, points, summaries=summaries, summary_diffs=diffs)

    if params.has_fuel:
        led.fuel_segments = _fuel_segments(points, params)
    if params.has_elec:
        led.elec_segments = _elec_segments(points, params)
    if params.has_fuel and params.has_elec:
        led.combined_segments = _combined_segments(points, led.fuel_segments, k)

    for energy, segs in (("fuel", led.fuel_segments), ("elec", led.elec_segments), ("combined", led.combined_segments)):
        for s in segs:
            check = "fuel" if energy == "combined" else energy
            if s.status == "ok" and _summary_overlaps(s, summaries, diffs, check):
                s.status = "incomplete"
            if energy != "combined" and s.cost_cents is None:
                s.cost_cents = _segment_cost(points, s._i, s._j, energy, s.amount) if s.status == "ok" else None
        _apply_marks(segs, marks, energy)
        _detect_anomalies(segs, energy, energy != "combined" and params.has_fuel and params.has_elec, params.kwh_per_l)
        if energy == "elec":
            _detect_idle_anomalies(segs)

    led.hints = _detect_missed(points, params, k)
    return led

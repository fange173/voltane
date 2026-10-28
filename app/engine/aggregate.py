"""
aggregate.py — 按时间范围汇总：里程、花费、能耗（按里程加权）、粗略口径与首页回退
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from app.engine.ledger import EST_QTY_ERR, Ledger, raw_added
from app.engine.types import Segment, worse

# 粗略口径至少需要的里程
ROUGH_MIN_KM = 100.0


# 按期间摊销的费用分类（每公里总成本用）；没填期间时按一年
AMORTIZE_CATS = ("insurance",)
AMORTIZE_DAYS = 365


@dataclass
class Exp:
    id: int
    category: str
    ts: datetime
    amount_cents: int
    odo: Optional[float] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None


def _d(x) -> date:
    return x.date() if isinstance(x, datetime) else x


# ---------- 里程 ----------


def odo_observations(led: Ledger, expenses: list[Exp]) -> list[tuple]:
    obs = [(p.ts, p.odo) for p in led.points if not p.odo_estimated]
    obs += [(e.ts, e.odo) for e in expenses if e.odo is not None]
    obs.sort()
    return obs


def _odo_at(obs: list[tuple], t: datetime, initial_odo: Optional[float]) -> float:
    """t 时刻的里程：在前后两次观测之间按时间线性插值；早于第一次观测时用初始里程。"""
    first_t, first_o = obs[0]
    if t <= first_t:
        return initial_odo if (initial_odo is not None and initial_odo <= first_o) else first_o
    prev_t, prev_o = obs[0]
    for ot, oo in obs[1:]:
        if t <= ot:
            span = (ot - prev_t).total_seconds()
            frac = (t - prev_t).total_seconds() / span if span > 0 else 1
            return prev_o + (oo - prev_o) * frac
        prev_t, prev_o = ot, oo
    return prev_o


def _mono(obs: list[tuple]) -> list[tuple]:
    """里程按时间取累计最大值，避免录错的回退把结果算成负数。"""
    mono = []
    top = None
    for t, o in obs:
        top = o if top is None else max(top, o)
        mono.append((t if isinstance(t, datetime) else datetime.combine(t, datetime.min.time()), top))
    return mono


def mileage(obs: list[tuple], d0: date, d1: date, initial_odo: Optional[float] = None) -> float:
    """范围内行驶的里程。两次记录之间的里程按天数平均分摊，补录前的长时间空档不会全算进某一个月。"""
    if not obs:
        return 0.0
    mono = _mono(obs)
    t0 = datetime.combine(d0, datetime.min.time())
    t1 = datetime.combine(d1 + timedelta(days=1), datetime.min.time())
    return round(max(_odo_at(mono, t1, initial_odo) - _odo_at(mono, t0, initial_odo), 0.0), 1)


def latest_odo(obs: list[tuple]) -> Optional[float]:
    return max((o for _, o in obs), default=None)


def avg_daily_km(obs: list[tuple], today: date, days: int = 60) -> Optional[float]:
    """最近 days 天的日均里程；数据跨度不足 7 天时返回 None。"""
    recent = [(t, o) for t, o in obs if _d(t) >= today - timedelta(days=days)]
    if len(recent) < 2:
        recent = obs[-10:]
    if len(recent) < 2:
        return None
    span_days = (_d(recent[-1][0]) - _d(recent[0][0])).days
    if span_days < 7:
        return None
    km = max(o for _, o in recent) - min(o for _, o in recent)
    return round(km / span_days, 1)


# ---------- 能耗汇总 ----------


def _fracs(s: Segment, d0: Optional[date], d1: Optional[date], mono: Optional[list]) -> tuple:
    """段落在 [d0, d1] 内的比例：(按里程, 按时间)。跨范围的段按段内里程插值分摊，静置部分按天数分摊；
    没有里程观测时按段结束日整段归属。"""
    if d0 is None and d1 is None:
        return 1.0, 1.0
    lo = datetime.combine(d0, datetime.min.time()) if d0 else s.start_ts
    hi = datetime.combine(d1 + timedelta(days=1), datetime.min.time()) if d1 else s.end_ts
    if mono is None:
        d = s.end_ts.date()
        inside = 1.0 if ((d0 is None or d >= d0) and (d1 is None or d <= d1)) else 0.0
        return inside, inside
    a, b = max(lo, s.start_ts), min(hi, s.end_ts)
    total = (s.end_ts - s.start_ts).total_seconds()
    if total <= 0:
        inside = 1.0 if lo <= s.end_ts < hi else 0.0
        return inside, inside
    if b <= a:
        return 0.0, 0.0
    f_time = (b - a).total_seconds() / total
    o0, o1 = _odo_at(mono, s.start_ts, None), _odo_at(mono, s.end_ts, None)
    if o1 - o0 >= 1:
        f_km = min(max((_odo_at(mono, b, None) - _odo_at(mono, a, None)) / (o1 - o0), 0.0), 1.0)
    else:
        f_km = f_time
    return f_km, f_time


def _pack(amount, distance, quality, err) -> dict:
    per100 = amount / distance * 100 if distance >= 1 else None
    return {
        "amount": round(amount, 2),
        "distance": round(distance, 1),
        "per100": round(per100, 2) if per100 is not None else None,
        "quality": quality,
        "err100": round(err / distance * 100, 2) if (err and distance >= 1) else None,
    }


def energy_totals(segs: list[Segment], d0: Optional[date] = None, d1: Optional[date] = None,
                  obs: Optional[list] = None) -> dict:
    """行驶（不含静置和排除段）/ 静置 / 综合 三种口径，按里程加权。
    标成静置但有里程的段：估算的行驶部分进行驶口径（排除时不进），其余进静置。"""
    drive = [0.0, 0.0, "A", 0.0, 0]
    idle = [0.0, 0.0, "A", 0.0, 0]
    allv = [0.0, 0.0, "A", 0.0, 0]
    idle_days = 0.0
    mono = _mono(obs) if obs else None

    def add(t, amount, dist, quality, err):
        t[0] += amount
        t[1] += dist
        t[2] = worse(t[2], quality)
        t[3] += err
        t[4] += 1

    for s in segs:
        if s.status != "ok" or s.amount is None:
            continue
        f_km, f_t = _fracs(s, d0, d1, mono)
        if f_km <= 0 and f_t <= 0:
            continue
        if s.kind == "idle":
            part = s.drive_part or 0.0
            f_drive = f_km if s.distance >= 1 else f_t
            add(allv, part * f_drive + s.idle_amount * f_t, s.distance * f_drive, s.quality, (s.err or 0) * f_t)
            add(idle, s.idle_amount * f_t, 0.0, s.quality, (s.err or 0) * f_t)
            idle_days += s.days * f_t
            if s.drive_part is not None and not s.excluded and f_km > 0:
                add(drive, part * f_km, s.distance * f_km, worse(s.quality, "B"), 0.0)
            continue
        if f_km <= 0:
            continue
        add(allv, s.amount * f_km, s.distance * f_km, s.quality, (s.err or 0) * f_km)
        if not s.excluded:
            add(drive, s.amount * f_km, s.distance * f_km, s.quality, (s.err or 0) * f_km)
    out = {}
    for name, t in (("drive", drive), ("overall", allv)):
        out[name] = _pack(t[0], t[1], t[2], t[3]) if t[4] else None
    out["idle"] = (
        {
            "amount": round(idle[0], 2),
            "days": round(idle_days, 1),
            "per_day": round(idle[0] / idle_days, 2) if idle_days > 0 else None,
            "share": round(idle[0] / allv[0], 3) if allv[0] > 0 else None,
            "count": idle[4],
        }
        if idle[4]
        else None
    )
    return out


def combined_totals(segs: list[Segment], d0=None, d1=None, kwh_per_l: float = 8.9,
                    obs: Optional[list] = None) -> Optional[dict]:
    fuel = elec = dist = 0.0
    cost = 0.0
    cost_ok = True
    quality = "A"
    n = 0
    mono = _mono(obs) if obs else None
    for s in segs:
        if s.status != "ok" or s.elec_kwh is None or s.excluded:
            continue
        f = _fracs(s, d0, d1, mono)[0]
        if f <= 0:
            continue
        fuel += s.amount * f
        elec += s.elec_kwh * f
        dist += s.distance * f
        quality = worse(quality, s.quality)
        if s.cost_cents is None:
            cost_ok = False
        else:
            cost += s.cost_cents * f
        n += 1
    if not n or dist < 1:
        return None
    return {
        "fuel_per100": round(fuel / dist * 100, 2),
        "elec_per100": round(elec / dist * 100, 2),
        "equiv_per100": round((fuel + elec / kwh_per_l) / dist * 100, 2),
        "elec_share": round(elec / (elec + fuel * kwh_per_l), 3) if (elec + fuel) > 0 else None,
        "cost_per_km": round(cost / 100 / dist, 3) if cost_ok else None,
        "distance": round(dist, 1),
        "quality": quality,
        "count": n,
    }


def segment_dict(s: Segment, kwh_per_l: float = 8.9) -> dict:
    d = {
        "energy": s.energy,
        "start_id": s.start_id,
        "end_id": s.end_id,
        "start_ts": s.start_ts.isoformat(timespec="minutes"),
        "end_ts": s.end_ts.isoformat(timespec="minutes"),
        "start_odo": s.start_odo,
        "end_odo": s.end_odo,
        "distance": s.distance,
        "days": round(s.days, 2),
        "amount": s.amount,
        "err": s.err,
        "per100": round(s.per100, 2) if s.per100 is not None else None,
        "err100": round(s.err / s.distance * 100, 2) if (s.err and s.distance >= 1) else None,
        "per_day": round(s.idle_amount / s.days, 2) if (s.kind == "idle" and s.amount and s.days > 0) else None,
        "idle_amount": round(s.idle_amount, 2) if (s.kind == "idle" and s.amount is not None) else None,
        "drive_part": round(s.drive_part, 2) if s.drive_part is not None else None,
        "drive_base100": round(s.drive_base100, 2) if s.drive_base100 is not None else None,
        "quality": s.quality,
        "status": s.status,
        "kind": s.kind,
        "tag": s.tag,
        "excluded": s.excluded,
        "anomaly": s.anomaly,
        "cost_cents": s.cost_cents,
        "cost_per_km": round(s.cost_cents / 100 / s.distance, 3) if (s.cost_cents is not None and s.distance >= 1) else None,
        "record_ids": s.record_ids,
    }
    if s.energy == "combined":
        dist = s.distance
        d["elec_kwh"] = s.elec_kwh
        d["elec_err"] = s.elec_err
        d["elec_quality"] = s.elec_quality
        d["elec_per100"] = round(s.elec_kwh / dist * 100, 2) if (s.elec_kwh is not None and dist >= 1) else None
        if s.amount is not None and s.elec_kwh is not None and dist >= 1:
            d["equiv_per100"] = round((s.amount + s.elec_kwh / kwh_per_l) / dist * 100, 2)
            tot = s.elec_kwh + s.amount * kwh_per_l
            d["elec_share"] = round(s.elec_kwh / tot, 3) if tot > 0 else None
    return d


# ---------- 粗略口径（C） ----------


def rough(led: Ledger, energy: str, start_idx: int, end_idx: Optional[int] = None) -> Optional[dict]:
    """长期口径：起点存量 − 终点存量 + 期间全部补能（含汇总补差）。两端存量未知时只用补能总量。"""
    pts = led.points
    real = [i for i, p in enumerate(pts) if not p.odo_estimated]
    if end_idx is None:
        # 优先取最后一个知道存量的时点作为终点，误差更小
        known = [i for i in real if i > start_idx and pts[i].levels(energy)[0] is not None]
        end_idx = known[-1] if known else (real[-1] if real else None)
    if end_idx is None or end_idx <= start_idx:
        return None
    a, b = pts[start_idx], pts[end_idx]
    distance = b.odo - a.odo
    if distance < 1:
        return None
    la = a.levels(energy)[1]
    lb = b.levels(energy)[0]
    added = sum(raw_added(pts[m].rec, energy) for m in range(start_idx + 1, end_idx))
    key = "fuel_l" if energy == "fuel" else "elec_kwh"
    sum_err = 0.0
    for s in led.summaries:
        if a.ts.date() <= s.end and s.start <= b.ts.date():
            d = led.summary_diffs.get(s.id, {})
            q = d.get(key) or 0
            added += q
            if d.get(energy + "_estimated"):
                sum_err += q * EST_QTY_ERR
    if la is not None and lb is not None:
        amount = la.value - lb.value + added
        err = la.err + lb.err + sum_err
    else:
        amount = added + raw_added(b.rec, energy)
        err = None
    if amount < 0:
        return None
    return {
        "amount": round(amount, 2),
        "distance": round(distance, 1),
        "per100": round(amount / distance * 100, 2),
        "err100": round(err / distance * 100, 2) if err else None,
        "low100": round(max(amount - err, 0) / distance * 100, 2) if err else None,
        "high100": round((amount + err) / distance * 100, 2) if err else None,
        "quality": "C",
        "from_ts": a.ts.isoformat(timespec="minutes"),
        "to_ts": b.ts.isoformat(timespec="minutes"),
        "start_id": a.id,
        "end_id": b.id,
    }


def _tag_index(led: Ledger, tag: str) -> Optional[int]:
    for i, p in enumerate(led.points):
        if p.rec.kind == "snapshot" and p.rec.snapshot_tag == tag and not p.odo_estimated:
            return i
    return None


def _first_real(led: Ledger) -> Optional[int]:
    return next((i for i, p in enumerate(led.points) if not p.odo_estimated), None)


def ledger_start_index(led: Ledger) -> Optional[int]:
    i = _tag_index(led, "ledger_start")
    return i if i is not None else _first_real(led)


def delivery_index(led: Ledger) -> Optional[int]:
    i = _tag_index(led, "delivery")
    return i if i is not None else _first_real(led)


def rough_best(led: Ledger, energy: str) -> Optional[dict]:
    """在「记账期」和「提车至今」两个口径里取相对误差更小的；误差未知按 30% 计，同分取里程长的。"""
    li, di = ledger_start_index(led), delivery_index(led)
    cands = []
    for idx, scope in ((li, "ledger"), (di, "delivery")):
        if idx is None or any(c[1] == idx for c in cands):
            continue
        r = rough(led, energy, idx)
        if r and r["distance"] >= ROUGH_MIN_KM:
            cands.append((r, idx, scope))
    if not cands:
        return None

    def score(c):
        r = c[0]
        rel = (r["err100"] / r["per100"]) if (r["err100"] and r["per100"]) else 0.3
        return (round(rel, 3), -r["distance"])

    best = min(cands, key=score)
    # 提车快照同时也是第一条记录时，两个口径是同一个起点，按「提车至今」显示
    scope = "delivery" if led.points[best[1]].rec.snapshot_tag == "delivery" else best[2]
    return {**best[0], "scope": scope}


# ---------- 首页「最近能耗」回退 ----------


def _hint_code(led: Ledger, energy: str) -> str:
    recs = [p.rec for p in led.points]
    if not recs:
        return "need_start"
    if energy == "fuel":
        if not any(r.kind == "fuel" and r.fuel_full for r in recs):
            return "need_full"
        return "need_second_full"
    if not any(r.soc_before is not None or r.soc_after is not None for r in recs):
        return "need_soc"
    return "need_more"


def latest_energy(led: Ledger, energy: str) -> dict:
    """A → B → C → D 逐级回退。纯电取最近 3 个 A 段按里程加权。"""
    segs = led.fuel_segments if energy == "fuel" else led.elec_segments
    usable = [s for s in segs if s.status == "ok" and s.kind == "drive" and not s.excluded and s.amount is not None]
    a_segs = [s for s in usable if s.quality == "A"]
    if a_segs:
        take = a_segs[-3:] if energy == "elec" else a_segs[-1:]
        amount = sum(s.amount for s in take)
        dist = sum(s.distance for s in take)
        return {
            "quality": "A",
            "per100": round(amount / dist * 100, 2),
            "distance": round(dist, 1),
            "segments": len(take),
            "end_ts": take[-1].end_ts.isoformat(timespec="minutes"),
            "cost_per_km": _cost_per_km(take),
        }
    b_segs = [s for s in usable if s.quality == "B"]
    if b_segs:
        s = b_segs[-1]
        return {
            "quality": "B",
            "per100": round(s.per100, 2),
            "err100": round(s.err / s.distance * 100, 2) if s.err else None,
            "distance": s.distance,
            "segments": 1,
            "end_ts": s.end_ts.isoformat(timespec="minutes"),
            "cost_per_km": _cost_per_km([s]),
        }
    r = rough_best(led, energy)
    if r:
        return {**r, "hint": _hint_code(led, energy)}
    return {"quality": "D", "per100": None, "hint": _hint_code(led, energy)}


def latest_pair(led: Ledger) -> Optional[dict]:
    """油电混动首页：油耗和电耗取同一区间，再给出折合油耗。
    优先最近一段精确综合段，其次最近一段综合段，最后用油耗粗略口径的同一区间算期间用电。"""
    kwh = led.params.kwh_per_l
    comb = [s for s in led.combined_segments if s.status == "ok" and s.elec_kwh is not None and s.distance >= 1]
    exact = [s for s in comb if s.quality == "A"]
    if exact or comb:
        return {**segment_dict((exact or comb)[-1], kwh), "scope": "segment"}
    f = rough_best(led, "fuel")
    if not f:
        return None
    idx = led.point_index()
    e = rough(led, "elec", idx[f["start_id"]], idx[f["end_id"]])
    out = {
        "energy": "combined",
        "start_id": f["start_id"],
        "end_id": f["end_id"],
        "start_ts": f["from_ts"],
        "end_ts": f["to_ts"],
        "distance": f["distance"],
        "amount": f["amount"],
        "per100": f["per100"],
        "low100": f.get("low100"),
        "high100": f.get("high100"),
        "quality": "C",
        "status": "ok",
        "scope": f["scope"],
        "elec_kwh": e["amount"] if e else None,
        "elec_per100": e["per100"] if e else None,
        "elec_quality": "C" if e else None,
    }
    if e:
        out["equiv_per100"] = round((f["amount"] + e["amount"] / kwh) / f["distance"] * 100, 2)
        tot = e["amount"] + f["amount"] * kwh
        out["elec_share"] = round(e["amount"] / tot, 3) if tot > 0 else None
    return out


def _cost_per_km(segs: list[Segment]) -> Optional[float]:
    if any(s.cost_cents is None for s in segs):
        return None
    dist = sum(s.distance for s in segs)
    return round(sum(s.cost_cents for s in segs) / 100 / dist, 3) if dist >= 1 else None


def since_last_full(led: Ledger) -> Optional[dict]:
    """距离上一次加满已行驶的里程（「累计中」提示）。"""
    real = [p for p in led.points if not p.odo_estimated]
    fulls = [p for p in real if p.rec.kind == "fuel" and p.rec.fuel_full]
    if not fulls or not real:
        return None
    return {"km": round(real[-1].odo - fulls[-1].odo, 1), "ts": fulls[-1].ts.isoformat(timespec="minutes")}


# ---------- 现金口径 ----------


def _alloc(total, s_start: date, s_end: date, d0: date, d1: date) -> float:
    """把汇总量按天数平均分摊到 [d0, d1]。"""
    if not total:
        return 0.0
    days = (s_end - s_start).days + 1
    lo, hi = max(s_start, d0), min(s_end, d1)
    if hi < lo or days <= 0:
        return 0.0
    return total * ((hi - lo).days + 1) / days


def amortized_cents(e: Exp, d0: date, d1: date) -> float:
    """每公里成本用的费用口径：保险按保险期间（没填按一年）按天摊到 [d0, d1]，其余按发生日计入。"""
    if e.category in AMORTIZE_CATS:
        start = e.period_start or e.ts.date()
        end = e.period_end or (start + timedelta(days=AMORTIZE_DAYS - 1))
        if end >= start:
            return _alloc(e.amount_cents, start, end, d0, d1)
    return float(e.amount_cents) if d0 <= e.ts.date() <= d1 else 0.0


def cash_and_qty(led: Ledger, expenses: list[Exp], d0: date, d1: date) -> dict:
    fuel_c = elec_c = extra_c = 0
    fuel_l = elec_kwh = 0.0
    fuel_n = elec_n = 0
    home_kwh = 0.0
    sum_fuel_c = sum_elec_c = 0.0
    sum_fuel_l = sum_elec_kwh = 0.0
    for p in led.points:
        r = p.rec
        if not d0 <= r.ts.date() <= d1:
            continue
        extra_c += r.extra_cents or 0
        if r.kind == "fuel":
            fuel_c += r.cost_cents or 0
            fuel_l += r.fuel_l or 0
            fuel_n += 1
        elif r.kind == "charge":
            elec_c += r.cost_cents or 0
            elec_kwh += r.charge_kwh or 0
            elec_n += 1
            if r.charge_type == "home":
                home_kwh += r.charge_kwh or 0
    for s in led.summaries:
        d = led.summary_diffs.get(s.id, {})
        sum_fuel_c += _alloc(d.get("fuel_cents"), s.start, s.end, d0, d1)
        sum_elec_c += _alloc(d.get("elec_cents"), s.start, s.end, d0, d1)
        sum_fuel_l += _alloc(d.get("fuel_l"), s.start, s.end, d0, d1)
        sum_elec_kwh += _alloc(d.get("elec_kwh"), s.start, s.end, d0, d1)
    cats: dict = {}
    amort = 0.0
    for e in expenses:
        amort += amortized_cents(e, d0, d1)
        if d0 <= e.ts.date() <= d1:
            cats[e.category] = cats.get(e.category, 0) + e.amount_cents
    if extra_c:
        cats["parking"] = cats.get("parking", 0) + extra_c
    fuel_total_c = fuel_c + round(sum_fuel_c)
    elec_total_c = elec_c + round(sum_elec_c)
    other_c = sum(cats.values())
    return {
        "fuel_cents": fuel_total_c,
        "elec_cents": elec_total_c,
        "energy_cents": fuel_total_c + elec_total_c,
        "other_cents": other_c,
        "total_cents": fuel_total_c + elec_total_c + other_c,
        "amortized_cents": fuel_total_c + elec_total_c + extra_c + round(amort),
        "summary_cents": round(sum_fuel_c + sum_elec_c),
        "categories": cats,
        "fuel_l": round(fuel_l + sum_fuel_l, 2),
        "elec_kwh": round(elec_kwh + sum_elec_kwh, 2),
        "fuel_count": fuel_n,
        "elec_count": elec_n,
        "home_share": round(home_kwh / elec_kwh, 3) if home_kwh > 0 else None,
    }


def period_report(led: Ledger, expenses: list[Exp], d0: date, d1: date, initial_odo=None) -> dict:
    obs = odo_observations(led, expenses)
    km = mileage(obs, d0, d1, initial_odo)
    cash = cash_and_qty(led, expenses, d0, d1)
    out = {
        "from": d0.isoformat(),
        "to": d1.isoformat(),
        "mileage": km,
        **cash,
        "energy_cost_per_km": round(cash["energy_cents"] / 100 / km, 3) if km >= 1 else None,
        "total_cost_per_km": round(cash["amortized_cents"] / 100 / km, 3) if km >= 1 else None,
        "fuel": energy_totals(led.fuel_segments, d0, d1, obs) if led.params.has_fuel else None,
        "elec": energy_totals(led.elec_segments, d0, d1, obs) if led.params.has_elec else None,
        "combined": (combined_totals(led.combined_segments, d0, d1, led.params.kwh_per_l, obs)
                     if led.combined_segments else None),
    }
    return out


def month_bounds(y: int, m: int) -> tuple:
    d0 = date(y, m, 1)
    d1 = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d0, d1


def months_between(d0: date, d1: date) -> list[tuple]:
    out = []
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def data_span(led: Ledger, expenses: list[Exp]) -> Optional[tuple]:
    dates = [p.ts.date() for p in led.points] + [e.ts.date() for e in expenses]
    dates += [s.start for s in led.summaries] + [s.end for s in led.summaries]
    if not dates:
        return None
    return min(dates), max(dates)

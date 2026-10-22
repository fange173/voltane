"""
seed_demo.py — 生成演示数据：一辆插混（中途接入 + 历史汇总 + 哨兵静置）和一辆纯电，归属 admin。
车名带「演示」，备注带 [demo]；--clear 删除全部演示车辆。

用法：DATABASE_URL=sqlite:////path/to/db python scripts/seed_demo.py [--clear]
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import (  # noqa: E402
    Expense,
    PeriodSummary,
    Place,
    Record,
    Reminder,
    SessionLocal,
    User,
    Vehicle,
    VehicleMember,
    init_db,
)

TAG = "[demo]"


def clear(db) -> int:
    rows = list(db.scalars(select(Vehicle).where(Vehicle.name.like("%演示%"))))
    for v in rows:
        db.query(Reminder).filter(Reminder.vehicle_id == v.id).delete()
        db.delete(v)
    db.commit()
    return len(rows)


def seed_phev(db, admin: User, today: date) -> None:
    rnd = random.Random(7)
    v = Vehicle(name="秦PLUS（演示）", plate="京A·D1234", model="比亚迪 秦PLUS DM-i", energy_type="phev",
                tank_capacity_l=48, battery_usable_kwh=18.3, color="#4F46E5", default_fuel_grade="92",
                initial_odometer_km=12, delivery_date=today - timedelta(days=300), created_by=admin.id)
    db.add(v)
    db.flush()
    db.add(VehicleMember(vehicle_id=v.id, user_id=admin.id, role="owner"))
    home = Place(vehicle_id=v.id, owner_user_id=admin.id, name="家里车位", kind="home_charger", default_charge_type="home",
                 tou_tariff=json.dumps([{"from": "22:00", "to": "08:00", "price": 0.3}, {"from": "08:00", "to": "22:00", "price": 0.62}]),
                 use_count=40)
    gas = Place(vehicle_id=v.id, owner_user_id=admin.id, name="中石化 望京站", kind="gas_station", default_fuel_grade="92",
                last_unit_price=7.62, use_count=3)
    pub = Place(vehicle_id=v.id, owner_user_id=admin.id, name="公司地库充电桩", kind="charge_station",
                default_charge_type="public_ac", last_unit_price=1.2, use_count=6)
    db.add_all([home, gas, pub])
    db.flush()

    ddate = v.delivery_date
    start = today - timedelta(days=120)

    def add(**kw):
        r = Record(vehicle_id=v.id, created_by=admin.id, updated_by=admin.id, payer_user_id=admin.id, **kw)
        db.add(r)
        return r

    dts = datetime.combine(ddate, datetime.min.time()).replace(hour=9)
    add(kind="snapshot", occurred_at=dts, odometer_km=12, snapshot_tag="delivery",
        fuel_level_before_pct=round(5 / 48 * 100, 2), estimated_fields='["fuel_before_pct"]', note=TAG + " 提车")
    add(kind="fuel", occurred_at=dts + timedelta(minutes=1), odometer_km=12, fuel_liters=30, fuel_grade="92",
        paid_cents=22500, amount_cents=22500, fuel_unit_price=7.5, note=TAG + " 提车时加油")
    db.add(PeriodSummary(vehicle_id=v.id, start_date=ddate + timedelta(days=1), end_date=start - timedelta(days=1),
                         mode="extra", fuel_cents=30000, fuel_price=7.6, elec_kwh=1250, elec_cents=45500,
                         note=TAG + " 历史汇总", created_by=admin.id))

    # 从记账起点开始模拟：每天通勤，电量低于 20% 后用油
    odo = 9800.0
    soc = 60.0
    fuel = 0.4 * 48
    add(kind="snapshot", occurred_at=datetime.combine(start, datetime.min.time()).replace(hour=8), odometer_km=odo,
        snapshot_tag="ledger_start", soc_before_pct=soc, fuel_level_before_pct=40, note=TAG)
    k = 0.2
    for i in range(1, 120):
        day = start + timedelta(days=i)
        weekend = day.weekday() >= 5
        dist = rnd.choice([0, 0, 120, 180]) if weekend else rnd.uniform(38, 62)
        winter = day.month in (12, 1, 2)
        per100_ev = (13.5 if not winter else 16.5) + rnd.uniform(-1, 1)
        km_left = dist
        while km_left > 0:
            if soc > 20:
                ev_km = min(km_left, (soc - 20) * k * 0.9 / per100_ev * 100)
                soc -= ev_km * per100_ev / 100 / (k * 0.9)
                km_left -= ev_km
                if km_left < 0.5:
                    break
            else:
                fuel -= km_left * (4.6 + rnd.uniform(-0.3, 0.3)) / 100
                soc -= km_left * 0.01
                km_left = 0
        odo += dist
        # 哨兵：偶尔周末停放两天掉电
        if weekend and dist == 0 and rnd.random() < 0.35 and day.weekday() == 5:
            t0 = datetime.combine(day, datetime.min.time()).replace(hour=9)
            add(kind="snapshot", occurred_at=t0, odometer_km=round(odo), soc_before_pct=round(soc), snapshot_tag="before_park", note=TAG)
            soc -= 6
            add(kind="snapshot", occurred_at=t0 + timedelta(days=1, hours=10), odometer_km=round(odo), soc_before_pct=round(soc),
                snapshot_tag="after_park", note=TAG + " 开了哨兵")
        # 加油：油量低于 25% 加满
        if fuel < 0.25 * 48:
            liters = round(48 - fuel, 2)
            price = round(7.45 + rnd.uniform(0, 0.35), 2)
            paid = round(liters * price * 100) - (1000 if rnd.random() < 0.4 else 0)
            add(kind="fuel", occurred_at=datetime.combine(day, datetime.min.time()).replace(hour=19, minute=10),
                odometer_km=round(odo), fuel_liters=liters, fuel_unit_price=price, fuel_grade="92", fuel_is_full=True,
                amount_cents=round(liters * price * 100), paid_cents=paid, soc_before_pct=round(max(soc, 5)),
                place_id=gas.id, place_name=gas.name, note=TAG)
            fuel = 48
        # 充电：晚上回家电量低于 45% 充到 100%；偶尔公司充
        if dist > 0 and soc < 45:
            public = rnd.random() < 0.15
            s0 = round(max(soc, 3))
            kwh = round((100 - s0) * k * rnd.uniform(0.97, 1.03), 1)
            t = datetime.combine(day, datetime.min.time()).replace(hour=13 if public else 22, minute=30)
            price = 1.2 if public else 0.3
            cents = round(kwh * price * 100)
            add(kind="charge", occurred_at=t, odometer_km=round(odo), soc_before_pct=s0, soc_after_pct=100, charge_kwh=kwh,
                charge_type="public_ac" if public else "home", charge_unit_price=price, paid_cents=cents, amount_cents=cents,
                charge_minutes=int(kwh / 3.3 * 60), place_id=(pub if public else home).id,
                place_name=(pub if public else home).name, extra_fee_cents=500 if public and rnd.random() < 0.5 else None,
                note=TAG)
            soc = 100
    # 费用与提醒
    def exp(days_ago, cat, cents, **kw):
        db.add(Expense(vehicle_id=v.id, category=cat, amount_cents=cents, created_by=admin.id, payer_user_id=admin.id,
                       occurred_at=datetime.combine(today - timedelta(days=days_ago), datetime.min.time()).replace(hour=11),
                       note=kw.pop("note", TAG), **kw))

    exp(95, "maintenance", 48000, title="首保", vendor="比亚迪 4S 店", odometer_km=10500)
    exp(70, "wash", 3500, title="精洗")
    exp(40, "parking", 2000, title="商场停车")
    exp(20, "accessory", 12900, title="行车记录仪")
    exp(10, "toll", 8500, title="京沪高速")
    exp(300, "insurance", 386000, title="首年保险", sub_items=json.dumps([{"name": "交强险", "cents": 95000}, {"name": "商业险", "cents": 291000}], ensure_ascii=False),
        period_start=ddate, period_end=ddate + timedelta(days=364))
    db.add(Reminder(vehicle_id=v.id, title="保险到期", template_key="insurance", due_date=ddate + timedelta(days=364),
                    interval_months=12, lead_days=30, created_by=admin.id))
    db.add(Reminder(vehicle_id=v.id, title="保养", template_key="maintenance", due_odometer_km=20500, interval_km=10000,
                    interval_months=12, due_date=today + timedelta(days=270), lead_km=800, last_done_odometer_km=10500,
                    last_done_at=today - timedelta(days=95), created_by=admin.id))
    db.add(Reminder(vehicle_id=v.id, title="年检", template_key="inspection", due_date=today + timedelta(days=12), created_by=admin.id))


def seed_bev(db, admin: User, today: date) -> None:
    rnd = random.Random(11)
    v = Vehicle(name="Model Y（演示）", plate="沪B·E8888", model="特斯拉 Model Y 长续航", energy_type="bev",
                battery_usable_kwh=75, color="#0EA5E9", default_charge_type="home", initial_odometer_km=3000,
                created_by=admin.id)
    db.add(v)
    db.flush()
    db.add(VehicleMember(vehicle_id=v.id, user_id=admin.id, role="owner"))
    odo = 3000.0
    soc = 80.0
    k = 0.82
    start = today - timedelta(days=75)
    db.add(Record(vehicle_id=v.id, kind="snapshot", occurred_at=datetime.combine(start, datetime.min.time()).replace(hour=8),
                  odometer_km=odo, soc_before_pct=soc, snapshot_tag="ledger_start", created_by=admin.id, note=TAG))
    for i in range(1, 75):
        day = start + timedelta(days=i)
        dist = rnd.uniform(25, 70) if day.weekday() < 5 else rnd.choice([0, 150])
        soc -= dist * (14.5 + rnd.uniform(-1.2, 1.2)) / 100 / (k * 0.92)
        odo += dist
        if dist == 0 and rnd.random() < 0.5:
            t0 = datetime.combine(day, datetime.min.time()).replace(hour=10)
            db.add(Record(vehicle_id=v.id, kind="snapshot", occurred_at=t0, odometer_km=round(odo), soc_before_pct=round(soc),
                          snapshot_tag="before_park", created_by=admin.id, note=TAG))
            soc -= rnd.uniform(4, 9)
            db.add(Record(vehicle_id=v.id, kind="snapshot", occurred_at=t0 + timedelta(hours=30), odometer_km=round(odo),
                          soc_before_pct=round(soc), snapshot_tag="after_park", created_by=admin.id, note=TAG + " 哨兵"))
        if soc < 30:
            s0 = round(soc)
            dc = rnd.random() < 0.25
            target = 80 if dc else 90
            kwh = round((target - s0) * (k * (0.95 if dc else 1.0)), 1)
            price = 1.6 if dc else 0.45
            cents = round(kwh * price * 100)
            db.add(Record(vehicle_id=v.id, kind="charge", occurred_at=datetime.combine(day, datetime.min.time()).replace(hour=21),
                          odometer_km=round(odo), soc_before_pct=s0, soc_after_pct=target, charge_kwh=kwh,
                          charge_type="super" if dc else "home", charge_unit_price=price, paid_cents=cents, amount_cents=cents,
                          place_name="超充站" if dc else "小区充电桩", created_by=admin.id, payer_user_id=admin.id, note=TAG))
            soc = target


def main() -> None:
    init_db()
    with SessionLocal() as db:
        n = clear(db)
        if "--clear" in sys.argv:
            print(f"已删除 {n} 辆演示车辆")
            return
        admin = db.scalar(select(User).where(User.username == "admin"))
        today = date.today()
        seed_phev(db, admin, today)
        seed_bev(db, admin, today)
        db.commit()
        print("已生成演示数据：秦PLUS（演示）、Model Y（演示）")


if __name__ == "__main__":
    main()

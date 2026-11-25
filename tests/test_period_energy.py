"""期间能耗：缺量不作零，能定位的静置耗电按发生时间归属。"""
from datetime import date

import pytest

from app.engine.aggregate import latest_energy, latest_pair, month_bounds, period_report, rough
from app.engine.ledger import build_ledger
from app.engine.types import Summary, VehicleParams
from tests.test_engine import PHEV, charge, fuel, snap


@pytest.mark.parametrize("energy", ["fuel", "elec"])
@pytest.mark.parametrize("qty", [None, 0, 8])
def test_rough_requires_evidence_per_energy(energy, qty):
    other = "elec" if energy == "fuel" else "fuel"
    end = (fuel(2, "2026-03-01", 200, qty, 500, full=False) if energy == "fuel"
           else charge(2, "2026-03-01", 200, None, None, qty, 500))
    led = build_ledger(VehicleParams(**PHEV), [snap(1, "2026-01-01", 0), end])
    for side, known in [(energy, qty), (other, None)]:
        result = latest_energy(led, side)
        assert result["quality"] == ("D" if known is None else "C")
        assert result["per100"] == (None if known is None else known / 2)
    assert latest_pair(led) is None


def test_rough_does_not_ignore_unquantified_refill_between_known_levels():
    led = build_ledger(VehicleParams(**PHEV), [
        snap(1, "2026-01-01", 0, soc=50),
        charge(2, "2026-01-02", 50, None, None, 10, 500),
        charge(3, "2026-01-03", 100, None, None, None, 500),
        snap(4, "2026-01-04", 200, soc=50),
    ])
    assert rough(led, "elec", 0) is None


def test_rough_accepts_stock_difference_and_explicit_summary_zero():
    led = build_ledger(VehicleParams(**PHEV), [
        snap(1, "2026-01-01", 0, soc=50),
        charge(2, "2026-01-02", 100, 30, 70, None, None),
        snap(3, "2026-01-04", 200, soc=50),
    ], summaries=[Summary(1, date(2026, 1, 2), date(2026, 1, 3), fuel_l=0)])
    assert rough(led, "elec", 0)["amount"] == pytest.approx(40 * led.calib.k, abs=0.01)
    assert rough(led, "fuel", 0)["amount"] == 0
    led = build_ledger(VehicleParams(**PHEV), [snap(1, "2026-01-01", 0, soc=0), snap(2, "2026-01-04", 200, soc=0)])
    assert rough(led, "elec", 0)["amount"] == 0


def test_rough_prorates_overlapping_history_and_estimation_error():
    summary = Summary(1, date(2026, 1, 1), date(2026, 1, 10), fuel_cents=10000, fuel_price=5)
    led = build_ledger(VehicleParams(**PHEV, gauge_err_pct=0), [
        snap(1, "2026-01-01", 0, fuel_pct=50), snap(2, "2026-01-04", 100, fuel_pct=50),
        snap(3, "2026-01-05", 100, fuel_pct=50), snap(4, "2026-01-10", 200, fuel_pct=50),
    ], summaries=[summary])
    full = rough(led, "fuel", 0, 3)
    early = rough(led, "fuel", 0, 1)
    late = rough(led, "fuel", 2, 3)
    assert full["amount"] == 20
    assert [early["amount"], late["amount"]] == [8, 12]
    assert early["amount"] + late["amount"] == full["amount"]
    assert [early["err100"], late["err100"]] == pytest.approx([0.4, 0.6])


def test_cross_month_idle_electricity_stays_in_parked_period():
    led = build_ledger(VehicleParams(**PHEV), [
        fuel(1, "2026-01-15", 0, 15, 12000, soc=100),
        snap(2, "2026-01-30", 100, soc=80),
        snap(3, "2026-03-03", 100, soc=48),
        fuel(4, "2026-03-15", 300, 15, 12000, soc=10),
    ])
    [segment] = led.combined_segments
    idle = led.elec_segments[1].idle_amount
    driving = segment.elec_kwh - idle
    months = [period_report(led, [], *month_bounds(2026, m))["combined"] for m in (1, 2, 3)]
    full = period_report(led, [], date(2026, 1, 1), date(2026, 3, 31))["combined"]
    for m, km, days in zip(months, [100, 0, 200], [2, 28, 2]):
        assert m is not None
        assert m["distance"] == km
        assert m["elec_kwh"] == pytest.approx(driving * km / 300 + idle * days / 32, abs=0.01)
        assert m["fuel_l"] == pytest.approx(15 * km / 300)
    assert months[1]["elec_per100"] is None and months[1]["equiv_per100"] is None
    for key, expected in [("distance", 300), ("elec_kwh", segment.elec_kwh), ("fuel_l", 15)]:
        assert sum(m[key] for m in months) == pytest.approx(full[key], abs=0.02)
        assert full[key] == pytest.approx(expected, abs=0.01)
    day = period_report(led, [], date(2026, 2, 15), date(2026, 2, 15))["combined"]
    assert day["elec_kwh"] == pytest.approx(idle / 32, abs=0.01)

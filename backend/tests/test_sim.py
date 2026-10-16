import pytest
from app.sim.engine import SCENARIOS, build_trace, trace_hash, DT_H
from app.analytics.reserve import reserve_horizon, horizon_from_window
from app.analytics.detect import detect


def test_reference_horizon():
    r = reserve_horizon(12000, 200, 850, 3000, 1.0)
    assert r["hours_to_critical"] == pytest.approx(13.846, abs=0.01)
    assert r["hours_to_empty"] == pytest.approx(18.462, abs=0.01)


def test_horizon_states():
    assert reserve_horizon(12000, 800, 650, 3000, 1.0)["state"] == "no_depletion"
    assert reserve_horizon(2500, 200, 850, 3000, 1.0)["state"] == "below_critical"
    assert reserve_horizon(None, None, None, 3000, 0.0)["state"] == "unavailable"
    assert reserve_horizon(12000, 200, 850, 3000, 0.5)["state"] == "unavailable"


def test_seed_repeatable():
    sc = SCENARIOS["power-water"]
    assert trace_hash(build_trace(sc, 120, {})) == trace_hash(build_trace(sc, 120, {}))


def test_mass_conservation_and_bounds():
    sc = SCENARIOS["power-water"]
    rows = build_trace(sc, 200, {})
    for a, b in zip(rows, rows[1:]):
        dv = b["latent"]["volume"] - a["latent"]["volume"]
        exp = (a["latent"]["production"] - a["latent"]["demand"]) * DT_H
        assert dv == pytest.approx(exp, abs=1e-6)
        assert 0 <= b["latent"]["volume"] <= sc.capacity


def test_normal_has_no_incident():
    sc = SCENARIOS["normal"]
    assert detect(build_trace(sc, 300, {}), sc)["state"] == "none"


def test_power_water_sequence():
    sc = SCENARIOS["power-water"]
    d = detect(build_trace(sc, 120, {}), sc)
    assert d["state"] == "active"
    ff = d["first_fired"]
    assert ff["low_voltage"] < ff["low_production"] < ff["low_reserve_horizon"]
    assert abs(ff["low_voltage"] - 25) <= 2 and abs(ff["low_production"] - 34) <= 2 and abs(ff["low_reserve_horizon"] - 42) <= 2


def test_resolution_after_restore_not_before():
    sc = SCENARIOS["power-water"]
    rows = build_trace(sc, 160, {"restore_power_feed": 70})
    d = detect(rows, sc)
    assert d["state"] == "resolved" and d["resolved_step"] > 70
    assert detect(build_trace(sc, 75, {"restore_power_feed": 70}), sc)["state"] == "active"


def test_sensor_fault_suppresses_forecast():
    sc = SCENARIOS["sensor-fault"]
    rows = build_trace(sc, 60, {})
    assert horizon_from_window(rows, sc)["state"] == "unavailable"
    assert horizon_from_window(build_trace(sc, 30, {}), sc)["state"] != "unavailable"


def test_no_latent_in_observations():
    rows = build_trace(SCENARIOS["normal"], 5, {})
    assert "latent" in rows[0] and "latent" not in rows[0]["obs"]

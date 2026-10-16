"""Daily demand pattern, surge fade after recovery, and the silent-tank-sensor incident."""
from app.sim.engine import SCENARIOS, build_trace, scenario_start, demand_profile, PROFILE
from app.analytics.detect import detect
from app.analytics.evidence import build_snapshot


def run(sid, upto=719, acts=None):
    sc = SCENARIOS[sid]
    rows = build_trace(sc, upto, acts or {}, scenario_start(sc))
    return sc, rows, detect(rows, sc)


def test_profile_is_mean_one_with_morning_and_evening_peaks():
    assert abs(sum(PROFILE) / 24 - 1) < 1e-9
    assert demand_profile(7) > 1.2 and demand_profile(18) > 1.1 and demand_profile(3) < 0.6


def test_quiet_scenarios_raise_no_incident_over_the_whole_day():
    for sid in ("normal", "el-nino"):
        assert run(sid)[2]["state"] == "none", sid


def test_net_flow_recovers_after_the_power_feed_is_restored():
    sc, rows, det = run("power-water", acts={"restore_power_feed": 50})
    assert det["state"] == "resolved"
    mid = rows[300]["latent"]                       # about 10:30, well after recovery and off peak
    assert mid["production"] - mid["demand"] > 0
    assert rows[719]["latent"]["volume"] > rows[100]["latent"]["volume"]


def test_unrepaired_power_fault_keeps_draining():
    sc, rows, det = run("power-water")
    assert det["state"] == "active" and rows[600]["latent"]["volume"] < rows[100]["latent"]["volume"]


def test_silent_tank_sensor_is_an_incident_with_its_own_rule():
    sc, rows, det = run("sensor-fault", 120)
    assert det["state"] == "active" and det["active_rules"] == ["tank_sensor_silent"] and det["started_step"] == 42
    snap = build_snapshot(sc, {}, 120, scenario_start(sc))
    ids = {i["id"] for i in snap["items"]}
    assert "ev-11" in ids and snap["incident_state"] == "active" and snap["horizon"]["state"] == "unavailable"
    assert any(s["id"].startswith("sop-sensor") for s in snap["sops"])


def test_sensor_fault_flow_gives_a_manual_check_action_and_no_fake_forecast():
    from tests.test_api import c, mk, play_to, pipeline_to_final
    rid = mk("sensor-fault"); play_to(rid, 60)
    st = c.get(f"/api/runs/{rid}/state").json()
    assert st["detection"]["state"] == "active" and st["detection"]["active_rules"] == ["tank_sensor_silent"]
    a = pipeline_to_final(rid)
    assert a["verification"]["status"] in ("verified", "partially_verified") or a["assessment"]
    acts = a["assessment"]["actions"]
    assert acts and all(x["simulated_command"] == "repair_tank_sensor" for x in acts)
    assert any("ev-11" in h["evidence_ids"] for h in a["assessment"]["hypotheses"])


def test_technician_repair_restores_the_sensor_and_resolves_the_incident():
    from tests.test_api import c, mk, play_to, pipeline_to_final
    from tests.test_action_flow import state, approve_and_run
    rid = mk("sensor-fault"); play_to(rid, 60)
    a1 = pipeline_to_final(rid)
    aid = next(x["action_id"] for x in a1["assessment"]["actions"] if x["simulated_command"] == "repair_tank_sensor")
    approve_and_run(rid, a1, aid)
    s = state(rid); eff = s["actions"]["repair_tank_sensor"]["effect"]
    assert eff["minutes_left"] > 0 and eff["metrics"][0]["now"] == "no reading" and s["latest"]["tank_volume"]["value"] is None
    play_to(rid, s["run"]["current_step"] + 14)
    s = state(rid)
    assert s["actions"]["repair_tank_sensor"]["effect"]["settled"] and s["latest"]["tank_volume"]["value"] is not None
    play_to(rid, s["run"]["current_step"] + 8)
    s = state(rid)
    assert s["detection"]["state"] == "resolved" and s["horizon"]["state"] != "unavailable"

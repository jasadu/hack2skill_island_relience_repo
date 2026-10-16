"""Real-time anchoring: the simulated day starts at the real wall-clock time the run was created."""
import re
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.sim.engine import SCENARIOS, build_trace, clock_label
from app.analytics.evidence import build_snapshot
from app.ai.validate import allowed_numbers
from app.ai.numcheck import unsupported_numbers

c = TestClient(app)


def mk(scn, step):
    rid = c.post("/api/runs", json={"scenario_id": scn}).json()["run_id"]
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    cur = 0
    while cur < step:
        cur = c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": step}).json()["current_step"]
    c.post(f"/api/runs/{rid}/control", json={"cmd": "pause"})
    return rid


def test_run_starts_today_at_the_scenario_hour_in_pht():
    today = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).day
    for scn, hhmm in (("power-water", "05:30"), ("sensor-fault", "16:00"), ("normal", "04:00")):
        rid = c.post("/api/runs", json={"scenario_id": scn}).json()["run_id"]
        s = c.get(f"/api/runs/{rid}/state").json()
        assert re.fullmatch(rf"{today} \w{{3}} 2\d{{3}}, {hhmm} PHT", s["sim_clock"]) and s["sim_clock"] == s["sim_start_clock"]


def test_one_step_is_one_minute_and_series_carries_hhmm():
    rid = c.post("/api/runs", json={"scenario_id": "normal"}).json()["run_id"]
    t0 = datetime.fromisoformat(c.get(f"/api/runs/{rid}/state").json()["sim_time"])
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": 5})
    s = c.get(f"/api/runs/{rid}/state").json()
    assert s["run"]["current_step"] == 5 and datetime.fromisoformat(s["sim_time"]) - t0 == timedelta(minutes=5)
    assert s["series"][0]["t"] != s["series"][-1]["t"] and re.fullmatch(r"\d\d:\d\d", s["series"][-1]["t"])


def test_trace_depends_on_time_of_day_not_on_the_date():
    sc = SCENARIOS["power-water"]
    a, b = build_trace(sc, 90, {}, "2026-10-15T00:00:00+00:00"), build_trace(sc, 90, {}, "2030-01-01T00:00:00+00:00")
    assert [r["obs"] for r in a] == [r["obs"] for r in b] and a[0]["clock"] != b[0]["clock"]
    night = build_trace(SCENARIOS["normal"], 5, {}, "2026-10-14T19:00:00+00:00")    # 03:00 PHT
    peak = build_trace(SCENARIOS["normal"], 5, {}, "2026-10-14T23:00:00+00:00")     # 07:00 PHT
    assert peak[0]["latent"]["demand"] > 2 * night[0]["latent"]["demand"]


def test_evidence_uses_clock_times_and_times_pass_the_number_check():
    sc = SCENARIOS["power-water"]
    snap = build_snapshot(sc, {}, 90, "2026-10-15T00:07:00+00:00")
    assert snap["clock"] == "15 Oct 2026, 09:37 PHT" and snap["items"][0]["time_range"][1] == snap["clock"]
    assert snap["items"][0]["values"]["first_time_below_210"] and "steps" not in snap["items"][0]["label"]
    t = snap["items"][0]["time_range"][1]
    assert unsupported_numbers(f"Voltage stayed low as of {t}.", allowed_numbers(snap)) == []


def test_report_works_without_an_incident_and_lists_planner_detail():
    rid = mk("el-nino", 40)
    assert c.post(f"/api/runs/{rid}/outlook/plan", json={}).status_code == 200
    r = c.get(f"/api/runs/{rid}/report")
    assert r.status_code == 200 and "14-day outlook planner" in r.text and "PHT" in r.text and "No assessed incident" in r.text
    assert "step " not in r.text.split("## Audit")[1]
    assert c.get(f"/api/runs/{rid}/report?format=json").json()["outlook_planner"]["recommendation"]


def test_outlook_recovers_after_the_incident_is_fixed():
    """The projection must follow the live state: degraded supply during the dip, normal again after restore_power_feed."""
    from app.store import store
    rid = mk("power-water", 45)
    bad = c.get(f"/api/runs/{rid}/outlook").json()
    assert bad["supply_note"] and bad["inputs"]["supply_l_day_now"] < 12000
    store().update("runs", rid, {"actions": {"restore_power_feed": 45}})
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    for _ in range(4):
        c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": 70})
    c.post(f"/api/runs/{rid}/control", json={"cmd": "pause"})
    good = c.get(f"/api/runs/{rid}/outlook").json()
    assert good["supply_note"] is None and good["inputs"]["supply_l_day_now"] > 17000
    assert good["baseline"]["p_breach"] < bad["baseline"]["p_breach"] and good["as_of"] != bad["as_of"]

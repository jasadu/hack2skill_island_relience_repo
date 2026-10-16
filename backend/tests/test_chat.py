from fastapi.testclient import TestClient
from app.main import app
from app.ai import tools
from app.sim.engine import SCENARIOS, build_trace
from app.analytics.evidence import build_snapshot

c = TestClient(app)


def mk(scn, step):
    rid = c.post("/api/runs", json={"scenario_id": scn}).json()["run_id"]
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    cur = 0
    while cur < step:
        cur = c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": step}).json()["current_step"]
    c.post(f"/api/runs/{rid}/control", json={"cmd": "pause"})
    return rid


def ask(rid, q, history=None):
    return c.post(f"/api/runs/{rid}/qa", json={"question": q, "history": history or []})


def test_lowest_voltage_comes_from_the_trace_with_step_and_time():
    rid = mk("power-water", 60)
    r = ask(rid, "What was the lowest voltage so far?").json()
    sc = SCENARIOS["power-water"]
    rows = build_trace(sc, 60, {})
    lo = min(rows, key=lambda x: x["obs"]["voltage"][0])
    call = r["tool_calls"][0]
    assert call["name"] == "query_history" and call["result"]["step"] == lo["step"]
    assert call["result"]["value"] == round(lo["obs"]["voltage"][0], 2)
    assert r["verification"]["numbers_ok"], r["verification"]


def test_peak_demand_and_summary_and_short_run_note():
    rid = mk("power-water", 45)
    d = ask(rid, "When did demand peak?").json()
    assert d["tool_calls"][0]["result"]["metric"] == "outflow" and d["tool_calls"][0]["result"]["stat"] == "max"
    s = ask(rid, "Summarize the last 24 hours").json()
    res = s["tool_calls"][0]["result"]
    assert s["tool_calls"][0]["name"] == "summarize_period" and res["covered_hours"] < 24 and "only run" in res["note"]
    assert s["verification"]["numbers_ok"], s["verification"]


def test_silent_sensor_is_reported_not_guessed():
    rid = mk("sensor-fault", 55)
    r = ask(rid, "What was the lowest tank level?").json()
    # readings before the failure exist, so a value is returned; after it, the latest reading is simply not there
    s = tools.dispatch("summarize_period", {"hours": 1}, {"sc": SCENARIOS["sensor-fault"], "rows": build_trace(SCENARIOS["sensor-fault"], 55, {}),
                                                         "snapshot": build_snapshot(SCENARIOS["sensor-fault"], {}, 55)})
    assert s["metrics"]["tank_volume"]["max_step"] < 40          # nothing after the sensor died
    assert r["verification"]["numbers_ok"]


def test_chat_outlook_agrees_with_dashboard():
    rid = mk("el-nino", 40)
    dash = c.get(f"/api/runs/{rid}/outlook").json()["baseline"]["p_breach"]
    r = ask(rid, "What happens in the next 2 weeks with El Niño?").json()
    base = r["tool_calls"][0]["result"]["baseline"]
    assert r["tool_calls"][0]["name"] == "get_outlook" and abs(base["p_breach_pct"] - dash * 100) < 0.06
    assert r["verification"]["numbers_ok"]


def test_chat_history_is_accepted_and_capped_server_side():
    rid = mk("power-water", 30)
    hist = [{"q": f"q{i}", "a": "a" * 1200} for i in range(10)]          # 10 turns: only the last 4 are used
    assert ask(rid, "What is the critical reserve level?", hist).status_code == 200
    huge = [{"q": "q", "a": "a" * 30000}]
    assert ask(rid, "What is the critical reserve level?", huge).status_code == 413


def test_state_exposes_the_chat_cap_and_cap_is_enforced(monkeypatch):
    monkeypatch.setattr("app.config.PER_RUN_QA_CAP", 2)
    rid = mk("power-water", 30)
    assert c.get(f"/api/runs/{rid}/state").json()["run"]["qa_cap"] == 2
    assert ask(rid, "What was the highest voltage?").status_code == 200
    assert ask(rid, "What was the highest voltage?").status_code == 200
    assert ask(rid, "What was the highest voltage?").status_code == 429


def test_outlook_early_step_message_is_not_misleading():
    rid = mk("el-nino", 0)
    r = c.get(f"/api/runs/{rid}/outlook")
    assert r.status_code == 409 and "Collecting telemetry" in r.json()["detail"]

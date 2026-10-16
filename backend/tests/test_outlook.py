import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.sim.engine import SCENARIOS, build_trace
from app.analytics.detect import detect
from app.outlook import model, climate, planner, service
from app.ai.schemas import PlannerAnswer

c = TestClient(app)


def mk(scn="el-nino", step=40):
    rid = c.post("/api/runs", json={"scenario_id": scn}).json()["run_id"]
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    cur = 0
    while cur < step:
        cur = c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": step}).json()["current_step"]
    c.post(f"/api/runs/{rid}/control", json={"cmd": "pause"})
    return rid


def test_el_nino_has_no_incident_today_but_outlook_shows_risk():
    sc = SCENARIOS["el-nino"]
    assert detect(build_trace(sc, 200, {}), sc)["state"] == "none"
    j = c.get(f"/api/runs/{mk()}/outlook").json()
    assert j["baseline"]["p_breach"] > 0.4          # the detector is quiet, the outlook is not
    assert j["label"].startswith("Scenario projection")


def test_projection_is_deterministic_and_more_hours_never_hurt():
    rid = mk()
    a, b = c.get(f"/api/runs/{rid}/outlook").json(), c.get(f"/api/runs/{rid}/outlook").json()
    assert a["baseline"] == b["baseline"]
    risks = [p["p_breach_pct"] for p in a["plans"] if p["conservation_pct"] == 0]
    assert risks == sorted(risks, reverse=True)      # 10 h >= 11 h >= 12 h >= 14 h (common random numbers)


def test_stronger_el_nino_raises_risk():
    rid = mk()
    weak = c.get(f"/api/runs/{rid}/outlook?enso=neutral").json()["baseline"]["p_breach"]
    strong = c.get(f"/api/runs/{rid}/outlook?enso=very_strong").json()["baseline"]["p_breach"]
    assert strong > weak


def test_withholds_when_tank_sensor_is_silent():
    r = c.get(f"/api/runs/{mk('sensor-fault', 55)}/outlook")
    assert r.status_code == 409 and "cannot be computed" in r.json()["detail"]


def test_bad_params_rejected():
    rid = mk()
    assert c.get(f"/api/runs/{rid}/outlook?enso=banana").status_code == 400
    assert c.get(f"/api/runs/{rid}/outlook?beta=0.9").status_code == 400


def test_plan_then_approve_and_stale_plan_refused():
    rid = mk()
    d = c.post(f"/api/runs/{rid}/outlook/plan", json={}).json()
    rec = d["recommendation"]
    assert d["requires_approval"] and d["status"] == "unverified" and rec["target_met"]
    assert rec["verification"]["numbers_ok"] and rec["verification"]["recommended_meets_target"]
    p = rec["plan"]
    assert c.post(f"/api/runs/{rid}/outlook/approve", json={"desal_hours": 10, "conservation_pct": 0}).status_code == 409
    ok = c.post(f"/api/runs/{rid}/outlook/approve", json={"desal_hours": p["desal_hours"], "conservation_pct": p["conservation_pct"]})
    assert ok.status_code == 200
    act = c.get(f"/api/runs/{rid}/outlook").json()["active_plan"]
    assert act["projection"]["p_breach"] <= 0.05


def test_agent_plan_that_fails_resimulation_is_replaced(monkeypatch):
    def bad_agent(ctx):
        r = planner.dispatch("project_plan", {"desal_hours": 10, "conservation_pct": 0}, ctx)
        return PlannerAnswer(recommended_desal_hours=10, recommended_conservation_pct=0, summary="Keep the baseline.",
                             rationale=[], watch_items=[], procedure_ids=[]), [("project_plan", {"desal_hours": 10, "conservation_pct": 0}, r)]
    monkeypatch.setattr(planner, "run_agent", bad_agent)
    rec = c.post(f"/api/runs/{mk()}/outlook/plan", json={}).json()["recommendation"]
    assert rec["overridden"] and rec["target_met"]
    assert rec["plan"]["desal_hours"] > 10 or rec["plan"]["conservation_pct"] > 0


def test_invented_numbers_in_agent_text_are_flagged(monkeypatch):
    def liar(ctx):
        r = planner.dispatch("project_plan", {"desal_hours": 12, "conservation_pct": 10}, ctx)
        return PlannerAnswer(recommended_desal_hours=12, recommended_conservation_pct=10,
                             summary="This plan saves exactly 48213 litres.", rationale=[], watch_items=[], procedure_ids=[]), \
            [("project_plan", {"desal_hours": 12, "conservation_pct": 10}, r)]
    monkeypatch.setattr(planner, "run_agent", liar)
    v = c.post(f"/api/runs/{mk()}/outlook/plan", json={}).json()["recommendation"]["verification"]
    assert not v["numbers_ok"] and 48213.0 in v["unmatched_numbers"]


def test_agent_failure_uses_single_call_gemini_tier(monkeypatch):
    def boom(ctx):
        raise RuntimeError("agent loop broke")
    monkeypatch.setattr(planner, "run_agent", boom)
    d = c.post(f"/api/runs/{mk()}/outlook/plan", json={}).json()
    assert d["answer"] is not None and d["recommendation"]["target_met"] and "agent loop broke" in d["error"]


def test_ai_failure_falls_back_to_code_optimiser(monkeypatch):
    def boom(ctx):
        raise RuntimeError("model down")
    monkeypatch.setattr(planner, "run_agent", boom)
    monkeypatch.setattr(planner, "run_single_shot", boom)
    d = c.post(f"/api/runs/{mk()}/outlook/plan", json={}).json()
    assert d["source"] == "fixed-optimiser" and d["recommendation"]["target_met"] and "model down" in d["error"]


def test_per_run_planner_cap(monkeypatch):
    monkeypatch.setattr("app.config.PER_RUN_OUTLOOK_CAP", 1)
    rid = mk()
    assert c.post(f"/api/runs/{rid}/outlook/plan", json={}).status_code == 200
    assert c.post(f"/api/runs/{rid}/outlook/plan", json={}).status_code == 429


def test_climate_parsing_and_fallback(monkeypatch):
    climate._cache.clear()
    raw = {"daily": {"time": [f"2026-10-{13 + i}" for i in range(16)], "temperature_2m_max": [32.0 + i * 0.1 for i in range(16)],
                     "precipitation_sum": [0.0] * 16}}
    ok = climate.get_forecast(_fetch=lambda: raw)
    assert ok["source"] == "open-meteo" and len(ok["days"]) == 16
    climate._cache.clear()

    def down():
        raise OSError("no network")
    fb = climate.get_forecast(_fetch=down)
    assert fb["source"] == "fallback-climatology" and "unavailable" in fb["note"]
    climate._cache.clear()


def test_real_sdk_path_tool_loop_with_mocked_gemini(monkeypatch):
    """Exercises run_agent's non-fake branch: function_call steps -> function_result replies -> JSON answer."""
    import json
    from types import SimpleNamespace as NS
    monkeypatch.setattr("app.config.FAKE_AI", False)
    seen = []

    def fake_create(**kw):
        seen.append(kw)
        n = len(seen)
        if n == 1:
            assert "CURRENT INPUTS" in kw["input"]              # inputs are supplied up front: no model round trip for them
            return NS(id="i1", steps=[NS(type="function_call", id="c2", name="project_plan",
                                         arguments={"desal_hours": 12, "conservation_pct": 10})], output_text="")
        res = json.loads(seen[1]["input"][0]["result"][0]["text"])
        txt = f"At 12 h/day with 10% conservation the chance of reaching critical is {res['p_breach_pct']:g}%."
        return NS(id="i2", steps=[], output_text=json.dumps({"recommended_desal_hours": 12, "recommended_conservation_pct": 10,
                  "summary": txt, "rationale": [], "watch_items": [], "procedure_ids": []}))
    monkeypatch.setattr(planner, "create", fake_create)
    d = c.post(f"/api/runs/{mk()}/outlook/plan", json={}).json()
    assert d["source"] == "gemini" and d["error"] == ""
    assert [t["name"] for t in d["tool_calls"]] == ["get_outlook_inputs", "project_plan"]
    assert d["recommendation"]["verification"]["numbers_ok"] and d["recommendation"]["plan"] == {"desal_hours": 12.0, "conservation_pct": 10}
    assert len(seen) == 2 and seen[1]["previous_interaction_id"] == "i1" and seen[1]["input"][0]["type"] == "function_result"


def test_degraded_supply_is_flagged_on_the_outlook():
    j = c.get(f"/api/runs/{mk('power-water', 60)}/outlook").json()
    assert j["supply_note"] and "worst case" in j["supply_note"]
    assert c.get(f"/api/runs/{mk()}/outlook").json()["supply_note"] is None

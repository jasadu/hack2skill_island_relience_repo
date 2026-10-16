import os
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)


def mk(scn="power-water"):
    return c.post("/api/runs", json={"scenario_id": scn}).json()["run_id"]


def play_to(rid, step):
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    cur = 0
    while cur < step:
        cur = c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": step}).json()["current_step"]


def pipeline_to_final(rid, force=False):
    for _ in range(6):
        r = c.post(f"/api/runs/{rid}/pipeline/advance", json={"force_live": force}).json()
        if r["stage"] == "final":
            return r
    raise AssertionError("pipeline did not finish")


def test_health_and_no_secrets():
    h = c.get("/api/health").json()
    assert h["ok"] and "key" not in str(h).lower()


def test_tick_idempotent_and_clamped():
    rid = mk()
    assert c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": 3}).json()["current_step"] == 0   # paused
    c.post(f"/api/runs/{rid}/control", json={"cmd": "play"})
    assert c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": 100}).json()["current_step"] == 5   # clamp
    assert c.post(f"/api/runs/{rid}/control", json={"cmd": "tick", "to_step": 5}).json()["current_step"] == 5     # idempotent


def test_normal_no_incident_and_pipeline_409():
    rid = mk("normal")
    play_to(rid, 60)
    assert c.get(f"/api/runs/{rid}/state").json()["detection"]["state"] == "none"
    assert c.post(f"/api/runs/{rid}/pipeline/advance", json={}).status_code == 409


def test_full_flow_clean_path():
    rid = mk()
    play_to(rid, 45)
    st = c.get(f"/api/runs/{rid}/state").json()
    assert st["detection"]["state"] == "active" and set(st["detection"]["active_rules"]) == {"low_voltage", "low_production", "low_reserve_horizon"}
    a = pipeline_to_final(rid)
    assert a["verification"]["status"] == "verified" and a["calls"] == 2        # assess + judge only
    ids = {x["action_id"]: x for x in a["assessment"]["actions"]}
    assert all(x["requires_approval"] for x in ids.values())
    # cannot simulate before approval
    assert c.post(f"/api/runs/{rid}/actions/a-01/simulate").status_code == 409
    assert c.post(f"/api/runs/{rid}/actions/a-01/approve", json={"assessment_id": a["id"]}).status_code == 200
    assert c.post(f"/api/runs/{rid}/actions/a-01/simulate").status_code == 200
    play_to(rid, 130)
    st = c.get(f"/api/runs/{rid}/state").json()
    assert st["detection"]["state"] == "resolved"
    rep = c.get(f"/api/runs/{rid}/report")
    assert rep.status_code == 200 and "demonstration purposes only" in rep.text and a["snapshot_hash"][:16] in rep.text


def test_repair_path_catches_corruption(monkeypatch):
    monkeypatch.setenv("FAKE_AI_CORRUPT", "1")
    rid = mk()
    play_to(rid, 45)
    a = pipeline_to_final(rid, force=True)
    v = a["verification"]
    assert a["calls"] == 4 and v["repaired"] is True          # assess, judge, repair, judge2
    assert v["status"] in ("verified", "verified_with_removals")
    stages = [t["stage"] for t in a["timeline"]]
    assert stages == ["assess", "judge", "repair", "judge2"]
    txt = str(a["assessment"])
    assert "ev-99" not in txt and "Transformer failure" not in txt


def test_cross_visitor_cache():
    r1 = mk(); play_to(r1, 45); a1 = pipeline_to_final(r1)
    r2 = mk(); play_to(r2, 45); a2 = pipeline_to_final(r2)
    assert a1["source"] == "live" and a2["source"] == "cached" and a2["calls"] == 0
    assert a2["snapshot_hash"] == a1["snapshot_hash"]


def test_stale_approval_conflict():
    rid = mk(); play_to(rid, 30)           # checkpoint 25 only
    a = pipeline_to_final(rid)
    play_to(rid, 45)                       # new checkpoints 34, 42
    assert c.post(f"/api/runs/{rid}/actions/a-01/approve", json={"assessment_id": a["id"]}).status_code == 409


def test_budget_exhaustion_falls_back(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "PER_RUN_AI_CAP", 0)
    rid = mk(); play_to(rid, 45)
    a = pipeline_to_final(rid, force=True)
    assert a["ai_status"] == "unavailable" and a["assessment"] is None and a["fallback_checklist"]


def test_qa_whatif_does_not_mutate_and_checks_numbers():
    rid = mk(); play_to(rid, 45)
    before = c.get(f"/api/runs/{rid}/state").json()
    r = c.post(f"/api/runs/{rid}/qa", json={"question": "What happens if demand falls to 650 L/h?"}).json()
    assert r["answer"]["used_what_if"] and r["verification"]["numbers_ok"]
    assert r["tool_calls"][0]["name"] == "calculate_reserve_horizon"
    after = c.get(f"/api/runs/{rid}/state").json()
    assert before["horizon"] == after["horizon"] and before["run"]["current_step"] == after["run"]["current_step"]


def test_reset_gives_new_run_and_drops_old_results():
    rid = mk(); play_to(rid, 45)
    new = c.post(f"/api/runs/{rid}/control", json={"cmd": "reset"}).json()["run_id"]
    assert new != rid
    assert c.get(f"/api/runs/{new}/state").json()["run"]["current_step"] == 0


def test_rate_limit():
    from app import config
    codes = [c.post("/api/runs", json={"scenario_id": "normal"}).status_code for _ in range(25)]
    assert 429 in codes


def test_deep_health_roundtrip():
    r = c.get("/api/health/deep")
    assert r.status_code == 200 and r.json()["ok"] is True

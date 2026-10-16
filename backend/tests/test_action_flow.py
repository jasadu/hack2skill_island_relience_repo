"""One action at a time: simulate, wait for the effect to settle, re-assess, then handle the next action."""
from tests.test_api import c, mk, play_to, pipeline_to_final


def state(rid):
    return c.get(f"/api/runs/{rid}/state").json()


def approve_and_run(rid, a, action_id):
    assert c.post(f"/api/runs/{rid}/actions/{action_id}/approve", json={"assessment_id": a["id"]}).status_code == 200
    assert c.post(f"/api/runs/{rid}/actions/{action_id}/simulate").status_code == 200


def test_effect_is_measured_and_reassessment_follows_the_effect():
    rid = mk(); play_to(rid, 45)
    a1 = pipeline_to_final(rid)
    ids = {x["simulated_command"]: x["action_id"] for x in a1["assessment"]["actions"]}
    assert "restore_power_feed" in ids
    approve_and_run(rid, a1, ids["restore_power_feed"])
    t0 = state(rid)["run"]["current_step"]

    s = state(rid)                                    # effect still landing: no new checkpoint, no stale approvals, nothing to re-assess yet
    eff = s["actions"]["restore_power_feed"]
    assert eff["status"] == "executing" and eff["effect"]["minutes_left"] > 0 and not s["new_evidence"]

    play_to(rid, t0 + 4)
    s = state(rid)
    assert not s["new_evidence"]                      # voltage rule clears here, but the picture is half-finished: still no checkpoint

    play_to(rid, t0 + 20)
    s = state(rid)
    assert s["actions"]["restore_power_feed"]["status"] == "completed" and s["new_evidence"]
    m = {x["name"]: x for x in s["actions"]["restore_power_feed"]["effect"]["metrics"]}
    assert m["voltage"]["before"] < 210 < m["voltage"]["now"] and m["inflow"]["now"] > m["inflow"]["before"] + 300

    a2 = pipeline_to_final(rid)                       # the re-assessment is told what was done and measured
    ev10 = next(i for i in a2["snapshot"]["items"] if i["id"] == "ev-10")
    assert ev10["values"]["executed_count"] == 1 and ev10["values"]["actions"][0]["settled"]
    assert all(x["simulated_command"] != "restore_power_feed" for x in a2["assessment"]["actions"])
    assert "ev-10" in a2["assessment"]["summary"] or "ev-10" in a2["assessment"]["evidence_ids"]
    assert a2["id"] != a1["id"]


def test_old_approval_is_cleared_once_the_effect_lands():
    rid = mk(); play_to(rid, 45)
    a1 = pipeline_to_final(rid)
    ids = {x["simulated_command"]: x["action_id"] for x in a1["assessment"]["actions"]}
    c.post(f"/api/runs/{rid}/actions/{ids['restore_power_feed']}/approve", json={"assessment_id": a1["id"]})
    other = [x for x in a1["assessment"]["actions"] if x["simulated_command"] == "conserve_demand"]
    assert other
    c.post(f"/api/runs/{rid}/actions/{other[0]['action_id']}/approve", json={"assessment_id": a1["id"]})
    c.post(f"/api/runs/{rid}/actions/{ids['restore_power_feed']}/simulate")
    # second approved action can still be simulated while the first is landing (same evidence), but not after the effect arrives
    play_to(rid, state(rid)["run"]["current_step"] + 20)
    assert c.post(f"/api/runs/{rid}/actions/{other[0]['action_id']}/simulate").status_code == 409


def test_action_run_in_the_same_minute_as_the_checkpoint_keeps_its_assessment():
    """Assess at the checkpoint minute, then simulate in that same minute: the assessment must still be current."""
    from app.pipeline import latest_checkpoint
    from app.runs import get_run
    for stop in range(34, 60):
        rid = mk(); play_to(rid, stop)
        cp = latest_checkpoint(get_run(rid))[0]
        if cp != stop:
            continue
        a = pipeline_to_final(rid)
        ids = {x["simulated_command"]: x["action_id"] for x in a["assessment"]["actions"]}
        approve_and_run(rid, a, ids["restore_power_feed"])
        assert not state(rid)["new_evidence"], f"checkpoint {cp} lost after simulating at the same step"
        return
    raise AssertionError("no step with a checkpoint to test")

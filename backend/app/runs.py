"""Sandbox runs, playback control, state view and the approve/simulate action workflow."""
import uuid
from datetime import datetime, timezone
from .store import store
from .sim.engine import SCENARIOS, build_trace, clock_at, settle_steps, scenario_start
from .analytics.effects import effect_of
from .analytics.detect import detect, RULES
from .analytics.reserve import horizon_from_window
from .analytics.evidence import build_snapshot
from . import pipeline, config

SPEEDS = (1, 10, 60)
MAX_CATCHUP = 5
ALLOWED_COMMANDS = ("restore_power_feed", "conserve_demand", "repair_tank_sensor")


def _now():
    return datetime.now(timezone.utc).isoformat()


def audit(run, event, **kw):
    run.setdefault("audit", []).append({"at": _now(), "step": run["current_step"], "event": event, **kw})
    run["audit"] = run["audit"][-200:]


def _start_now(sc):
    """Start of the simulated day: today's real date at the scenario's planned time of day (power-water starts at 05:30, for example)."""
    return scenario_start(sc)


def create_run(scenario_id):
    if scenario_id not in SCENARIOS:
        raise ValueError("unknown scenario")
    sc = SCENARIOS[scenario_id]
    run = {"id": uuid.uuid4().hex[:12], "scenario_id": scenario_id, "seed": sc.seed, "state": "paused",
           "current_step": 0, "speed": 60, "actions": {}, "approvals": {}, "ai_calls": 0, "qa_calls": 0,
           "created_at": _now(), "sim_start": _start_now(sc), "audit": []}
    audit(run, "run_created", scenario=scenario_id)
    store().set("runs", run["id"], run)
    return run


def get_run(run_id):
    return store().get("runs", run_id)


def control(run_id, cmd, to_step=None, speed=None):
    run = get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    sc = SCENARIOS[run["scenario_id"]]
    if cmd == "reset":
        return create_run(run["scenario_id"])
    if cmd == "play":
        run["state"] = "playing"
    elif cmd == "pause":
        run["state"] = "paused"
    elif cmd == "speed":
        if speed not in SPEEDS:
            raise ValueError("speed must be one of 1, 10, 60")
        run["speed"] = speed
    elif cmd == "tick":
        # Idempotent: ignored when paused or not ahead of the current step; catch-up is clamped.
        if run["state"] == "playing" and to_step is not None and to_step > run["current_step"]:
            run["current_step"] = min(to_step, run["current_step"] + MAX_CATCHUP, sc.n_steps - 1)
    else:
        raise ValueError("unknown command")
    audit(run, "control", cmd=cmd) if cmd != "tick" else None
    store().set("runs", run_id, run)
    return run


def _series(rows, n=120):
    out = []
    for r in rows[-n:]:
        o = r["obs"]
        out.append({"step": r["step"], "t": r["hhmm"], **{k: round(o[k][0], 2) if k in o else None
                                          for k in ("tank_volume", "inflow", "outflow", "voltage", "production_power")}})
    return out


def action_status(run, sc, cmd):
    s = run["actions"].get(cmd)
    if s is None:
        return None
    return "completed" if run["current_step"] >= s + settle_steps(sc, cmd) else "executing"


def view(run_id):
    run = get_run(run_id)
    if run is None:
        return None
    sc = SCENARIOS[run["scenario_id"]]
    start = run.get("sim_start")
    rows = build_trace(sc, run["current_step"], run["actions"], start)
    det = detect(rows, sc)
    last = rows[-1]
    h = horizon_from_window(rows, sc)
    fresh = {}
    for ch, (val, unit) in last["obs"].items():
        fresh[ch] = {"value": round(val, 2), "unit": unit, "age_steps": 0}
    if "tank_volume" not in last["obs"]:
        seen = [r["step"] for r in rows if "tank_volume" in r["obs"]]
        fresh["tank_volume"] = {"value": None, "unit": "L", "age_steps": (run["current_step"] - seen[-1]) if seen else None}
    cp, _ = pipeline.latest_checkpoint(run)
    ai = pipeline.get_current(run_id)
    return {
        "run": {**{k: run[k] for k in ("id", "scenario_id", "state", "current_step", "speed", "ai_calls", "qa_calls")}, "qa_cap": config.PER_RUN_QA_CAP},
        "scenario": {"id": sc.id, "title": sc.title, "n_steps": sc.n_steps, "critical_L": sc.critical,
                     "capacity_L": sc.capacity},
        "sim_time": last["sim_time"], "sim_clock": last["clock"], "sim_start_clock": clock_at(start, 0),
        "series": _series(rows),
        "latest": fresh,
        "horizon": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in h.items()},
        "detection": {**{k: det[k] for k in ("state", "started_step", "resolved_step", "active_rules", "first_fired", "severity")},
                      "started_at": clock_at(start, det["started_step"]) if det["started_step"] is not None else None,
                      "resolved_at": clock_at(start, det["resolved_step"]) if det["resolved_step"] is not None else None},
        "incident": ({"id": f"{run_id}-inc1", "checkpoint_step": cp, "state": det["state"]} if cp is not None else None),
        "assessment": ai,
        "new_evidence": bool(ai and ai.get("stage") == "none"),
        "actions": {c: {"step": run["actions"][c], "status": action_status(run, sc, c),
                        "effect": effect_of(sc, rows, c, run["actions"][c], run["current_step"], start)} for c in run["actions"]},
        "approvals": run["approvals"],
    }


class Conflict(Exception):
    pass


def approve(run_id, assessment_id, action_id):
    run = get_run(run_id)
    cp, _ = pipeline.latest_checkpoint(run)
    doc = store().get("assessments", assessment_id)
    if doc is None or doc["run_id"] != run_id or doc["stage"] != "final" or not doc.get("assessment"):
        raise Conflict("no verified assessment to approve against")
    if cp is None or doc["step"] != cp:
        raise Conflict("the evidence has changed since this assessment; re-assess first")
    act = next((a for a in doc["assessment"]["actions"] if a["action_id"] == action_id), None)
    if act is None:
        raise KeyError("action")
    run["approvals"][action_id] = {"assessment_id": assessment_id, "snapshot_hash": doc["snapshot_hash"],
                                   "command": act["simulated_command"], "approved_at": _now(), "approved_step": run["current_step"]}
    audit(run, "action_approved", action_id=action_id, command=act["simulated_command"])
    store().set("runs", run_id, run)
    return run["approvals"][action_id]


def simulate(run_id, action_id):
    run = get_run(run_id)
    ap = run["approvals"].get(action_id)
    if not ap:
        raise Conflict("action is not approved")
    cmd = ap["command"]
    if cmd not in ALLOWED_COMMANDS:
        raise Conflict("this action has no simulated command")
    cp, _ = pipeline.latest_checkpoint(run)
    doc = store().get("assessments", ap["assessment_id"])
    if doc is None or doc["step"] != cp:
        raise Conflict("approval is stale: the evidence has changed; re-assess and re-approve")
    if cmd in run["actions"]:
        return {"command": cmd, "step": run["actions"][cmd], "note": "already executed"}
    run["actions"][cmd] = run["current_step"]
    audit(run, "action_simulated", action_id=action_id, command=cmd)
    store().set("runs", run_id, run)
    return {"command": cmd, "step": run["current_step"]}

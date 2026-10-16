"""Evidence registry: fixed-order items with stable IDs ev-01..ev-11 (ev-11 is the sensor rule). Deterministic for (scenario, seed, step, actions)."""
import hashlib, json
from .detect import RULES, RULE_LABELS, detect
from .reserve import horizon_from_window
from ..sim.engine import build_trace, clock_at, settle_steps
from .effects import effect_of
from .. import sops as sopmod


RULE_EV = {"low_voltage": "ev-06", "low_production": "ev-07", "low_reserve_horizon": "ev-08", "tank_sensor_silent": "ev-11"}


def _r(x, n=2):
    return None if x is None else round(float(x), n)


def _window(rows, ch, n=10):
    vals = [(r["step"], r["obs"][ch][0]) for r in rows[-n:] if ch in r["obs"]]
    if not vals:
        return None
    v = [x for _, x in vals]
    return {"first_step": vals[0][0], "last_step": vals[-1][0], "min": _r(min(v)), "mean": _r(sum(v) / len(v)),
            "last": _r(v[-1]), "readings": len(v)}


def checkpoints(sc, actions, upto, start=None):
    """Steps at which a new assessment is meaningful: rule-set changes, executed actions, resolution."""
    rows = build_trace(sc, upto, actions, start)
    det = detect(rows, sc)
    steps = set(det["change_steps"])
    if det["resolved_step"] is not None:
        steps.add(det["resolved_step"])
    # While an executed action is still taking effect the picture is half-finished: no checkpoint until it has settled.
    windows = [(s, s + settle_steps(sc, c)) for c, s in actions.items() if s <= upto]
    steps = {k for k in steps if not any(a < k < b for a, b in windows)} | {b for _a, b in windows if b <= upto}
    return sorted(steps), det


def build_snapshot(sc, actions, step, start=None):
    acts = {c: s for c, s in actions.items() if s <= step}
    rows = build_trace(sc, step, acts, start)
    det = detect(rows, sc)
    h = horizon_from_window(rows, sc)
    last = rows[-1]
    first_below_v = next((r["step"] for r in rows if r["obs"]["voltage"][0] < 210), None)
    first_low_in = next((r["step"] for r in rows if r["obs"]["inflow"][0] < 400), None)
    tank_age = None
    tank_steps = [r["step"] for r in rows if "tank_volume" in r["obs"]]
    if tank_steps:
        tank_age = step - tank_steps[-1]

    items = [
        {"id": "ev-01", "kind": "telemetry_window", "label": "Supply voltage, last 10 minutes (V)",
         "step_range": [max(0, step - 9), step],
         "values": {**(_window(rows, "voltage") or {}), "first_step_below_210": first_below_v}},
        {"id": "ev-02", "kind": "telemetry_window", "label": "Water inflow, last 10 minutes (L/h)",
         "step_range": [max(0, step - 9), step],
         "values": {**(_window(rows, "inflow") or {}), "first_step_below_400": first_low_in}},
        {"id": "ev-03", "kind": "telemetry_window", "label": "Water outflow (demand), last 10 minutes (L/h)",
         "step_range": [max(0, step - 9), step], "values": _window(rows, "outflow") or {}},
        {"id": "ev-04", "kind": "telemetry_window", "label": "Tank volume, last 10 minutes (L)",
         "step_range": [max(0, step - 9), step], "values": _window(rows, "tank_volume") or {"status": "no readings"}},
    ]
    calc = {k: (_r(v, 3) if isinstance(v, float) else v) for k, v in h.items()}
    items.append({"id": "ev-05", "kind": "calculation", "label": "Reserve horizon (calculated, inflow and day-average demand held constant)",
                  "step_range": [max(0, step - 9), step], "values": calc})
    for rule in RULES:
        fs = det["first_fired"][rule]
        items.append({"id": RULE_EV[rule], "kind": "detection_rule", "label": f"Rule: {RULE_LABELS[rule]}",
                      "step_range": [fs, step] if fs is not None else [step, step],
                      "values": {"fired": fs is not None and fs <= step, "first_sustained_step": fs,
                                 "active_now": rule in det["active_rules"]}})
    items.append({"id": "ev-09", "kind": "data_quality", "label": "Data quality and sensor freshness",
                  "step_range": [step, step],
                  "values": {"horizon_coverage": h.get("coverage"), "tank_sensor_age_steps": tank_age,
                             "tank_sensor_missing": tank_age is None,
                             "readings_per_channel_in_window": min(10, step + 1)}})
    def at(k):
        return None if k is None else clock_at(start, k)
    for it in items:                       # the assessor reads clock time and date, never step numbers
        it["time_range"] = [at(it["step_range"][0]), at(it["step_range"][1])]
    items[0]["values"]["first_time_below_210"] = at(first_below_v)
    items[1]["values"]["first_time_below_400"] = at(first_low_in)
    for it in items:
        if it["kind"] == "detection_rule":
            it["values"]["first_sustained_at"] = at(it["values"].get("first_sustained_step"))
    done = [effect_of(sc, rows, c, s, step, start) for c, s in sorted(acts.items(), key=lambda kv: kv[1])]
    items.append({"id": "ev-10", "kind": "action_effect", "label": "Simulated actions already executed and their measured effect",
                  "step_range": [min(acts.values()) if acts else step, step], "time_range": [at(min(acts.values())) if acts else at(step), at(step)],
                  "values": {"executed_count": len(done), "actions": done, "note": "Executed actions are done: do not recommend them again."
                             if done else "No simulated action has been executed yet."}})
    fired = [r for r in RULES if det["first_fired"][r] is not None and det["first_fired"][r] <= step]
    sensor_problem = h["state"] == "unavailable"
    sop_secs = sopmod.sops_for(fired, sensor_problem, det["state"] == "resolved")
    digest = {"scenario": sc.id, "seed": sc.seed, "step": step, "actions": sorted(acts.items()), "items": items,
              "sops": [s["id"] for s in sop_secs]}
    snap_hash = hashlib.sha256(json.dumps(digest, sort_keys=True, default=str).encode()).hexdigest()
    return {"snapshot_hash": snap_hash, "step": step, "sim_time": last["sim_time"], "clock": last["clock"], "incident_state": det["state"],
            "active_rules": det["active_rules"], "severity": det["severity"], "items": items,
            "sops": [{"id": s["id"], "title": s["title"], "text": s["text"], "banner": s["banner"]} for s in sop_secs],
            "actions_executed": acts, "horizon": calc}


def numeric_leaves(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out += [float(obj), round(float(obj), 1), round(float(obj), 0)]
    elif isinstance(obj, dict):
        for v in obj.values():
            numeric_leaves(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            numeric_leaves(v, out)
    return out

"""Measured effect of simulated actions: before vs now, straight from the observed telemetry (no AI)."""
from ..sim.engine import clock_at, settle_steps

METRICS = {"restore_power_feed": (("voltage", "V"), ("inflow", "L/h")), "conserve_demand": (("outflow", "L/h"),), "repair_tank_sensor": (("tank_volume", "L"),)}
TITLES = {"restore_power_feed": "Power feed restoration", "conserve_demand": "Demand conservation", "repair_tank_sensor": "Tank sensor repair"}


def effect_of(sc, rows, cmd, s, step, start):
    """rows must cover steps 0..step. before = reading when the action ran; now = latest reading."""
    ready = s + settle_steps(sc, cmd)
    now = rows[min(step, len(rows) - 1)]["obs"]
    before = rows[min(s, len(rows) - 1)]["obs"]
    metrics = []
    for ch, u in METRICS.get(cmd, ()):
        if cmd == "repair_tank_sensor":                      # the point is the reading coming back, so silence is a value too
            metrics.append({"name": ch, "unit": u, "before": round(before[ch][0], 1) if ch in before else "no reading",
                            "now": round(now[ch][0], 1) if ch in now else "no reading"})
        elif ch in before and ch in now:
            metrics.append({"name": ch, "unit": u, "before": round(before[ch][0], 1), "now": round(now[ch][0], 1)})
    return {"command": cmd, "title": TITLES.get(cmd, cmd), "executed_at": clock_at(start, s), "settled": step >= ready,
            "ready_at": clock_at(start, ready), "minutes_left": max(0, ready - step), "metrics": metrics}

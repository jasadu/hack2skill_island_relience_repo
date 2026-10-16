from .reserve import horizon_from_window

RULES = ("low_voltage", "low_production", "low_reserve_horizon", "tank_sensor_silent")
RULE_LABELS = {
    "low_voltage": "Supply voltage below 210 V for 3 consecutive steps",
    "low_production": "Water inflow below 400 L/h for 3 consecutive steps",
    "low_reserve_horizon": "Hours to critical reserve below 16 h for 3 consecutive steps",
    "tank_sensor_silent": "Tank level sensor sent no reading for 3 consecutive steps",
}


def per_step_flags(rows, sc):
    out = []
    for i, r in enumerate(rows):
        h = horizon_from_window(rows[max(0, i - 9): i + 1], sc)
        out.append({"step": r["step"],
                    "low_voltage": r["obs"]["voltage"][0] < 210,
                    "low_production": r["obs"]["inflow"][0] < 400,
                    "low_reserve_horizon": h["state"] == "depleting" and h["hours_to_critical"] < 16,
                    "tank_sensor_silent": "tank_volume" not in r["obs"],
                    "horizon": h})
    return out


def _sustained(flags, key, i, n=3):
    return i + 1 >= n and all(f[key] for f in flags[i - n + 1: i + 1])


def detect(rows, sc):
    """Pure function over the trace. One incident per run in the MVP."""
    flags = per_step_flags(rows, sc)
    start = resolved = None
    healthy = 0
    change_steps, prev_active = [], ()
    for i, f in enumerate(flags):
        active = tuple(k for k in RULES if _sustained(flags, k, i))
        if start is None:
            if active:
                start = i
                change_steps.append(i)
                prev_active = active
            continue
        if resolved is None and active != prev_active and active:
            change_steps.append(i)
            prev_active = active
        healthy = healthy + 1 if not any(f[k] for k in RULES) else 0
        if healthy >= 5 and resolved is None:
            resolved = i
    i = len(flags) - 1
    active_now = [k for k in RULES if _sustained(flags, k, i)] if flags else []
    first_fired = {k: next((j for j in range(len(flags)) if _sustained(flags, k, j)), None) for k in RULES}
    state = "none" if start is None else ("resolved" if resolved is not None else "active")
    return {"state": state, "started_step": start, "resolved_step": resolved, "active_rules": active_now,
            "first_fired": first_fired, "change_steps": change_steps, "flags": flags,
            "severity": {0: "info", 1: "watch", 2: "warning"}.get(len(active_now), "high")}

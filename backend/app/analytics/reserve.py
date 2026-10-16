def reserve_horizon(volume, mean_in, mean_out, critical, coverage, min_coverage=0.8):
    base = {"critical_L": critical, "coverage": round(coverage, 3), "assumption": "inflow and day-average demand held constant"}
    if volume is None or mean_in is None or mean_out is None or coverage < min_coverage:
        return {**base, "state": "unavailable"}                      # never report zero when we do not know
    net = mean_out - mean_in
    if volume <= critical:
        return {**base, "state": "below_critical", "hours_to_critical": 0.0,
                "hours_to_empty": (volume / net if net > 0 else None), "net_drawdown_lph": net}
    if net <= 0:
        return {**base, "state": "no_depletion", "net_drawdown_lph": net}
    return {**base, "state": "depleting", "net_drawdown_lph": net,
            "hours_to_critical": (volume - critical) / net, "hours_to_empty": volume / net}


def _day_average(r):
    """Outflow reading divided by the daily demand pattern at that time = what demand would be on an average hour."""
    from datetime import datetime
    from ..sim.engine import demand_profile, hour_of
    v = r["obs"]["outflow"][0]
    return v / max(0.3, demand_profile(hour_of(datetime.fromisoformat(r["sim_time"])))) if r.get("sim_time") else v


def horizon_from_window(rows, sc, window=10, max_age=2):
    """rows: trace rows ending at the step of interest (only the last `window` are used)."""
    w = rows[-window:]
    ins = [r["obs"]["inflow"][0] for r in w if "inflow" in r["obs"]]
    outs = [_day_average(r) for r in w if "outflow" in r["obs"]]       # day-average demand, so a morning peak is not projected flat for hours
    vols = [(r["step"], r["obs"]["tank_volume"][0]) for r in w if "tank_volume" in r["obs"]]
    if not vols or rows[-1]["step"] - vols[-1][0] > max_age:         # stale or missing tank sensor
        return reserve_horizon(None, None, None, sc.critical, 0.0)
    cov = min(len(ins), len(outs)) / window
    return {**reserve_horizon(vols[-1][1], sum(ins) / len(ins), sum(outs) / len(outs), sc.critical, cov),
            "window_steps": len(w), "volume_step": vols[-1][0]}

"""Probabilistic 14-day reserve projection. Pure code: no AI, deterministic for a given seed.

Each path draws a heat-driven demand multiplier, plant outages and efficiency noise ONCE (common random numbers),
so every plan is evaluated against the same futures and plans are directly comparable.
All coefficients are labelled assumptions and are returned to the UI.
"""
import hashlib, json, math, random
from dataclasses import dataclass, asdict
from datetime import datetime
from ..sim.engine import demand_profile, hour_of

H0 = 10.0            # baseline desalination hours per day
H_MAX = 16.0
C_MAX = 20.0         # max demand conservation, percent
TARGET_BREACH = 0.05


@dataclass(frozen=True)
class Assumptions:
    days: int = 14
    beta_per_c: float = 0.04       # demand change per deg C above today's temperature (assumed)
    enso_bias_c: float = 1.2
    rain_relief: float = 0.03      # demand reduction on days with >= 10 mm forecast rain (assumed)
    outage_prob: float = 0.05      # chance per day of a plant outage (assumed)
    demand_sigma: float = 0.04     # day-to-day demand noise (lognormal)
    eff_sigma: float = 0.03        # plant efficiency noise
    temp_sigma0: float = 0.5       # forecast error (deg C) at day 0, growing with lead time
    temp_sigma_slope: float = 0.12
    temp_persistence: float = 0.8  # day-to-day persistence of the temperature error (heat waves last)


def derive_inputs(rows, sc, window=10, max_age=3):
    """Current state from OBSERVED telemetry only. Returns None when the tank sensor is stale or missing."""
    w = rows[-window:]
    vols = [(r["step"], r["obs"]["tank_volume"][0]) for r in w if "tank_volume" in r["obs"]]
    ins = [r["obs"]["inflow"][0] for r in w if "inflow" in r["obs"]]
    outs = [r["obs"]["outflow"][0] for r in w if "outflow" in r["obs"]]
    pw = [r["obs"]["production_power"][0] for r in w if "production_power" in r["obs"]]
    if not vols or rows[-1]["step"] - vols[-1][0] > max_age or len(ins) < window * 0.8 or len(outs) < window * 0.8:
        return None
    recent = ins[-5:]                       # supply follows the last 5 minutes so a repair shows up quickly
    inflow, outflow = sum(recent) / len(recent), sum(outs[-5:]) / len(outs[-5:])
    # Demand follows the time of day, so a spot reading at 03:00 or 07:00 must not be projected flat for 14 days: use the day average.
    if w[-1].get("sim_time"):
        outflow /= max(0.3, demand_profile(hour_of(datetime.fromisoformat(w[-1]["sim_time"]))))
    if inflow <= 1:
        return None
    return {"level_l": round(vols[-1][1], 1), "demand_l_day": round(outflow * 24, 1), "supply_l_day_now": round(inflow * 24, 1),
            "run_rate_lph": round(inflow * 24 / H0, 1),
            "kw_per_lph": round((sum(pw) / len(pw)) / inflow, 5) if pw else 0.0225,
            "capacity_l": sc.capacity, "critical_l": sc.critical, "baseline_hours": H0}


def make_paths(inputs, forecast_days, a: Assumptions, n, seed):
    """Returns n paths; each is a list (per day) of (demand_l, supply_per_hour_factor)."""
    fc = [d["tmax_c"] for d in forecast_days]
    rain = [d["rain_mm"] for d in forecast_days]
    t_ref = sum(fc[:3]) / min(3, len(fc))
    paths, temps = [], []
    for i in range(n):
        rng = random.Random(f"{seed}:{i}")
        err, path, tpath = 0.0, [], []
        for d in range(1, a.days + 1):
            idx = min(d, len(fc) - 1)
            sig = a.temp_sigma0 + a.temp_sigma_slope * d
            err = a.temp_persistence * err + math.sqrt(1 - a.temp_persistence ** 2) * sig * rng.gauss(0, 1)
            temp = fc[idx] + a.enso_bias_c * d / a.days + err
            mult = 1 + a.beta_per_c * (temp - t_ref)
            if idx < len(rain) and rain[idx] >= 10:
                mult -= a.rain_relief
            mult = min(1.8, max(0.7, mult))
            demand = inputs["demand_l_day"] * mult * math.exp(rng.gauss(0, a.demand_sigma))
            avail = 1.0 - (rng.uniform(0.25, 0.6) if rng.random() < a.outage_prob else 0.0)
            eff = 1 + rng.gauss(0, a.eff_sigma)
            path.append((demand, avail * eff))
            tpath.append(temp)
        paths.append(path)
        temps.append(tpath)
    return paths, temps, t_ref


def _q(sorted_vals, p):
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))]


def evaluate(paths, inputs, hours, conserve_pct):
    cap, crit, days = inputs["capacity_l"], inputs["critical_l"], len(paths[0])
    c = conserve_pct / 100.0
    run_l_per_h = inputs["run_rate_lph"]
    levels = [[] for _ in range(days)]
    first, unmet_tot = [], []
    for path in paths:
        lvl, f, un = inputs["level_l"], None, 0.0
        for d, (dem, sf) in enumerate(path):
            lvl += run_l_per_h * hours * sf - dem * (1 - c)
            if lvl < 0:
                un += -lvl
                lvl = 0.0
            lvl = min(cap, lvl)
            levels[d].append(lvl)
            if f is None and lvl <= crit:
                f = d + 1
        first.append(f)
        unmet_tot.append(un)
    n = len(paths)
    fan = []
    for d in range(days):
        s = sorted(levels[d])
        fan.append({"day": d + 1, "p10": round(_q(s, .1), 0), "p50": round(_q(s, .5), 0), "p90": round(_q(s, .9), 0)})
    breach_by_day = [round(sum(1 for f in first if f is not None and f <= d + 1) / n, 4) for d in range(days)]
    inf = days + 1
    fs = sorted(f if f is not None else inf for f in first)
    p_breach = breach_by_day[-1]
    kwh = hours * run_l_per_h * inputs["kw_per_lph"]
    base_kwh = H0 * run_l_per_h * inputs["kw_per_lph"]
    return {"desal_hours": hours, "conservation_pct": conserve_pct, "p_breach": p_breach, "breach_by_day": breach_by_day,
            "breach_day_p10": (_q(fs, .1) if _q(fs, .1) <= days else None),
            "breach_day_p50": (_q(fs, .5) if _q(fs, .5) <= days else None),
            "fan": fan, "end_p10_l": fan[-1]["p10"], "end_p50_l": fan[-1]["p50"],
            "unmet_p90_l": round(_q(sorted(unmet_tot), .9), 0), "kwh_per_day": round(kwh, 1),
            "extra_kwh_per_day": round(kwh - base_kwh, 1)}


def burden(plan):
    """Assumed cost weights for ranking plans that meet the target (shown to the user): kWh/day plus 6 per % conservation."""
    return plan["extra_kwh_per_day"] + 6.0 * plan["conservation_pct"]


def search(paths, inputs, target=TARGET_BREACH):
    cands = []
    h = H0
    while h <= H_MAX + 1e-9:
        for c in (0, 5, 10, 15, 20):
            cands.append(evaluate(paths, inputs, h, c))
        h += 1
    ok = sorted((p for p in cands if p["p_breach"] <= target), key=lambda p: (burden(p), p["desal_hours"]))
    best = ok[0] if ok else min(cands, key=lambda p: (p["p_breach"], burden(p)))
    return best, bool(ok), cands


def seed_for(inputs, forecast_days, a: Assumptions):
    blob = json.dumps({"i": {k: round(v, 0) if isinstance(v, float) else v for k, v in inputs.items()},
                       "t": [(round(d["tmax_c"], 1), round(d["rain_mm"], 0)) for d in forecast_days], "a": asdict(a)}, sort_keys=True)
    return int(hashlib.sha256(blob.encode()).hexdigest()[:12], 16)


def compact(p):
    """Planner/tool-facing summary: percentages are precomputed so the model never does arithmetic."""
    return {"desal_hours": p["desal_hours"], "conservation_pct": p["conservation_pct"],
            "p_breach_pct": round(p["p_breach"] * 100, 1), "breach_day_p10": p["breach_day_p10"], "breach_day_p50": p["breach_day_p50"],
            "day14_level_p10_l": round(p["end_p10_l"], -1), "day14_level_p50_l": round(p["end_p50_l"], -1),
            "unmet_demand_p90_l": round(p["unmet_p90_l"], -1), "kwh_per_day": p["kwh_per_day"],
            "extra_kwh_per_day": p["extra_kwh_per_day"]}

"""Outlook workflow on top of a run: view, planner agent, approve. Deterministic except the planner's choice of plans to test."""
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from .. import config
from ..store import store
from ..sim.engine import SCENARIOS, build_trace
import logging
from . import climate, model, planner

log = logging.getLogger("outlook")

STANDARD_PLANS = ((10, 0), (11, 0), (12, 0), (12, 10), (14, 0))


class Unavailable(Exception):
    pass


class Conflict(Exception):
    pass


class Budget(Exception):
    pass


def N0(x):
    return f"{x:,.0f}"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _params(enso, beta):
    if enso not in climate.ENSO_BIAS_C:
        raise ValueError("enso must be one of " + ", ".join(climate.ENSO_BIAS_C))
    beta = 0.04 if beta is None else float(beta)
    if not 0.0 <= beta <= 0.10:
        raise ValueError("beta must be between 0 and 0.10")
    return model.Assumptions(enso_bias_c=climate.ENSO_BIAS_C[enso], beta_per_c=beta)


def build(run, enso="strong", beta=None, n=None):
    a = _params(enso, beta)
    sc = SCENARIOS[run["scenario_id"]]
    rows = build_trace(sc, run["current_step"], run["actions"], run.get("sim_start"))
    inputs = model.derive_inputs(rows, sc)
    if inputs is None and run["current_step"] < 9:
        raise Unavailable("Collecting telemetry: the outlook needs about 10 simulated minutes of readings. Press Play.")
    if inputs is None:
        raise Unavailable("The tank sensor or flow data is stale or missing, so a reserve outlook cannot be computed. Nothing is assumed.")
    fc = climate.get_forecast()
    days = fc["days"]
    seed = model.seed_for(inputs, days, a)
    paths, temps, t_ref = model.make_paths(inputs, days, a, n or config.OUTLOOK_PATHS, seed)
    return {"as_of": rows[-1]["clock"], "sc": sc, "inputs": inputs, "forecast": fc, "assumptions": a, "paths": paths, "temps": temps, "t_ref": t_ref,
            "seed": seed, "enso": enso}


def _climate_summary(b):
    d = b["forecast"]["days"][: b["assumptions"].days + 1]
    return {"source": b["forecast"]["source"], "tmax_min_c": round(min(x["tmax_c"] for x in d), 1),
            "tmax_max_c": round(max(x["tmax_c"] for x in d), 1), "reference_tmax_c": round(b["t_ref"], 1),
            "enso_level": b["enso"], "enso_extra_warming_c": b["assumptions"].enso_bias_c}


def _with_fan(p):
    return {**model.compact(p), "fan": p["fan"]}


def view(run_id, run, enso="strong", beta=None):
    b = build(run, enso, beta)
    inp, a = b["inputs"], b["assumptions"]
    base = model.evaluate(b["paths"], inp, model.H0, 0)
    active = run.get("outlook_plan")
    act_eval = model.evaluate(b["paths"], inp, active["desal_hours"], active["conservation_pct"]) if active else None
    best, met, _ = model.search(b["paths"], inp)
    temp_band = []
    for d in range(a.days):
        s = sorted(t[d] for t in b["temps"])
        temp_band.append({"day": d + 1, "p10": round(model._q(s, .1), 1), "p50": round(model._q(s, .5), 1), "p90": round(model._q(s, .9), 1)})
    fc = b["forecast"]
    return {
        "run_id": run_id, "step": run["current_step"], "as_of": b["as_of"], "available": True,
        "inputs": inp, "target_pct": model.TARGET_BREACH * 100,
        "climate": {"source": fc["source"], "note": fc["note"], "fetched_at": fc["fetched_at"],
                    "location": {"lat": config.OUTLOOK_LAT, "lon": config.OUTLOOK_LON, "label": "Philippine reference point (the island is fictional)"},
                    "days": fc["days"][: a.days + 1], "reference_tmax_c": round(b["t_ref"], 1), "temp_band": temp_band},
        "enso": {"level": enso, "label": climate.ENSO_LABEL[enso], "extra_warming_c": a.enso_bias_c, "reference": climate.ENSO_REFERENCE,
                 "levels": [{"id": k, "label": climate.ENSO_LABEL[k], "extra_warming_c": v} for k, v in climate.ENSO_BIAS_C.items()]},
        "assumptions": {**asdict(a), "baseline_hours": model.H0, "max_hours": model.H_MAX, "max_conservation_pct": model.C_MAX,
                        "paths": len(b["paths"]), "burden_note": "ranking cost = extra kWh/day + 6 per 1% conservation (assumed weights)"},
        "baseline": base, "active_plan": ({"plan": {k: active[k] for k in ("desal_hours", "conservation_pct", "approved_at", "approved_step")},
                                           "projection": act_eval} if active else None),
        "plans": [_with_fan(model.evaluate(b["paths"], inp, h, c)) for h, c in STANDARD_PLANS],
        "optimiser": {"plan": {"desal_hours": best["desal_hours"], "conservation_pct": best["conservation_pct"]}, "target_met": met,
                      "projection": _with_fan(best)},
        "supply_note": (f"Supply is currently degraded ({N0(inp['supply_l_day_now'])} L/day against a normal {N0(b['sc'].prod_normal * 24)} L/day), "
                        "and this projection assumes that continues. Treat the risk below as a worst case until supply is restored.")
        if inp["supply_l_day_now"] < 0.8 * b["sc"].prod_normal * 24 else None,
        "latest_plan_id": run.get("latest_outlook_plan_id"), "outlook_calls": run.get("outlook_calls", 0),
        "label": "Scenario projection under stated assumptions, not a weather forecast.",
    }


def _spend(run_id):
    st = store()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if st.incr("counters", f"ai_{day}", "calls", 0) >= config.DAILY_AI_BUDGET:
        raise Budget("daily AI budget reached")
    run = st.get("runs", run_id)
    if run.get("outlook_calls", 0) >= config.PER_RUN_OUTLOOK_CAP:
        raise Budget("planner limit reached for this run")
    st.incr("counters", f"ai_{day}", "calls", 3)          # a planner request makes up to a few model calls
    st.incr("runs", run_id, "outlook_calls", 1)


def plan(run_id, run, enso="strong", beta=None):
    from ..ai.client import AIUnavailable
    b = build(run, enso, beta)
    ctx = planner.Ctx(b["inputs"], b["paths"], _climate_summary(b), asdict(b["assumptions"]), model.TARGET_BREACH)
    _spend(run_id)
    source, answer, calls, errors = "gemini", None, [], []
    fin = None
    for tier, fn in (("gemini", planner.run_agent), ("gemini-single-call", planner.run_single_shot)):
        try:
            answer, calls = fn(ctx)
            fin = planner.verify_and_finalise(ctx, answer, calls)
            source = "fake-ai (tests only)" if config.FAKE_AI else tier
            break
        except Exception as e:                               # AIUnavailable, invalid output, SDK errors: try the next tier
            answer, calls, fin = None, [], None
            errors.append(f"{tier}: {type(e).__name__}: {str(e)[:300]}")
            log.warning("planner tier %s failed: %s", tier, e)
    if fin is None:
        fin, source = planner.fixed_recommendation(ctx), "fixed-optimiser"
    error = " | ".join(errors)
    doc = {"id": uuid.uuid4().hex[:12], "run_id": run_id, "step": run["current_step"], "created_at": _now(), "enso": enso,
           "beta": b["assumptions"].beta_per_c, "source": source, "error": error,
           "answer": answer.model_dump() if answer else None,
           "tool_calls": [{"name": n, "arguments": x, "result": r} for n, x, r in calls],
           "recommendation": {**fin, "projection": _with_fan(fin["projection"])},
           "status": "unverified", "requires_approval": True}
    store().set("outlook_plans", doc["id"], doc)
    store().update("runs", run_id, {"latest_outlook_plan_id": doc["id"]})
    return doc


def approve(run_id, run, desal_hours, conservation_pct, enso="strong", beta=None):
    h, c = planner.clamp_plan(desal_hours, conservation_pct)
    b = build(run, enso, beta)
    proj = model.evaluate(b["paths"], b["inputs"], h, c)
    if proj["p_breach"] > model.TARGET_BREACH:
        raise Conflict(f"This plan no longer meets the {model.TARGET_BREACH * 100:g}% risk target under the current inputs "
                       f"({proj['p_breach'] * 100:.1f}% chance of reaching critical). Re-plan before approving.")
    run["outlook_plan"] = {"desal_hours": h, "conservation_pct": c, "approved_at": _now(), "approved_step": run["current_step"],
                           "p_breach_at_approval": proj["p_breach"], "enso": enso, "simulated": True}
    run.setdefault("audit", []).append({"at": _now(), "step": run["current_step"], "event": "outlook_plan_approved",
                                        "desal_hours": h, "conservation_pct": c})
    store().set("runs", run_id, run)
    return run["outlook_plan"]


def summary_from_rows(sc, rows, enso="strong", beta=None):
    """Compact outlook for the chat tool. Same inputs, seed and path count as the dashboard, so the numbers agree."""
    a = _params(enso, beta)
    inputs = model.derive_inputs(rows, sc)
    if inputs is None:
        return {"error": "Outlook unavailable: the tank sensor or flow data is stale, missing or too short. Nothing is assumed."}
    fc = climate.get_forecast()
    paths, _t, _ref = model.make_paths(inputs, fc["days"], a, config.OUTLOOK_PATHS, model.seed_for(inputs, fc["days"], a))
    base = model.evaluate(paths, inputs, model.H0, 0)
    best, met, _ = model.search(paths, inputs)
    return {"label": "scenario projection under stated assumptions, not a weather forecast", "enso_level": enso,
            "weather_source": fc["source"], "horizon_days": a.days, "target_pct": model.TARGET_BREACH * 100,
            "level_l_now": inputs["level_l"], "baseline": model.compact(base), "recommended_plan": model.compact(best),
            "recommended_meets_target": met}

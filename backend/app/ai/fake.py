"""Deterministic stand-in for Gemini, used ONLY for local tests (FAKE_AI=1). Never used in the deployed service."""
import os


def _g(items, id_):
    return next(i for i in items if i["id"] == id_)["values"]


def _f(x):
    return f"{x:g}"


def _assess(snap, corrupt=False):
    items = snap["items"]
    v1, v2, v3, v5, v9 = (_g(items, k) for k in ("ev-01", "ev-02", "ev-03", "ev-05", "ev-09"))
    fired = {r: _g(items, i)["fired"] for r, i in zip(("low_voltage", "low_production", "low_reserve_horizon"),
                                                    ("ev-06", "ev-07", "ev-08"))}
    hyps, acts = [], []
    ex = snap["actions_executed"]
    if fired["low_voltage"]:
        hyps.append({"hyp_id": "h-01", "status": "unverified", "evidence_ids": ["ev-01", "ev-06"],
                     "cause": f"Supply voltage fell below 210 V (minimum {_f(v1['min'])} V, first below 210 V at "
                              f"{v1.get('first_time_below_210')}), which is consistent with reduced pumping output."})
    if fired["low_production"]:
        hyps.append({"hyp_id": "h-02", "status": "unverified", "evidence_ids": ["ev-02", "ev-03", "ev-07"],
                     "cause": f"Inflow averaged {_f(v2['mean'])} L/h against outflow of {_f(v3['mean'])} L/h in the last window."})
    if v5.get("state") == "depleting":
        hyps.append({"hyp_id": "h-03", "status": "unverified", "evidence_ids": ["ev-05", "ev-08"],
                     "cause": f"At constant rates the reserve reaches the critical level in {_f(v5['hours_to_critical'])} h."})
    if v5.get("state") == "unavailable":
        hyps.append({"hyp_id": "h-04", "status": "unverified", "evidence_ids": ["ev-09", "ev-11"],
                     "cause": "The tank level sensor is stale or missing, so no reserve forecast can be given."})
    if fired["low_voltage"] and "restore_power_feed" not in ex:
        acts.append({"action_id": "a-01", "title": "Request restoration of the power feed", "owner_role": "facilities coordinator",
                     "priority": 1, "rationale": f"Voltage minimum {_f(v1['min'])} V is below the 210 V threshold.",
                     "prerequisites": ["Operator approval"], "evidence_ids": ["ev-01", "ev-06"],
                     "procedure_ids": ["sop-power-01#2"], "requires_approval": True, "simulated_command": "restore_power_feed"})
    if v5.get("state") == "depleting" and "conserve_demand" not in ex:
        acts.append({"action_id": "a-02", "title": "Request demand conservation", "owner_role": "water operator",
                     "priority": 2, "rationale": f"Hours to critical reserve is {_f(v5['hours_to_critical'])} h.",
                     "prerequisites": ["Operator approval"], "evidence_ids": ["ev-05"],
                     "procedure_ids": ["sop-reserve-01#2"], "requires_approval": True, "simulated_command": "conserve_demand"})
    if v5.get("state") == "unavailable" and "repair_tank_sensor" not in ex:
        acts.append({"action_id": "a-03", "title": "Send a technician to check and repair the tank gauge", "owner_role": "facilities coordinator",
                     "priority": 1, "rationale": "Forecast withheld because tank data is missing or stale.",
                     "prerequisites": [], "evidence_ids": ["ev-09", "ev-11"], "procedure_ids": ["sop-sensor-01#2"],
                     "requires_approval": True, "simulated_command": "repair_tank_sensor"})
    summary = ("No sustained anomaly in the supplied evidence." if not hyps else
               f"{len(snap['active_rules'])} detection rule(s) are active as of {snap['clock']}; hypotheses are unverified.")
    done = _g(items, "ev-10")
    if done["executed_count"]:
        summary += " The simulated " + " and ".join(x["title"].lower() for x in done["actions"]) + " has been executed and its effect is in ev-10."
    a = {"summary": summary, "hypotheses": hyps, "data_quality": "limited" if v5.get("state") == "unavailable" else "sufficient",
         "missing_evidence": ["Plant controller status", "Feed breaker status"],
         "actions": acts, "limitations": ["Synthetic data; rates held constant in the forecast."],
         "evidence_ids": [i["id"] for i in items][:4] + (["ev-10"] if done["executed_count"] else [])}
    if corrupt and hyps:
        hyps[0]["evidence_ids"] = ["ev-99"]                     # invented ID
        if len(hyps) > 2:
            hyps[2]["cause"] = "At constant rates the reserve reaches the critical level in 14.9 h."   # altered number
        hyps.append({"hyp_id": f"h-0{len(hyps) + 1}", "status": "unverified", "evidence_ids": ["ev-01"],
                     "cause": "Transformer failure confirmed at the substation."}) if len(hyps) < 4 else None
    return a


def _judge(ctx):
    snap, a = ctx["snapshot"], ctx["assessment"]
    ids = {i["id"] for i in snap["items"]}
    checks, bad = [], False
    def chk(item_id, evs, text):
        nonlocal bad
        if any(e not in ids for e in evs):
            checks.append({"item_id": item_id, "verdict": "unsupported", "problem": "wrong_reference", "reason": "cites an ID that does not exist"}); bad = True
        elif "confirmed" in text.lower():
            checks.append({"item_id": item_id, "verdict": "unsupported", "problem": "overreach", "reason": "claims confirmation the evidence cannot show"}); bad = True
        else:
            checks.append({"item_id": item_id, "verdict": "supported", "problem": "none", "reason": "cited evidence matches"})
    chk("summary", [], a["summary"])
    for h in a["hypotheses"]:
        chk(h["hyp_id"], h["evidence_ids"], h["cause"])
    for x in a["actions"]:
        chk(x["action_id"], x["evidence_ids"], x["rationale"])
    return {"checks": checks, "overall": "repair" if bad else "accept",
            "repair_instructions": "Remove or fix flagged items; cite only existing IDs." if bad else ""}


def respond(stage, ctx):
    if stage == "assess":
        return _assess(ctx["snapshot"], corrupt=os.getenv("FAKE_AI_CORRUPT") == "1")
    if stage == "repair":
        return _assess(ctx["snapshot"], corrupt=False)
    if stage in ("judge", "judge2"):
        return _judge(ctx)
    raise ValueError(stage)

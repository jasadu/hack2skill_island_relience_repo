"""Deterministic code checks that run before (and after) the judge. Free and instant."""
from .numcheck import unsupported_numbers, numbers_in
from ..analytics.evidence import numeric_leaves

COMMANDS = {"restore_power_feed", "conserve_demand", "repair_tank_sensor", "none"}


def _time_strings(obj):
    """Clock strings anywhere in an evidence value (keys ending _at or starting first_time)."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and (k.endswith("_at") or k.startswith("first_time")):
                out.append(v)
            else:
                out += _time_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _time_strings(v)
    return out


def allowed_numbers(snapshot):
    allowed = numeric_leaves(snapshot["items"]) + numeric_leaves(snapshot["horizon"])
    for s in snapshot["sops"]:
        allowed += numbers_in(s["text"])
    times = [snapshot.get("clock") or ""]                       # clock and date strings the assessor is told to use
    for i in snapshot["items"]:
        times += [t for t in i.get("time_range", []) if t]
        times += _time_strings(i["values"])
    for t in times:
        allowed += numbers_in(t)
    return allowed


def normalise(assessment: dict) -> dict:
    """Safety invariants enforced in code, never trusted to the model."""
    for h in assessment["hypotheses"]:
        h["status"] = "unverified"
    for a in assessment["actions"]:
        a["requires_approval"] = True
    return assessment


def code_check(assessment: dict, snapshot: dict) -> dict:
    ev_ids = {i["id"] for i in snapshot["items"]}
    sop_ids = {s["id"] for s in snapshot["sops"]}
    allowed = allowed_numbers(snapshot)
    items, glob = {}, []

    def add(item, msg):
        items.setdefault(item, []).append(msg)

    def refs(item, evs, sops):
        for e in evs:
            if e not in ev_ids:
                add(item, f"evidence ID {e} does not exist in the registry")
        for p in sops:
            if p not in sop_ids:
                add(item, f"procedure ID {p} is not in the supplied SOP sections")

    refs("summary", assessment["evidence_ids"], [])
    for n in unsupported_numbers(assessment["summary"], allowed):
        add("summary", f"number {n:g} in the summary does not match any evidence or calculation value")
    for h in assessment["hypotheses"]:
        refs(h["hyp_id"], h["evidence_ids"], [])
        if not h["evidence_ids"]:
            add(h["hyp_id"], "hypothesis cites no evidence")
        for n in unsupported_numbers(h["cause"], allowed):
            add(h["hyp_id"], f"number {n:g} does not match any evidence or calculation value")
    for a in assessment["actions"]:
        refs(a["action_id"], a["evidence_ids"], a["procedure_ids"])
        if not a["evidence_ids"]:
            add(a["action_id"], "action cites no evidence")
        if a["simulated_command"] not in COMMANDS:
            add(a["action_id"], f"command {a['simulated_command']} is not allowlisted")
        for n in unsupported_numbers(a["rationale"] + " " + a["title"], allowed):
            add(a["action_id"], f"number {n:g} does not match any evidence or calculation value")
    if len(assessment["actions"]) > 5:
        glob.append("more than 5 actions")
    return {"items": items, "global": glob, "ok": not items and not glob}


def format_problems(cc: dict) -> str:
    lines = [f"- {k}: {m}" for k, ms in cc["items"].items() for m in ms] + [f"- {g}" for g in cc["global"]]
    return "\n".join(lines)

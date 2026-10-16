import json
from ..analytics.reserve import reserve_horizon
from ..analytics.detect import detect
from .. import sops as sopmod

CHANNELS = ("voltage", "inflow", "outflow", "tank_volume", "production_power")

TOOLS = [
    {"type": "function", "name": "get_telemetry_window",
     "description": "Observed telemetry for one metric between two simulation steps (max 30 steps, never beyond the current step).",
     "parameters": {"type": "object", "properties": {
         "metric": {"type": "string", "enum": list(CHANNELS)},
         "start_step": {"type": "integer"}, "end_step": {"type": "integer"}},
         "required": ["metric", "start_step", "end_step"]}},
    {"type": "function", "name": "query_history",
     "description": "Statistic of one observed metric over the run so far (default: the whole run up to now). stat is min, max, mean, first or last. min and max also return the step and simulated clock time when it happened.",
     "parameters": {"type": "object", "properties": {
         "metric": {"type": "string", "enum": list(CHANNELS)},
         "stat": {"type": "string", "enum": ["min", "max", "mean", "first", "last"]},
         "start_step": {"type": "integer"}, "end_step": {"type": "integer"}},
         "required": ["metric", "stat"]}},
    {"type": "function", "name": "summarize_period",
     "description": "Min, max, mean and latest value of every metric over the last N simulated hours (1 to 24; the run may be shorter, and the result says how many hours it actually covers), plus detection state and fired rules.",
     "parameters": {"type": "object", "properties": {"hours": {"type": "number"}}}},
    {"type": "function", "name": "get_outlook",
     "description": "The 14-day reserve outlook: chance of reaching the critical level at the baseline 10 desalination hours/day, and the least intrusive plan that meets the risk target. It is a scenario projection under stated assumptions, not a weather forecast.",
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "calculate_reserve_horizon",
     "description": "Compute hours to critical reserve and to empty. Optional overrides make it a what-if; it never changes the live system.",
     "parameters": {"type": "object", "properties": {
         "inflow_lph": {"type": "number"}, "outflow_lph": {"type": "number"},
         "volume_l": {"type": "number"}, "critical_l": {"type": "number"}}}},
    {"type": "function", "name": "retrieve_procedure",
     "description": "Keyword search over the demo SOP sections. Returns section ids, text and a match score.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


def dispatch(name, args, ctx):
    """ctx: {sc, rows (trace up to current step), snapshot}. The model cannot choose a run or mutate state."""
    if name == "get_telemetry_window":
        m = args.get("metric")
        if m not in CHANNELS:
            return {"error": "unknown metric"}
        cur = ctx["rows"][-1]["step"]
        e = min(int(args.get("end_step", cur)), cur)
        s = max(int(args.get("start_step", e - 9)), e - 29, 0)
        out = [{"step": r["step"], "clock": r["clock"], "value": round(r["obs"][m][0], 2), "unit": r["obs"][m][1]}
               for r in ctx["rows"] if s <= r["step"] <= e and m in r["obs"]]
        return {"metric": m, "start_step": s, "end_step": e, "readings": out}
    if name == "query_history":
        m = args.get("metric")
        if m not in CHANNELS or args.get("stat") not in ("min", "max", "mean", "first", "last"):
            return {"error": "unknown metric or stat"}
        cur = ctx["rows"][-1]["step"]
        e = min(int(args.get("end_step", cur)), cur)
        st = max(int(args.get("start_step", 0)), 0)
        pts = [(r["step"], r["obs"][m][0], r["clock"], r["obs"][m][1]) for r in ctx["rows"] if st <= r["step"] <= e and m in r["obs"]]
        if not pts:
            return {"metric": m, "stat": args["stat"], "error": "no readings in that window (sensor silent or window empty)", "start_step": st, "end_step": e}
        k = args["stat"]
        if k == "mean":
            return {"metric": m, "stat": k, "value": round(sum(p[1] for p in pts) / len(pts), 2), "unit": pts[0][3], "start_step": st, "end_step": e, "readings": len(pts)}
        pick = {"min": min(pts, key=lambda p: p[1]), "max": max(pts, key=lambda p: p[1]), "first": pts[0], "last": pts[-1]}[k]
        return {"metric": m, "stat": k, "value": round(pick[1], 2), "unit": pick[3], "step": pick[0], "sim_clock": pick[2],
                "start_step": st, "end_step": e, "readings": len(pts)}
    if name == "summarize_period":
        try:
            hours = min(24.0, max(1.0, float(args.get("hours", 24))))
        except (TypeError, ValueError):
            hours = 24.0
        cur = ctx["rows"][-1]["step"]
        start = max(0, cur - int(hours * 60) + 1)
        win = [r for r in ctx["rows"] if r["step"] >= start]
        out = {}
        for m in CHANNELS:
            pts = [(r["step"], r["obs"][m][0]) for r in win if m in r["obs"]]
            if not pts:
                out[m] = {"error": "no readings (sensor silent)"}
                continue
            lo, hi = min(pts, key=lambda p: p[1]), max(pts, key=lambda p: p[1])
            out[m] = {"unit": win[0]["obs"][m][1] if m in win[0]["obs"] else "", "min": round(lo[1], 2), "min_step": lo[0], "min_at": ctx["rows"][lo[0]]["clock"], "max": round(hi[1], 2), "max_step": hi[0], "max_at": ctx["rows"][hi[0]]["clock"],
                      "mean": round(sum(p[1] for p in pts) / len(pts), 2), "latest": round(pts[-1][1], 2), "readings": len(pts)}
        det = detect(ctx["rows"], ctx["sc"])
        return {"requested_hours": hours, "covered_hours": round(len(win) / 60, 1), "from": ctx["rows"][start]["clock"], "to": ctx["rows"][cur]["clock"],
                "note": ("The simulation has only run for the covered hours so far." if len(win) / 60 < hours else ""),
                "metrics": out, "detection": {"state": det["state"], "started_at": ctx["rows"][det["started_step"]]["clock"] if det["started_step"] is not None else None}}
    if name == "get_outlook":
        from ..outlook import service as outlook
        return outlook.summary_from_rows(ctx["sc"], ctx["rows"])
    if name == "calculate_reserve_horizon":
        items = {i["id"]: i["values"] for i in ctx["snapshot"]["items"]}
        vol = args.get("volume_l", items["ev-04"].get("last"))
        inflow = args.get("inflow_lph", items["ev-02"].get("mean"))
        outflow = args.get("outflow_lph", items["ev-03"].get("mean"))
        crit = args.get("critical_l", ctx["sc"].critical)
        res = reserve_horizon(vol, inflow, outflow, crit, 1.0 if all(x is not None for x in (vol, inflow, outflow)) else 0.0)
        res = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res.items()}
        res["inputs"] = {"volume_l": vol, "inflow_lph": inflow, "outflow_lph": outflow, "critical_l": crit,
                         "overridden": [k for k in ("volume_l", "inflow_lph", "outflow_lph", "critical_l") if k in args]}
        return res
    if name == "retrieve_procedure":
        return {"results": [{"id": s["id"], "title": s["title"], "text": s["text"], "score": s["score"]}
                            for s in sopmod.retrieve(args.get("query", ""))]}
    return {"error": "unknown tool"}

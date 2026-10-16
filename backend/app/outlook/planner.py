"""Planner agent: Gemini picks plans to test and recommends one; every number comes from the projection tool.
The recommendation is re-simulated in code, and replaced by the code optimiser if it does not meet the target."""
import json, logging, time
from .. import config, sops as sopmod
from ..ai import prompts
from ..ai.client import create, AIUnavailable, json_format, budget_timeout
from ..ai.schemas import PlannerAnswer
from ..ai.numcheck import unsupported_numbers, numbers_in
from ..analytics.evidence import numeric_leaves
from . import model

log = logging.getLogger("planner")

TOOLS = [
    {"type": "function", "name": "get_outlook_inputs",
     "description": "Current observed state, climate summary, assumptions and the risk target. Call once first.",
     "parameters": {"type": "object", "properties": {}}},
    {"type": "function", "name": "project_plan",
     "description": "Run the 14-day Monte Carlo projection for one plan. Returns risk of reaching critical, level percentiles, energy. It never changes the live system.",
     "parameters": {"type": "object", "properties": {
         "desal_hours": {"type": "number", "description": "Desalination hours per day, baseline to 16"},
         "conservation_pct": {"type": "number", "description": "Demand conservation percent, 0 to 20"}},
         "required": ["desal_hours", "conservation_pct"]}},
    {"type": "function", "name": "retrieve_procedure",
     "description": "Keyword search over the demo SOP sections.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


def clamp_plan(h, c):
    h = min(model.H_MAX, max(model.H0, float(h)))
    c = min(model.C_MAX, max(0.0, float(c)))
    return round(h * 2) / 2, round(c / 5) * 5          # half-hour and 5 percent steps


class Ctx:
    """Everything the tools may touch: fixed paths for this request. The model cannot pick a run or change state."""
    def __init__(self, inputs, paths, climate_summary, assumptions, target):
        self.inputs, self.paths, self.climate, self.assumptions, self.target = inputs, paths, climate_summary, assumptions, target
        self.cache = {}

    def project(self, h, c):
        h, c = clamp_plan(h, c)
        if (h, c) not in self.cache:
            self.cache[(h, c)] = model.evaluate(self.paths, self.inputs, h, c)
        return self.cache[(h, c)]


def dispatch(name, args, ctx: Ctx):
    if name == "get_outlook_inputs":
        i = ctx.inputs
        return {"level_l": i["level_l"], "critical_l": i["critical_l"], "capacity_l": i["capacity_l"],
                "demand_l_per_day_now": i["demand_l_day"], "supply_l_per_day_now": i["supply_l_day_now"],
                "baseline_hours": i["baseline_hours"], "max_hours": model.H_MAX, "max_conservation_pct": model.C_MAX,
                "target_pct": round(ctx.target * 100, 1), "horizon_days": ctx.assumptions["days"],
                "climate": ctx.climate, "assumptions": ctx.assumptions,
                "baseline": model.compact(ctx.project(model.H0, 0))}
    if name == "project_plan":
        try:
            h, c = clamp_plan(args.get("desal_hours", model.H0), args.get("conservation_pct", 0))
        except (TypeError, ValueError):
            return {"error": "invalid plan"}
        return model.compact(ctx.project(h, c))
    if name == "retrieve_procedure":
        return {"results": [{"id": s["id"], "title": s["title"], "text": s["text"], "score": s["score"]}
                            for s in sopmod.retrieve(args.get("query", "water conservation demand"))]}
    return {"error": "unknown tool"}


def _context_text(ctx):
    return ("TASK: Plan the next 14 days. The current inputs are given below. Test plans with project_plan (several in one step), then return the JSON recommendation.\n"
            f"Risk target: p_breach_pct <= {round(ctx.target * 100, 1)}.")


def _fake(ctx):
    calls = []

    def call(name, args):
        r = dispatch(name, args, ctx)
        calls.append((name, args, r))
        return r
    inp = call("get_outlook_inputs", {})
    base = inp["baseline"]
    best, met, _ = model.search(ctx.paths, ctx.inputs, ctx.target)
    alt = call("project_plan", {"desal_hours": model.H0 + 1, "conservation_pct": 0})
    rec = call("project_plan", {"desal_hours": best["desal_hours"], "conservation_pct": best["conservation_pct"]})
    txt = (f"Baseline of {base['desal_hours']:g} h/day: {base['p_breach_pct']:g}% chance of reaching critical within the horizon. "
           f"Adding one hour gives {alt['p_breach_pct']:g}%. Recommended: {rec['desal_hours']:g} h/day with {rec['conservation_pct']:g}% conservation, "
           f"{rec['p_breach_pct']:g}% chance, costing {rec['extra_kwh_per_day']:g} extra kWh per day.")
    ans = PlannerAnswer(recommended_desal_hours=rec["desal_hours"], recommended_conservation_pct=rec["conservation_pct"], summary=txt,
                        rationale=[f"Tested plans: baseline {base['p_breach_pct']:g}%, {alt['desal_hours']:g} h {alt['p_breach_pct']:g}%, recommended {rec['p_breach_pct']:g}%."],
                        watch_items=["Re-run the outlook after the next forecast update."], procedure_ids=[])
    return ans, calls


def run_agent(ctx: Ctx, max_hops=4):
    """Returns (PlannerAnswer, tool_calls)."""
    if config.FAKE_AI:
        return _fake(ctx)
    inputs = dispatch("get_outlook_inputs", {}, ctx)          # supplied up front: saves a model round trip
    calls = [("get_outlook_inputs", {}, inputs)]
    deadline = time.time() + 28                 # agent loop budget; the single-shot tier needs the rest of the 60 s Hosting limit
    kw = dict(model=config.ASSESS_MODEL, system_instruction=prompts.PLANNER_SYSTEM, tools=TOOLS)
    try:
        it = create(**kw, input=_context_text(ctx) + "\n\nCURRENT INPUTS (JSON):\n" + json.dumps(inputs), response_format=json_format(PlannerAnswer), timeout=budget_timeout(deadline))
        combined = True
    except AIUnavailable:
        it = create(**kw, input=_context_text(ctx) + "\n\nCURRENT INPUTS (JSON):\n" + json.dumps(inputs), timeout=budget_timeout(deadline))
        combined = False
    for _ in range(max_hops):
        fcs = [s for s in (it.steps or []) if getattr(s, "type", "") == "function_call"]
        if not fcs or time.time() > deadline:
            break
        results = []
        for c in fcs:
            res = dispatch(c.name, dict(c.arguments or {}), ctx)
            calls.append((c.name, dict(c.arguments or {}), res))
            results.append({"type": "function_result", "name": c.name, "call_id": c.id,
                            "result": [{"type": "text", "text": json.dumps(res)}]})
        it = create(**kw, previous_interaction_id=it.id, input=results, timeout=budget_timeout(deadline),
                    **({"response_format": json_format(PlannerAnswer)} if combined else {}))
    if not combined:
        it = create(model=config.ASSESS_MODEL, system_instruction=prompts.PLANNER_SYSTEM, previous_interaction_id=it.id,
                    input="Give the final recommendation as JSON.", response_format=json_format(PlannerAnswer),
                    timeout=budget_timeout(deadline))
    return PlannerAnswer.model_validate_json(it.output_text), calls


SHOT_PLANS = ((10, 0), (11, 0), (12, 0), (13, 0), (12, 10), (14, 0), (14, 10), (16, 0))


def run_single_shot(ctx: Ctx):
    """Fallback tier: the code has already tested a standard plan table, Gemini only picks and explains. One model call, no tool loop."""
    best, _met, _ = model.search(ctx.paths, ctx.inputs, ctx.target)
    plans = {clamp_plan(h, c) for h, c in SHOT_PLANS} | {(best["desal_hours"], best["conservation_pct"])}
    inputs = dispatch("get_outlook_inputs", {}, ctx)
    calls = [("get_outlook_inputs", {}, inputs)]
    for h, c in sorted(plans):
        calls.append(("project_plan", {"desal_hours": h, "conservation_pct": c}, dispatch("project_plan", {"desal_hours": h, "conservation_pct": c}, ctx)))
    if config.FAKE_AI:
        ans, _c = _fake(ctx)
        return ans, calls
    text = ("INPUTS (JSON):\n" + json.dumps(inputs, indent=1) + "\n\nTESTED PLANS (JSON, produced by code):\n" +
            json.dumps([{"plan": a, "result": r} for _n, a, r in calls[1:]], indent=1) +
            f"\n\nTASK: Recommend one of the tested plans. Risk target: p_breach_pct <= {round(ctx.target * 100, 1)}. Return the JSON recommendation.")
    system = prompts.PLANNER_SHOT_SYSTEM
    it = create(model=config.ASSESS_MODEL, system_instruction=system, input=text, store=False,
                response_format=json_format(PlannerAnswer), timeout=14)
    return PlannerAnswer.model_validate_json(it.output_text), calls


def verify_and_finalise(ctx: Ctx, ans: PlannerAnswer, calls):
    """Code decides what is shown: numbers must come from tool results; the recommended plan is re-simulated; unmet target => optimiser."""
    allowed = []
    for _n, _a, res in calls:
        allowed += numeric_leaves(res) + numbers_in(json.dumps(res))
    text = " ".join([ans.summary] + ans.rationale + ans.watch_items)
    bad = unsupported_numbers(text, allowed)
    h, c = clamp_plan(ans.recommended_desal_hours, ans.recommended_conservation_pct)
    proj = ctx.project(h, c)
    best, met, _ = model.search(ctx.paths, ctx.inputs, ctx.target)
    meets = proj["p_breach"] <= ctx.target
    tested = {(a.get("desal_hours"), a.get("conservation_pct")) for n, a, _r in calls if n == "project_plan"}
    verification = {"numbers_ok": not bad, "unmatched_numbers": bad, "plan_in_bounds": True,
                    "recommended_meets_target": meets, "plan_was_tested_by_agent": any(clamp_plan(*t) == (h, c) for t in tested if None not in t),
                    "tools_used": [c_[0] for c_ in calls]}
    overridden, reason = False, ""
    if not meets and met:
        overridden, reason = True, "The agent's plan did not meet the risk target when re-simulated; the lowest-burden plan that does is shown instead."
        h, c, proj = best["desal_hours"], best["conservation_pct"], best
    elif bad:
        reason = "Some numbers in the explanation could not be matched to tool results; they are flagged."
    return {"plan": {"desal_hours": h, "conservation_pct": c}, "projection": proj, "overridden": overridden, "override_reason": reason,
            "target_met": proj["p_breach"] <= ctx.target, "verification": verification}


def fixed_recommendation(ctx: Ctx):
    best, met, _ = model.search(ctx.paths, ctx.inputs, ctx.target)
    return {"plan": {"desal_hours": best["desal_hours"], "conservation_pct": best["conservation_pct"]}, "projection": best,
            "overridden": False, "override_reason": "", "target_met": met,
            "verification": {"numbers_ok": True, "unmatched_numbers": [], "recommended_meets_target": met, "tools_used": []}}

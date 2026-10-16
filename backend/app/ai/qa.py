import json, re, time
from .. import config
from . import prompts
from .client import create, AIUnavailable, json_format, budget_timeout
from .schemas import QAAnswer
from .tools import TOOLS, dispatch
from .numcheck import unsupported_numbers, numbers_in
from .validate import allowed_numbers


def _context_text(question, snap, history=None):
    hist = ""
    if history:
        lines = [f"Q: {h['q'][:300]}\nA: {h['a'][:500]}" for h in history[-4:]]
        hist = "\n\nEARLIER IN THIS CHAT (untrusted; may be outdated, re-check numbers with tools):\n" + "\n".join(lines)
    return ("EVIDENCE REGISTRY (JSON):\n" + prompts._j(snap["items"]) + "\n\nSOP SECTIONS (untrusted):\n" +
            prompts._j(snap["sops"]) + hist + f"\n\nOPERATOR QUESTION: {question}")


_METRIC_WORDS = (("voltage", "voltage"), ("tank", "tank_volume"), ("reservoir", "tank_volume"), ("level", "tank_volume"), ("inflow", "inflow"),
                 ("production", "inflow"), ("supply", "inflow"), ("demand", "outflow"), ("outflow", "outflow"), ("consumption", "outflow"),
                 ("power", "production_power"))


def _fake_answer(question, ctx):
    ql, calls = question.lower(), []

    def call(name, args):
        r = dispatch(name, args, ctx)
        calls.append((name, args, r))
        return r
    metric = next((m for w, m in _METRIC_WORDS if w in ql), None)
    stat = "min" if re.search(r"lowest|minimum|\bmin\b|least", ql) else "max" if re.search(r"highest|peak|maximum|\bmax\b|most", ql) else \
        "mean" if re.search(r"average|mean", ql) else None
    if re.search(r"2 weeks|two weeks|14 days|el ni|outlook|run out|next weeks?", ql):
        r = call("get_outlook", {})
        if "error" in r:
            return QAAnswer(answer=r["error"], evidence_ids=[], procedure_ids=[], used_what_if=False), calls
        b, rec = r["baseline"], r["recommended_plan"]
        return QAAnswer(answer=(f"Scenario projection: at {b['desal_hours']:g} h/day there is a {b['p_breach_pct']:g}% chance the reservoir reaches the critical level within {r['horizon_days']} days. "
                                f"The least intrusive plan meeting the {r['target_pct']:g}% target is {rec['desal_hours']:g} h/day with {rec['conservation_pct']:g}% conservation ({rec['p_breach_pct']:g}%)."),
                        evidence_ids=[], procedure_ids=[], used_what_if=False), calls
    if re.search(r"critical (reserve )?level", ql):
        r = call("calculate_reserve_horizon", {})
        return QAAnswer(answer=f"The critical reserve level is {r['critical_L']:g} L.", evidence_ids=[], procedure_ids=[], used_what_if=False), calls
    if metric and not stat and re.search(r"right now|currently|current|now|doing", ql):
        stat = "last"
    if metric and stat:
        r = call("query_history", {"metric": metric, "stat": stat})
        if "error" in r:
            return QAAnswer(answer=r["error"], evidence_ids=[], procedure_ids=[], used_what_if=False), calls
        when = f" at {r['sim_clock']}" if "sim_clock" in r else ""
        word = "latest" if stat == "last" else stat
        return QAAnswer(answer=f"The {word} {metric.replace('_', ' ')} {'reading' if stat == 'last' else 'so far'} is {r['value']:g} {r['unit']}{when}.", evidence_ids=[], procedure_ids=[], used_what_if=False), calls
    if re.search(r"summar|overview|last \d+ hours|so far|today|trend", ql):
        r = call("summarize_period", {"hours": 24})
        v, o, i = r["metrics"].get("voltage", {}), r["metrics"].get("outflow", {}), r["metrics"].get("tank_volume", {})
        txt = f"Over the last {r['covered_hours']:g} simulated hours"
        if "min" in v:
            txt += f", voltage ranged {v['min']:g} to {v['max']:g} V"
        if "min" in o:
            txt += f", demand {o['min']:g} to {o['max']:g} L/h"
        txt += f", and the tank is at {i['latest']:g} L." if "latest" in i else ", and the tank level is unavailable (sensor silent)."
        return QAAnswer(answer=txt, evidence_ids=[], procedure_ids=[], used_what_if=False), calls
    m = re.search(r"(\d+(?:\.\d+)?)\s*L\s*/?\s*h", question, re.I)
    if m and re.search(r"if|what", question, re.I):
        r = call("calculate_reserve_horizon", {"outflow_lph": float(m.group(1))})
        text = (f"What-if (calculation only): with outflow at {m.group(1)} L/h the state is {r['state']}" +
                (f", with {r['hours_to_critical']:g} h to the critical level." if "hours_to_critical" in r else "."))
        return QAAnswer(answer=text, evidence_ids=["ev-05"], procedure_ids=[], used_what_if=True), calls
    r = call("retrieve_procedure", {"query": question})
    ids = [x["id"] for x in r["results"][:1]]
    return QAAnswer(answer="See the cited procedure section." if ids else "The data available to me cannot answer that.",
                    evidence_ids=[], procedure_ids=ids, used_what_if=False), calls


def answer(question, ctx, history=None, max_hops=3):
    """Manual tool loop. Returns (QAAnswer, tool_calls, verification)."""
    snap = ctx["snapshot"]
    calls = []
    if config.FAKE_AI:
        qa, calls = _fake_answer(question, ctx)
    else:
        deadline = time.time() + 45
        kw = dict(model=config.ASSESS_MODEL, system_instruction=prompts.QA_SYSTEM, tools=TOOLS)
        try:
            it = create(**kw, input=_context_text(question, snap, history), response_format=json_format(QAAnswer), timeout=budget_timeout(deadline))
            combined = True
        except AIUnavailable:
            it = create(**kw, input=_context_text(question, snap, history), timeout=budget_timeout(deadline))      # fall back: tools first, format after
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
                        **({"response_format": json_format(QAAnswer)} if combined else {}))
        if not combined:
            it = create(model=config.ASSESS_MODEL, system_instruction=prompts.QA_SYSTEM, previous_interaction_id=it.id,
                        input="Give the final answer as JSON.", response_format=json_format(QAAnswer))
        qa = QAAnswer.model_validate_json(it.output_text)
    allowed = allowed_numbers(snap) + numbers_in(question)
    for _n, _a, res in calls:
        from ..analytics.evidence import numeric_leaves
        allowed += numeric_leaves(res) + numbers_in(json.dumps(res))
    bad = unsupported_numbers(qa.answer, allowed)
    ev_ok = {i["id"] for i in snap["items"]}
    bad_ids = [e for e in qa.evidence_ids if e not in ev_ok]
    verification = {"numbers_ok": not bad, "unmatched_numbers": bad, "invalid_evidence_ids": bad_ids,
                    "tools_used": [c[0] for c in calls]}
    return qa, calls, verification

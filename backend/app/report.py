"""Run report: the full detail that the screen no longer shows. Built from stored immutable documents, never from live telemetry.
Works with or without an incident (the El Niño scenario has none), and with or without AI output."""
import json
from datetime import datetime
from .store import store
from .sim.engine import clock_at, clock_label
from . import config


def _stamp(iso):
    try:
        return clock_label(iso)
    except Exception:
        return str(iso)


def _clock(run, step):
    return clock_at(run.get("sim_start"), step) if step is not None else "-"


def _plan_doc(st, run):
    pid = run.get("latest_outlook_plan_id")
    return st.get("outlook_plans", pid) if pid else None


def build(run_id, fmt="md"):
    st = store()
    run = st.get("runs", run_id)
    if run is None:
        return None
    aid = run.get("latest_assessment_id")
    d = st.get("assessments", aid) if aid else None
    if d is not None and d["stage"] != "final":
        d = None
    plan = _plan_doc(st, run)
    snap = d["snapshot"] if d else None
    a, v = (d.get("assessment"), d["verification"]) if d else (None, None)
    payload = {"run_id": run_id, "scenario": run["scenario_id"], "synthetic_data": True,
               "simulation_started": _stamp(run.get("sim_start")) if run.get("sim_start") else None,
               "simulation_now": _clock(run, run["current_step"]), "checkpoint": snap["clock"] if snap else None,
               "snapshot_hash": d["snapshot_hash"] if d else None, "evidence": snap["items"] if snap else [],
               "calculation_snapshot": snap["horizon"] if snap else None,
               "assessment": a, "verification": v, "timeline": d.get("timeline") if d else None,
               "code_check": {"v1": d.get("code_check_v1"), "v2": d.get("code_check_v2")} if d else None,
               "judge": {"first": d.get("judge1"), "second": d.get("judge2")} if d else None,
               "ai_source": d.get("source") if d else None, "models": {"assess": config.ASSESS_MODEL, "judge": config.JUDGE_MODEL},
               "prompt_version": d.get("prompt_version") if d else None,
               "outlook_planner": plan, "approved_outlook_plan": run.get("outlook_plan"),
               "approvals": run["approvals"], "actions_executed": run["actions"], "audit": run["audit"]}
    if fmt == "json":
        return json.dumps(payload, indent=2, default=str), "application/json"

    L = [f"# Run report: {run['scenario_id']} (run {run_id})", "", "> Demo report for demonstration purposes only.", "",
         f"- Simulation started: {payload['simulation_started'] or 'n/a'}",
         f"- Simulation clock now: {payload['simulation_now']}"]
    if d:
        L += [f"- Incident checkpoint: {snap['clock']}", f"- Evidence snapshot: `{d['snapshot_hash'][:16]}`",
              f"- AI: {d.get('source')} · assessor `{config.ASSESS_MODEL}` · judge `{config.JUDGE_MODEL}` · prompt {d.get('prompt_version')}",
              f"- Verification: **{v.get('status')}**"
              + (f" · {v.get('supported')}/{v.get('claims_total')} claims supported, {len(v.get('removed', []))} removed" if "claims_total" in v else "")]
    L.append("")

    if d:
        if a:
            L += ["## Summary", a["summary"], "", "## Hypotheses (all unverified)"]
            L += [f"- **{h['hyp_id']}** {h['cause']} _(evidence: {', '.join(h['evidence_ids'])})_" for h in a["hypotheses"]] or ["- none"]
            L += ["", "## Recommended actions (each needs operator approval)"]
            L += [f"- **{x['action_id']}** {x['title']}: {x['owner_role']}; {x['rationale']} _(evidence: {', '.join(x['evidence_ids'])}; procedures: {', '.join(x['procedure_ids']) or '-'})_" for x in a["actions"]] or ["- none"]
            L += ["", "## Limitations and missing evidence"] + [f"- {x}" for x in a["limitations"]] + [f"- Missing: {x}" for x in a.get("missing_evidence", [])]
        else:
            L += ["## AI assessment", f"AI was unavailable for this checkpoint ({v.get('reason', 'no reason recorded')}); the fixed SOP checklist was shown instead."]
        L += ["", "## Verification detail"]
        L += [f"- Removed {r['item_id']} ({r['source']} check): {r['reason']}" for r in v.get("removed", [])] or ["- No claims removed."]
        L += [f"- Partially supported {p['item_id']}: {p['reason']}" for p in v.get("partially_supported", [])]
        for n, cc in (("first draft", d.get("code_check_v1")), ("repaired draft", d.get("code_check_v2"))):
            if cc:
                L.append(f"- Code checks, {n}: " + ("passed" if cc["ok"] else "; ".join(f"{k}: {'; '.join(m)}" for k, m in cc["items"].items()) + " " + "; ".join(cc["global"])))
        L += ["", "## Model calls"]
        L += [f"- {t['at'][11:19]} UTC · {t['stage']} · {t['model']} · {t['latency_ms'] / 1000:.1f} s · {'ok' if t['ok'] else 'FAILED'} · {t['note']}" for t in d.get("timeline", [])] or ["- none (served from cache)"]
        L += ["", "## Calculation snapshot (reserve horizon)", "```json", json.dumps(snap["horizon"], indent=2), "```",
              "", "## Evidence"] + [f"- **{i['id']}** {i['label']}, {i['time_range'][0]} to {i['time_range'][1]}: `{json.dumps(i['values'])}`" for i in snap["items"]]
    else:
        L += ["## Incident", "No assessed incident in this run."]

    if plan:
        rec, ver = plan["recommendation"], plan["recommendation"]["verification"]
        L += ["", "## 14-day outlook planner",
              f"- Asked at {_clock(run, plan['step'])} (real time {_stamp(plan['created_at'])}) · ENSO {plan['enso']} · beta {plan['beta']} · source **{plan['source']}**",
              f"- Recommended plan: {rec['plan']['desal_hours']:g} h/day desalination, {rec['plan']['conservation_pct']:g}% conservation; "
              f"risk {rec['projection']['p_breach_pct']:g}% ({'meets' if rec['target_met'] else 'does not meet'} the target)",
              f"- Verification: numbers_ok={ver.get('numbers_ok')}, unmatched={ver.get('unmatched_numbers')}, tools={ver.get('tools_used')}"]
        if rec.get("overridden"):
            L.append(f"- Replaced by the code optimiser: {rec['override_reason']}")
        if plan.get("error"):
            L.append(f"- AI errors (full text): {plan['error']}")
        if plan.get("answer"):
            L += ["", plan["answer"]["summary"]] + [f"- {x}" for x in plan["answer"]["rationale"]] + [f"- Watch: {x}" for x in plan["answer"]["watch_items"]]
        L += ["", "Tool calls:"] + [f"- `{t['name']}` {json.dumps(t['arguments'])}" for t in plan.get("tool_calls", [])]
    if run.get("outlook_plan"):
        o = run["outlook_plan"]
        L += ["", f"Approved plan: {o['desal_hours']:g} h/day, {o['conservation_pct']:g}% conservation at {_clock(run, o['approved_step'])} (simulated)."]

    L += ["", "## Approvals and execution (simulated)"]
    L += [f"- {k}: approved at {_clock(run, x['approved_step'])} → `{x['command']}`" for k, x in run["approvals"].items()] or ["- none"]
    L += [f"- Command `{c}` executed in simulation at {_clock(run, s)}" for c, s in run["actions"].items()]
    L += ["", "## Audit timeline (simulation clock)"] + [f"- {_clock(run, e['step'])}: {e['event']} {({k: x for k, x in e.items() if k not in ('at', 'step', 'event')}) or ''}" for e in run["audit"]]
    return "\n".join(L), "text/markdown"

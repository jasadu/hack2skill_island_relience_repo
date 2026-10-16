"""Assess -> code checks -> judge -> (repair -> judge2) -> final. ONE LLM call per request (Hosting times out at 60 s)."""
import copy, logging, time
from datetime import datetime, timezone
from . import config
from .store import store
from .sim.engine import SCENARIOS
from .analytics.evidence import checkpoints, build_snapshot
from .ai.client import call_json, AIUnavailable
from .ai.schemas import Assessment, JudgeResult
from .ai import prompts
from .ai.validate import code_check, format_problems, normalise

log = logging.getLogger("pipeline")


class NoIncident(Exception):
    pass


class AIBudget(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def assessment_id(run_id, step):
    return f"{run_id}-{step}"


def latest_checkpoint(run):
    sc = SCENARIOS[run["scenario_id"]]
    cps, det = checkpoints(sc, run["actions"], run["current_step"], run.get("sim_start"))
    return (cps[-1] if cps else None), det


def _spend(run_id):
    st = store()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    if st.incr("counters", f"ai_{day}", "calls", 0) >= config.DAILY_AI_BUDGET:
        raise AIBudget("daily AI budget reached")
    run = st.get("runs", run_id)
    if run.get("ai_calls", 0) >= config.PER_RUN_AI_CAP:
        raise AIBudget("per-run AI cap reached")
    st.incr("counters", f"ai_{day}", "calls", 1)
    st.incr("runs", run_id, "ai_calls", 1)


def _timeline(doc, stage, model, t0, ok=True, note=""):
    doc["timeline"].append({"stage": stage, "model": model, "latency_ms": int((time.time() - t0) * 1000),
                            "ok": ok, "note": note, "at": _now()})
    doc["calls"] += 1


def _fixed_fallback(doc, reason):
    doc.update(stage="final", ai_status="unavailable", source="none", assessment=None,
               verification={"status": "unavailable", "reason": reason},
               fallback_checklist=[{"id": s["id"], "title": s["title"], "text": s["text"]} for s in doc["snapshot"]["sops"]])
    return doc


def _finalize(doc):
    a = copy.deepcopy(doc.get("assessment_v2") or doc["assessment_v1"])
    cc = doc["code_check_v2"] if doc.get("assessment_v2") else doc["code_check_v1"]
    jr = doc.get("judge2") or doc.get("judge1")
    judge_ok = jr is not None
    checks = {c["item_id"]: c for c in (jr["checks"] if jr else [])}
    removed, partial = [], []

    def verdict(item_id):
        for e in cc["items"].get(item_id, []):
            removed.append({"item_id": item_id, "reason": e, "source": "code"})
            return "removed"
        c = checks.get(item_id)
        if c and c["verdict"] == "unsupported":
            removed.append({"item_id": item_id, "reason": c["reason"], "source": "judge"})
            return "removed"
        if c and c["verdict"] == "partially_supported":
            partial.append({"item_id": item_id, "reason": c["reason"]})
            return "partial"
        return "ok"

    total = 1 + len(a["hypotheses"]) + len(a["actions"])
    if verdict("summary") == "removed":
        a["summary"] = "The summary was removed after verification; rely on the verified hypotheses and actions below."
    a["hypotheses"] = [h for h in a["hypotheses"] if verdict(h["hyp_id"]) != "removed"]
    a["actions"] = [x for x in a["actions"] if verdict(x["action_id"]) != "removed"]
    if removed:
        a["limitations"].append(f"{len(removed)} claim(s) were removed after verification.")
    if not judge_ok:
        a["limitations"].append("Independent verification was unavailable: treat this assessment as unverified.")
    for h in a["hypotheses"]:
        h["flag"] = "partially_supported" if any(p["item_id"] == h["hyp_id"] for p in partial) else None
    for x in a["actions"]:
        x["flag"] = "partially_supported" if any(p["item_id"] == x["action_id"] for p in partial) else None
    status = "unverified" if not judge_ok else ("verified_with_removals" if removed else "verified")
    doc.update(stage="final", ai_status="live", assessment=a,
               verification={"status": status, "claims_total": total, "removed": removed, "partially_supported": partial,
                             "supported": total - len(removed) - len(partial), "repaired": bool(doc.get("assessment_v2")),
                             "judge_model": config.JUDGE_MODEL, "assess_model": config.ASSESS_MODEL})
    return doc


def _cache_key(doc):
    return f"{doc['snapshot_hash']}:{config.PROMPT_VERSION}"


def public(doc):
    d = {k: v for k, v in doc.items() if k not in ("assessment_v1", "assessment_v2")}
    return d


def advance(run_id, force_live=False):
    st = store()
    run = st.get("runs", run_id)
    if run is None:
        raise KeyError("run")
    step, _det = latest_checkpoint(run)
    if step is None:
        raise NoIncident()
    sc = SCENARIOS[run["scenario_id"]]
    aid = assessment_id(run_id, step)
    doc = st.get("assessments", aid)
    if doc and doc["stage"] == "final":
        return public(doc)
    if doc is None:
        snap = build_snapshot(sc, run["actions"], step, run.get("sim_start"))
        doc = {"id": aid, "run_id": run_id, "step": step, "snapshot_hash": snap["snapshot_hash"], "snapshot": snap,
               "stage": "new", "timeline": [], "calls": 0, "created_at": _now(), "prompt_version": config.PROMPT_VERSION,
               "source": "live", "ai_status": "pending", "force_live": bool(force_live)}
        if not force_live:
            hit = st.get("ai_cache", _cache_key(doc))
            if hit:
                doc.update(stage="final", source="cached", ai_status="cached", assessment=hit["assessment"],
                           verification=hit["verification"], cached_from=hit.get("created_at"))
                st.set("assessments", aid, doc)
                st.update("runs", run_id, {"latest_assessment_id": aid})
                return public(doc)
    snap = doc["snapshot"]
    try:
        stage = doc["stage"]
        t0 = time.time()
        if stage == "new":
            _spend(run_id)
            a = call_json("assess", config.ASSESS_MODEL, prompts.ASSESS_SYSTEM, prompts.assess_text(snap), Assessment,
                          fake_ctx={"snapshot": snap})
            a = normalise(a.model_dump())
            doc["assessment_v1"], doc["code_check_v1"] = a, code_check(a, snap)
            doc["stage"] = "assessed"
            _timeline(doc, "assess", config.ASSESS_MODEL, t0, note=format_problems(doc["code_check_v1"]) or "code checks passed")
        elif stage in ("assessed", "repaired"):
            n = "judge" if stage == "assessed" else "judge2"
            a = doc["assessment_v1"] if stage == "assessed" else doc["assessment_v2"]
            cc = doc["code_check_v1"] if stage == "assessed" else doc["code_check_v2"]
            try:
                _spend(run_id)
                jr = call_json(n, config.JUDGE_MODEL, prompts.JUDGE_SYSTEM, prompts.judge_text(snap, a), JudgeResult,
                               fake_ctx={"snapshot": snap, "assessment": a}).model_dump()
            except AIBudget:
                raise
            except AIUnavailable as e:
                _timeline(doc, n, config.JUDGE_MODEL, t0, ok=False, note=str(e))
                _finalize(doc)
                return _save(st, aid, doc, run_id)
            doc["judge1" if stage == "assessed" else "judge2"] = jr
            _timeline(doc, n, config.JUDGE_MODEL, t0, note=f"overall={jr['overall']}")
            needs_fix = jr["overall"] != "accept" or not cc["ok"]
            if stage == "assessed" and needs_fix and jr["overall"] != "reject":
                doc["stage"] = "needs_repair"
            else:
                _finalize(doc)
        elif stage == "needs_repair":
            _spend(run_id)
            probs = format_problems(doc["code_check_v1"])
            j1 = doc.get("judge1") or {}
            for c in j1.get("checks", []):
                if c["verdict"] != "supported":
                    probs += f"\n- {c['item_id']}: {c['problem']} ({c['reason']})"
            probs += "\n" + j1.get("repair_instructions", "")
            a = call_json("repair", config.ASSESS_MODEL, prompts.ASSESS_SYSTEM,
                          prompts.repair_text(snap, doc["assessment_v1"], probs), Assessment, fake_ctx={"snapshot": snap})
            a = normalise(a.model_dump())
            doc["assessment_v2"], doc["code_check_v2"] = a, code_check(a, snap)
            doc["stage"] = "repaired"
            _timeline(doc, "repair", config.ASSESS_MODEL, t0, note=format_problems(doc["code_check_v2"]) or "code checks passed")
    except AIBudget as e:
        _fixed_fallback(doc, str(e))
    except AIUnavailable as e:
        _timeline(doc, doc["stage"], "-", time.time(), ok=False, note=str(e))
        _fixed_fallback(doc, "AI unavailable: " + str(e))
    return _save(st, aid, doc, run_id)


def _save(st, aid, doc, run_id):
    if st.get("runs", run_id) is None:          # run was reset/deleted while the call was in flight: discard
        return public(doc)
    st.set("assessments", aid, doc)
    if doc["stage"] == "final":
        st.update("runs", run_id, {"latest_assessment_id": aid})
    if doc["stage"] == "final" and doc.get("ai_status") == "live" and doc["verification"]["status"] != "unverified":
        st.set("ai_cache", _cache_key(doc), {"assessment": doc["assessment"], "verification": doc["verification"],
                                             "created_at": _now(), "snapshot_hash": doc["snapshot_hash"]})
    return public(doc)


def get_current(run_id):
    run = store().get("runs", run_id)
    if not run:
        return None
    step, det = latest_checkpoint(run)
    if step is None:
        return None
    doc = store().get("assessments", assessment_id(run_id, step))
    return public(doc) if doc else {"id": assessment_id(run_id, step), "step": step, "stage": "none"}

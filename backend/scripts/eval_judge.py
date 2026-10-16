"""Measured judge statistics for the deck. Needs GEMINI_API_KEY and runs in-process (memory store, real Gemini).
   python scripts/eval_judge.py natural 20      # 20 live pipeline runs at the 3-rule checkpoint
   python scripts/eval_judge.py inject 10       # 10 corruptions of each of 3 kinds
Prints a JSON summary; paste the numbers into the deck as 'Verification results on synthetic scenarios (n=...)'."""
import copy, json, os, sys, time
os.environ["STORE"] = "memory"
os.environ.pop("FAKE_AI", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, runs, pipeline
from app.sim.engine import SCENARIOS
from app.analytics.evidence import build_snapshot
from app.ai.client import call_json
from app.ai.schemas import Assessment, JudgeResult
from app.ai import prompts
from app.ai.validate import code_check, normalise

STEP = 45


def new_run():
    r = runs.create_run("power-water")
    r["current_step"] = STEP
    pipeline.store().set("runs", r["id"], r)
    return r["id"]


def natural(n):
    rows = []
    for _ in range(n):
        rid = new_run()
        t0, calls = time.time(), 0
        while True:
            d = pipeline.advance(rid, force_live=True)
            if d["stage"] == "final":
                break
        v = d["verification"]
        rows.append({"status": v.get("status"), "repaired": v.get("repaired"), "removed": len(v.get("removed", [])),
                     "partial": len(v.get("partially_supported", [])), "calls": d["calls"], "secs": round(time.time() - t0, 1),
                     "first_pass_clean": d["timeline"][0]["note"] == "code checks passed" and "overall=accept" in d["timeline"][1]["note"]})
    k = len(rows)
    print(json.dumps({"n": k, "first_pass_accept": sum(r["first_pass_clean"] for r in rows), "repaired": sum(bool(r["repaired"]) for r in rows),
                      "claims_removed_total": sum(r["removed"] for r in rows), "mean_calls": sum(r["calls"] for r in rows) / k,
                      "mean_secs": sum(r["secs"] for r in rows) / k, "rows": rows}, indent=1))


def inject(n):
    sc = SCENARIOS["power-water"]
    snap = build_snapshot(sc, {}, STEP)
    base = normalise(call_json("assess", config.ASSESS_MODEL, prompts.ASSESS_SYSTEM, prompts.assess_text(snap), Assessment).model_dump())
    kinds = {
        "invented_id": lambda a, i: a["hypotheses"][0].update(evidence_ids=[f"ev-{90 + i}"]),
        "altered_number": lambda a, i: a["hypotheses"][-1].update(cause=a["hypotheses"][-1]["cause"].replace("h", f"h (about {14 + i / 10:.1f} h)", 1)),
        "unsupported_hypothesis": lambda a, i: a["hypotheses"].append({"hyp_id": "h-09", "status": "unverified", "evidence_ids": ["ev-01"],
                                                                      "cause": ["Transformer failure confirmed at the substation.", "A cyberattack on the plant controller caused the dip.",
                                                                                "The pump motor has failed and needs replacement."][i % 3]}),
    }
    out = {}
    for name, fn in kinds.items():
        code = judge = missed = 0
        for i in range(n):
            a = copy.deepcopy(base)
            fn(a, i)
            if not code_check(a, snap)["ok"]:
                code += 1
                continue
            jr = call_json("judge", config.JUDGE_MODEL, prompts.JUDGE_SYSTEM, prompts.judge_text(snap, a), JudgeResult)
            if jr.overall != "accept" or any(c.verdict != "supported" for c in jr.checks):
                judge += 1
            else:
                missed += 1
        out[name] = {"n": n, "caught_by_code": code, "caught_by_judge": judge, "missed": missed}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    mode, n = sys.argv[1], int(sys.argv[2])
    natural(n) if mode == "natural" else inject(n)

#!/usr/bin/env python3
"""End-to-end check of a deployed API (real Gemini). Usage: python scripts/verify_hosted.py https://SERVICE_URL
Creates two sandbox runs, exercises assess+judge, data chat and the El Nino planner, prints PASS/FAIL with the exact error text."""
import json, sys, time, urllib.request, urllib.error

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:8080"


def call(method, path, body=None, timeout=70):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r), time.time() - t0
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e), time.time() - t0
        except Exception:
            return e.code, {"detail": str(e)}, time.time() - t0
    except Exception as e:
        return 0, {"detail": f"{type(e).__name__}: {e}"}, time.time() - t0


def run_to(scn, step):
    _, d, _ = call("POST", "/api/runs", {"scenario_id": scn})
    rid = d["run_id"]
    call("POST", f"/api/runs/{rid}/control", {"cmd": "play"})
    cur = 0
    while cur < step:
        _, c, _ = call("POST", f"/api/runs/{rid}/control", {"cmd": "tick", "to_step": step})
        cur = c["current_step"]
    call("POST", f"/api/runs/{rid}/control", {"cmd": "pause"})
    return rid


results = []


def report(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


s, h, _ = call("GET", "/api/health")
report("health", s == 200 and h.get("ok") and not h.get("fake_ai"), f"models {h.get('assess_model')} / {h.get('judge_model')}")
s, h, _ = call("GET", "/api/health/deep")
report("firestore (health/deep)", s == 200 and h.get("ok"), "" if s == 200 else str(h)[:200])

# 1. assessment through the judge
rid = run_to("power-water", 45)
final = None
for _ in range(6):
    s, d, dt = call("POST", f"/api/runs/{rid}/pipeline/advance", {"force_live": True})
    if s != 200:
        report("assess + judge", False, f"HTTP {s}: {str(d)[:240]}"); break
    if d["stage"] == "final":
        final = d; break
if final:
    v = final.get("verification", {})
    report("assess + judge", final.get("ai_status") == "live" and v.get("status") in ("verified", "verified_with_removals"),
           f"status={v.get('status')} supported={v.get('supported')}/{v.get('claims_total')} removed={len(v.get('removed', []))} calls={final.get('calls')}")
    if final.get("ai_status") != "live":
        print("       reason:", v.get("reason"))

# 2. data chat
for q in ("How is the tank doing right now?", "What was the lowest voltage so far?"):
    s, d, dt = call("POST", f"/api/runs/{rid}/qa", {"question": q})
    ok = s == 200 and d.get("answer", {}).get("answer")
    report(f"chat: {q}", bool(ok), f"{dt:.1f}s tools={[t['name'] for t in d.get('tool_calls', [])]} numbers_ok={d.get('verification', {}).get('numbers_ok')}" if ok else f"HTTP {s}: {str(d)[:240]}")

# 3. El Nino outlook + planner
rid = run_to("el-nino", 40)
s, o, _ = call("GET", f"/api/runs/{rid}/outlook")
report("outlook", s == 200 and o.get("available"), f"weather={o.get('climate', {}).get('source')} baseline risk={round(o.get('baseline', {}).get('p_breach', 0) * 100, 1)}%" if s == 200 else str(o)[:200])
s, p, dt = call("POST", f"/api/runs/{rid}/outlook/plan", {}, timeout=90)
if s == 200:
    src = p["source"]
    report("planner (Gemini)", src.startswith("gemini"), f"source={src} {dt:.1f}s plan={p['recommendation']['plan']} numbers_ok={p['recommendation']['verification'].get('numbers_ok')}")
    if p.get("error"):
        print("       ERRORS:", p["error"])
else:
    report("planner (Gemini)", False, f"HTTP {s}: {str(p)[:300]}")

print("\nALL PASS" if all(results) else f"\n{results.count(False)} check(s) FAILED: send me the FAIL lines and any ERRORS text.")

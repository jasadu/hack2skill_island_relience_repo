import logging, os, time
from collections import defaultdict, deque
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import config, runs, pipeline, report
from .store import store
from .sim.engine import SCENARIOS, build_trace
from .analytics.evidence import checkpoints, build_snapshot
from .ai import qa as qamod
from .outlook import service as outlook

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Island Resilience AI", docs_url="/api/docs", openapi_url="/api/openapi.json")

_hits = defaultdict(deque)


def _limit(request: Request, bucket: str, per_min: int):
    ip = (request.headers.get("x-forwarded-for") or request.client.host or "?").split(",")[0].strip()
    q, now = _hits[(ip, bucket)], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_min:
        raise HTTPException(429, "Too many requests; slow down.")
    q.append(now)


@app.middleware("http")
async def size_and_errors(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > 20_000:
        return JSONResponse({"error": "request too large"}, status_code=413)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"ok": True, "assess_model": config.ASSESS_MODEL, "judge_model": config.JUDGE_MODEL,
            "prompt_version": config.PROMPT_VERSION, "store": config.STORE, "fake_ai": config.FAKE_AI}


@app.get("/api/health/deep")
def health_deep(request: Request):
    """Round-trips a Firestore document: proves the service account, IAM roles and database exist."""
    _limit(request, "deep", 6)
    try:
        store().set("health", "ping", {"at": time.time()})
        ok = store().get("health", "ping") is not None
    except Exception as e:
        raise HTTPException(503, f"store error: {type(e).__name__}: {str(e)[:160]}")
    return {"ok": ok, "store": config.STORE}


@app.get("/api/scenarios")
def scenarios():
    return [{"id": s.id, "title": s.title, "description": s.description} for s in SCENARIOS.values()]


class NewRun(BaseModel):
    scenario_id: str = "power-water"


@app.post("/api/runs")
def new_run(body: NewRun, request: Request):
    _limit(request, "runs", 20)
    try:
        return {"run_id": runs.create_run(body.scenario_id)["id"]}
    except ValueError as e:
        raise HTTPException(400, str(e))


class Control(BaseModel):
    cmd: str
    to_step: int | None = None
    speed: int | None = None


def _run_or_404(run_id):
    if runs.get_run(run_id) is None:
        raise HTTPException(404, "run not found")


@app.post("/api/runs/{run_id}/control")
def control(run_id: str, body: Control, request: Request):
    _limit(request, "ctl", config.RATE_LIMIT_PER_MIN)
    _run_or_404(run_id)
    try:
        r = runs.control(run_id, body.cmd, body.to_step, body.speed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"run_id": r["id"], "current_step": r["current_step"], "state": r["state"], "speed": r["speed"]}


@app.get("/api/runs/{run_id}/state")
def state(run_id: str, request: Request):
    _limit(request, "state", config.RATE_LIMIT_PER_MIN * 2)
    v = runs.view(run_id)
    if v is None:
        raise HTTPException(404, "run not found")
    return v


class Advance(BaseModel):
    force_live: bool = False


@app.post("/api/runs/{run_id}/pipeline/advance")
def advance(run_id: str, body: Advance, request: Request):
    _limit(request, "ai", config.AI_RATE_LIMIT_PER_MIN)
    _run_or_404(run_id)
    try:
        return pipeline.advance(run_id, body.force_live)
    except pipeline.NoIncident:
        raise HTTPException(409, "no incident has been detected yet")


class Approve(BaseModel):
    assessment_id: str


@app.post("/api/runs/{run_id}/actions/{action_id}/approve")
def approve(run_id: str, action_id: str, body: Approve, request: Request):
    _limit(request, "act", 30)
    _run_or_404(run_id)
    try:
        return runs.approve(run_id, body.assessment_id, action_id)
    except runs.Conflict as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404, "action not found")


@app.post("/api/runs/{run_id}/actions/{action_id}/simulate")
def simulate(run_id: str, action_id: str, request: Request):
    _limit(request, "act", 30)
    _run_or_404(run_id)
    try:
        return runs.simulate(run_id, action_id)
    except runs.Conflict as e:
        raise HTTPException(409, str(e))


class Turn(BaseModel):
    q: str
    a: str


class Ask(BaseModel):
    question: str
    history: list[Turn] = []


@app.post("/api/runs/{run_id}/qa")
def ask(run_id: str, body: Ask, request: Request):
    _limit(request, "ai", config.AI_RATE_LIMIT_PER_MIN)
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    q = body.question.strip()[:400]
    if not q:
        raise HTTPException(400, "empty question")
    if run.get("qa_calls", 0) >= config.PER_RUN_QA_CAP:
        raise HTTPException(429, "question limit reached for this run")
    sc = SCENARIOS[run["scenario_id"]]
    rows = build_trace(sc, run["current_step"], run["actions"], run.get("sim_start"))
    snap = build_snapshot(sc, run["actions"], run["current_step"], run.get("sim_start"))
    store().incr("runs", run_id, "qa_calls", 1)
    try:
        a, calls, ver = qamod.answer(q, {"sc": sc, "rows": rows, "snapshot": snap}, [t.model_dump() for t in body.history[-4:]])
    except Exception as e:                       # AIUnavailable, invalid output...
        raise HTTPException(503, f"AI unavailable: {str(e)[:120]}")
    return {"answer": a.model_dump(), "tool_calls": [{"name": n, "arguments": x, "result": r} for n, x, r in calls],
            "verification": ver, "snapshot_step": snap["step"]}


def _outlook_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except outlook.Unavailable as e:
        raise HTTPException(409, str(e))
    except outlook.Conflict as e:
        raise HTTPException(409, str(e))
    except outlook.Budget as e:
        raise HTTPException(429, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/runs/{run_id}/outlook")
def get_outlook(run_id: str, request: Request, enso: str = "strong", beta: float | None = None):
    _limit(request, "outlook", 30)
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return _outlook_call(outlook.view, run_id, run, enso, beta)


class OutlookParams(BaseModel):
    enso: str = "strong"
    beta: float | None = None


@app.post("/api/runs/{run_id}/outlook/plan")
def outlook_plan(run_id: str, body: OutlookParams, request: Request):
    _limit(request, "ai", config.AI_RATE_LIMIT_PER_MIN)
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return _outlook_call(outlook.plan, run_id, run, body.enso, body.beta)


class OutlookApprove(OutlookParams):
    desal_hours: float
    conservation_pct: float


@app.post("/api/runs/{run_id}/outlook/approve")
def outlook_approve(run_id: str, body: OutlookApprove, request: Request):
    _limit(request, "act", 30)
    run = runs.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return _outlook_call(outlook.approve, run_id, run, body.desal_hours, body.conservation_pct, body.enso, body.beta)


@app.get("/api/runs/{run_id}/report")
def get_report(run_id: str, format: str = "md"):
    _run_or_404(run_id)
    out = report.build(run_id, "json" if format == "json" else "md")
    if out is None:
        raise HTTPException(404, "run not found")
    body, mime = out
    return PlainTextResponse(body, media_type=mime,
                             headers={"Content-Disposition": f'attachment; filename="incident-{run_id}.{"json" if format == "json" else "md"}"'})


@app.get("/api/runs/{run_id}/sop/{sop_id:path}")
def sop(run_id: str, sop_id: str):
    from . import sops
    s = sops.SECTION_BY_ID.get(sop_id)
    if not s:
        raise HTTPException(404, "unknown SOP section")
    return s


# Local convenience: serve the built frontend when it exists (in production Firebase Hosting serves it).
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists() and os.getenv("SERVE_FRONTEND", "") == "1":
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")

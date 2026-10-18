# SentriCORE Island Resilience AI

AI Builder Cup 2026 entry, theme **Sustainability & Social Impact**. An operations assistant for a **fictional** island whose drinking water depends on electricity. All data is synthetic.

**Live demo:** `https://crested-polygon-511408-h9.web.app/` (replace before submitting) · **Video:** `https://youtu.be/7atzFh3NOBI` · **Deck:** `https://drive.google.com/file/d/1G8V3m6c0MKLRsh5jdSLIhSRSLgXI9Myb/view?usp=drive_link`

When power drops, the desalination plant slows, the reservoir drains and barangays run dry. The app spots that chain early, explains it with evidence, and helps an operator act before taps go dry.

## How it works

1. **Detect.** Deterministic rules watch voltage, water inflow, the reserve horizon and tank sensor health. No AI in the alarm.
2. **Assess.** Gemini writes an assessment that cites evidence IDs (`ev-01` …) and procedure IDs. Times are given as real clock times.
3. **Verify.** Code checks every ID and number, then a second Gemini model judges every claim. Unsupported claims are removed and shown. One repair loop.
4. **Approve.** Nothing happens without an operator approving it. Recovery is **simulated**; the detector, never an approval, decides when the incident is resolved.
5. **Re-assess.** One action at a time: the simulator measures the effect, pauses, and the assessment is refreshed with what was done.
6. **Report.** A downloadable run report holds every detail: verification, model calls, planner, approvals, audit trail.

Also: a **data chat** (Gemini with run-scoped tools; every number is checked against tool results) and a **14-day El Niño outlook** (live Open-Meteo weather with a labelled fallback, seeded Monte Carlo, a Gemini planner agent whose recommendation is re-simulated in code, with a code optimiser as the last fallback).

## Scenarios

| Scenario | Starts | What happens |
|---|---|---|
| Normal operations | 04:00 | Daily demand pattern, no incident |
| Power instability reduces water supply | 05:30 | Voltage dips at the morning peak; production falls, reserve depletes. Restore the feed (and optionally conserve demand) and the incident resolves |
| Tank sensor fault | 16:00 | The tank gauge goes silent; forecast withheld; a simulated technician restores it |
| El Niño heat wave | 04:00 | No fault today; the 14-day outlook shows the reserve at risk and the planner recommends desalination hours and conservation |

## Architecture

```
Browser (React) --/api/**--> Firebase Hosting --> Cloud Run (FastAPI) --> Firestore
                                                       \--> Gemini API (key in Secret Manager)
```

Google Cloud: Cloud Run (asia-southeast1), Firestore, Secret Manager, Firebase Hosting, Gemini API. Models: `ASSESS_MODEL=gemini-3.8-flash`, `JUDGE_MODEL=gemini-3.6-flash`, thinking level `low`, prompt version `v1` (`backend/app/config.py`).

The simulator is stateless: the telemetry trace is a pure function of (scenario, seed, step, executed actions, start time). Firestore stores runs, assessments, approvals and counters, not telemetry.

## Run locally (no Google account needed)

```bash
cd backend && pip install -r requirements-dev.txt
FAKE_AI=1 STORE=memory CLIMATE_LIVE=0 python -m pytest -q        # 63 tests
cd ../frontend && npm ci && npm run build
cd ../backend && FAKE_AI=1 STORE=memory SERVE_FRONTEND=1 uvicorn app.main:app --port 8080   # http://localhost:8080
# real Gemini locally: export GEMINI_API_KEY=...; STORE=memory SERVE_FRONTEND=1 uvicorn app.main:app --port 8080
```

`FAKE_AI=1` is a deterministic stand-in used **only by tests**. Never set it in the deployed service (`/api/health` shows `fake_ai`).

## Deploy to Google Cloud

One-time setup (project, billing, APIs, service account, Firestore, secret `gemini-api-key`) is in the build guide, section 4. Then:

```bash
PROJECT_ID=your-project ./deploy.sh                 # tests, Cloud Run, Firebase Hosting, hosted check
MIN_INSTANCES=1 PROJECT_ID=your-project ./deploy.sh # keep one instance warm during judging
./check_submission.sh                               # secrets scan, pinned versions, tests
python backend/scripts/verify_hosted.py <CLOUD_RUN_URL>   # real-Gemini end-to-end check
```

The Gemini key lives only in Secret Manager and is injected into Cloud Run; it is never in the repo or the frontend bundle. Sandbox runs need no sign-in: each visitor gets an isolated synthetic run, protected by rate limits, a per-run AI cap and a daily AI budget.

## Layout

`backend/app/sim` stateless simulator and daily demand pattern · `analytics` reserve horizon, detection rules, evidence registry, measured action effects · `ai` Gemini client, prompts, schemas, code checks, Q&A tools · `outlook` 14-day projection and planner agent · `pipeline.py` one-model-call-per-request state machine (stays under the 60 s Hosting limit) · `runs.py` runs, approvals, simulated execution · `report.py` run report · `sop_docs/` five demo SOPs · `scripts/` smoke test, judge evaluation, hosted check · `frontend/src` React UI.

## Verification results

Measured with `backend/scripts/eval_judge.py`, real Gemini, synthetic power-instability incident, 16 Oct 2026. No claim is made about field accuracy.

**Live pipeline (n = 20 runs, no cache).** 8 of 20 first drafts (40%) passed the code checks and the judge. 11 (55%) were flagged, repaired once and then passed. 1 (5%) ended UNVERIFIED because the judge step did not complete, and is labelled as such on screen. 19 of 20 final assessments were verified. Mean 3.1 model calls and 28.7 s per assessment (each call is its own request, under the 60 s Hosting limit).

**Injected errors (n = 10 per type, starting from a draft that passes the code checks; the judge accepted that clean draft as a control).**

| Corruption | Caught by code checks | Caught by the judge model | Missed |
|---|---|---|---|
| Invented evidence ID | 10 | 0 | 0 |
| Altered number (clearly false value) | 10 | 0 | 0 |
| Unsupported claim (for example "transformer failure confirmed") | 0 | 10 | 0 |

Scope: three corruption types on one scenario. This measures whether the checks catch deliberate errors, not how correct the assessments are in general.

## Limitations

Synthetic scenarios only. Detection thresholds are scenario settings, not safety limits. The reserve horizon holds inflow and day-average demand constant. The 14-day outlook is a scenario projection under stated, adjustable assumptions, not a weather forecast; the island and its coordinates are fictional. SOPs are demo text and retrieval is keyword-based, not semantic. Simulated actions do not touch any real system. Production would add sign-in, roles, real telemetry adapters and approved procedures.

## License

MIT, see `LICENSE`.

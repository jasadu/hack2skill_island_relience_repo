import { useCallback, useEffect, useRef, useState } from "react";
import { api, Doc, Scenario, State } from "./api";
import Charts from "./Charts";
import Outlook from "./Outlook";
import IslandMap from "./IslandMap";
import Incident from "./Incident";
import Chat from "./Chat";
import Drawer from "./Drawer";

const fmt = (n: number | null | undefined, d = 0) => (n === null || n === undefined ? "n/a" : n.toLocaleString(undefined, { maximumFractionDigits: d }));

function Metric({ label, value, unit, sub, src, tone }: { label: string; value: string; unit?: string; sub?: string; src: "Observed" | "Calculated"; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className="metric">
      <div className="lbl"><span>{label}</span><span className={`src ${src === "Calculated" ? "calc" : ""}`}>{src}</span></div>
      <div className="val" style={tone ? { color: `var(--${tone})` } : undefined}>{value}{unit && <span className="muted" style={{ fontSize: 13 }}> {unit}</span>}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scn, setScn] = useState("power-water");
  const [runId, setRunId] = useState<string | null>(() => { try { return sessionStorage.getItem("runId"); } catch { return null; } });
  const [s, setS] = useState<State | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selNode, setSelNode] = useState("tank");
  const [drawer, setDrawer] = useState<string | null>(null);
  const [lastDoc, setLastDoc] = useState<Doc | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const acc = useRef(0);
  const settledSeen = useRef(-1);                 // -1 = not yet read, so loading an old run never triggers the pause
  const prevDet = useRef("none");
  const ticking = useRef(false);

  useEffect(() => { api.scenarios().then(setScenarios).catch(() => setErr("Cannot reach the API.")); }, []);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const st = await api.state(runId);
      setS(st);
      const a = st.assessment as Doc | null;
      if (a && a.stage === "final") setLastDoc(a);
      if (prevDet.current === "none" && st.detection.state === "active" && st.run.state === "playing") {
        await api.control(runId, { cmd: "pause" });
        setToast(`Incident detected at ${st.detection.started_at}. Simulation paused: assess it when ready, or resume.`);
        st.run.state = "paused"; setS({ ...st });
      }
      const settled = Object.values(st.actions).filter((x) => x.effect?.settled).length;
      if (settledSeen.current >= 0 && settled > settledSeen.current && st.run.state === "playing") {
        await api.control(runId, { cmd: "pause" });
        setToast(st.detection.state === "resolved"
          ? `The effect of your action is now visible and the detector confirms recovery (${st.sim_clock}). Simulation paused.`
          : `The effect of your action is now visible (${st.sim_clock}). Simulation paused: re-assess the incident to update it.`);
        st.run.state = "paused"; setS({ ...st });
      }
      settledSeen.current = settled;
      prevDet.current = st.detection.state;
    } catch (e) {
      if ((e as { status?: number }).status === 404) { setRunId(null); setS(null); } else setErr((e as Error).message);
    }
  }, [runId]);

  useEffect(() => { if (runId) { try { sessionStorage.setItem("runId", runId); } catch { /* ignore */ } refresh(); } }, [runId, refresh]);

  // Client-driven ticks: speed/60 steps per second; the server clamps catch-up and is idempotent.
  useEffect(() => {
    if (!runId || !s || s.run.state !== "playing") return;
    const t = setInterval(async () => {
      if (ticking.current) return;
      ticking.current = true;
      try {
        acc.current += s.run.speed / 60;
        const n = Math.floor(acc.current);
        if (n >= 1) {
          acc.current -= n;
          await api.control(runId, { cmd: "tick", to_step: s.run.current_step + n });
        }
        await refresh();
      } catch (e) { setErr((e as Error).message); } finally { ticking.current = false; }
    }, 1000);
    return () => clearInterval(t);
  }, [runId, s?.run.state, s?.run.speed, s?.run.current_step, refresh]);

  async function start() {
    setErr(null); setToast(null); setLastDoc(null); prevDet.current = "none"; settledSeen.current = -1; acc.current = 0;
    const r = await api.newRun(scn); setS(null); setRunId(r.run_id);
  }
  async function ctl(body: object) {
    if (!runId) return;
    const r = await api.control(runId, body) as { run_id: string };
    if ((body as { cmd: string }).cmd === "reset") { setLastDoc(null); prevDet.current = "none"; settledSeen.current = -1; acc.current = 0; setToast(null); setRunId(r.run_id); } else await refresh();
  }

  if (!runId || !s) {
    return (<>
      <div className="banner">Demo for demonstration purposes only. Not for operational use.</div>
      <div className="intro">
        <h1>Island Resilience AI</h1>
        <p className="lead">An AI operations assistant for an island whose drinking water depends on electricity. When power drops, the desalination plant slows, the reservoir drains and barangays run dry. This app spots that chain early, explains it with evidence, and helps an operator act before taps go dry.</p>
        <div className="how">
          <div><b>1. Detect</b><span>Deterministic rules watch tank level, voltage and flows. No AI guesswork in the alarm.</span></div>
          <div><b>2. Assess</b><span>Gemini writes an assessment that cites its evidence. Every number is checked in code.</span></div>
          <div><b>3. Verify</b><span>An independent judge model checks each claim before the operator sees it.</span></div>
          <div><b>4. Approve</b><span>Nothing happens until the operator approves. Recovery then runs in the simulator.</span></div>
        </div>
        <ul className="feat">
          <li><b>Live island map</b> with the reservoir, plant, power supply and three barangays.</li>
          <li><b>Ask about the data:</b> chat with the AI about current or past readings.</li>
          <li><b>El Niño 14-day outlook:</b> forecast heat, demand and reserve risk, and let a planner agent recommend desalination hours and conservation.</li>
        </ul>
        <div className="card">
          <h2>Choose a scenario</h2>
          <p className="small muted">Each run is a 12-hour simulated day that starts at the real date and time you press Start run. Pick a situation to explore.</p>
          <select value={scn} onChange={(e) => setScn(e.target.value)} aria-label="Scenario">{scenarios.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}</select>
          <p className="small muted">{scenarios.find((x) => x.id === scn)?.description}</p>
          <button className="primary" onClick={start} disabled={!scenarios.length}>Start run</button>
          {err && <div className="alert bad small" role="alert" style={{ marginTop: 8 }}>{err}</div>}
        </div>
      </div></>);
  }

  const { run, horizon: h, latest: L } = s;
  const net = L.inflow?.value !== null && L.outflow?.value !== null && L.inflow && L.outflow ? (L.inflow.value as number) - (L.outflow.value as number) : null;
  const doc = (s.assessment && s.assessment.stage === "final" ? (s.assessment as Doc) : null) ?? (s.new_evidence ? lastDoc : null);
  const outdated = !!doc && !!s.assessment && s.assessment.stage !== "final";
  const aiBadge = !doc ? { t: "AI: idle", c: "info" } : doc.ai_status === "unavailable" ? { t: "AI: unavailable", c: "warn" } : doc.source === "cached" ? { t: "AI: cached", c: "info" } : { t: "AI: live", c: "ok" };
  const hLabel = h.state === "unavailable" ? "Unavailable" : h.state === "no_depletion" ? "No depletion" : h.state === "below_critical" ? "Below critical" : `${fmt(h.hours_to_critical, 1)} h`;
  const tankAge = L.tank_volume?.value === null ? L.tank_volume?.age_steps : 0;

  return (<>
    <div className="banner">Demo for demonstration purposes only. Not for operational use.</div>
    <header className="top">
      <h1>Island Resilience AI</h1>
      <span className="badge info">{s.scenario.title}</span>
      <div className="seg" role="group" aria-label="Playback">
        <button className={run.state === "playing" ? "on" : ""} onClick={() => ctl({ cmd: "play" })}>Play</button>
        <button className={run.state === "paused" ? "on" : ""} onClick={() => ctl({ cmd: "pause" })}>Pause</button>
      </div>
      <div className="seg" role="group" aria-label="Speed">{[1, 10, 60].map((x) => <button key={x} className={run.speed === x ? "on" : ""} onClick={() => ctl({ cmd: "speed", speed: x })}>{x}×</button>)}</div>
      <button onClick={() => ctl({ cmd: "reset" })}>Reset</button>
      <button onClick={() => { setRunId(null); setS(null); try { sessionStorage.removeItem("runId"); } catch { /* ignore */ } }}>New scenario</button>
      <span className="spacer" />
      <progress className="runbar" max={s.scenario.n_steps - 1} value={run.current_step} aria-label="Run progress" />
      <span className="small muted" title={`Simulation started ${s.sim_start_clock}`}>{s.sim_clock} · {(run.current_step / 60).toFixed(1)} h elapsed</span>
      <span className={`badge ${aiBadge.c}`}>{aiBadge.t}</span>
    </header>
    {toast && <div className="alert warn" role="status" style={{ margin: "12px 16px 0" }}>{toast} <button className="link" onClick={() => setToast(null)}>dismiss</button></div>}
    {err && <div className="alert bad" role="alert" style={{ margin: "12px 16px 0" }}>{err} <button className="link" onClick={() => setErr(null)}>dismiss</button></div>}
    <main className="dash">
      <div className="kpis">
        <Metric label="Tank volume" src="Observed" value={fmt(L.tank_volume?.value)} unit="L" sub={tankAge ? `sensor silent ${tankAge} min` : "fresh"} tone={tankAge ? "bad" : undefined} />
        <Metric label="Inflow (production)" src="Observed" value={fmt(L.inflow?.value)} unit="L/h" />
        <Metric label="Outflow (demand)" src="Observed" value={fmt(L.outflow?.value)} unit="L/h" />
        <Metric label="Net flow" src="Calculated" value={fmt(net)} unit="L/h" sub={net !== null && net < 0 ? "tank is losing water" : "tank is stable or filling"} />
        <Metric label="Voltage" src="Observed" value={fmt(L.voltage?.value, 1)} unit="V" tone={(L.voltage?.value ?? 230) < 210 ? "bad" : undefined} />
        <Metric label="Reserve horizon" src="Calculated" value={hLabel} sub={h.state === "depleting" ? `to critical · at average daily demand` : h.state === "unavailable" ? "forecast withheld: data too thin" : undefined}
          tone={h.state === "depleting" && (h.hours_to_critical ?? 99) < 16 ? "bad" : h.state === "unavailable" ? "warn" : undefined} />
      </div>
      <div className="map-row">
        <IslandMap s={s} selected={selNode} onSelect={setSelNode} />
        <div className="side">
          <Incident s={s} doc={doc} outdated={outdated} runId={runId} openEv={setDrawer} refresh={refresh} />
          <Chat runId={runId} used={run.qa_calls} cap={run.qa_cap} openEv={setDrawer} />
        </div>
      </div>
      <Outlook runId={runId} step={run.current_step} sig={`${s.detection.state}|${s.detection.active_rules.join(",")}|${Object.keys(s.actions).join(",")}`} onToast={setToast} />
      <Charts s={s} />
      <div className="small muted foot">Run {run.id} · {run.ai_calls} model call(s) this run · <a href={api.reportUrl(run.id, "md")}>Download run report</a> (AI details, errors, planner)</div>
    </main>
    {drawer && <Drawer id={drawer} doc={doc ?? lastDoc} onClose={() => setDrawer(null)} />}
  </>);
}

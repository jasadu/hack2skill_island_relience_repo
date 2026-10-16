import { useState } from "react";
import { api, Doc, State } from "./api";

const PROGRESS: Record<string, string> = {
  new: "Assessing the evidence with Gemini…", assessed: "Checking IDs and numbers, then verifying with an independent judge…",
  needs_repair: "Repairing flagged claims…", repaired: "Re-verifying the repaired assessment…",
};

const STEPS = ["Assess", "Approve", "Simulate", "Watch effect", "Re-assess"];
const METRIC = { voltage: "Voltage", inflow: "Inflow", outflow: "Demand", tank_volume: "Tank reading" } as Record<string, string>;

/** Where the operator is in the loop, in one line. */
function Steps({ s, doc, final }: { s: State; doc: Doc | null; final: boolean }) {
  const pending = Object.values(s.actions).filter((a) => a.effect && !a.effect.settled);
  const approvedHere = doc ? Object.values(s.approvals).some((a) => a.assessment_id === doc.id) : false;
  const executedHere = Object.keys(s.actions).length > 0;
  let at = 0, text = "Assess the incident to get an evidence-cited assessment.";
  if (s.detection.state === "resolved") { at = 5; text = "Recovered. The detector confirmed the readings are back to normal."; }
  else if (s.new_evidence && executedHere) { at = 4; text = "The effect of your action is now visible. Re-assess to update the picture, then approve the next action."; }
  else if (s.new_evidence) { at = 0; text = "New evidence since the last assessment. Re-assess to refresh; earlier approvals no longer apply."; }
  else if (pending.length) { const p = pending[0].effect; at = 3; text = `${p.title} is taking effect. Let the simulation run: the effect shows in the readings by ${p.ready_at} (${p.minutes_left} min).`; }
  else if (final && approvedHere) { at = 2; text = "Approved. Click the button on the action to run it in the simulator."; }
  else if (final && executedHere && doc?.assessment && doc.assessment.actions.length === 0) { at = 4; text = "No further actions are needed. Press Play: the detector resolves the incident after 5 healthy minutes."; }
  else if (final) { at = 1; text = "Review the recommended actions and approve one. Do one at a time."; }
  return (<div className="steps" aria-label="Incident workflow">
    <ol>{STEPS.map((n, i) => <li key={n} className={i < at ? "done" : i === at ? "now" : ""}>{n}</li>)}</ol>
    <div className="small">{text}</div></div>);
}

function Chips({ ids, sop, open }: { ids: string[]; sop?: boolean; open: (id: string) => void }) {
  return <>{ids.map((i) => <button key={i} className={`chip ${sop ? "sop" : ""}`} onClick={() => open(i)}>{i}</button>)}</>;
}

const RULE_WORDS: Record<string, string> = { low_voltage: "low supply voltage", low_production: "low water production", low_reserve_horizon: "reservoir running down", tank_sensor_silent: "tank level sensor silent" };

export default function Incident({ s, doc, outdated, runId, openEv, refresh }: {
  s: State; doc: Doc | null; outdated: boolean; runId: string; openEv: (id: string) => void; refresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [force, setForce] = useState(false);
  const det = s.detection;

  async function assess() {
    setErr(null); setBusy(PROGRESS.new);
    try {
      let stage = "new";
      for (let i = 0; i < 6 && stage !== "final"; i++) {
        setBusy(PROGRESS[stage] ?? "Working…");
        const d = await api.advance(runId, force);
        stage = d.stage;
        await refresh();
      }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  }
  async function act(fn: () => Promise<unknown>) {
    setErr(null);
    try { await fn(); await refresh(); } catch (e) { setErr((e as Error).message); }
  }

  if (det.state === "none") return (
    <div className="card incident"><h2>Incident workspace</h2>
      <p className="muted">No incident. Operations look normal. Start playback to watch for a disruption.</p></div>
  );

  const a = doc?.assessment, v = doc?.verification;
  const final = doc?.stage === "final";
  const pendingMin = Math.max(0, ...Object.values(s.actions).filter((x) => x.effect && !x.effect.settled).map((x) => x.effect.minutes_left));
  return (
    <div className="card incident">
      <h2>Incident workspace</h2>
      <div className={`alert ${det.state === "active" ? "bad" : "ok"}`}>
        <div className="row"><span className={`badge ${det.state === "active" ? "bad" : "ok"}`}>{det.state === "active" ? `ACTIVE · ${det.severity}` : "RESOLVED"}</span>
          <span className="small">Started {det.started_at}{det.resolved_at && ` · resolved ${det.resolved_at}`}</span></div>
        <div className="small muted" style={{ marginTop: 4 }}>Triggered by: {det.active_rules.map((r) => RULE_WORDS[r] ?? r).join(", ") || "nothing now (recovering)"}. Resolution is decided by the detector after 5 healthy minutes, never by an approval.</div>
      </div>

      <Steps s={s} doc={doc} final={!!final} />
      {err && <div className="alert bad small" role="alert">{err}</div>}

      <div className="row" style={{ marginBottom: 8 }}>
        <button className="primary" disabled={!!busy || (final && !s.new_evidence)} onClick={assess}>{pendingMin > 0 ? `Waiting for the effect (${pendingMin} min)` : final && !s.new_evidence ? "Assessment up to date" : s.new_evidence ? "Re-assess incident" : "Assess incident"}</button>
        <label className="small"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Force live (bypass cache)</label>
      </div>
      {busy && <div className="progress" role="status"><span className="spin" />{busy}</div>}

      {doc && doc.ai_status === "unavailable" && (<>
        <div className="alert warn"><span className="badge warn">AI unavailable</span> Gemini could not be reached right now (details in the report). Charts, detection and forecasts keep working. Fixed SOP checklist:</div>
        {doc.fallback_checklist?.map((c) => <div key={c.id} className="small"><button className="chip sop" onClick={() => openEv(c.id)}>{c.id}</button> {c.title}</div>)}
      </>)}

      {a && v && (
        <div style={outdated ? { opacity: 0.55 } : undefined}>
          {outdated && <div className="alert warn small">Outdated assessment ({doc!.snapshot.clock ?? "earlier"}). Re-assess before acting.</div>}
          <div className="row">
            <span className={`badge ${doc!.source === "cached" ? "info" : "ok"}`}>{doc!.source === "cached" ? "Cached (same evidence)" : "Live"}</span>
            <span className={`badge ${v.status === "verified" ? "ok" : v.status === "unverified" ? "bad" : "warn"}`}>
              {v.status === "verified" && `Verified · ${v.supported}/${v.claims_total} claims supported`}
              {v.status === "verified_with_removals" && `${v.removed?.length} claim(s) removed after verification`}
              {v.status === "unverified" && "UNVERIFIED (judge unavailable)"}
            </span>
            {v.repaired && <span className="badge info">repaired once</span>}
          </div>
          <p><strong>{a.summary}</strong> <span className="src ai">AI</span></p>
          <div className="small muted">Data quality: {a.data_quality}</div>

          <h2 style={{ marginTop: 12 }}>Hypotheses (unverified)</h2>
          {a.hypotheses.map((h) => (
            <div className="hyp" key={h.hyp_id}>
              <div><span className="src ai">AI hypothesis</span> <strong>{h.hyp_id}</strong> {h.cause} {h.flag && <span className="badge warn">partially supported</span>}</div>
              <Chips ids={h.evidence_ids} open={openEv} />
            </div>
          ))}
          {!a.hypotheses.length && <div className="muted small">None.</div>}

          <h2 style={{ marginTop: 12 }}>Recommended actions</h2>
          {a.actions.map((x) => {
            const ap = s.approvals[x.action_id];
            const ex = x.simulated_command !== "none" ? s.actions[x.simulated_command] : undefined;
            const status = ex ? ex.status : ap ? "approved" : "proposed";
            return (
              <div className="act" key={x.action_id}>
                <div className="row"><strong>{x.action_id} · {x.title}</strong><span className="spacer" /><span className="badge info">{status}</span></div>
                {ex?.effect && <div className="effect small"><b>{ex.effect.settled ? "Effect confirmed" : `Taking effect, ready ${ex.effect.ready_at} (${ex.effect.minutes_left} min)`}</b>
                  {ex.effect.metrics.map((m) => <span key={m.name}> · {METRIC[m.name] ?? m.name} {m.before} → {m.now}{typeof m.now === "number" ? ` ${m.unit}` : ""}</span>)}</div>}
                <div className="small">{x.owner_role} · priority {x.priority}. {x.rationale} {x.flag && <span className="badge warn">partially supported</span>}</div>
                <div><Chips ids={x.evidence_ids} open={openEv} /><Chips ids={x.procedure_ids} sop open={openEv} /></div>
                <div className="row" style={{ marginTop: 6 }}>
                  <button disabled={outdated || !!ap || !final} onClick={() => act(() => api.approve(runId, x.action_id, doc!.id))}>Approve</button>
                  <button disabled={!ap || !!ex || x.simulated_command === "none"} onClick={() => act(() => api.simulate(runId, x.action_id))}>
                    {x.simulated_command === "none" ? "Manual task (no simulation)" : x.simulated_command === "repair_tank_sensor" ? "Dispatch technician (simulated)" : "Simulate execution"}</button>
                </div>
              </div>
            );
          })}
          {!a.actions.length && <div className="muted small">None.</div>}

          <p className="small muted">Verification detail, limitations, model calls and timing are in the <a href={api.reportUrl(runId, "md")}>downloadable report</a>.</p>
        </div>
      )}

      {det.state === "resolved" && <p className="small">Recovery confirmed by the detector. <a href={api.reportUrl(runId, "md")}>Download report</a> · <a href={api.reportUrl(runId, "json")}>JSON</a></p>}
      {det.state === "active" && final && <p className="small"><a href={api.reportUrl(runId, "md")}>Download interim report</a> · <a href={api.reportUrl(runId, "json")}>JSON</a></p>}
    </div>
  );
}

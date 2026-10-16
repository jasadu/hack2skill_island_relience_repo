import { useCallback, useEffect, useRef, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, FanPt, Outlook as OutlookT, PlanDoc, PlanSummary } from "./api";

const axis = { stroke: "var(--muted)", fontSize: 11 };
const N = (x: number, d = 0) => x.toLocaleString(undefined, { maximumFractionDigits: d });
const planName = (h: number, c: number) => `${N(h, 1)} h/day${c ? ` + ${c}% conservation` : ""}`;
// Plain-language wording for the recommendation card (exact figures stay in the plan table and the downloadable report).
const riskWord = (pct: number) => (pct < 5 ? "very low" : pct < 15 ? "low" : pct < 35 ? "moderate" : pct < 60 ? "high" : "very high");
const saveWord = (c: number) => (c <= 0 ? "" : c <= 5 ? "ask everyone to use a little less water" : c <= 12 ? "ask everyone to use noticeably less water" : "ask everyone to cut water use significantly");
const planWords = (h: number, c: number, base: number) => {
  const parts = [h > base ? `run the desalination plant longer, about ${N(h, 0)} hours a day instead of ${N(base, 0)}` : `keep the plant at ${N(h, 0)} hours a day`];
  if (c > 0) parts.push(saveWord(c));
  return parts.join(", and ");
};

function fanData(base: FanPt[], over?: FanPt[], inputLevel?: number) {
  const rows = [{ day: 0, base: [inputLevel ?? base[0].p50, inputLevel ?? base[0].p50], baseMed: inputLevel, plan: over ? [inputLevel, inputLevel] : undefined, planMed: over ? inputLevel : undefined }] as Record<string, unknown>[];
  base.forEach((b, i) => rows.push({ day: b.day, base: [b.p10, b.p90], baseMed: b.p50, plan: over ? [over[i].p10, over[i].p90] : undefined, planMed: over ? over[i].p50 : undefined }));
  return rows;
}

export default function Outlook({ runId, step, sig, onToast }: { runId: string; step: number; sig: string; onToast: (m: string) => void }) {
  const [enso, setEnso] = useState("strong");
  const [beta, setBeta] = useState(4);                       // percent demand change per deg C
  const [o, setO] = useState<OutlookT | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<PlanSummary | null>(null);
  const [plan, setPlan] = useState<PlanDoc | null>(null);
  const [busy, setBusy] = useState<"" | "plan" | "approve">("");
  const timer = useRef<number | undefined>(undefined);
  const bucket = Math.floor(step / 5);          // refresh every 5 simulated minutes

  const load = useCallback(() => {
    api.outlook(runId, enso, beta / 100).then((r) => { setO(r); setErr(null); }).catch((e: Error) => { setErr(e.message); setO(null); });
  }, [runId, enso, beta]);
  useEffect(() => { window.clearTimeout(timer.current); timer.current = window.setTimeout(load, 250); return () => window.clearTimeout(timer.current); }, [load, bucket, sig]);   // sig changes when an incident starts, resolves, or an action runs: refresh at once
  useEffect(() => { setPlan(null); setPreview(null); }, [runId, sig]);   // a recommendation made under old conditions is not kept

  async function runPlanner() {
    setBusy("plan"); setPlan(null);
    try { const d = await api.outlookPlan(runId, enso, beta / 100); setPlan(d); setPreview(null); } catch (e) { onToast((e as Error).message); } finally { setBusy(""); }
  }
  async function approve(h: number, c: number) {
    setBusy("approve");
    try { await api.outlookApprove(runId, enso, beta / 100, h, c); onToast(`Plan approved: ${planName(h, c)}.`); setPreview(null); load(); }
    catch (e) { onToast((e as Error).message); } finally { setBusy(""); }
  }

  if (err) return (<div className="card"><h2>14-day El Niño outlook</h2><div className="alert warn small" role="status">{err}</div></div>);
  if (!o) return (<div className="card"><h2>14-day El Niño outlook</h2><div className="small muted">Computing projection…</div></div>);

  const active = o.active_plan;
  const over = active?.projection.fan ?? preview?.fan;
  const rows = fanData(o.baseline.fan, over, o.inputs.level_l);
  const risk = o.baseline.p_breach * 100;
  const tone = risk >= 50 ? "bad" : risk >= 10 ? "warn" : "ok";
  const rec = plan?.recommendation;
  const live = o.climate.source === "open-meteo";
  const temps = o.climate.temp_band.map((t, i) => ({ day: t.day, band: [t.p10, t.p90], med: t.p50, fc: o.climate.days[Math.min(i + 1, o.climate.days.length - 1)]?.tmax_c }));

  return (
    <div className="card outlook">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>14-day El Niño outlook</h2>
        <span className={`badge ${live ? "ok" : "warn"}`} title={o.climate.note}>{live ? "Weather: Open-Meteo live" : "Weather: fallback climatology"}</span>
      </div>
      <p className="small muted" style={{ margin: "4px 0 10px" }}>As of {o.as_of ?? "now"}: {N(o.inputs.level_l)} L in the reservoir, demand {N(o.inputs.demand_l_day)} L/day, supply {N(o.inputs.supply_l_day_now)} L/day.</p>

      {o.supply_note && <div className="alert warn small" role="status">{o.supply_note}</div>}
      <div className="ol-grid">
        <div className={`ol-kpi ${tone}`}>
          <div className="small muted">Chance of reaching the critical level within 14 days, at {o.inputs.baseline_hours} h/day</div>
          <div className="big">{N(risk, 0)}%</div>
          <div className="small muted">{o.baseline.breach_day_p50 ? `typically by day ${o.baseline.breach_day_p50}` : o.baseline.breach_day_p10 ? `earliest likely day ${o.baseline.breach_day_p10}` : "no breach in most futures"} · target ≤ {o.target_pct}%</div>
          {active && <div className="small okc">With the approved plan: {N(active.projection.p_breach * 100, 1)}%</div>}
        </div>
        <label className="small">El Niño strength (scenario input)
          <select value={enso} onChange={(e) => setEnso(e.target.value)}>{o.enso.levels.map((l) => <option key={l.id} value={l.id}>{l.label} (+{l.extra_warming_c} °C)</option>)}</select>
        </label>
        <label className="small">Heat sensitivity of demand: +{beta}% per °C
          <input type="range" min={0} max={8} step={0.5} value={beta} onChange={(e) => setBeta(Number(e.target.value))} />
        </label>
      </div>

      <div className="chart tall" role="img" aria-label="Projected reservoir level, 14 days, with uncertainty band">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="day" tick={axis} stroke="var(--line)" label={{ value: "days from now", position: "insideBottomRight", offset: -2, fill: "var(--muted)", fontSize: 11 }} />
            <YAxis tick={axis} stroke="var(--line)" width={48} domain={[0, o.inputs.capacity_l]} tickFormatter={(v) => `${v / 1000}k`} label={{ value: "litres", angle: -90, position: "insideLeft", fill: "var(--muted)", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" }} formatter={(v) => (Array.isArray(v) ? `${N(v[0] as number)} – ${N(v[1] as number)} L` : `${N(v as number)} L`)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine y={o.inputs.critical_l} stroke="var(--bad)" strokeDasharray="5 4" label={{ value: "critical reserve", fill: "var(--bad)", fontSize: 11, position: "insideTopLeft" }} />
            <Area type="monotone" dataKey="base" name={`${o.inputs.baseline_hours} h/day: 10–90% range`} stroke="none" fill="var(--bad)" fillOpacity={0.18} isAnimationActive={false} />
            <Line type="monotone" dataKey="baseMed" name={`${o.inputs.baseline_hours} h/day: median`} stroke="var(--bad)" strokeWidth={2} dot={false} isAnimationActive={false} />
            {over && <Area type="monotone" dataKey="plan" name="plan: 10–90% range" stroke="none" fill="var(--ok)" fillOpacity={0.2} isAnimationActive={false} />}
            {over && <Line type="monotone" dataKey="planMed" name={active ? `active plan (${planName(active.plan.desal_hours, active.plan.conservation_pct)})` : preview ? `preview (${planName(preview.desal_hours, preview.conservation_pct)})` : "plan: median"} stroke="var(--ok)" strokeWidth={2} dot={false} isAnimationActive={false} />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="ol-two">
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>Heat driving demand: forecast daily maximum and its uncertainty (°C)</div>
          <div className="chart short" role="img" aria-label="Forecast temperature">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={temps} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
                <XAxis dataKey="day" tick={axis} stroke="var(--line)" />
                <YAxis tick={axis} stroke="var(--line)" width={36} domain={["dataMin - 1", "dataMax + 1"]} />
                <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" }} />
                <Area type="monotone" dataKey="band" name="10–90% (incl. El Niño)" stroke="none" fill="var(--warn)" fillOpacity={0.2} isAnimationActive={false} />
                <Line type="monotone" dataKey="fc" name="forecast max" stroke="var(--c3)" strokeWidth={2} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div>
          <div className="small muted" style={{ marginBottom: 4 }}>Compare plans (click a row to preview on the chart)</div>
          <table className="plans">
            <thead><tr><th>Plan</th><th>Risk</th><th>Day-14 low</th><th>+kWh/day</th></tr></thead>
            <tbody>
              {[...o.plans, ...(o.plans.some((p) => p.desal_hours === o.optimiser.projection.desal_hours && p.conservation_pct === o.optimiser.projection.conservation_pct) ? [] : [o.optimiser.projection])].map((p) => {
                const sel = preview && preview.desal_hours === p.desal_hours && preview.conservation_pct === p.conservation_pct;
                return (<tr key={`${p.desal_hours}-${p.conservation_pct}`} className={sel ? "sel" : ""} onClick={() => setPreview(sel ? null : p)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setPreview(sel ? null : p)}>
                  <td>{planName(p.desal_hours, p.conservation_pct)}</td>
                  <td className={p.p_breach_pct <= o.target_pct ? "okc" : p.p_breach_pct >= 50 ? "badc" : "warnc"}>{N(p.p_breach_pct, 1)}%</td>
                  <td>{N(p.day14_level_p10_l)} L</td><td>{p.extra_kwh_per_day ? `+${N(p.extra_kwh_per_day)}` : "0"}</td></tr>);
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="planner">
        <div className="row">
          <button className="primary" onClick={runPlanner} disabled={busy !== ""}>{busy === "plan" ? "Planner agent working…" : "Ask the planner agent"}</button>
          {busy === "plan" && <span className="spin" aria-label="working" />}
          {active && <span className="badge ok">Plan active: {planName(active.plan.desal_hours, active.plan.conservation_pct)}</span>}
          <span className="small muted">The agent tests plans with the projection tool; code re-checks its numbers and its recommended plan.</span>
        </div>
        {plan && rec && (() => {
          const approved = !!active && active.plan.desal_hours === rec.plan.desal_hours && active.plan.conservation_pct === rec.plan.conservation_pct;
          const before = riskWord(o.baseline.p_breach * 100), after = riskWord(rec.projection.p_breach_pct);
          return (
          <div className="plan-card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>Recommendation</strong>
              <span className="row">
                <span className={`badge ${plan.source.startsWith("gemini") ? "ok" : "warn"}`}>{plan.source === "gemini" ? "Gemini planner" : plan.source === "fixed-optimiser" ? "Code optimiser (AI unavailable)" : plan.source === "gemini-single-call" ? "Gemini planner (single call)" : plan.source}</span>
                <span className={`badge ${approved ? "ok" : "info"}`}>{approved ? "APPROVED" : "needs your approval"}</span>
              </span>
            </div>
            <p style={{ margin: "8px 0", fontSize: 16 }}>To stay safe, <b>{planWords(rec.plan.desal_hours, rec.plan.conservation_pct, o.inputs.baseline_hours)}</b>.</p>
            <p className="small" style={{ margin: "0 0 8px" }}>Chance of the reservoir reaching its critical level in the next 14 days: <b>{before}</b> as things are, <b className={rec.target_met ? "okc" : "warnc"}>{after}</b> with this plan{rec.target_met ? "" : " (still above the safe target)"}.</p>
            {plan.answer && <p style={{ margin: "8px 0" }}>{plan.answer.summary}</p>}
            {plan.answer && plan.answer.rationale.length > 0 && <ul className="small" style={{ margin: "0 0 8px 18px", padding: 0 }}>{plan.answer.rationale.map((r, i) => <li key={i}>{r}</li>)}</ul>}
            {!plan.answer && <p className="small muted" style={{ margin: "8px 0" }}>The AI planner was not available, so the safe-plan calculator chose the least disruptive plan that meets the target.</p>}
            {plan.answer && plan.answer.watch_items.length > 0 && <div className="small muted">Keep an eye on: {plan.answer.watch_items.join(" · ")}</div>}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" disabled={approved || busy !== "" || !rec.target_met} onClick={() => approve(rec.plan.desal_hours, rec.plan.conservation_pct)}>{approved ? "Plan approved ✓" : busy === "approve" ? "Approving…" : "Approve plan"}</button>
              <button onClick={() => setPreview(rec.projection)}>Show on chart</button>
              {approved && <span className="small okc">In effect from {o.as_of ?? "now"}. The chart and risk above now include this plan.</span>}
            </div>
            <details className="small" style={{ marginTop: 8 }}>
              <summary>Technical details</summary>
              <div style={{ marginTop: 6 }}>
                Plan: {planName(rec.plan.desal_hours, rec.plan.conservation_pct)} · risk {N(rec.projection.p_breach_pct, 1)}% against a target of {o.target_pct}% · {rec.verification.numbers_ok ? "numbers match tool results" : `unmatched numbers: ${rec.verification.unmatched_numbers.join(", ")}`}
                {rec.overridden && ` · replaced by code optimiser (${rec.override_reason})`} · tools: {plan.tool_calls.map((t) => t.name).join(", ") || "none"}. Full detail is in the downloadable run report.
              </div>
            </details>
          </div>);
        })()}
      </div>

      <details className="small" style={{ marginTop: 10 }}>
        <summary>Assumptions and sources</summary>
        <ul style={{ margin: "6px 0 0 18px", padding: 0 }}>
          <li>Weather: {o.climate.note} Reference point {o.climate.location.lat}°N, {o.climate.location.lon}°E ({o.climate.location.label}).</li>
          <li>El Niño: {o.enso.reference} Selected: {o.enso.label}, +{o.enso.extra_warming_c} °C by day 14 (assumption).</li>
          <li>Demand rises {beta}% per °C above today's temperature, with 3% less on days of 10 mm or more rain. Plant outages 5% per day. Day-to-day demand noise 4%. All are assumptions, shown so you can change them.</li>
          <li>{String(o.assumptions.paths)} simulated futures; every plan is tested on the same futures. Supply = plant rate × hours; the rate is derived from today's observed inflow at {o.inputs.baseline_hours} h/day.</li>
          <li>Plan ranking: {String(o.assumptions.burden_note)}. The risk target is {o.target_pct}%.</li>
        </ul>
      </details>
    </div>
  );
}

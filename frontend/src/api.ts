export type Effect = { command: string; title: string; executed_at: string; settled: boolean; ready_at: string; minutes_left: number; metrics: { name: string; unit: string; before: number | string; now: number | string }[] };
export type Reading = { value: number | null; unit: string; age_steps: number | null };
export type SeriesPt = { step: number; t: string; tank_volume: number | null; inflow: number | null; outflow: number | null; voltage: number | null; production_power: number | null };
export type Horizon = { state: "unavailable" | "below_critical" | "no_depletion" | "depleting"; hours_to_critical?: number; hours_to_empty?: number; net_drawdown_lph?: number; coverage: number };
export type Hypothesis = { hyp_id: string; cause: string; evidence_ids: string[]; status: string; flag: string | null };
export type Action = { action_id: string; title: string; owner_role: string; priority: number; rationale: string; prerequisites: string[]; evidence_ids: string[]; procedure_ids: string[]; requires_approval: boolean; simulated_command: string; flag: string | null };
export type Assessment = { summary: string; hypotheses: Hypothesis[]; data_quality: string; missing_evidence: string[]; actions: Action[]; limitations: string[]; evidence_ids: string[] };
export type EvidenceItem = { id: string; kind: string; label: string; step_range: [number, number]; time_range?: [string, string]; values: Record<string, unknown> };
export type Doc = {
  id: string; step: number; stage: string; source?: string; ai_status?: string; calls: number; snapshot_hash: string;
  assessment: Assessment | null; timeline: { stage: string; model: string; latency_ms: number; ok: boolean; note: string }[];
  verification: { status: string; claims_total?: number; supported?: number; removed?: { item_id: string; reason: string; source: string }[]; partially_supported?: { item_id: string; reason: string }[]; repaired?: boolean; reason?: string };
  fallback_checklist?: { id: string; title: string; text: string }[];
  snapshot: { clock?: string; items: EvidenceItem[]; sops: { id: string; title: string; text: string; banner: string }[]; step: number };
};
export type State = {
  run: { id: string; scenario_id: string; state: "playing" | "paused"; current_step: number; speed: number; ai_calls: number; qa_calls: number; qa_cap: number };
  scenario: { id: string; title: string; n_steps: number; critical_L: number; capacity_L: number };
  sim_time: string; sim_clock: string; sim_start_clock: string; series: SeriesPt[]; latest: Record<string, Reading>; horizon: Horizon;
  detection: { state: "none" | "active" | "resolved"; started_step: number | null; resolved_step: number | null; started_at: string | null; resolved_at: string | null; active_rules: string[]; first_fired: Record<string, number | null>; severity: string };
  incident: { id: string; checkpoint_step: number; state: string } | null;
  assessment: (Partial<Doc> & { id: string; step: number; stage: string }) | null;
  new_evidence: boolean;
  actions: Record<string, { step: number; status: string | null; effect: Effect }>;
  approvals: Record<string, { assessment_id: string; command: string; approved_step: number }>;
};
export type Scenario = { id: string; title: string; description: string };
export type QAResp = { answer: { answer: string; evidence_ids: string[]; procedure_ids: string[]; used_what_if: boolean }; tool_calls: { name: string; arguments: unknown; result: unknown }[]; verification: { numbers_ok: boolean; unmatched_numbers: number[]; tools_used: string[] } };

export type FanPt = { day: number; p10: number; p50: number; p90: number };
export type PlanSummary = { desal_hours: number; conservation_pct: number; p_breach_pct: number; breach_day_p10: number | null; breach_day_p50: number | null; day14_level_p10_l: number; day14_level_p50_l: number; unmet_demand_p90_l: number; kwh_per_day: number; extra_kwh_per_day: number; fan?: FanPt[] };
export type Outlook = {
  run_id: string; step: number; as_of?: string; target_pct: number; label: string; supply_note: string | null;
  inputs: { level_l: number; demand_l_day: number; supply_l_day_now: number; capacity_l: number; critical_l: number; baseline_hours: number };
  climate: { source: string; note: string; fetched_at: string; location: { lat: number; lon: number; label: string }; days: { date: string | null; tmax_c: number; rain_mm: number }[]; reference_tmax_c: number; temp_band: { day: number; p10: number; p50: number; p90: number }[] };
  enso: { level: string; label: string; extra_warming_c: number; reference: string; levels: { id: string; label: string; extra_warming_c: number }[] };
  assumptions: Record<string, number | string>;
  baseline: { p_breach: number; fan: FanPt[]; breach_day_p10: number | null; breach_day_p50: number | null; end_p10_l: number; end_p50_l: number };
  active_plan: { plan: { desal_hours: number; conservation_pct: number; approved_step: number }; projection: { p_breach: number; fan: FanPt[]; extra_kwh_per_day: number } } | null;
  plans: PlanSummary[]; optimiser: { plan: { desal_hours: number; conservation_pct: number }; target_met: boolean; projection: PlanSummary };
};
export type PlanDoc = {
  id: string; source: string; error: string; status: string; requires_approval: boolean;
  answer: { summary: string; rationale: string[]; watch_items: string[]; procedure_ids: string[] } | null;
  tool_calls: { name: string; arguments: Record<string, number>; result: unknown }[];
  recommendation: { plan: { desal_hours: number; conservation_pct: number }; projection: PlanSummary; overridden: boolean; override_reason: string; target_met: boolean;
    verification: { numbers_ok: boolean; unmatched_numbers: number[]; recommended_meets_target: boolean; tools_used: string[]; plan_was_tested_by_agent?: boolean } };
};

/** One short, readable sentence for the screen. Full detail lives in the downloadable report. */
export function friendly(msg: string, status?: number): string {
  if (status && [502, 503, 504].includes(status)) return "The service took too long to answer. Please try again.";
  if (status === 429) return "Too many requests right now. Wait a moment and try again.";
  const first = msg.split(/(?<=[.!?])\s/)[0];
  return first.length > 140 ? first.slice(0, 137) + "…" : first;
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try { r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...init }); }
  catch { throw Object.assign(new Error("Cannot reach the service. Check your connection and try again."), { status: 0 }); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const d = (await r.json()).detail; msg = typeof d === "string" ? d : d ? "The request was not accepted." : msg; } catch { /* ignore */ }
    throw Object.assign(new Error(friendly(String(msg), r.status)), { status: r.status });
  }
  return r.json();
}
const post = <T,>(p: string, body?: unknown) => j<T>(p, { method: "POST", body: JSON.stringify(body ?? {}) });

export const api = {
  scenarios: () => j<Scenario[]>("/scenarios"),
  newRun: (scenario_id: string) => post<{ run_id: string }>("/runs", { scenario_id }),
  control: (id: string, body: object) => post<{ run_id: string; current_step: number; state: string }>(`/runs/${id}/control`, body),
  state: (id: string) => j<State>(`/runs/${id}/state`),
  advance: (id: string, force_live: boolean) => post<Doc>(`/runs/${id}/pipeline/advance`, { force_live }),
  approve: (id: string, action: string, assessment_id: string) => post(`/runs/${id}/actions/${action}/approve`, { assessment_id }),
  simulate: (id: string, action: string) => post(`/runs/${id}/actions/${action}/simulate`),
  ask: (id: string, question: string, history: { q: string; a: string }[] = []) => post<QAResp>(`/runs/${id}/qa`, { question, history }),
  outlook: (id: string, enso: string, beta: number) => j<Outlook>(`/runs/${id}/outlook?enso=${enso}&beta=${beta}`),
  outlookPlan: (id: string, enso: string, beta: number) => post<PlanDoc>(`/runs/${id}/outlook/plan`, { enso, beta }),
  outlookApprove: (id: string, enso: string, beta: number, desal_hours: number, conservation_pct: number) => post(`/runs/${id}/outlook/approve`, { enso, beta, desal_hours, conservation_pct }),
  reportUrl: (id: string, fmt: "md" | "json") => `/api/runs/${id}/report?format=${fmt}`,
};

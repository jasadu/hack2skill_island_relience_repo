import { useEffect, useRef, useState } from "react";
import { api, QAResp } from "./api";

type Msg = { role: "user" | "ai"; text: string; r?: QAResp };

const SUGGEST = [
  "How is the tank doing right now?", "What was the lowest voltage so far?", "When did demand peak?",
  "What is the critical reserve level?", "Summarize the last 24 hours", "What happens in the next 2 weeks?",
];
const TOOL_LABEL: Record<string, string> = {
  query_history: "history", summarize_period: "period summary", get_outlook: "14-day outlook", calculate_reserve_horizon: "reserve calculation",
  get_telemetry_window: "telemetry", retrieve_procedure: "SOP search",
};

export default function Chat({ runId, used, cap, openEv }: { runId: string; used: number; cap: number; openEv: (id: string) => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [localUsed, setLocalUsed] = useState(0);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { setMsgs([]); setLocalUsed(0); }, [runId]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);
  const left = cap - Math.max(used, localUsed);

  async function ask(text: string) {
    const t = text.trim();
    if (!t || busy || left <= 0) return;
    const history: { q: string; a: string }[] = [];
    for (let i = 0; i + 1 < msgs.length; i++) if (msgs[i].role === "user" && msgs[i + 1].role === "ai" && msgs[i + 1].r) history.push({ q: msgs[i].text, a: msgs[i + 1].text });
    setMsgs((m) => [...m, { role: "user", text: t }]); setQ(""); setBusy(true);
    try {
      const r = await api.ask(runId, t, history.slice(-4));
      setLocalUsed((n) => n + 1);
      setMsgs((m) => [...m, { role: "ai", text: r.answer.answer, r }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "ai", text: `I could not answer that right now. ${(e as Error).message}` }]);
    } finally { setBusy(false); }
  }

  return (
    <div className="card chat">
      <h2><svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" style={{ verticalAlign: "-2px", marginRight: 6 }}><path d="M4 4h16v11H9l-5 4z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /></svg>Ask about the data</h2>
      <div className="chat-log" role="log" aria-live="polite">
        {!msgs.length && <p className="muted small">Ask about current readings, past trends, the incident or the 14-day El Niño outlook. Every number comes from a tool and is checked.</p>}
        {msgs.map((m, i) => m.role === "user"
          ? <div key={i} className="bubble user">{m.text}</div>
          : (<div key={i} className="bubble ai">
              <div>{m.text}</div>
              {m.r && (<>
                {m.r.answer.used_what_if && <span className="badge info">what-if (calculation only)</span>}
                <div>{m.r.answer.evidence_ids.map((id) => <button key={id} className="chip" onClick={() => openEv(id)}>{id}</button>)}
                  {m.r.answer.procedure_ids.map((id) => <button key={id} className="chip sop" onClick={() => openEv(id)}>{id}</button>)}</div>
                <div className="small muted meta">
                  {m.r.verification.tools_used.length ? `Used: ${[...new Set(m.r.verification.tools_used)].map((t) => TOOL_LABEL[t] ?? t).join(", ")} · ` : ""}
                  {m.r.verification.numbers_ok ? <span className="okc">numbers verified</span> : <span className="warnc">unverified numbers: {m.r.verification.unmatched_numbers.join(", ")}</span>}
                </div>
              </>)}
            </div>))}
        {busy && <div className="bubble ai"><span className="spin" /> Gemini is using tools…</div>}
        <div ref={end} />
      </div>
      <div className="suggest">{SUGGEST.map((s) => <button key={s} className="pill" disabled={busy || left <= 0} onClick={() => ask(s)}>{s}</button>)}</div>
      <form className="chat-in" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask anything about the data…" maxLength={400} aria-label="Question" disabled={left <= 0} />
        <button className="primary" disabled={busy || left <= 0 || !q.trim()} aria-label="Send">Send</button>
      </form>
      <div className="small muted" style={{ marginTop: 4 }}>{left > 0 ? `${left} of ${cap} questions left for this run` : "Question limit reached for this run. Reset to continue."}</div>
    </div>
  );
}

import type { State } from "./api";

type Node = { id: string; label: string; status: "ok" | "warn" | "bad" | "unknown"; detail: string };

export function nodesFor(s: State): Node[] {
  const f = s.detection.first_fired, now = s.run.current_step;
  const fired = (r: string) => f[r] !== null && f[r]! <= now && s.detection.active_rules.includes(r);
  const v = s.latest.voltage?.value, inflow = s.latest.inflow?.value, outflow = s.latest.outflow?.value, tank = s.latest.tank_volume?.value;
  const h = s.horizon;
  return [
    { id: "power", label: "Power feed", status: fired("low_voltage") ? "bad" : "ok", detail: `Voltage ${v ?? "n/a"} V (observed). Rule: below 210 V for 3 minutes.` },
    { id: "plant", label: "Water plant", status: fired("low_production") ? "bad" : "ok", detail: `Inflow ${inflow ?? "n/a"} L/h (observed). Rule: below 400 L/h for 3 minutes.` },
    { id: "tank", label: "Reservoir", status: tank === null || tank === undefined ? "unknown" : h.state === "depleting" && fired("low_reserve_horizon") ? "bad" : h.state === "below_critical" ? "bad" : "ok",
      detail: tank === null || tank === undefined ? `Tank sensor silent for ${s.latest.tank_volume?.age_steps ?? "?"} min: forecast withheld.` : `Volume ${tank} L (observed). Reserve horizon: ${h.state}${h.hours_to_critical !== undefined ? `, ${h.hours_to_critical} h to critical (calculated)` : ""}.` },
    { id: "demand", label: "Island demand", status: "ok", detail: `Outflow ${outflow ?? "n/a"} L/h (observed).` },
  ];
}

const col = { ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)", unknown: "var(--muted)" };
const word = { ok: "Normal", warn: "Watch", bad: "Alert", unknown: "No data" };

export default function Schematic({ s, selected, onSelect }: { s: State; selected: string; onSelect: (id: string) => void }) {
  const nodes = nodesFor(s);
  const sel = nodes.find((n) => n.id === selected);
  return (
    <div className="card">
      <h2>Island schematic</h2>
      <svg viewBox="0 0 640 120" width="100%" role="group" aria-label="Island system schematic">
        {nodes.slice(0, -1).map((_, i) => <line key={i} x1={130 + i * 170} y1={55} x2={170 + i * 170} y2={55} stroke="var(--muted)" strokeWidth="2" markerEnd="url(#a)" />)}
        <defs><marker id="a" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--muted)" /></marker></defs>
        {nodes.map((n, i) => (
          <g key={n.id} className="node" tabIndex={0} role="button" aria-label={`${n.label}: ${word[n.status]}`} onClick={() => onSelect(n.id)} onKeyDown={(e) => e.key === "Enter" && onSelect(n.id)} transform={`translate(${i * 170 + 10},20)`}>
            <rect width="120" height="70" rx="10" fill="var(--surface)" stroke={col[n.status]} strokeWidth={selected === n.id ? 4 : 2} />
            <text x="60" y="30" textAnchor="middle" fill="var(--ink)" fontSize="13" fontWeight="600">{n.label}</text>
            <text x="60" y="52" textAnchor="middle" fill={col[n.status]} fontSize="12" fontWeight="700">{word[n.status]}</text>
          </g>
        ))}
      </svg>
      <div className="small muted">{sel ? `${sel.label}: ${sel.detail}` : "Select a node for its metrics."}</div>
    </div>
  );
}

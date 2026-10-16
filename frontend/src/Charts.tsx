import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { SeriesPt, State } from "./api";

const axis = { stroke: "var(--muted)", fontSize: 11 };

function Chart({ title, data, lines, refs, unit, start, domain }: {
  title: string; domain?: [number, number]; data: SeriesPt[]; unit: string; start: number | null;
  lines: { key: keyof SeriesPt; name: string; color: string }[]; refs?: { y: number; label: string }[];
}) {
  const startT = start === null ? undefined : data.find((d) => d.step === start)?.t;
  return (
    <div className="card">
      <h2>{title}</h2>
      <div className="chart" role="img" aria-label={`${title} chart`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis dataKey="t" tick={axis} stroke="var(--line)" minTickGap={24} />
            <YAxis tick={axis} stroke="var(--line)" width={72} domain={domain ?? ["auto", "auto"]} unit={unit ? ` ${unit}` : ""} />
            <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", color: "var(--ink)" }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {refs?.map((r) => <ReferenceLine key={r.label} y={r.y} stroke="var(--bad)" strokeDasharray="5 4" label={{ value: r.label, fill: "var(--bad)", fontSize: 11, position: "insideTopLeft" }} />)}
            {startT !== undefined && <ReferenceLine x={startT} stroke="var(--warn)" label={{ value: "incident", fill: "var(--warn)", fontSize: 11 }} />}
            {lines.map((l) => <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color} dot={false} strokeWidth={2} isAnimationActive={false} connectNulls={false} />)}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default function Charts({ s }: { s: State }) {
  const st = s.detection.started_step;
  return (
    <div className="charts">
      <Chart title="Tank volume" domain={[0, s.scenario.capacity_L]} data={s.series} unit="L" start={st} refs={[{ y: s.scenario.critical_L, label: "critical reserve" }]}
        lines={[{ key: "tank_volume", name: "Tank volume (observed)", color: "var(--c1)" }]} />
      <Chart title="Inflow vs outflow" data={s.series} unit="L/h" start={st} refs={[{ y: 400, label: "low-inflow rule" }]}
        lines={[{ key: "inflow", name: "Inflow (production)", color: "var(--c1)" }, { key: "outflow", name: "Outflow (demand)", color: "var(--c2)" }]} />
      <Chart title="Supply voltage" data={s.series} unit="V" start={st} refs={[{ y: 210, label: "low-voltage rule" }]}
        lines={[{ key: "voltage", name: "Voltage (observed)", color: "var(--c3)" }]} />
    </div>
  );
}

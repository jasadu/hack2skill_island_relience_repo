import type { State } from "./api";
import { nodesFor } from "./Schematic";
import islandUrl from "./assets/island.jpg";

// Aerial photo base with live overlays; fixed colours so it reads the same in both themes.
const COLOR = { ok: "#3ddc84", warn: "#ffb020", bad: "#ff5a4d", unknown: "#8b96a3" } as const;
const WORD = { ok: "Normal", warn: "Watch", bad: "Alert", unknown: "No data" } as const;
type Status = keyof typeof COLOR;

function Pill({ x, y, text, size = 14, anchor = "middle" }: { x: number; y: number; text: string; size?: number; anchor?: "middle" | "start" }) {
  const w = text.length * size * 0.56 + 18;
  const x0 = anchor === "middle" ? x - w / 2 : x;
  return (<g pointerEvents="none">
    <rect x={x0} y={y - size - 3} width={w} height={size + 10} rx="6" fill="#0b1620" opacity=".86" />
    <text x={x0 + 9} y={y + 1} fontSize={size} fill="#f2f6f9">{text}</text>
  </g>);
}

function Callout({ x, y, label, value, status, selected, onClick }: {
  x: number; y: number; label: string; value: string; status: Status; selected: boolean; onClick: () => void;
}) {
  const c = COLOR[status];
  const w = Math.max(label.length * 7.2, value.length * 10.5) + 24;
  return (
    <g className="node" tabIndex={0} role="button" aria-label={`${label}: ${value}, ${WORD[status]}`} onClick={onClick}
       onKeyDown={(e) => e.key === "Enter" && onClick()} transform={`translate(${x},${y})`}>
      <rect x="0" y="-22" width={w} height="44" rx="8" fill="#0d1b27" opacity=".92" stroke={status === "bad" ? c : "#2c4254"} strokeWidth={selected ? 3 : 1.5} />
      <circle cx="12" cy="-10" r="4" fill={c} />
      <text x="22" y="-6" fontSize="12" fill="#9fb2c2">{label}</text>
      <text x="12" y="14" fontSize="17" fontWeight="700" fill={status === "ok" ? "#f2f6f9" : c}>{value}</text>
    </g>
  );
}

function Marker({ x, y, status, label, selected, onClick, children, size = 58 }: {
  x: number; y: number; status: Status; label: string; selected: boolean; onClick: () => void; children: React.ReactNode; size?: number;
}) {
  const c = COLOR[status], r = size / 2;
  return (
    <g className="node" tabIndex={0} role="button" aria-label={`${label}: ${WORD[status]}`} onClick={onClick}
       onKeyDown={(e) => e.key === "Enter" && onClick()} transform={`translate(${x},${y})`}>
      {status === "bad" && <rect x={-r - 4} y={-r - 4} width={size + 8} height={size + 8} rx="16" fill="none" stroke={c} strokeWidth="2.5" className="pulse" />}
      <rect x={-r} y={-r} width={size} height={size} rx="13" fill="#0d2233" stroke={c} strokeWidth={selected ? 5 : 3.5} filter="url(#glow)" />
      {children}
    </g>
  );
}

function Flow({ d, flow, status, width = 1 }: { d: string; flow: number | null; status: Status; width?: number }) {
  const f = Math.max(0, flow ?? 0);
  const speed = f > 0 ? Math.max(0.5, 3.2 - f / 350) : 0;
  const c = flow === null ? COLOR.unknown : status === "bad" ? COLOR.bad : "#5cc8ff";
  const w = (3 + f / 250) * width;
  return (<>
    <path d={d} fill="none" stroke={c} strokeWidth={w + 8} strokeLinecap="round" opacity=".22" filter="url(#blur)" />
    <path d={d} fill="none" stroke={c} strokeWidth={w} strokeLinecap="round" className={f > 0 ? "flow" : undefined}
          style={f > 0 ? { animationDuration: `${speed}s` } : { strokeDasharray: "2 9" }} />
  </>);
}

export default function IslandMap({ s, selected, onSelect }: { s: State; selected: string; onSelect: (id: string) => void }) {
  const nodes = nodesFor(s);
  const st = Object.fromEntries(nodes.map((n) => [n.id, n.status])) as Record<string, Status>;
  const L = s.latest, h = s.horizon;
  const cap = s.scenario.capacity_L, crit = s.scenario.critical_L;
  const tank = L.tank_volume?.value ?? null;
  const v = L.voltage?.value ?? null, qin = L.inflow?.value ?? null, qout = L.outflow?.value ?? null;
  const sel = nodes.find((n) => n.id === selected);
  const alert = nodes.find((n) => n.status === "bad") ?? nodes.find((n) => n.status === "unknown");
  const f = (x: number | null, d = 0) => (x === null ? "n/a" : x.toLocaleString(undefined, { maximumFractionDigits: d }));
  const horizonText = h.state === "depleting" ? `${f(h.hours_to_critical ?? null, 1)} h to critical`
    : h.state === "unavailable" ? "forecast withheld" : h.state === "below_critical" ? "BELOW CRITICAL" : "no depletion forecast";
  const gH = 30, gFill = tank === null ? 0 : Math.max(0, Math.min(1, tank / cap)) * gH, gCrit = (crit / cap) * gH;

  return (
    <div className="card">
      <h2>Island utility map</h2>
      <div className="map-wrap map-dark">
        <svg viewBox="0 0 1000 558" width="100%" role="group" aria-label="Aerial map of the island with power feed, water plant, reservoir and three barangays">
          <defs>
            <filter id="blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="5" /></filter>
            <filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="3.5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
            <linearGradient id="shade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#04101a" stopOpacity=".35" /><stop offset=".5" stopColor="#04101a" stopOpacity=".05" /><stop offset="1" stopColor="#04101a" stopOpacity=".35" /></linearGradient>
            <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="#8b96a3" strokeWidth="2" opacity=".7" /></pattern>
            <clipPath id="gauge"><rect x="-17" y="-12" width="34" height="30" /></clipPath>
          </defs>

          <image href={islandUrl} x="0" y="0" width="1000" height="558" preserveAspectRatio="xMidYMid slice" />
          <rect width="1000" height="558" fill="url(#shade)" pointerEvents="none" />

          {/* barangays */}
          {[
            { id: "sj", pts: "630,98 690,86 738,110 730,158 676,170 636,142", c: "#5cc8ff", label: "Brgy. San Jose", lx: 684, ly: 152 },
            { id: "sr", pts: "790,255 845,240 888,285 884,350 836,378 795,335", c: "#5cc8ff", label: "Brgy. Santa Rosa", lx: 836, ly: 372 },
            { id: "po", pts: "560,430 650,418 725,430 715,478 630,492 565,472", c: "#7ee0a1", label: "Brgy. Poblacion", lx: 640, ly: 470 },
          ].map((b) => (
            <g key={b.id} pointerEvents="none">
              <polygon points={b.pts} fill={b.c} fillOpacity=".18" stroke={b.c} strokeWidth="2.5" strokeOpacity=".95" />
              <Pill x={b.lx} y={b.ly} text={b.label} size={13} />
            </g>
          ))}

          {/* flows: power -> plant -> reservoir -> villages */}
          <Flow d="M318 410 C290 396 252 376 228 354" flow={v === null ? null : v > 150 ? 400 : 0} status={st.power} />
          <Flow d="M214 346 C260 290 380 240 508 204" flow={qin} status={st.plant} />
          <Flow d="M572 184 C610 150 650 135 690 128" flow={qout} status="ok" width={0.6} />
          <Flow d="M572 204 C670 220 760 255 810 285" flow={qout} status="ok" />

          {/* village houses (demand) */}
          <g className="node" tabIndex={0} role="button" aria-label={`Island demand: ${WORD[st.demand]}`} onClick={() => onSelect("demand")} onKeyDown={(e) => e.key === "Enter" && onSelect("demand")} transform="translate(808,282)">
            {[[0, 14], [28, 0], [56, 16], [14, 42], [46, 44]].map(([x, y], i) => (
              <path key={i} transform={`translate(${x},${y})`} d="M0 14 L14 0 L28 14 V30 H0 Z" fill="#0d2233" fillOpacity=".75"
                    stroke={selected === "demand" ? "#fff" : "#5cc8ff"} strokeWidth={selected === "demand" ? 3 : 2} />
            ))}
          </g>

          {/* power feed */}
          <Marker x={335} y={428} status={st.power} label="Power feed" selected={selected === "power"} onClick={() => onSelect("power")}>
            <path d="M5 -20 L-12 4 H-2 L-6 20 L13 -5 H3 Z" fill={COLOR[st.power]} />
          </Marker>
          <Pill x={335} y={480} text="Power Feed" size={13} />
          {/* water plant */}
          <Marker x={205} y={330} status={st.plant} label="Water plant" selected={selected === "plant"} onClick={() => onSelect("plant")}>
            <path d="M0 -20 C-11 -4 -14 2 -14 8 a14 14 0 0 0 28 0 C14 2 11 -4 0 -20 Z" fill={COLOR[st.plant]} />
          </Marker>
          <Pill x={205} y={376} text="Water Treatment Plant" size={13} />
          {/* reservoir sits on the real hilltop pond */}
          <Marker x={540} y={192} status={st.tank} label="Reservoir" selected={selected === "tank"} onClick={() => onSelect("tank")} size={64}>
            <ellipse cx="0" cy="-12" rx="17" ry="5.5" fill="none" stroke="#bfe6f6" strokeWidth="2" />
            <path d="M-17 -12 V16 a17 5.5 0 0 0 34 0 V-12" fill="none" stroke="#bfe6f6" strokeWidth="2" />
            <g clipPath="url(#gauge)">
              {tank === null
                ? <rect x="-17" y="-12" width="34" height={gH} fill="url(#hatch)" />
                : <rect x="-17" y={18 - gFill} width="34" height={gFill} fill="#5cc8ff" opacity=".85" />}
            </g>
            <line x1="-19" x2="19" y1={18 - gCrit} y2={18 - gCrit} stroke="#ff5a4d" strokeWidth="1.8" strokeDasharray="3 2" />
          </Marker>
          <Pill x={540} y={138} text="Central Reservoir / Tanks" size={15} />

          {/* sensor callouts */}
          <Callout x={372} y={428} label="Voltage sensor" value={v === null ? "n/a" : `${f(v, 1)} V`} status={st.power} selected={selected === "power"} onClick={() => onSelect("power")} />
          <Callout x={240} y={322} label="Inflow meter" value={qin === null ? "n/a" : `${f(qin)} L/h`} status={st.plant} selected={selected === "plant"} onClick={() => onSelect("plant")} />
          <Callout x={308} y={168} label={tank === null ? "Level sensor: SILENT" : "Level sensor"}
                   value={tank === null ? `silent ${L.tank_volume?.age_steps ?? "?"} min` : `${f(tank)} L`} status={st.tank} selected={selected === "tank"} onClick={() => onSelect("tank")} />
          <Pill x={470} y={238} text={`${horizonText} · calculated`} size={12} anchor="start" />
          <Callout x={742} y={226} label="Demand meter" value={qout === null ? "n/a" : `${f(qout)} L/h`} status={st.demand} selected={selected === "demand"} onClick={() => onSelect("demand")} />

          {/* HUD: legend */}
          <g pointerEvents="none">
            <rect x="14" y="14" width="140" height="98" rx="10" fill="#0b1620" opacity=".88" stroke="#24384a" />
            <text x="28" y="37" fontSize="13" fontWeight="700" fill="#f2f6f9">Legend</text>
            {(["ok", "bad", "unknown"] as Status[]).map((k, i) => (
              <g key={k} transform={`translate(28,${58 + i * 20})`}>
                <circle r="6" fill={COLOR[k]} /><text x="14" y="5" fontSize="14" fill="#dbe6ee">{k === "ok" ? "Normal" : k === "bad" ? "Alert" : "No data"}</text>
              </g>
            ))}
          </g>
          {/* HUD: alert bar */}
          {alert && (
            <g pointerEvents="none">
              <rect x="14" y="516" width="972" height="30" rx="8" fill="#0b1620" opacity=".92" stroke={alert.status === "bad" ? COLOR.bad : "#24384a"} />
              <text x="30" y="536" fontSize="14" fill={alert.status === "bad" ? COLOR.bad : "#dbe6ee"} fontWeight="700">▲ {alert.status === "bad" ? "Alert" : "No data"}: {alert.label}</text>
              <text x={30 + (alert.label.length + 10) * 8.2} y="536" fontSize="14" fill="#dbe6ee">{alert.detail.length > 120 ? alert.detail.slice(0, 118) + "…" : alert.detail}</text>
            </g>
          )}
        </svg>
      </div>
      <div className="map-legend">
        <span>Moving dashes = flow (faster = more L/h). Values are observed; the hours figure is calculated.</span>
      </div>
      <div className="map-detail">{sel ? `${sel.label}: ${sel.detail}` : "Select an asset or sensor for its metrics."}</div>
    </div>
  );
}

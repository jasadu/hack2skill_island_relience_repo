import type { Doc } from "./api";

export default function Drawer({ id, doc, onClose }: { id: string; doc: Doc | null; onClose: () => void }) {
  const ev = doc?.snapshot.items.find((i) => i.id === id);
  const sop = doc?.snapshot.sops.find((i) => i.id === id);
  return (
    <aside className="drawer" role="dialog" aria-label={`Evidence ${id}`}>
      <div className="row"><strong style={{ fontSize: 16 }}>{id}</strong><span className="spacer" /><button onClick={onClose}>Close</button></div>
      {ev && (<>
        <p><span className={`src ${ev.kind === "calculation" ? "calc" : ""}`}>{ev.kind === "calculation" ? "Calculated" : "Observed"}</span> {ev.label}</p>
        <p className="small muted">{ev.time_range ? `${ev.time_range[0]} to ${ev.time_range[1]}` : `Minutes ${ev.step_range[0]}–${ev.step_range[1]}`}</p>
        <dl className="kv">{Object.entries(ev.values).map(([k, v]) => (<><dt key={k}>{k}</dt><dd>{String(v)}</dd></>))}</dl>
      </>)}
      {sop && (<>
        <p><span className="src ai">SOP</span> {sop.title}</p>
        <p>{sop.text}</p>
        <p className="small muted">Keyword-matched reference text.</p>
      </>)}
      {!ev && !sop && <p className="muted">Not found in this snapshot.</p>}
    </aside>
  );
}

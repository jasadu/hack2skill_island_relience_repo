"""Keyword retrieval over the five demo SOPs (not semantic search; the score is shown in the UI)."""
import re
from pathlib import Path

_DIR = Path(__file__).parent / "sop_docs"
_SECTION = re.compile(r"^### (\S+) (.+)$", re.M)


def _load():
    out = []
    for p in sorted(_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        parts = re.split(r"(?m)^(?=### )", text)
        for part in parts[1:]:
            m = _SECTION.match(part)
            if m:
                out.append({"id": m.group(1), "title": m.group(2).strip(), "sop": p.stem,
                            "text": part.split("\n", 1)[1].strip(),
                            "banner": "Demo SOP — not an approved field procedure."})
    return out


SECTIONS = _load()
SECTION_BY_ID = {s["id"]: s for s in SECTIONS}
_TOKEN = re.compile(r"[a-z0-9_]+")


def retrieve(query: str, k: int = 4):
    q = set(_TOKEN.findall(query.lower()))
    scored = []
    for s in SECTIONS:
        words = set(_TOKEN.findall((s["title"] + " " + s["text"]).lower()))
        score = len(q & words)
        if score:
            scored.append({**s, "score": score})
    return sorted(scored, key=lambda s: (-s["score"], s["id"]))[:k]


# Which procedures are relevant to which fired rule (deterministic, so the snapshot is deterministic).
RULE_SOPS = {
    "low_voltage": ["sop-power-01#1", "sop-power-01#2"],
    "low_production": ["sop-prod-01#1", "sop-prod-01#2"],
    "low_reserve_horizon": ["sop-reserve-01#1", "sop-reserve-01#2", "sop-reserve-01#3"],
    "tank_sensor_silent": ["sop-sensor-01#1", "sop-sensor-01#2"],
    "sensor": ["sop-sensor-01#1", "sop-sensor-01#2"],
    "recover": ["sop-recover-01#1", "sop-recover-01#2"],
}


def sops_for(fired_rules, sensor_problem, resolved):
    ids = []
    for r in fired_rules:
        ids += RULE_SOPS[r]
    if sensor_problem:
        ids += RULE_SOPS["sensor"]
    if resolved or fired_rules:
        ids += RULE_SOPS["recover"][:1]
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(SECTION_BY_ID[i])
    return out

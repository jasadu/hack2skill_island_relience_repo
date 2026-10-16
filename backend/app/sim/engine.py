"""Stateless island simulator. The trace is a pure function of (scenario, seed, step, executed actions)."""
from __future__ import annotations
import hashlib, json, random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DT_S = 60
DT_H = DT_S / 3600
SIM_EPOCH = datetime(2026, 10, 12, 2, 0, 0, tzinfo=timezone.utc)      # fallback for runs created before real-time anchoring
PHT = timezone(timedelta(hours=8))                                      # Philippine Time, used for every displayed clock
PROD_TRIGGER_V = 210.0   # production target drops when lagged voltage is below this (scenario setting)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str = ""
    description: str = ""
    seed: int = 42
    n_steps: int = 720
    capacity: float = 20_000
    start_volume: float = 12_000
    critical: float = 3_000
    prod_normal: float = 800
    prod_disrupted: float = 200
    demand_normal: float = 650
    demand_incident: float = 850
    demand_conserved: float = 700
    v_nominal: float = 230
    v_dip: float = 190
    dip_start: int | None = None
    demand_rise_start: int | None = None
    prod_lag: int = 6
    ramp: int = 5
    tank_sensor_fail_at: int | None = None
    start_hour: float = 4.0         # local (PHT) time of day at step 0; the run starts on today's date at this hour
    sensor_repair_min: int = 12     # minutes for a technician to reach the site and restore the tank sensor
    surge_fade: int = 90            # minutes for the incident demand surge to fade once the power feed is restored


SCENARIOS = {
    "normal": Scenario(id="normal", title="Normal operations",
                       description="Stable power and production over a 04:00 to 16:00 day, with the usual morning peak in demand. No incident should appear."),
    "power-water": Scenario(id="power-water", title="Power instability reduces water supply",
                            description="Starts at 05:30. Voltage dips at 05:50 just as the morning rush begins: pumping output falls, demand rises and the reserve starts to deplete.",
                            dip_start=20, demand_rise_start=30, start_hour=5.5),
    "sensor-fault": Scenario(id="sensor-fault", title="Tank sensor fault",
                             description="Starts at 16:00. The tank level sensor goes silent at 16:40, before the evening peak: the reserve forecast must be withheld.",
                             tank_sensor_fail_at=40, start_hour=16.0),
    "el-nino": Scenario(id="el-nino", title="El Niño heat wave: 14-day outlook",
                        description="No fault today: every incident rule stays quiet. The 14-day outlook shows the reserve at risk "
                                    "as the heat wave raises demand, and the planner compares desalination hours.",
                        demand_normal=790, start_volume=8_000, start_hour=4.0),
}


# Relative water demand by local hour (typical island pattern: morning and evening peaks, quiet night). Mean-normalised to 1.
_PROFILE_RAW = [0.55, 0.50, 0.45, 0.45, 0.55, 0.85, 1.20, 1.30, 1.15, 1.00, 0.92, 0.92, 1.00, 0.95, 0.88, 0.88, 0.95, 1.10, 1.25, 1.20, 1.00, 0.80, 0.68, 0.60]
_PROFILE_MEAN = sum(_PROFILE_RAW) / 24
PROFILE = [x / _PROFILE_MEAN for x in _PROFILE_RAW]


def demand_profile(hour: float) -> float:
    """Demand multiplier at a local hour of day (0 to 24, linear between hourly points, wraps at midnight)."""
    h = hour % 24
    i = int(h)
    f = h - i
    return PROFILE[i] * (1 - f) + PROFILE[(i + 1) % 24] * f


def hour_of(t) -> float:
    d = t.astimezone(PHT)
    return d.hour + d.minute / 60


def scenario_start(sc, now=None) -> str:
    """ISO start of a new run: today's date (PHT) at the scenario's own start hour, so every demo begins at its planned time of day."""
    now = now or datetime.now(timezone.utc)
    d = now.astimezone(PHT)
    t = d.replace(hour=int(sc.start_hour), minute=int(round((sc.start_hour % 1) * 60)), second=0, microsecond=0)
    return t.astimezone(timezone.utc).isoformat()


def parse_start(start) -> datetime:
    """The run's real start time (ISO string) as an aware datetime; old runs fall back to the fixed epoch."""
    if not start:
        return SIM_EPOCH
    return datetime.fromisoformat(start) if isinstance(start, str) else start


def clock_label(iso) -> str:
    """'12 Oct 2026, 14:03 PHT' from any ISO timestamp."""
    d = datetime.fromisoformat(iso) if isinstance(iso, str) else iso
    d = d.astimezone(PHT)
    return f"{d.day} {d.strftime('%b %Y, %H:%M')} PHT"


def clock_at(start, step) -> str:
    return clock_label(parse_start(start) + timedelta(seconds=step * DT_S))


def settle_steps(sc, cmd) -> int:
    """Minutes after execution until the effect has fully reached the readings (voltage ramp + production lag + production ramp)."""
    if cmd == "restore_power_feed":
        return sc.ramp + sc.prod_lag + sc.ramp + 1
    if cmd == "repair_tank_sensor":
        return sc.sensor_repair_min + 1
    return sc.ramp + 1


def tank_sensor_ok(sc, k, acts) -> bool:
    if sc.tank_sensor_fail_at is None or k < sc.tank_sensor_fail_at:
        return True
    r = acts.get("repair_tank_sensor")
    return r is not None and k >= r + sc.sensor_repair_min


def _ramp(k, start, steps, a, b):
    if start is None or k < start:
        return a
    return a + (b - a) * min(1.0, (k - start) / max(1, steps))


def voltage_true(sc, k, acts):
    v = _ramp(k, sc.dip_start, sc.ramp, sc.v_nominal, sc.v_dip)
    s = acts.get("restore_power_feed")
    if s is not None and k > s:
        v_at_s = _ramp(s, sc.dip_start, sc.ramp, sc.v_nominal, sc.v_dip)
        v = _ramp(k, s + 1, sc.ramp, v_at_s, sc.v_nominal)
    return v


def demand_true(sc, k, acts, t0=None):
    """Daily pattern x scenario incident surge x conservation. The surge fades out once the power feed is restored."""
    base = sc.demand_normal * demand_profile(hour_of(parse_start(t0) + timedelta(seconds=k * DT_S)))
    surge = (sc.demand_incident - sc.demand_normal) * _ramp(k, sc.demand_rise_start, sc.ramp, 0.0, 1.0)
    r = acts.get("restore_power_feed")
    if r is not None and k > r:
        fade_from = r + settle_steps(sc, "restore_power_feed")
        surge *= 1.0 - min(1.0, max(0.0, (k - fade_from) / max(1, sc.surge_fade)))
    d = base + surge
    c = acts.get("conserve_demand")
    if c is not None and k > c:
        cut = 1.0 - sc.demand_conserved / sc.demand_incident         # about 18 percent
        d *= 1.0 - cut * min(1.0, (k - (c + 1)) / max(1, sc.ramp))
    return d


def _noise(sc, k, ch, amp):
    return random.Random(f"{sc.seed}:{k}:{ch}").uniform(-amp, amp)


def build_trace(sc: Scenario, upto: int, acts: dict[str, int], start=None) -> list[dict]:
    """acts maps an allowlisted command to the step at which it was executed. start = real wall-clock start of the run (ISO)."""
    t0 = parse_start(start)
    upto = max(0, min(upto, sc.n_steps - 1))
    rows, vol, prod, v_hist = [], sc.start_volume, sc.prod_normal, []
    rate = (sc.prod_normal - sc.prod_disrupted) / max(1, sc.ramp)
    for k in range(upto + 1):
        v_hist.append(voltage_true(sc, k, acts))
        lagged = v_hist[k - sc.prod_lag] if k >= sc.prod_lag else sc.v_nominal
        target = sc.prod_disrupted if lagged < PROD_TRIGGER_V else sc.prod_normal
        prod += max(-rate, min(rate, target - prod))
        dem = demand_true(sc, k, acts, start)
        nxt = vol + (prod - dem) * DT_H
        spill, unmet = max(0.0, nxt - sc.capacity), max(0.0, -nxt)
        nxt = min(max(nxt, 0.0), sc.capacity)
        in_eff, out_eff = prod - spill / DT_H, dem - unmet / DT_H
        obs = {
            "voltage": (v_hist[k] + _noise(sc, k, "v", 1.5), "V"),
            "inflow": (in_eff + _noise(sc, k, "in", 10), "L/h"),
            "outflow": (out_eff + _noise(sc, k, "out", 10), "L/h"),
            "production_power": (0.0225 * prod + _noise(sc, k, "kw", 0.15), "kW"),
        }
        if tank_sensor_ok(sc, k, acts):
            obs["tank_volume"] = (vol + _noise(sc, k, "tank", 15), "L")
        now = t0 + timedelta(seconds=k * DT_S)
        rows.append({"step": k, "sim_time": now.isoformat(), "clock": clock_label(now), "hhmm": now.astimezone(PHT).strftime("%H:%M"),
                     "obs": obs, "latent": {"volume": vol, "production": prod, "demand": dem}})
        vol = nxt
    return rows


def trace_hash(rows) -> str:
    return hashlib.sha256(json.dumps([r["obs"] for r in rows], sort_keys=True).encode()).hexdigest()

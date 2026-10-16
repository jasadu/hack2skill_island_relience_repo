"""Weather feed for the outlook. Live: Open-Meteo daily forecast (free, no key). Fallback: flat climatology, clearly labelled."""
import json, logging, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from .. import config

log = logging.getLogger("climate")

# Scenario input, not a measurement: extra warming (deg C) reached by the end of the horizon, by El Niño strength.
ENSO_BIAS_C = {"neutral": 0.0, "weak": 0.4, "moderate": 0.8, "strong": 1.2, "very_strong": 1.6}
ENSO_LABEL = {"neutral": "Neutral", "weak": "Weak El Niño", "moderate": "Moderate El Niño",
              "strong": "Strong El Niño", "very_strong": "Very strong El Niño"}
ENSO_REFERENCE = "PAGASA El Niño advisories (Sep–Oct 2026). The strength is an operator-selected scenario input."
FALLBACK_TMAX_C, FALLBACK_RAIN_MM = 31.0, 4.0
_cache: dict = {}
TTL_S = 3 * 3600


def _fallback(days, why):
    return {"source": "fallback-climatology", "note": f"Live forecast unavailable ({why}); flat climatology used.",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "days": [{"date": None, "tmax_c": FALLBACK_TMAX_C, "rain_mm": FALLBACK_RAIN_MM} for _ in range(days)]}


def get_forecast(lat=None, lon=None, days=16, _fetch=None):
    lat = config.OUTLOOK_LAT if lat is None else lat
    lon = config.OUTLOOK_LON if lon is None else lon
    key = (round(lat, 2), round(lon, 2), days)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL_S:
        return hit[1]
    if not config.CLIMATE_LIVE and _fetch is None:
        return _fallback(days, "live feed disabled")
    try:
        if _fetch is not None:
            raw = _fetch()
        else:
            q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "forecast_days": days,
                                        "daily": "temperature_2m_max,precipitation_sum", "timezone": "Asia/Manila"})
            with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + q, timeout=4) as r:
                raw = json.load(r)
        d = raw["daily"]
        out = [{"date": t, "tmax_c": float(m), "rain_mm": float(p or 0.0)}
               for t, m, p in zip(d["time"], d["temperature_2m_max"], d["precipitation_sum"]) if m is not None]
        if len(out) < 7:
            raise ValueError("too few forecast days")
        doc = {"source": "open-meteo", "note": "Open-Meteo daily forecast (model output, not an observation).",
               "fetched_at": datetime.now(timezone.utc).isoformat(), "days": out}
        _cache[key] = (time.time(), doc)
        return doc
    except Exception as e:
        log.warning("climate fetch failed: %s", e)
        return _fallback(days, type(e).__name__)

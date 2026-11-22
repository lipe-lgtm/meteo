"""Předpověď počasí z Open-Meteo (zdarma pro nekomerční použití, data CC BY 4.0).

Server se ptá za aplikaci: poloha domova zůstává na Pi, odpověď se drží
15 minut v paměti a při výpadku Open-Meteo se vrátí poslední známá
předpověď s příznakem "stale".
"""
import json
import threading
import time
import urllib.parse
import urllib.request

CACHE_SECONDS = 15 * 60
USER_AGENT = "meteo-home/1.0 (personal weather station)"

_cache = {"key": None, "at": 0.0, "data": None}
_lock = threading.Lock()


def fetch_upstream(url, timeout=8):
    """Stáhne JSON. Samostatná funkce, aby šla v testech podvrhnout."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_url(base, lat, lon):
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "hourly": "temperature_2m,precipitation,weather_code",
        "minutely_15": "precipitation",
        "forecast_days": 6,
        "forecast_hours": 24,
        "forecast_minutely_15": 12,   # 3 hodiny dopředu po 15 min
        "timezone": "Europe/Prague",
        "timeformat": "unixtime",
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def _num(v):
    return round(float(v), 2) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def normalize(raw):
    """Z odpovědi Open-Meteo vybere jen to, co aplikace potřebuje."""
    daily = raw.get("daily") or {}
    days = []
    for i, t in enumerate(daily.get("time") or []):
        def pick(key):
            arr = daily.get(key) or []
            return _num(arr[i]) if i < len(arr) else None
        days.append({
            "t": int(t),
            "code": pick("weather_code"),
            "tmax": pick("temperature_2m_max"),
            "tmin": pick("temperature_2m_min"),
            "precip": pick("precipitation_sum"),
            "prob": pick("precipitation_probability_max"),
        })

    m15 = raw.get("minutely_15") or {}
    nowcast = [
        {"t": int(t), "precip": _num(p)}
        for t, p in zip(m15.get("time") or [], m15.get("precipitation") or [])
    ]

    hourly = raw.get("hourly") or {}
    hours = [
        {"t": int(t), "temp": _num(temp), "precip": _num(p), "code": _num(c)}
        for t, temp, p, c in zip(
            hourly.get("time") or [],
            hourly.get("temperature_2m") or [],
            hourly.get("precipitation") or [],
            hourly.get("weather_code") or [],
        )
    ]
    return {"days": days, "nowcast": nowcast, "hours": hours}


def get_forecast(base_url, lat, lon, now=None):
    """Vrátí (data, stale). Vyhodí výjimku jen když nemá ani starou kopii."""
    now = now or time.time()
    key = (base_url, round(lat, 4), round(lon, 4))
    with _lock:
        if _cache["key"] == key and _cache["data"] and now - _cache["at"] < CACHE_SECONDS:
            return _cache["data"], False
    try:
        data = normalize(fetch_upstream(build_url(base_url, lat, lon)))
        data["updated"] = int(now)
        if not data["days"]:
            raise ValueError("prázdná předpověď")
    except Exception:
        with _lock:
            if _cache["key"] == key and _cache["data"]:
                return _cache["data"], True
        raise
    with _lock:
        _cache.update(key=key, at=now, data=data)
    return data, False


def clear_cache():
    with _lock:
        _cache.update(key=None, at=0.0, data=None)

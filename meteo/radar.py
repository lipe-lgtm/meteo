"""Radar srážek z otevřených dat ČHMÚ (opendata.chmi.cz).

ČHMÚ publikuje každých 5 minut kompozit z radarů Brdy a Skalky jako PNG
v Mercatorově projekci (EPSG:3857, 1 km pixel) – přímo použitelné nad mapou.
Obrázek má vpravo a nahoře řezy a popisek, proto se ořízne jen na mapovou část.

Používá se "maskovaná" varianta MAX_Z: plnou barvou jen srážky, které podle
ČHMÚ dopadají na zem, ostatní odrazy jsou bledé. K tomu extrapolace pohybu
srážek na +10 až +60 minut (ČHMÚ, metoda COTREC – posouvá, nemění intenzitu).

Server stahuje jen když se někdo dívá (nejvýš jednou za minutu), snímky drží
v paměti. Prohlížeč tak mluví jen s naším serverem.
"""
import calendar
import io
import math
import re
import tarfile
import threading
import time
import urllib.error
import urllib.request

from PIL import Image

SLOT = 300                       # ČHMÚ publikuje po 5 minutách
PAST_STEP = 600                  # v animaci minulost po 10 minutách
PAST_FRAMES = 7                  # poslední hodina
REFRESH_EVERY = 60
USER_AGENT = "meteo-home/1.0 (personal weather station)"

OBS_DIR = "maxz/png_masked"
FCT_DIR = "fct_maxz/png_masked"

# Hranice podle popisu dat ČHMÚ (radar_popis_cz.pdf): Z, J, V, S
FULL_EXTENT = (11.267, 48.047, 20.770, 52.167)    # celý obrázek
DATA_EXTENT = (11.267, 48.047, 19.624, 51.458)    # mapová část

MAX_PNG = 3 * 1024 * 1024
MAX_TAR = 12 * 1024 * 1024
ATTRIBUTION = "Radar © ČHMÚ"

_FCT_MEMBER = re.compile(r"fct_z_max\.(\d{8})\.(\d{4})\.(\d{1,3})\.png$")
FRAME_ID = re.compile(r"^(obs-\d{10}|fct-\d{10}-\d{10})$")

_lock = threading.Lock()
_state = {"frames": {}, "missing": {}, "checked": 0.0, "fct_calc": None}


class RadarUnavailable(Exception):
    pass


# ------------------------------------------------------------------ síť

def fetch_bytes(url, limit, timeout=10):
    """Stáhne soubor. 404 → None (snímek ještě není / vypadl). Samostatně kvůli testům."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if len(data) > limit:
        raise RadarUnavailable("soubor je nečekaně velký")
    return data


def _stamp(ts):
    return time.strftime("%Y%m%d.%H%M", time.gmtime(ts))


def obs_url(base, ts):
    return f"{base}/{OBS_DIR}/pacz2gmaps3.z_max3d.{_stamp(ts)}.0.png"


def fct_url(base, ts):
    return f"{base}/{FCT_DIR}/pacz2gmaps3.fct_z_max.{_stamp(ts)}.ft60s10.tar"


# ------------------------------------------------------------------ obrázky

def _merc_y(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def crop_box(width, height):
    """Výřez mapové části (vlevo dole) spočtený z rozsahů, ne z pevných pixelů."""
    w0, s0, e0, n0 = FULL_EXTENT
    _, _, e1, n1 = DATA_EXTENT
    right = round(width * (e1 - w0) / (e0 - w0))
    top = round(height * (_merc_y(n0) - _merc_y(n1)) / (_merc_y(n0) - _merc_y(s0)))
    return (0, top, right, height)


def process_png(raw):
    img = Image.open(io.BytesIO(raw))
    if img.format != "PNG" or img.width > 4000 or img.height > 4000:
        raise RadarUnavailable("neočekávaný formát snímku")
    img = img.convert("RGBA").crop(crop_box(img.width, img.height))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def parse_forecast_tar(raw, calc_ts):
    """Vrátí [(platnost, png)] z archivu extrapolace. Nic se nezapisuje na disk."""
    frames = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
        for member in tar.getmembers():
            m = _FCT_MEMBER.search(member.name)
            if not member.isfile() or not m or member.size > MAX_PNG:
                continue
            lead = int(m.group(3))
            if lead <= 0:
                continue
            name_ts = calendar.timegm(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
            # popis ČHMÚ uvádí v názvu čas platnosti; kdyby to byl čas výpočtu, dopočítáme
            valid = calc_ts + lead * 60 if name_ts == calc_ts else name_ts
            fh = tar.extractfile(member)
            if fh is None:
                continue
            frames.append((valid, process_png(fh.read(MAX_PNG))))
    return sorted(frames)


# ------------------------------------------------------------------ cache

def _get(base, url, limit, key):
    """Stáhne a zapamatuje si 404 (ať se na chybějící snímek neptáme pořád)."""
    if key in _state["missing"]:
        return None
    data = fetch_bytes(url, limit)
    if data is None:
        _state["missing"][key] = time.time()
    return data


def refresh(base, now=None):
    now = now or time.time()
    with _lock:
        if now - _state["checked"] < REFRESH_EVERY:
            if not _state["frames"]:
                raise RadarUnavailable("ČHMÚ před chvílí neodpověděl")
            return
        _state["checked"] = now          # i při chybě: další pokus nejdřív za minutu
        frames, missing = _state["frames"], _state["missing"]
        newest = int(now // SLOT * SLOT)

        # nejnovější zveřejněný snímek (ČHMÚ bývá 1–3 minuty pozadu)
        latest = None
        for k in range(8):
            ts = newest - k * SLOT
            fid = f"obs-{ts}"
            if fid in frames:
                latest = ts
                break
            if k == 0:
                missing.pop(fid, None)      # aktuální slot zkoušet vždy znovu
            raw = _get(base, obs_url(base, ts), MAX_PNG, fid)
            if raw:
                frames[fid] = {"t": ts, "kind": "obs", "png": process_png(raw)}
                latest = ts
                break
        if latest is None:
            raise RadarUnavailable("ČHMÚ nemá žádný čerstvý snímek")

        for k in range(1, PAST_FRAMES):
            ts = latest - k * PAST_STEP
            fid = f"obs-{ts}"
            if fid not in frames:
                raw = _get(base, obs_url(base, ts), MAX_PNG, fid)
                if raw:
                    frames[fid] = {"t": ts, "kind": "obs", "png": process_png(raw)}

        # extrapolace z nejnovějšího výpočtu
        for calc in (latest, latest - SLOT):
            if _state["fct_calc"] == calc:
                break
            raw = _get(base, fct_url(base, calc), MAX_TAR, f"fct-{calc}")
            if raw:
                parsed = parse_forecast_tar(raw, calc)
                if parsed:
                    for fid in [f for f, v in frames.items() if v["kind"] == "fct"]:
                        del frames[fid]
                    for valid, png in parsed:
                        frames[f"fct-{calc}-{valid}"] = {"t": valid, "kind": "fct", "png": png}
                    _state["fct_calc"] = calc
                break

        # úklid: starší než hodina, staré záznamy o chybějících snímcích
        oldest = latest - (PAST_FRAMES - 1) * PAST_STEP
        for fid in [f for f, v in frames.items() if v["kind"] == "obs" and v["t"] < oldest]:
            del frames[fid]
        for fid in [f for f, at in missing.items() if now - at > 3 * 3600]:
            del missing[fid]


def get_frames(base, now=None):
    """Vrátí (seznam snímků, stale). stale = ČHMÚ teď neodpovídá, ukazujeme poslední stav."""
    stale = False
    try:
        refresh(base, now)
    except Exception:
        with _lock:
            if not _state["frames"]:
                raise
        stale = True
    with _lock:
        items = sorted(_state["frames"].items(), key=lambda kv: kv[1]["t"])
        return [{"id": fid, "t": v["t"], "kind": v["kind"]} for fid, v in items], stale


def get_png(frame_id):
    if not FRAME_ID.match(frame_id or ""):
        return None
    with _lock:
        frame = _state["frames"].get(frame_id)
        return frame["png"] if frame else None


def clear_cache():
    with _lock:
        _state.update(frames={}, missing={}, checked=0.0, fct_calc=None)

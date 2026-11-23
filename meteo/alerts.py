"""Upozornění: pravidla nad naměřenými daty a předpovědí, odesílání přes Web Push.

Spouští se každých 5 minut (systemd timer → meteo-manage check-alerts).
Každé pravidlo má stav v tabulce alert_state:
  since   – od kdy podmínka platí (zpráva až když platí dost dlouho, ať neruší výkyvy)
  active  – zpráva už odešla; další přijde až po návratu do normálu (hystereze)
V nočním klidu se zprávy jen zapíšou; pokud ráno pořád platí, odešlou se.
"""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db, webpush

PRAGUE = ZoneInfo("Europe/Prague")
OUTDOOR = "outdoor_station"

DEFAULTS = {
    "offline": {"on": True, "minutes": 20},
    "frost": {"on": True, "below": 0.0},
    "frost_forecast": {"on": True, "below": 0.0},
    "rain": {"on": True, "mm": 0.2},
    "air": {"on": True, "pm25": 35},
    "sensor": {"on": True},
    "quiet": {"on": True, "from": 22, "to": 7},
}
LIMITS = {
    ("offline", "minutes"): (10, 720),
    ("frost", "below"): (-30, 10),
    ("frost_forecast", "below"): (-30, 10),
    ("rain", "mm"): (0.1, 20),
    ("air", "pm25"): (5, 500),
    ("quiet", "from"): (0, 23),
    ("quiet", "to"): (0, 23),
}
# Tyto zprávy projdou i nočním klidem
URGENT = {"frost"}
SENSOR_NAMES = {"ds18b20": "teploty (DS18B20)", "dht22": "vlhkosti (DHT22)",
                "bh1750": "světla (BH1750)", "pms5003": "kvality vzduchu (PMS5003)"}
SENSOR_METRIC = {"temperature": "ds18b20", "humidity": "dht22", "light": "bh1750",
                 "pm25": "pms5003", "pm10": "pms5003", "pm1": "pms5003"}


def fmt(v, digits=1):
    return f"{v:.{digits}f}".replace(".", ",").replace("-", "−")


# ------------------------------------------------------------------ nastavení

def load_settings(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'alerts'").fetchone()
    stored = {}
    if row:
        try:
            stored = json.loads(row["value"])
        except ValueError:
            stored = {}
    return validate(stored)


def validate(data):
    """Doplní výchozí hodnoty, zahodí neznámé klíče, ohlídá rozsahy."""
    out = {}
    data = data if isinstance(data, dict) else {}
    for rule, defaults in DEFAULTS.items():
        given = data.get(rule) if isinstance(data.get(rule), dict) else {}
        merged = {}
        for key, default in defaults.items():
            val = given.get(key, default)
            if isinstance(default, bool):
                merged[key] = val if isinstance(val, bool) else default
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                val = default
            lo, hi = LIMITS.get((rule, key), (val, val))
            val = min(max(val, lo), hi)
            merged[key] = int(val) if isinstance(default, int) else round(val, 1)
        out[rule] = merged
    return out


def save_settings(conn, data):
    clean = validate(data)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('alerts', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps(clean),),
    )
    conn.commit()
    return clean


def in_quiet(settings, now):
    q = settings["quiet"]
    if not q["on"]:
        return False
    hour = datetime.fromtimestamp(now, PRAGUE).hour
    start, end = q["from"], q["to"]
    return (start <= hour or hour < end) if start > end else (start <= hour < end)


# ------------------------------------------------------------------ odesílání

def load_key(path):
    """VAPID klíč, nebo None když ještě není (upozornění se pak jen zapisují)."""
    try:
        return webpush.load_vapid(path) if path else None
    except OSError:
        return None


class Sender:
    """Odesílá na všechna přihlášená zařízení. Bez VAPID klíče jen zapisuje historii."""

    def __init__(self, conn, key=None, subject=None):
        self.conn, self.key, self.subject = conn, key, subject

    def push(self, message, only_endpoint=None):
        if self.key is None:
            return 0, 0
        query = "SELECT id, endpoint, p256dh, auth FROM push_subscriptions"
        args = ()
        if only_endpoint:
            query += " WHERE endpoint = ?"
            args = (only_endpoint,)
        sent = failed = 0
        for sub in self.conn.execute(query, args).fetchall():
            try:
                webpush.send(dict(sub), message, self.key, self.subject,
                             urgency="high" if message.get("urgent") else "normal")
                self.conn.execute("UPDATE push_subscriptions SET last_ok = ?, fails = 0 WHERE id = ?",
                                  (db.now(), sub["id"]))
                sent += 1
            except webpush.PushGone:
                self.conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
                failed += 1
            except Exception:  # noqa: BLE001 – jedno zařízení nesmí zastavit ostatní
                self.conn.execute("UPDATE push_subscriptions SET fails = fails + 1 WHERE id = ?", (sub["id"],))
                self.conn.execute("DELETE FROM push_subscriptions WHERE id = ? AND fails > 50", (sub["id"],))
                failed += 1
        self.conn.commit()
        return sent, failed


def notify(conn, sender, rule, title, body, now, quiet, url="/"):
    hold = quiet and rule.split(":")[0] not in URGENT
    cur = conn.execute(
        "INSERT INTO notifications (ts, rule, title, body, sent) VALUES (?, ?, ?, ?, ?)",
        (now, rule, title, body, 0 if hold else 1),
    )
    conn.commit()
    if not hold:
        sender.push({"title": title, "body": body, "tag": rule, "url": url, "ts": now,
                     "id": cur.lastrowid, "urgent": rule.split(":")[0] in URGENT})


def flush_held(conn, sender, now, active_rules):
    """Po nočním klidu pošle zadržené zprávy, které pořád platí."""
    rows = conn.execute(
        "SELECT id, rule, title, body, ts FROM notifications WHERE sent = 0 AND ts > ?",
        (now - 16 * 3600,)).fetchall()
    for r in rows:
        if r["rule"] in active_rules:
            sender.push({"title": r["title"], "body": r["body"], "tag": r["rule"], "url": "/", "ts": r["ts"], "id": r["id"]})
            conn.execute("UPDATE notifications SET sent = 1 WHERE id = ?", (r["id"],))
        else:
            conn.execute("UPDATE notifications SET sent = 2 WHERE id = ?", (r["id"],))
    conn.execute("UPDATE notifications SET sent = 2 WHERE sent = 0 AND ts <= ?", (now - 16 * 3600,))
    conn.commit()


# ------------------------------------------------------------------ stav pravidel

def _state(conn, rule):
    row = conn.execute("SELECT active, since, last_sent FROM alert_state WHERE rule = ?", (rule,)).fetchone()
    return dict(row) if row else {"active": 0, "since": None, "last_sent": None}


def _save(conn, rule, st):
    conn.execute(
        "INSERT INTO alert_state (rule, active, since, last_sent) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(rule) DO UPDATE SET active = excluded.active, since = excluded.since, "
        "last_sent = excluded.last_sent",
        (rule, st["active"], st["since"], st["last_sent"]),
    )


class Ctx:
    def __init__(self, conn, sender, now, quiet):
        self.conn, self.sender, self.now, self.quiet = conn, sender, now, quiet
        self.active = set()

    def step(self, rule, on, hold=0, cooldown=0, message=None, resolved=None):
        """on: podmínka platí (None = nevíme, stav se nemění)."""
        st = _state(self.conn, rule)
        if on is None:
            if st["active"]:
                self.active.add(rule)
            return
        if on:
            st["since"] = st["since"] or self.now
            ready = self.now - st["since"] >= hold
            cooled = not st["last_sent"] or self.now - st["last_sent"] >= cooldown
            if not st["active"] and ready and cooled and message:
                title, body = message() if callable(message) else message
                notify(self.conn, self.sender, rule, title, body, self.now, self.quiet)
                st["active"], st["last_sent"] = 1, self.now
            if st["active"]:
                self.active.add(rule)
        else:
            if st["active"] and resolved:
                title, body = resolved() if callable(resolved) else resolved
                notify(self.conn, self.sender, rule + ":ok", title, body, self.now, self.quiet)
            st["active"], st["since"] = 0, None
        _save(self.conn, rule, st)


# ------------------------------------------------------------------ data

def _latest(conn, station, metric, max_age, now):
    row = conn.execute(
        "SELECT value, ts FROM readings WHERE station_id = ? AND metric = ? ORDER BY ts DESC LIMIT 1",
        (station, metric)).fetchone()
    return row["value"] if row and now - row["ts"] <= max_age else None


def _avg(conn, station, metric, seconds, now):
    row = conn.execute(
        "SELECT AVG(value) AS v, COUNT(*) AS n FROM readings WHERE station_id = ? AND metric = ? AND ts > ?",
        (station, metric, now - seconds)).fetchone()
    return row["v"] if row and row["n"] else None


def _hm(ts):
    return datetime.fromtimestamp(ts, PRAGUE).strftime("%H:%M")


# ------------------------------------------------------------------ pravidla

def check(conn, settings, sender, forecast=None, now=None):
    """Vyhodnotí všechna pravidla. forecast: normalizovaná předpověď nebo None."""
    now = int(now or time.time())
    quiet = in_quiet(settings, now)
    ctx = Ctx(conn, sender, now, quiet)
    stations = conn.execute("SELECT id, name, last_seen, diag, diag_at FROM stations").fetchall()

    # Výpadek stanice
    cfg = settings["offline"]
    for st in stations:
        rule = f"offline:{st['id']}"
        if not cfg["on"] or not st["last_seen"]:
            ctx.step(rule, False)
            continue
        gone = now - st["last_seen"]
        ctx.step(
            rule, gone > cfg["minutes"] * 60,
            message=("Stanice neposílá data",
                     f"{st['name']}: poslední měření v {_hm(st['last_seen'])}. Zkontroluj napájení a Wi-Fi."),
            resolved=("Stanice je zase online", f"{st['name']}: data zase přicházejí."),
        )

    # Porucha čidla (podle diagnostiky z firmwaru a hodnot, které server zahodil)
    for st in stations:
        try:
            diag = json.loads(st["diag"]) if st["diag"] else {}
        except ValueError:
            diag = {}
        fresh = st["diag_at"] and now - st["diag_at"] < 15 * 60
        sensors = ((diag.get("station") or {}).get("sensors")) or {}
        bad = {name: (s.get("err") or "chyba čtení") for name, s in sensors.items()
               if isinstance(s, dict) and s.get("ok") is False}
        for item in diag.get("rejected") or []:
            name = SENSOR_METRIC.get(item.get("metric"))
            if name and name not in bad:
                bad[name] = f"nesmyslná hodnota {item.get('value')}"
        for name, label in SENSOR_NAMES.items():
            rule = f"sensor:{st['id']}:{name}"
            if not settings["sensor"]["on"]:
                ctx.step(rule, False)
                continue
            if not fresh:
                ctx.step(rule, None)        # stanice mlčí – to hlídá výpadek
                continue
            err = bad.get(name)
            ctx.step(rule, err is not None, hold=10 * 60,
                     message=(f"Porucha čidla {label}", f"{st['name']}: {err}."),
                     resolved=(f"Čidlo {label} zase měří", f"{st['name']}: v pořádku."))

    # Mráz – naměřený (hystereze 1 °C, ať nepípá kolem nuly)
    cfg = settings["frost"]
    temp = _latest(conn, OUTDOOR, "temperature", 30 * 60, now)
    st = _state(conn, "frost")
    limit = cfg["below"] + (1.0 if st["active"] else 0.0)
    ctx.step("frost", None if temp is None else (cfg["on"] and temp <= limit), hold=9 * 60,
             message=lambda: ("Venku mrzne", f"Teplota {fmt(temp)} °C."))

    # Mráz – předpověď na noc, jednou denně po 17. hodině
    cfg = settings["frost_forecast"]
    local = datetime.fromtimestamp(now, PRAGUE)
    rule = f"frost_forecast:{local:%Y-%m-%d}"
    tmin = None
    if forecast and forecast.get("hours"):
        temps = [(h["t"], h["temp"]) for h in forecast["hours"]
                 if h.get("temp") is not None and now <= h["t"] <= now + 16 * 3600]
        if temps:
            tmin = min(temps, key=lambda x: x[1])
    conn.execute("DELETE FROM alert_state WHERE rule LIKE 'frost_forecast:%' AND rule < ?", (rule,))
    if cfg["on"] and local.hour >= 17 and tmin and tmin[1] <= cfg["below"] and "frost" not in ctx.active:
        ctx.step(rule, True, message=("V noci bude mrznout",
                                      f"Kolem {_hm(tmin[0])} až {fmt(tmin[1])} °C. Rostliny a hadice dovnitř."))

    # Déšť v příští hodině (z 15minutové předpovědi), nejvýš jednou za 3 hodiny
    cfg = settings["rain"]
    rain_on = None
    first = None
    if forecast and forecast.get("nowcast"):
        soon = [n for n in forecast["nowcast"] if n.get("precip") is not None and now - 900 <= n["t"] <= now + 3600]
        if soon:
            total = sum(n["precip"] for n in soon)
            wet = [n for n in soon if n["precip"] >= 0.1]
            first = wet[0] if wet else None
            rain_on = cfg["on"] and total >= cfg["mm"] and first is not None
    ctx.step("rain", rain_on, cooldown=3 * 3600,
             message=lambda: ("Začíná pršet" if first["t"] <= now else "Do hodiny bude pršet",
                              f"Srážky kolem {_hm(max(first['t'], now))}, "
                              f"až {fmt(max(n['precip'] for n in forecast['nowcast']))} mm za 15 min."))

    # Kvalita vzduchu – průměr PM2.5 za 30 min, návrat pod 70 % prahu
    cfg = settings["air"]
    pm = _avg(conn, OUTDOOR, "pm25", 30 * 60, now)
    st = _state(conn, "air")
    limit = cfg["pm25"] * (0.7 if st["active"] else 1.0)
    ctx.step("air", None if pm is None else (cfg["on"] and pm >= limit), hold=20 * 60, cooldown=2 * 3600,
             message=lambda: ("Zhoršená kvalita vzduchu",
                              f"PM2.5 {fmt(pm, 0)} µg/m³ za poslední půlhodinu. Raději nevětrej."))

    conn.commit()
    if not quiet:
        flush_held(conn, sender, now, ctx.active)
    return ctx.active

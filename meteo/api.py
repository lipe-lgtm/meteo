"""JSON API v1.

Stanice (hlavička X-API-Key):
  POST /api/v1/ingest            – odeslání měření
  GET  /api/v1/station/relay     – požadovaný stav relé pro tuto stanici

Aplikace (session cookie po přihlášení):
  POST /api/v1/auth/login | /auth/logout,  GET /api/v1/auth/me
  GET  /api/v1/stations          – seznam stanic a jestli jsou online
  GET  /api/v1/latest            – poslední hodnota každé veličiny
  GET  /api/v1/history           – průběh jedné veličiny (zprůměrovaný)
  POST /api/v1/relay             – zapnout/vypnout relé
  POST /api/v1/admin/reset       – smazat naměřená data (vyžaduje heslo znovu)

Veřejné:
  GET  /api/v1/health            – jestli server žije (bez dat)
"""
from flask import Blueprint, current_app, g, jsonify, request, session

from . import db, forecast
from .auth import limiter, login_required, station_required, verify_user

bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ------------------------------------------------------------------ helpers

def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _num(value):
    """Číslo nebo None – odmítne bool, NaN, nekonečno a řetězce."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def extract_readings(sensors):
    """Převede payload ve formátu, který dnes posílá ESP32, na dvojice (veličina, hodnota).

    Stanice ví, jestli je venkovní nebo vnitřní, takže teplota z bloku
    "outdoor" i "indoor" se ukládá jako "temperature" dané stanice.
    """
    out = []
    if not isinstance(sensors, dict):
        return out

    for block in ("outdoor", "indoor"):
        part = sensors.get(block)
        if isinstance(part, dict):
            out.append(("temperature", part.get("temperature")))
            out.append(("humidity", part.get("humidity")))

    light = sensors.get("light")
    out.append(("light", light.get("lux") if isinstance(light, dict) else light))

    soil = sensors.get("soil")
    if isinstance(soil, dict):
        out.append(("soil_moisture", soil.get("moisture")))

    aq = sensors.get("air_quality")
    if isinstance(aq, dict):
        for key in ("pm1", "pm25", "pm10", "aqi"):
            out.append((key, aq.get(key)))

    clean = []
    for metric, raw in out:
        val = _num(raw)
        if val is None:
            continue
        lo, hi = db.METRICS[metric]
        if lo <= val <= hi:
            clean.append((metric, round(val, 2)))
    return clean


def _client_ip():
    return request.remote_addr or "?"


# ------------------------------------------------------------------ stanice

@bp.post("/ingest")
@station_required
def ingest():
    body = _json_body()
    if body is None:
        return jsonify(error="invalid_json"), 400

    readings = extract_readings(body.get("sensors"))
    ts = db.now()
    conn = db.get_db()
    conn.executemany(
        "INSERT INTO readings (station_id, ts, metric, value) VALUES (?, ?, ?, ?)",
        [(g.station_id, ts, m, v) for m, v in readings],
    )
    conn.execute(
        "UPDATE stations SET last_seen = ?, last_uptime = ? WHERE id = ?",
        (ts, _num(body.get("uptime")), g.station_id),
    )
    reported = body.get("relay_state")
    if reported in (0, 1):
        conn.execute(
            """INSERT INTO relays (station_id, desired_state, reported_state, updated_at)
               VALUES (?, 0, ?, ?)
               ON CONFLICT(station_id) DO UPDATE SET reported_state = excluded.reported_state""",
            (g.station_id, reported, ts),
        )
    conn.commit()
    return jsonify(ok=True, stored=len(readings))


@bp.get("/station/relay")
@station_required
def station_relay():
    row = db.get_db().execute(
        "SELECT desired_state FROM relays WHERE station_id = ?", (g.station_id,)
    ).fetchone()
    return jsonify(relay_state=row["desired_state"] if row else 0)


# ------------------------------------------------------------------ přihlášení

@bp.post("/auth/login")
def login():
    ip = _client_ip()
    if limiter.blocked(ip):
        return jsonify(error="too_many_attempts"), 429

    body = _json_body() or {}
    username = str(body.get("username", ""))[:64]
    password = str(body.get("password", ""))[:256]

    if not username or not password or not verify_user(username, password):
        limiter.fail(ip)
        return jsonify(error="invalid_credentials"), 401

    limiter.reset(ip)
    session.clear()  # nová session po přihlášení
    session.permanent = True
    session["user"] = username
    conn = db.get_db()
    db.audit(conn, username, "login", ip)
    conn.commit()
    return jsonify(user=username)


@bp.post("/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@bp.get("/auth/me")
@login_required
def me():
    return jsonify(user=g.user)


# ------------------------------------------------------------------ data pro aplikaci

@bp.get("/stations")
@login_required
def stations():
    now = db.now()
    offline_after = current_app.config["STATION_OFFLINE_AFTER"]
    rows = db.get_db().execute(
        """SELECT s.id, s.name, s.last_seen, s.last_uptime,
                  r.desired_state, r.reported_state
           FROM stations s LEFT JOIN relays r ON r.station_id = s.id
           ORDER BY s.id"""
    ).fetchall()
    return jsonify(
        now=now,
        stations=[
            {
                "id": r["id"],
                "name": r["name"],
                "last_seen": r["last_seen"],
                "online": r["last_seen"] is not None and now - r["last_seen"] <= offline_after,
                "uptime": r["last_uptime"],
                "relay": {"desired": r["desired_state"], "reported": r["reported_state"]},
            }
            for r in rows
        ],
    )


@bp.get("/latest")
@login_required
def latest():
    """Poslední známá hodnota každé veličiny každé stanice (i když přišla dřív než
    poslední paket – např. PM2.5 se měří jen každých 10 minut)."""
    rows = db.get_db().execute(
        """SELECT r.station_id, r.metric, r.value, r.ts
           FROM readings r
           JOIN (SELECT station_id, metric, MAX(ts) AS ts
                 FROM readings GROUP BY station_id, metric) m
             ON m.station_id = r.station_id AND m.metric = r.metric AND m.ts = r.ts"""
    ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["station_id"], {})[r["metric"]] = {"value": r["value"], "ts": r["ts"]}
    return jsonify(now=db.now(), stations=out)


@bp.get("/history")
@login_required
def history():
    station = request.args.get("station", "")
    metric = request.args.get("metric", "")
    if metric not in db.METRICS:
        return jsonify(error="unknown_metric"), 400
    try:
        hours = max(1, min(int(request.args.get("hours", 24)), 24 * 730))
        points = max(10, min(int(request.args.get("points", 300)), 2000))
    except ValueError:
        return jsonify(error="invalid_params"), 400

    now = db.now()
    since = now - hours * 3600
    bucket = max(60, (hours * 3600) // points)
    rows = db.get_db().execute(
        """SELECT (ts / ?) * ? AS t, AVG(value) AS avg, MIN(value) AS min, MAX(value) AS max
           FROM readings
           WHERE station_id = ? AND metric = ? AND ts >= ?
           GROUP BY t ORDER BY t""",
        (bucket, bucket, station, metric, since),
    ).fetchall()
    return jsonify(
        station=station,
        metric=metric,
        bucket_seconds=bucket,
        points=[[r["t"], round(r["avg"], 2), r["min"], r["max"]] for r in rows],
    )


@bp.get("/config")
@login_required
def config():
    lat, lon = current_app.config["LOCATION_LAT"], current_app.config["LOCATION_LON"]
    return jsonify(location={"lat": lat, "lon": lon} if lat is not None and lon is not None else None)


@bp.get("/forecast")
@login_required
def get_forecast():
    lat, lon = current_app.config["LOCATION_LAT"], current_app.config["LOCATION_LON"]
    if lat is None or lon is None:
        return jsonify(error="location_not_set"), 404
    try:
        data, stale = forecast.get_forecast(current_app.config["FORECAST_URL"], lat, lon)
    except Exception as exc:  # noqa: BLE001 – upstream může selhat jakkoli
        current_app.logger.warning("Předpověď nedostupná: %s", exc)
        return jsonify(error="forecast_unavailable"), 503
    return jsonify(stale=stale, source="Open-Meteo", **data)


# ------------------------------------------------------------------ ovládání

@bp.post("/relay")
@login_required
def set_relay():
    body = _json_body() or {}
    station = body.get("station")
    state = body.get("state")
    if state not in (0, 1) or not isinstance(station, str):
        return jsonify(error="invalid_params"), 400
    conn = db.get_db()
    if conn.execute("SELECT 1 FROM stations WHERE id = ?", (station,)).fetchone() is None:
        return jsonify(error="unknown_station"), 404
    ts = db.now()
    conn.execute(
        """INSERT INTO relays (station_id, desired_state, updated_at, updated_by)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(station_id) DO UPDATE SET desired_state = excluded.desired_state,
               updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
        (station, state, ts, g.user),
    )
    db.audit(conn, g.user, "relay", f"{station}={state}")
    conn.commit()
    return jsonify(station=station, desired=state)


@bp.post("/admin/reset")
@login_required
def admin_reset():
    """Smaže všechna měření. Vyžaduje znovu heslo a potvrzovací slovo."""
    body = _json_body() or {}
    if body.get("confirm") != "SMAZAT":
        return jsonify(error="confirmation_required"), 400
    ip = _client_ip()
    if limiter.blocked(ip):
        return jsonify(error="too_many_attempts"), 429
    if not verify_user(g.user, str(body.get("password", ""))):
        limiter.fail(ip)
        return jsonify(error="invalid_credentials"), 401
    conn = db.get_db()
    deleted = conn.execute("DELETE FROM readings").rowcount
    db.audit(conn, g.user, "reset", f"deleted={deleted}")
    conn.commit()
    conn.execute("VACUUM")
    return jsonify(deleted=deleted)


# ------------------------------------------------------------------ veřejné

@bp.get("/health")
def health():
    return jsonify(ok=True, now=db.now())

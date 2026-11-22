import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from meteo import create_app, db  # noqa: E402
from meteo.auth import hash_api_key, hash_password, limiter  # noqa: E402

KEY = "mk_test_outdoor_key"
APP_HDR = {"X-Requested-With": "meteo-app"}

PAYLOAD = {
    "device_id": "outdoor_station",
    "sensors": {
        "outdoor": {"temperature": 4.3, "humidity": 81.0},
        "light": {"lux": 1200.5, "category": "Jasno"},
        "air_quality": {"pm25": 14, "pm10": 22, "aqi": 55, "status": "Dobry"},
    },
    "relay_state": 0,
    "voltage": 5.0,
    "uptime": 1234,
}


@pytest.fixture
def app(tmp_path):
    limiter._fails.clear()
    app = create_app({
        "DATABASE": str(tmp_path / "t.db"),
        "SECRET_KEY": "test",
        "SESSION_COOKIE_SECURE": False,
        "TESTING": True,
    })
    with app.app_context():
        conn = db.get_db()
        conn.execute("INSERT INTO stations (id, name, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
                     ("outdoor_station", "Venku", hash_api_key(KEY), db.now()))
        conn.execute("INSERT INTO users VALUES (?, ?, ?)",
                     ("petr", hash_password("spravne-heslo-123"), db.now()))
        conn.commit()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client):
    r = client.post("/api/v1/auth/login",
                    json={"username": "petr", "password": "spravne-heslo-123"})
    assert r.status_code == 200


def test_ingest_requires_key(client):
    assert client.post("/api/v1/ingest", json=PAYLOAD).status_code == 401
    assert client.post("/api/v1/ingest", json=PAYLOAD,
                       headers={"X-API-Key": "spatny"}).status_code == 401


def test_ingest_and_latest(client):
    r = client.post("/api/v1/ingest", json=PAYLOAD, headers={"X-API-Key": KEY})
    assert r.status_code == 200 and r.json["stored"] == 6
    assert client.get("/api/v1/latest").status_code == 401
    login(client)
    data = client.get("/api/v1/latest").json["stations"]["outdoor_station"]
    assert data["temperature"]["value"] == 4.3
    assert data["pm25"]["value"] == 14
    st = client.get("/api/v1/stations").json["stations"][0]
    assert st["online"] is True and st["relay"]["reported"] == 0


def test_latest_keeps_older_pm(client):
    """PM se měří jen občas – poslední hodnota nesmí zmizet."""
    client.post("/api/v1/ingest", json=PAYLOAD, headers={"X-API-Key": KEY})
    no_pm = {**PAYLOAD, "sensors": {"outdoor": {"temperature": 5.0, "humidity": 80}}}
    client.post("/api/v1/ingest", json=no_pm, headers={"X-API-Key": KEY})
    login(client)
    data = client.get("/api/v1/latest").json["stations"]["outdoor_station"]
    assert data["pm25"]["value"] == 14


def test_bad_values_dropped(client):
    bad = {"sensors": {"outdoor": {"temperature": 999, "humidity": "abc"},
                       "light": {"lux": True}}}
    r = client.post("/api/v1/ingest", json=bad, headers={"X-API-Key": KEY})
    assert r.json["stored"] == 0
    assert {x["metric"] for x in r.json["rejected"]} == {"temperature", "humidity", "light"}


def test_diag_and_rejected_visible_in_stations(client):
    """85 °C z DS18B20 (nedoběhlý převod) server odmítne a ukáže to v aplikaci."""
    body = {
        "sensors": {"outdoor": {"temperature": 85.0, "humidity": None}, "light": {"lux": 10}},
        "diag": {
            "fw": "2026-11-22", "rssi": -67, "mem": 81234,
            "sensors": {"ds18b20": {"ok": False, "found": 1, "err": "85 °C" + "x" * 500},
                        "dht22": {"ok": False, "err": "ETIMEDOUT"}},
            "junk": [1, 2, 3],
        },
    }
    r = client.post("/api/v1/ingest", json=body, headers={"X-API-Key": KEY})
    assert r.json["stored"] == 1
    assert r.json["rejected"] == [{"metric": "temperature", "value": 85.0}]
    login(client)
    st = client.get("/api/v1/stations").json["stations"][0]
    assert st["diag"]["rejected"][0]["value"] == 85.0
    diag = st["diag"]["station"]
    assert diag["rssi"] == -67 and diag["junk"] is None
    assert len(diag["sensors"]["ds18b20"]["err"]) == 120
    assert st["diag_at"] is not None


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE stations (id TEXT PRIMARY KEY, name TEXT NOT NULL, api_key_hash TEXT NOT NULL,"
                 " created_at INTEGER NOT NULL, last_seen INTEGER, last_uptime REAL)")
    conn.commit()
    conn.close()
    create_app({"DATABASE": str(path), "SECRET_KEY": "t", "TESTING": True})
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(stations)")}
    assert {"diag", "diag_at"} <= cols


def test_history(client):
    client.post("/api/v1/ingest", json=PAYLOAD, headers={"X-API-Key": KEY})
    login(client)
    r = client.get("/api/v1/history?station=outdoor_station&metric=temperature&hours=24")
    assert r.status_code == 200 and r.json["points"][0][1] == 4.3
    assert client.get("/api/v1/history?metric=evil").status_code == 400


def test_relay_flow_and_csrf(client):
    login(client)
    # bez vlastní hlavičky = CSRF ochrana
    assert client.post("/api/v1/relay", json={"station": "outdoor_station", "state": 1}
                       ).status_code == 403
    r = client.post("/api/v1/relay", json={"station": "outdoor_station", "state": 1},
                    headers=APP_HDR)
    assert r.status_code == 200
    r = client.get("/api/v1/station/relay", headers={"X-API-Key": KEY})
    assert r.json["relay_state"] == 1


def test_login_rate_limit(client):
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "petr", "password": "x"})
    r = client.post("/api/v1/auth/login",
                    json={"username": "petr", "password": "spravne-heslo-123"})
    assert r.status_code == 429


def test_reset_needs_password(client):
    client.post("/api/v1/ingest", json=PAYLOAD, headers={"X-API-Key": KEY})
    login(client)
    assert client.post("/api/v1/admin/reset", json={"confirm": "SMAZAT", "password": "x"},
                       headers=APP_HDR).status_code == 401
    r = client.post("/api/v1/admin/reset",
                    json={"confirm": "SMAZAT", "password": "spravne-heslo-123"},
                    headers=APP_HDR)
    assert r.status_code == 200 and r.json["deleted"] == 6


def test_legacy_only_from_lan(client):
    assert client.get("/data/simple", environ_base={"REMOTE_ADDR": "10.8.0.5"}).status_code == 404
    r = client.get("/data/simple", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 200 and "outdoor" in r.json["sensors"]


def test_migrate_legacy(tmp_path):
    old = tmp_path / "home_center.db"
    c = sqlite3.connect(old)
    c.execute("""CREATE TABLE measurements (id INTEGER PRIMARY KEY, timestamp DATETIME,
                 device_id TEXT, outdoor_temperature REAL, outdoor_humidity REAL,
                 indoor_temperature REAL, indoor_humidity REAL, light_lux REAL,
                 soil_moisture REAL, pm25 REAL, pm10 REAL, aqi INTEGER)""")
    c.execute("INSERT INTO measurements VALUES (1,'2026-11-20 10:00:00.123+01:00','outdoor_station',"
              "3.5,80,NULL,NULL,500,NULL,12,20,50)")
    c.execute("INSERT INTO measurements VALUES (2,'2026-11-20 11:00:00','outdoor_station',"
              "3.9,79,NULL,NULL,NULL,NULL,NULL,NULL,NULL)")
    c.commit()
    c.close()

    new = tmp_path / "new.db"
    env = {**os.environ, "METEO_DB": str(new)}
    run = lambda *a: subprocess.run([sys.executable, "manage.py", *a], cwd=ROOT, env=env,
                                    capture_output=True, text=True, check=True)
    run("add-station", "outdoor_station", "Venku")
    out = run("migrate-legacy", str(old)).stdout
    assert "přeneseno 8 hodnot" in out
    conn = sqlite3.connect(new)
    ts = [r[0] for r in conn.execute(
        "SELECT ts FROM readings WHERE metric='temperature' ORDER BY ts")]
    assert ts == [1795165200, 1795168800]  # 2026-11-20 09:00 a 10:00 UTC


# ------------------------------------------------------------------ předpověď

FAKE_OM = {
    "daily": {
        "time": [1795302000, 1795388400],
        "weather_code": [61, 3],
        "temperature_2m_max": [6.8, 5.1],
        "temperature_2m_min": [1.2, -0.4],
        "precipitation_sum": [2.4, 0.0],
        "precipitation_probability_max": [80, 10],
    },
    "minutely_15": {"time": [1795370400, 1795371300, 1795372200], "precipitation": [0.0, 0.0, 0.3]},
    "hourly": {"time": [1795370400], "temperature_2m": [4.1], "precipitation": [0.0], "weather_code": [3]},
}


@pytest.fixture
def geo_app(tmp_path, monkeypatch):
    from meteo import forecast
    forecast.clear_cache()
    calls = []

    def fake_fetch(url, timeout=8):
        calls.append(url)
        if fake_fetch.fail:
            raise OSError("down")
        return FAKE_OM
    fake_fetch.fail = False
    monkeypatch.setattr(forecast, "fetch_upstream", fake_fetch)
    app = create_app({
        "DATABASE": str(tmp_path / "t.db"), "SECRET_KEY": "test", "SESSION_COOKIE_SECURE": False,
        "TESTING": True, "LOCATION_LAT": 50.0875, "LOCATION_LON": 14.4214,
    })
    with app.app_context():
        conn = db.get_db()
        conn.execute("INSERT INTO users VALUES (?, ?, ?)", ("petr", hash_password("spravne-heslo-123"), db.now()))
        conn.commit()
    limiter._fails.clear()
    return app, calls, fake_fetch


def test_forecast_requires_login_and_location(client):
    assert client.get("/api/v1/forecast").status_code == 401
    login(client)
    assert client.get("/api/v1/forecast").json["error"] == "location_not_set"
    assert client.get("/api/v1/config").json["location"] is None


def test_forecast_normalized_and_cached(geo_app):
    app, calls, _ = geo_app
    c = app.test_client()
    login(c)
    r = c.get("/api/v1/forecast")
    assert r.status_code == 200
    d = r.json
    assert d["stale"] is False and d["source"] == "Open-Meteo"
    assert d["days"][0] == {"t": 1795302000, "code": 61, "tmax": 6.8, "tmin": 1.2, "precip": 2.4, "prob": 80}
    assert d["nowcast"][2]["precip"] == 0.3
    assert "latitude=50.0875" in calls[0] and "timeformat=unixtime" in calls[0]
    c.get("/api/v1/forecast")
    assert len(calls) == 1          # druhý dotaz z cache
    assert c.get("/api/v1/config").json["location"] == {"lat": 50.0875, "lon": 14.4214}


def test_forecast_falls_back_to_stale(geo_app, monkeypatch):
    from meteo import forecast
    app, calls, fake = geo_app
    c = app.test_client()
    login(c)
    c.get("/api/v1/forecast")
    monkeypatch.setattr(forecast, "CACHE_SECONDS", 0)
    fake.fail = True
    r = c.get("/api/v1/forecast")
    assert r.status_code == 200 and r.json["stale"] is True
    forecast.clear_cache()
    assert c.get("/api/v1/forecast").status_code == 503


# ------------------------------------------------------------------ radar ČHMÚ

def _radar_png(color=(255, 0, 0, 255)):
    """Obrázek jako od ČHMÚ: 680×460, mapa vlevo dole, řezy vpravo/nahoře."""
    import io
    from PIL import Image
    img = Image.new("RGBA", (680, 460), (0, 0, 0, 0))
    img.paste(color, (100, 200, 140, 240))          # srážka v mapové části
    img.paste((200, 200, 200, 255), (620, 0, 680, 460))   # řez – musí se oříznout
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _radar_tar(calc_ts):
    import io
    import tarfile
    import time as _t
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for lead in range(10, 70, 10):
            stamp = _t.strftime("%Y%m%d.%H%M", _t.gmtime(calc_ts + lead * 60))
            data = _radar_png((0, 0, 255, 255))
            info = tarfile.TarInfo(f"pacz2gmaps3.fct_z_max.{stamp}.{lead:02d}.png")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        evil = tarfile.TarInfo("../../etc/passwd")
        evil.size = 4
        tar.addfile(evil, io.BytesIO(b"root"))
    return buf.getvalue()


@pytest.fixture
def radar_env(monkeypatch):
    from meteo import radar
    radar.clear_cache()
    now = 1795302000 + 7 * 60 + 30          # 3 minuty po zveřejnění slotu :05
    latest = now // 300 * 300 - 300          # aktuální slot ještě není venku
    calls = []

    def fake_fetch(url, limit, timeout=10):
        calls.append(url)
        if fake_fetch.down:
            raise OSError("down")
        if url.endswith(".tar"):
            return _radar_tar(latest) if radar._stamp(latest) in url else None
        if radar._stamp(latest + 300) in url:
            return None
        return _radar_png()
    fake_fetch.down = False
    monkeypatch.setattr(radar, "fetch_bytes", fake_fetch)
    monkeypatch.setattr(radar.time, "time", lambda: now)
    return radar, now, latest, calls, fake_fetch


def test_radar_frames_cropped_and_forecast(client, radar_env):
    import io
    from PIL import Image
    radar, now, latest, calls, _ = radar_env
    assert client.get("/api/v1/radar").status_code == 401
    login(client)
    r = client.get("/api/v1/radar")
    assert r.status_code == 200
    d = r.json
    assert d["bounds"] == [[48.047, 11.267], [51.458, 19.624]]
    obs = [f for f in d["frames"] if f["kind"] == "obs"]
    fct = [f for f in d["frames"] if f["kind"] == "fct"]
    assert len(obs) == 7 and obs[-1]["t"] == latest and obs[0]["t"] == latest - 3600
    assert [f["t"] - latest for f in fct] == [600, 1200, 1800, 2400, 3000, 3600]
    assert all("png_masked" in u for u in calls)

    img = client.get(obs[-1]["url"])
    assert img.status_code == 200 and img.mimetype == "image/png"
    assert "immutable" in img.headers["Cache-Control"]
    pic = Image.open(io.BytesIO(img.data))
    assert pic.size == (598, 378)
    assert pic.getpixel((120, 220 - 82))[:3] == (255, 0, 0)   # srážka zůstala na místě
    assert pic.getpixel((597, 10))[3] == 0                      # řez je pryč

    n = len(calls)
    client.get("/api/v1/radar")
    assert len(calls) == n                                       # do minuty z paměti


def test_radar_rejects_bad_ids_and_survives_outage(client, radar_env, monkeypatch):
    radar, now, latest, calls, fake = radar_env
    login(client)
    assert client.get("/api/v1/radar/..%2f..%2fetc.png").status_code == 404
    assert client.get("/api/v1/radar/obs-123.png").status_code == 404
    client.get("/api/v1/radar")
    fake.down = True
    monkeypatch.setattr(radar.time, "time", lambda: now + 120)
    r = client.get("/api/v1/radar")
    assert r.status_code == 200 and r.json["stale"] is True
    radar.clear_cache()
    assert client.get("/api/v1/radar").status_code == 503

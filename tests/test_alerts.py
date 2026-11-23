"""Upozornění: Web Push (šifrování ověřené dešifrováním) a pravidla."""
import json
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from test_api import APP_HDR, KEY, app, client, login  # noqa: F401 – fixtures
from meteo import alerts, db, webpush

PRAGUE_NOON = 1795431600          # 2026-11-23 12:00 Praha (mimo noční klid)
PRAGUE_NIGHT = 1795474800         # 2026-11-24 00:00 Praha


class Device:
    """Falešné zařízení: má vlastní klíče a umí zprávu dešifrovat (RFC 8291)."""

    def __init__(self, endpoint="https://web.push.apple.com/dev1"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def subscription(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": webpush.b64u(self.pub), "auth": webpush.b64u(self.auth)}}

    def decrypt(self, body):
        salt, idlen = body[:16], body[20]
        as_pub = body[21:21 + idlen]
        server = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub)
        secret = self.key.exchange(ec.ECDH(), server)
        ikm = webpush._hkdf(self.auth, secret, b"WebPush: info\x00" + self.pub + as_pub, 32)
        cek = webpush._hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = webpush._hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
        assert plain.endswith(b"\x02")
        return json.loads(plain[:-1])


@pytest.fixture
def push(app, tmp_path, monkeypatch):
    path = tmp_path / "vapid.pem"
    path.write_bytes(webpush.generate_vapid_pem())
    app.config["VAPID_KEY"] = str(path)
    app.config["VAPID_SUB"] = "https://meteo.example.cz"
    outbox = []

    def fake_post(url, body, headers, timeout=10):
        outbox.append((url, body, headers))
        return fake_post.status.get(url, 201)
    fake_post.status = {}
    monkeypatch.setattr(webpush, "post", fake_post)
    return outbox, fake_post, path


def test_subscribe_validates_endpoint_and_keys(client, push):
    login(client)
    dev = Device()
    assert client.get("/api/v1/push/key").json["publicKey"]
    bad = dev.subscription() | {"endpoint": "https://intranet.local/x"}
    assert client.post("/api/v1/push/subscribe", json=bad, headers=APP_HDR).status_code == 400
    bad = dev.subscription() | {"keys": {"p256dh": "abc", "auth": "x"}}
    assert client.post("/api/v1/push/subscribe", json=bad, headers=APP_HDR).status_code == 400
    assert client.post("/api/v1/push/subscribe", json=dev.subscription()).status_code == 403   # CSRF
    r = client.post("/api/v1/push/subscribe", json=dev.subscription() | {"label": "iPhone"}, headers=APP_HDR)
    assert r.status_code == 200
    devices = client.get("/api/v1/alerts").json["devices"]
    assert len(devices) == 1 and devices[0]["label"] == "iPhone" and "endpoint" not in devices[0]


def test_test_push_is_encrypted_and_signed(client, push):
    outbox, fake, _ = push
    login(client)
    dev = Device()
    client.post("/api/v1/push/subscribe", json=dev.subscription(), headers=APP_HDR)
    r = client.post("/api/v1/push/test", json={"endpoint": dev.endpoint}, headers=APP_HDR)
    assert r.json == {"sent": 1, "failed": 0}
    url, body, headers = outbox[0]
    assert url == dev.endpoint
    assert headers["Content-Encoding"] == "aes128gcm"
    assert headers["Authorization"].startswith("vapid t=")
    assert dev.decrypt(body)["title"] == "Zkušební upozornění"

    fake.status[dev.endpoint] = 410               # zařízení odinstalovalo aplikaci
    client.post("/api/v1/push/test", json={}, headers=APP_HDR)
    assert client.get("/api/v1/alerts").json["devices"] == []


def test_settings_are_validated(client):
    login(client)
    r = client.post("/api/v1/alerts/settings", headers=APP_HDR, json={"settings": {
        "frost": {"on": False, "below": -100}, "offline": {"minutes": "x"}, "evil": {"on": True}}})
    s = r.json["settings"]
    assert s["frost"] == {"on": False, "below": -30}
    assert s["offline"]["minutes"] == 20 and "evil" not in s
    assert client.get("/api/v1/alerts").json["settings"]["frost"]["on"] is False


# ------------------------------------------------------------------ pravidla

def _setup(app, push):
    outbox, _, path = push
    with app.app_context():
        conn = db.get_db()
        dev = Device()
        conn.execute("INSERT INTO push_subscriptions (username, endpoint, p256dh, auth, created_at) VALUES (?,?,?,?,?)",
                     ("petr", dev.endpoint, dev.subscription()["keys"]["p256dh"], dev.subscription()["keys"]["auth"], 0))
        conn.commit()
    sender = lambda conn: alerts.Sender(conn, webpush.load_vapid(path), "https://meteo.example.cz")  # noqa: E731
    return dev, outbox, sender


def _reading(conn, metric, value, ts):
    conn.execute("INSERT INTO readings (station_id, ts, metric, value) VALUES ('outdoor_station', ?, ?, ?)",
                 (ts, metric, value))
    conn.execute("UPDATE stations SET last_seen = ? WHERE id = 'outdoor_station'", (ts,))
    conn.commit()


def test_offline_and_back_online(app, push):
    dev, outbox, sender = _setup(app, push)
    with app.app_context():
        conn = db.get_db()
        settings = alerts.load_settings(conn)
        t = PRAGUE_NOON
        _reading(conn, "humidity", 80, t)
        alerts.check(conn, settings, sender(conn), now=t + 600)
        assert outbox == []
        alerts.check(conn, settings, sender(conn), now=t + 25 * 60)
        alerts.check(conn, settings, sender(conn), now=t + 30 * 60)       # podruhé už ne
        assert [dev.decrypt(b)["title"] for _, b, _ in outbox] == ["Stanice neposílá data"]
        _reading(conn, "humidity", 80, t + 40 * 60)
        alerts.check(conn, settings, sender(conn), now=t + 41 * 60)
        assert dev.decrypt(outbox[-1][1])["title"] == "Stanice je zase online"


def test_frost_hysteresis_and_quiet_hours(app, push):
    dev, outbox, sender = _setup(app, push)
    with app.app_context():
        conn = db.get_db()
        settings = alerts.load_settings(conn)
        t = PRAGUE_NIGHT                                                    # noční klid
        for i, temp in enumerate([0.4, -0.2, -0.6, -0.3, 0.5, -0.1]):
            _reading(conn, "temperature", temp, t + i * 300)
            alerts.check(conn, settings, sender(conn), now=t + i * 300 + 10)
        titles = [dev.decrypt(b)["title"] for _, b, _ in outbox]
        assert titles == ["Venku mrzne"]          # mráz projde nočním klidem, kolem nuly jen jednou
        assert "−0,3 °C" in dev.decrypt(outbox[0][1])["body"]   # druhé měření pod nulou


def test_air_held_during_quiet_then_sent(app, push):
    dev, outbox, sender = _setup(app, push)
    with app.app_context():
        conn = db.get_db()
        settings = alerts.load_settings(conn)
        t = PRAGUE_NIGHT + 5 * 3600                                         # 5:00, klid do 7:00
        for i in range(0, 120, 5):
            _reading(conn, "pm25", 60, t + i * 60)
            alerts.check(conn, settings, sender(conn), now=t + i * 60 + 10)
        assert outbox == []
        hist = conn.execute("SELECT rule, sent FROM notifications").fetchall()
        assert [(r["rule"], r["sent"]) for r in hist] == [("air", 0)]
        for i in range(120, 135, 5):                                        # po 7:00
            _reading(conn, "pm25", 60, t + i * 60)
            alerts.check(conn, settings, sender(conn), now=t + i * 60 + 10)
        assert [dev.decrypt(b)["title"] for _, b, _ in outbox] == ["Zhoršená kvalita vzduchu"]


def test_rain_from_nowcast_and_sensor_fault(app, push):
    dev, outbox, sender = _setup(app, push)
    with app.app_context():
        conn = db.get_db()
        settings = alerts.load_settings(conn)
        t = PRAGUE_NOON
        fc = {"nowcast": [{"t": t + i * 900, "precip": p} for i, p in enumerate([0, 0, 0.3, 0.6, 0.2])], "hours": []}
        alerts.check(conn, settings, sender(conn), fc, now=t)
        alerts.check(conn, settings, sender(conn), fc, now=t + 300)
        assert [dev.decrypt(b)["title"] for _, b, _ in outbox] == ["Do hodiny bude pršet"]
        assert "12:30" in dev.decrypt(outbox[0][1])["body"]

        diag = {"station": {"sensors": {"ds18b20": {"ok": False, "err": "CRC error"}}}, "rejected": []}
        for i in range(4):
            ts = t + 600 + i * 300
            conn.execute("UPDATE stations SET last_seen = ?, diag = ?, diag_at = ?", (ts, json.dumps(diag), ts))
            conn.commit()
            alerts.check(conn, settings, sender(conn), now=ts + 5)
        msgs = [dev.decrypt(b) for _, b, _ in outbox]
        assert msgs[-1]["title"] == "Porucha čidla teploty (DS18B20)" and "CRC error" in msgs[-1]["body"]
        assert len(msgs) == 2


def test_frost_forecast_once_per_evening(app, push):
    dev, outbox, sender = _setup(app, push)
    with app.app_context():
        conn = db.get_db()
        settings = alerts.load_settings(conn)
        t = PRAGUE_NOON + 6 * 3600                                          # 18:00
        fc = {"nowcast": [], "hours": [{"t": t + h * 3600, "temp": 3 - h * 0.5} for h in range(14)]}
        alerts.check(conn, settings, sender(conn), fc, now=t - 2 * 3600)    # 16:00 – ještě ne
        assert outbox == []
        alerts.check(conn, settings, sender(conn), fc, now=t)
        alerts.check(conn, settings, sender(conn), fc, now=t + 3600)
        assert [dev.decrypt(b)["title"] for _, b, _ in outbox] == ["V noci bude mrznout"]

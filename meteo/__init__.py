"""Meteo backend – JSON API pro meteostanice (ESP32) a PWA aplikaci."""
import os
from datetime import timedelta

from flask import Flask, jsonify

from . import db


def _env_bool(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None


def create_app(config=None):
    app = Flask(__name__)

    app.config.update(
        DATABASE=os.environ.get("METEO_DB", "/var/lib/meteo/meteo.db"),
        SECRET_KEY=os.environ.get("METEO_SECRET_KEY"),
        # Session cookie pro PWA: jen přes HTTPS, nečitelná z JS, neodesílá se z cizích webů
        SESSION_COOKIE_NAME="meteo_session",
        SESSION_COOKIE_SECURE=_env_bool("METEO_COOKIE_SECURE", True),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("METEO_SESSION_DAYS", "30"))),
        # Stanice je "offline", když neposlala data déle než tolik sekund
        STATION_OFFLINE_AFTER=int(os.environ.get("METEO_OFFLINE_AFTER", "600")),
        # Starý endpoint /data/simple pro displej (Pico WH) – jen z domácí sítě
        LEGACY_SIMPLE_NETS=os.environ.get("METEO_LEGACY_NETS", "192.168.1.0/24"),
        RETENTION_DAYS=int(os.environ.get("METEO_RETENTION_DAYS", "730")),
        # Poloha domova pro předpověď a radar (prázdné = vypnuto)
        LOCATION_LAT=_env_float("METEO_LAT"),
        LOCATION_LON=_env_float("METEO_LON"),
        FORECAST_URL=os.environ.get("METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast"),
        VAPID_KEY=os.environ.get("METEO_VAPID_KEY", "/etc/meteo/vapid.pem"),
        VAPID_SUB=os.environ.get("METEO_VAPID_SUB", "mailto:root@localhost"),
        PUSH_HOSTS=None,            # None = známé push služby prohlížečů (webpush.DEFAULT_PUSH_HOSTS)
        RADAR_URL=os.environ.get(
            "METEO_RADAR_URL", "https://opendata.chmi.cz/meteorology/weather/radar/composite"),
        MAX_CONTENT_LENGTH=64 * 1024,
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError("Chybí METEO_SECRET_KEY (viz .env.example)")

    db.init_app(app)

    from .api import bp as api_bp
    from .legacy import bp as legacy_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(legacy_bp)

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(error="not_found"), 404

    @app.errorhandler(405)
    def not_allowed(_e):
        return jsonify(error="method_not_allowed"), 405

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify(error="payload_too_large"), 413

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return app

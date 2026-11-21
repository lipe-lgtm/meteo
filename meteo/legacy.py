"""Zpětná kompatibilita pro displej s RSS (Pico WH), dokud ho nepřepíšeme.

GET /data/simple vrací stejný tvar jako starý server, ale jen klientům
z domácí sítě (METEO_LEGACY_NETS). Z VPN ani z internetu není dostupný.
"""
import ipaddress

from flask import Blueprint, abort, current_app, jsonify, request

from .db import get_db

bp = Blueprint("legacy", __name__)


def _allowed(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    nets = current_app.config["LEGACY_SIMPLE_NETS"]
    return any(
        addr in ipaddress.ip_network(n.strip(), strict=False)
        for n in nets.split(",") if n.strip()
    )


def _last(conn, station, metric):
    row = conn.execute(
        "SELECT value FROM readings WHERE station_id = ? AND metric = ? ORDER BY ts DESC LIMIT 1",
        (station, metric),
    ).fetchone()
    return row["value"] if row else None


@bp.get("/data/simple")
def simple():
    if not current_app.config["LEGACY_SIMPLE_NETS"] or not _allowed(request.remote_addr or ""):
        abort(404)
    conn = get_db()
    return jsonify(
        sensors={
            "outdoor": {
                "temperature": _last(conn, "outdoor_station", "temperature"),
                "humidity": _last(conn, "outdoor_station", "humidity"),
            },
            "indoor": {
                "temperature": _last(conn, "indoor_station", "temperature"),
                "humidity": _last(conn, "indoor_station", "humidity"),
            },
            "light": _last(conn, "outdoor_station", "light"),
            "air_quality": {
                "pm25": _last(conn, "outdoor_station", "pm25"),
                "aqi": _last(conn, "outdoor_station", "aqi"),
            },
        }
    )

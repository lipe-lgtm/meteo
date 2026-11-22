"""SQLite vrstva. Všechny časy jsou unix timestamp v UTC (int sekundy).

Data jsou v "dlouhém" formátu: jeden řádek = jedna hodnota jedné veličiny
z jedné stanice. Nový senzor = nová veličina, žádná změna schématu.
"""
import sqlite3
import time

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    api_key_hash  TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    last_seen     INTEGER,
    last_uptime   REAL
);

CREATE TABLE IF NOT EXISTS readings (
    station_id  TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    ts          INTEGER NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_lookup ON readings(station_id, metric, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

CREATE TABLE IF NOT EXISTS relays (
    station_id     TEXT PRIMARY KEY REFERENCES stations(id) ON DELETE CASCADE,
    desired_state  INTEGER NOT NULL DEFAULT 0,
    reported_state INTEGER,
    updated_at     INTEGER NOT NULL,
    updated_by     TEXT
);

CREATE TABLE IF NOT EXISTS users (
    username       TEXT PRIMARY KEY,
    password_hash  TEXT NOT NULL,
    created_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT
);
"""

# Povolené veličiny a jejich rozumný fyzikální rozsah (mimo rozsah = zahodit)
METRICS = {
    "temperature":   (-50.0, 70.0),
    "humidity":      (0.0, 100.0),
    "light":         (0.0, 200000.0),
    "soil_moisture": (0.0, 100.0),
    "pm1":           (0.0, 2000.0),
    "pm25":          (0.0, 2000.0),
    "pm10":          (0.0, 2000.0),
    "aqi":           (0.0, 500.0),
}


def now():
    return int(time.time())


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# Sloupce přidané po první verzi (starší databáze se doplní při startu)
MIGRATIONS = {
    "stations": {"diag": "TEXT", "diag_at": "INTEGER"},
}


def init_schema(conn):
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_schema(get_db())


def audit(conn, actor, action, detail=None):
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, detail) VALUES (?, ?, ?, ?)",
        (now(), actor, action, detail),
    )


def cleanup(conn, retention_days):
    cutoff = now() - retention_days * 86400
    cur = conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
    conn.commit()
    return cur.rowcount

#!/usr/bin/env python3
"""Správa meteo backendu z příkazové řádky.

  python3 manage.py init-db
  python3 manage.py set-password petr          # vytvoří uživatele / změní heslo
  python3 manage.py add-station outdoor_station "Venkovní stanice"
  python3 manage.py rotate-key outdoor_station  # nový klíč, starý přestane platit
  python3 manage.py list-stations
  python3 manage.py migrate-legacy /cesta/k/home_center.db [--since 2026-11-01]
  python3 manage.py cleanup                     # smaže data starší než METEO_RETENTION_DAYS

Cesta k databázi se bere z proměnné METEO_DB (stejně jako server).
"""
import argparse
import getpass
import os
import sqlite3
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from meteo import db
from meteo.auth import hash_api_key, hash_password, new_api_key

PRAGUE = ZoneInfo("Europe/Prague")


def open_db():
    path = os.environ.get("METEO_DB", "/var/lib/meteo/meteo.db")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = db.connect(path)
    db.init_schema(conn)
    return conn


def cmd_init_db(_args):
    open_db()
    print("Databáze připravena.")


def cmd_set_password(args):
    pw1 = getpass.getpass("Nové heslo: ")
    pw2 = getpass.getpass("Znovu: ")
    if pw1 != pw2:
        sys.exit("Hesla se neshodují.")
    if len(pw1) < 12:
        sys.exit("Heslo musí mít aspoň 12 znaků.")
    conn = open_db()
    conn.execute(
        """INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)
           ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash""",
        (args.username, hash_password(pw1), db.now()),
    )
    db.audit(conn, "cli", "set-password", args.username)
    conn.commit()
    print(f"Heslo pro '{args.username}' nastaveno.")


def _print_key(station_id, key):
    print()
    print(f"API klíč stanice '{station_id}' (zobrazí se JEN TEĎ, ulož ho do firmwaru):")
    print()
    print(f"    {key}")
    print()


def cmd_add_station(args):
    conn = open_db()
    if conn.execute("SELECT 1 FROM stations WHERE id = ?", (args.id,)).fetchone():
        sys.exit(f"Stanice '{args.id}' už existuje – pro nový klíč použij rotate-key.")
    key = new_api_key()
    conn.execute(
        "INSERT INTO stations (id, name, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
        (args.id, args.name or args.id, hash_api_key(key), db.now()),
    )
    db.audit(conn, "cli", "add-station", args.id)
    conn.commit()
    _print_key(args.id, key)


def cmd_ensure_station(args):
    """Pro install.sh: vytvoří stanici jen pokud chybí a vypíše POUZE nový klíč."""
    conn = open_db()
    if conn.execute("SELECT 1 FROM stations WHERE id = ?", (args.id,)).fetchone():
        return
    key = new_api_key()
    conn.execute(
        "INSERT INTO stations (id, name, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
        (args.id, args.name or args.id, hash_api_key(key), db.now()),
    )
    db.audit(conn, "install", "add-station", args.id)
    conn.commit()
    print(key)


def cmd_user_count(_args):
    print(open_db().execute("SELECT COUNT(*) FROM users").fetchone()[0])


def cmd_rotate_key(args):
    conn = open_db()
    key = new_api_key()
    cur = conn.execute(
        "UPDATE stations SET api_key_hash = ? WHERE id = ?", (hash_api_key(key), args.id)
    )
    if cur.rowcount == 0:
        sys.exit(f"Stanice '{args.id}' neexistuje.")
    db.audit(conn, "cli", "rotate-key", args.id)
    conn.commit()
    _print_key(args.id, key)


def cmd_list_stations(_args):
    conn = open_db()
    for r in conn.execute("SELECT id, name, last_seen FROM stations ORDER BY id"):
        seen = (
            datetime.fromtimestamp(r["last_seen"], PRAGUE).strftime("%Y-%m-%d %H:%M:%S")
            if r["last_seen"] else "nikdy"
        )
        print(f"{r['id']:<20} {r['name']:<30} naposledy: {seen}")


def cmd_cleanup(_args):
    days = int(os.environ.get("METEO_RETENTION_DAYS", "730"))
    deleted = db.cleanup(open_db(), days)
    print(f"Smazáno {deleted} záznamů starších než {days} dní.")


# ------------------------------------------------------------------ migrace

def parse_legacy_ts(raw):
    """Staré časy jsou v různých tvarech: s offsetem (+01:00), nebo bez něj.
    Bez offsetu je bereme jako pražský místní čas (starý server je tak ukládal
    po opravě časové zóny)."""
    if raw is None:
        return None
    text = str(raw).strip().replace("T", " ")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PRAGUE)
    return int(dt.astimezone(timezone.utc).timestamp())


# (stará tabulka, sloupec) -> (stanice, veličina)
LEGACY_COLUMNS = {
    "measurements": {
        "outdoor_temperature": ("outdoor_station", "temperature"),
        "outdoor_humidity": ("outdoor_station", "humidity"),
        "indoor_temperature": ("indoor_station", "temperature"),
        "indoor_humidity": ("indoor_station", "humidity"),
        "light_lux": ("outdoor_station", "light"),
        "soil_moisture": ("outdoor_station", "soil_moisture"),
        "pm25": ("outdoor_station", "pm25"),
        "pm10": ("outdoor_station", "pm10"),
        "aqi": ("outdoor_station", "aqi"),
    },
    "sensor_data": {
        "outdoor_temp": ("outdoor_station", "temperature"),
        "outdoor_hum": ("outdoor_station", "humidity"),
        "indoor_temp": ("indoor_station", "temperature"),
        "indoor_hum": ("indoor_station", "humidity"),
        "light_lux": ("outdoor_station", "light"),
        "soil_moisture": ("outdoor_station", "soil_moisture"),
    },
}


def cmd_migrate_legacy(args):
    src = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    since = parse_legacy_ts(args.since) if args.since else None
    dst = open_db()
    known = {r["id"] for r in dst.execute("SELECT id FROM stations")}

    total = skipped = 0
    for table, columns in LEGACY_COLUMNS.items():
        if table not in tables:
            continue
        existing_cols = {r[1] for r in src.execute(f"PRAGMA table_info({table})")}
        cols = [c for c in columns if c in existing_cols]
        print(f"Tabulka {table}: sloupce {', '.join(cols)}")
        batch = []
        for row in src.execute(f"SELECT timestamp, {', '.join(cols)} FROM {table}"):
            ts = parse_legacy_ts(row["timestamp"])
            if ts is None or (since and ts < since):
                skipped += 1
                continue
            for col in cols:
                station, metric = columns[col]
                value = row[col]
                if station not in known or value is None:
                    continue
                lo, hi = db.METRICS[metric]
                if isinstance(value, (int, float)) and lo <= value <= hi:
                    batch.append((station, ts, metric, float(value)))
            if len(batch) >= 5000:
                dst.executemany("INSERT INTO readings VALUES (?, ?, ?, ?)", batch)
                total += len(batch)
                batch.clear()
        if batch:
            dst.executemany("INSERT INTO readings VALUES (?, ?, ?, ?)", batch)
            total += len(batch)

    missing = {s for cols in LEGACY_COLUMNS.values() for s, _ in cols.values()} - known
    db.audit(dst, "cli", "migrate-legacy", f"{args.source} rows={total}")
    dst.commit()
    print(f"Hotovo: přeneseno {total} hodnot, přeskočeno {skipped} řádků.")
    if missing:
        print(f"Pozn.: stanice {', '.join(sorted(missing))} zatím neexistují, jejich data se nepřenesla.")


def main():
    p = argparse.ArgumentParser(description="Správa meteo backendu")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    sp = sub.add_parser("set-password")
    sp.add_argument("username")
    sp.set_defaults(func=cmd_set_password)

    sp = sub.add_parser("add-station")
    sp.add_argument("id")
    sp.add_argument("name", nargs="?")
    sp.set_defaults(func=cmd_add_station)

    sp = sub.add_parser("ensure-station", help=argparse.SUPPRESS)
    sp.add_argument("id")
    sp.add_argument("name", nargs="?")
    sp.set_defaults(func=cmd_ensure_station)

    sub.add_parser("user-count", help=argparse.SUPPRESS).set_defaults(func=cmd_user_count)

    sp = sub.add_parser("rotate-key")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_rotate_key)

    sub.add_parser("list-stations").set_defaults(func=cmd_list_stations)
    sub.add_parser("cleanup").set_defaults(func=cmd_cleanup)

    sp = sub.add_parser("migrate-legacy")
    sp.add_argument("source", help="cesta ke staré home_center.db")
    sp.add_argument("--since", help="přenést jen data od data, např. 2026-11-01")
    sp.set_defaults(func=cmd_migrate_legacy)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

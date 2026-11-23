"""Autentizace: stanice přes API klíč, lidé přes session (login heslem)."""
import hashlib
import hmac
import secrets
import threading
import time
from functools import wraps

from flask import g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

# ---------- API klíče stanic ----------

def new_api_key():
    """Náhodný klíč s vysokou entropií – stačí ho ukládat jako SHA-256."""
    return "mk_" + secrets.token_urlsafe(32)


def hash_api_key(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def station_required(view):
    """Endpoint pro stanice: hlavička X-API-Key musí patřit existující stanici."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if not key:
            return jsonify(error="missing_api_key"), 401
        key_hash = hash_api_key(key)
        rows = get_db().execute("SELECT id, api_key_hash FROM stations").fetchall()
        station = None
        for row in rows:  # porovnání v konstantním čase
            if hmac.compare_digest(row["api_key_hash"], key_hash):
                station = row["id"]
        if station is None:
            return jsonify(error="invalid_api_key"), 401
        g.station_id = station
        return view(*args, **kwargs)
    return wrapper


# ---------- Uživatelé ----------

def hash_password(password):
    return generate_password_hash(password, method="scrypt")


def verify_user(username, password):
    row = get_db().execute(
        "SELECT password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        # Stejná práce i pro neexistujícího uživatele (časový side-channel)
        check_password_hash(_DUMMY_HASH, password)
        return False
    return check_password_hash(row["password_hash"], password)


_DUMMY_HASH = generate_password_hash("dummy-password", method="scrypt")


def login_required(view):
    """Endpoint pro lidi: platná session + ochrana proti CSRF.

    Cookie je SameSite=Strict a změnové požadavky musí být JSON s vlastní
    hlavičkou X-Requested-With – obyčejný formulář z cizího webu to neumí.
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = session.get("user")
        if not user:
            return jsonify(error="unauthorized"), 401
        # Cookie je podepsaná, ale platí, dokud nevyprší. Proto při každém požadavku
        # ověřit, že uživatel pořád existuje a nebyl odhlášen všude (změna hesla atd.)
        row = get_db().execute(
            "SELECT session_version FROM users WHERE username = ?", (user,)).fetchone()
        if row is None or session.get("sv") != row["session_version"]:
            session.clear()
            return jsonify(error="unauthorized"), 401
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("X-Requested-With") != "meteo-app":
                return jsonify(error="csrf_check_failed"), 403
        g.user = user
        return view(*args, **kwargs)
    return wrapper


# ---------- Omezení pokusů o přihlášení ----------

class LoginLimiter:
    """Max N neúspěšných pokusů z jedné IP za okno. Paměťové – stačí pro 1 proces."""

    def __init__(self, max_failures=5, window=15 * 60):
        self.max_failures = max_failures
        self.window = window
        self._fails = {}
        self._lock = threading.Lock()

    def _prune(self, ip, t):
        self._fails[ip] = [x for x in self._fails.get(ip, []) if t - x < self.window]

    def blocked(self, ip):
        with self._lock:
            t = time.time()
            self._prune(ip, t)
            return len(self._fails[ip]) >= self.max_failures

    def fail(self, ip):
        with self._lock:
            t = time.time()
            self._prune(ip, t)
            self._fails[ip].append(t)

    def reset(self, ip):
        with self._lock:
            self._fails.pop(ip, None)


limiter = LoginLimiter()

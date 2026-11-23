"""Web Push bez dalších knihoven: šifrování RFC 8291 (aes128gcm) a VAPID RFC 8292.

Zpráva se šifruje klíčem zařízení, takže push služba (Apple, Google, Mozilla)
obsah nevidí – jen ho doručí. Server se prokazuje VAPID klíčem, který
vygeneruje install.sh a leží v /etc/meteo/vapid.pem.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
MAX_PAYLOAD = 3000

# Push služby prohlížečů. Subscription endpoint posílá klient – bez seznamu
# by šlo server přimět posílat požadavky kamkoli (SSRF).
DEFAULT_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "web.push.apple.com",
    ".push.apple.com",
    "updates.push.services.mozilla.com",
    ".push.services.mozilla.com",
    ".notify.windows.com",
)


class PushGone(Exception):
    """Zařízení se odhlásilo / odinstalovalo aplikaci – odběr smazat."""


class PushError(Exception):
    pass


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text):
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _public_bytes(key):
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ------------------------------------------------------------------ klíče

def generate_vapid_pem():
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def load_vapid(path):
    with open(path, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def vapid_public_key(key):
    """Veřejný klíč pro pushManager.subscribe (applicationServerKey)."""
    return b64u(_public_bytes(key))


# ------------------------------------------------------------------ šifrování

def encrypt(payload, p256dh, auth, salt=None, server_key=None):
    """RFC 8291: obsah zašifrovaný pro jedno zařízení (jeden záznam aes128gcm)."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    if len(ua_public) != 65 or ua_public[0] != 4 or len(auth_secret) != 16:
        raise PushError("neplatné klíče odběru")
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh_secret = server_key.exchange(ec.ECDH(), ua_key)

    ikm = _hkdf(auth_secret, ecdh_secret, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)   # 0x02 = poslední záznam
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


# ------------------------------------------------------------------ VAPID

def vapid_header(key, endpoint, subject, now=None):
    parsed = urllib.parse.urlsplit(endpoint)
    claims = {
        "aud": f"{parsed.scheme}://{parsed.netloc}",
        "exp": int((now or time.time()) + 12 * 3600),
        "sub": subject,
    }
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={vapid_public_key(key)}"


def endpoint_allowed(endpoint, hosts=DEFAULT_PUSH_HOSTS, allow_http=False):
    try:
        parsed = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return False
    if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h.lstrip(".") or (h.startswith(".") and host.endswith(h)) for h in hosts)


# ------------------------------------------------------------------ odeslání

def post(url, body, headers, timeout=10):
    """Samostatně kvůli testům. Vrací HTTP status."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def send(subscription, message, key, subject, ttl=6 * 3600, urgency="normal"):
    """Pošle zprávu (dict) jednomu zařízení. subscription: endpoint, p256dh, auth."""
    payload = json.dumps(message, ensure_ascii=False).encode()
    if len(payload) > MAX_PAYLOAD:
        raise PushError("zpráva je příliš dlouhá")
    body = encrypt(payload, subscription["p256dh"], subscription["auth"])
    status = post(subscription["endpoint"], body, {
        "Authorization": vapid_header(key, subscription["endpoint"], subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": urgency,
    })
    if status in (404, 410):
        raise PushGone(status)
    if not 200 <= status < 300:
        raise PushError(f"push služba vrátila HTTP {status}")
    return status

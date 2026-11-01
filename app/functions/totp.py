"""Time-based one-time passwords (RFC 6238) using only the standard library."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

PERIOD = 30
DIGITS = 6
ISSUER = "CalenCraft"
RECOVERY_CODE_COUNT = 8


def generate_secret():
    """160-bit random secret, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // PERIOD)


def code_at(secret, step):
    return _hotp(secret, step)


def verify(secret, code, last_step=0, now=None, window=1):
    """Return the matched time step, or None.

    Accepts +/- `window` steps for clock drift and refuses any step <= `last_step`, so a
    code can't be replayed (including one that was just used to log in).
    """
    code = "".join((code or "").split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    matched = None
    for s in range(step - window, step + window + 1):
        # compare every candidate (no early exit) to keep timing uniform
        if hmac.compare_digest(_hotp(secret, s), code) and s > last_step:
            matched = s
    return matched


def provisioning_uri(secret, account):
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"


def format_secret(secret):
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ── Recovery codes ──────────────────────────────────────────────────────────

def _normalise_recovery(code):
    return "".join((code or "").lower().split()).replace("-", "")


def _hash_recovery(code):
    return hashlib.sha256(_normalise_recovery(code).encode()).hexdigest()


def generate_recovery_codes():
    """Return (plaintext_codes, json_of_hashes). Plaintext is shown once and never stored."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(RECOVERY_CODE_COUNT)]
    pretty = [f"{c[:5]}-{c[5:]}" for c in codes]
    return pretty, json.dumps([_hash_recovery(c) for c in codes])


def consume_recovery_code(stored_json, code):
    """Return the updated JSON (code removed) if `code` is valid, else None."""
    try:
        hashes = json.loads(stored_json or "[]")
    except ValueError:
        return None
    target = _hash_recovery(code)
    hit = None
    for h in hashes:
        if hmac.compare_digest(h, target):
            hit = h
    if hit is None:
        return None
    hashes.remove(hit)
    return json.dumps(hashes)


def recovery_codes_remaining(stored_json):
    try:
        return len(json.loads(stored_json or "[]"))
    except ValueError:
        return 0


def provisioning_qr_svg(uri):
    """Inline SVG QR code for an otpauth:// URI (dark modules on a white quiet zone).

    `segno` is imported lazily so a missing optional install only disables the QR image;
    the key and link still work.
    """
    try:
        import segno
    except ImportError:
        return None
    qr = segno.make(uri, error="m", micro=False)
    return qr.svg_inline(scale=5, border=4, dark="#000000", light="#ffffff", omitsize=True,
                         title="QR code for your authenticator app", svgclass="totp-qr-svg")

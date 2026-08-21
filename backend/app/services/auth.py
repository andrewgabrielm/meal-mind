"""Authentication primitives — pure stdlib, same policy as the other models.

Passwords: PBKDF2-HMAC-SHA256, 200k iterations, 16-byte random salt, stored as
"pbkdf2$<salt-hex>$<hash-hex>". Tokens: HS256 JWT hand-rolled with hmac +
base64url — no PyJWT dependency. Constant-time comparisons throughout.

No SQL, no HTTP here.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

PBKDF2_ITERATIONS = 200_000
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30       # 30 days: it's a household app


# ------------------------------------------------------------ passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------ JWT (HS256)

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user_id: int, secret: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"sub": str(user_id),
                                  "exp": int(time.time()) + ttl}).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def parse_token(token: str, secret: str) -> int:
    """Returns the user id, or raises ValueError on any defect."""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        raise ValueError("malformed token")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected = _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig_b64):
        raise ValueError("bad signature")
    try:
        payload = json.loads(_b64url_decode(payload_b64))
        exp = int(payload["exp"])
        user_id = int(payload["sub"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise ValueError("bad payload")
    if time.time() > exp:
        raise ValueError("token expired")
    return user_id
